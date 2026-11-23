# Claude provider (anthropic). Only src/llm/base.py calls this.
from __future__ import annotations

import time

from src.llm.base import MAX_TOKENS, TIMEOUT_SECONDS, LLMResult


def call_claude(model: str, system: str, messages: list[dict], schema: dict | None) -> LLMResult:
    import anthropic

    client = anthropic.Anthropic(timeout=TIMEOUT_SECONDS)   # reads ANTHROPIC_API_KEY; seconds
    kwargs: dict = {
        "model": model,
        "max_tokens": MAX_TOKENS,
        "system": system,
        "messages": to_claude_messages(messages),
        # No temperature: the Messages API in anthropic 2.12 has no such setting (DECISIONS.md section 2).
    }
    if schema is not None:
        kwargs["output_config"] = {"format": {"type": "json_schema", "schema": schema}}

    start = time.perf_counter()
    r = client.messages.create(**kwargs)
    seconds = time.perf_counter() - start

    text = "".join(block.text for block in r.content if getattr(block, "type", "") == "text")
    usage = r.usage
    details = getattr(usage, "output_tokens_details", None)
    thinking = (getattr(details, "thinking_tokens", None) or 0) if details else 0
    cache_read = getattr(usage, "cache_read_input_tokens", None) or 0
    cache_write = getattr(usage, "cache_creation_input_tokens", None) or 0
    stop_reason = r.stop_reason or "unknown"

    return LLMResult(
        text=text,
        # Claude reports cached prompt tokens separately; add them so input_tokens means the same as Gemini's.
        input_tokens=(usage.input_tokens or 0) + cache_read + cache_write,
        # Claude's output_tokens includes thinking; keep only the visible part here.
        output_tokens=(usage.output_tokens or 0) - thinking,
        thinking_tokens=thinking,
        cache_read_tokens=cache_read,
        cache_write_tokens=cache_write,
        seconds=seconds,
        stop_reason=stop_reason,
        truncated=stop_reason == "max_tokens",
        blocked=stop_reason == "refusal",
        provider="claude",
        model=model,
    )


def to_claude_messages(messages: list[dict]) -> list[dict]:
    """Claude needs the first message from the user and roles that alternate.
    Consecutive messages with the same role are merged."""
    if not messages:
        raise ValueError("At least one message is required.")
    if messages[0]["role"] != "user":
        raise ValueError("The first message must come from the user.")
    merged: list[dict] = []
    for m in messages:
        role = "assistant" if m["role"] == "assistant" else "user"
        if merged and merged[-1]["role"] == role:
            merged[-1]["content"] += "\n\n" + m["content"]
        else:
            merged.append({"role": role, "content": m["content"]})
    return merged
