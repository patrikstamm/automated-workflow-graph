# Gemini provider (google-genai). Only src/llm/base.py calls this.
from __future__ import annotations

import time

from src.llm.base import MAX_TOKENS, TIMEOUT_SECONDS, LLMResult

BLOCKED_REASONS = {"SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "RECITATION", "IMAGE_SAFETY"}


def call_gemini(model: str, system: str, messages: list[dict], schema: dict | None) -> LLMResult:
    from google import genai
    from google.genai import types

    client = genai.Client(http_options=types.HttpOptions(timeout=TIMEOUT_SECONDS * 1000))  # milliseconds

    # Gemini calls the assistant role "model" and wraps text in "parts".
    contents = [
        {"role": "model" if m["role"] == "assistant" else "user", "parts": [{"text": m["content"]}]}
        for m in messages
    ]
    config = types.GenerateContentConfig(
        system_instruction=system,
        max_output_tokens=MAX_TOKENS,
        response_mime_type="application/json",
        # Full JSON Schema (with $defs/$ref). The older response_schema only takes an OpenAPI subset.
        response_json_schema=schema,
        # Temperature and thinking are left at Gemini's defaults (DECISIONS.md section 2).
    )

    start = time.perf_counter()
    r = client.models.generate_content(model=model, contents=contents, config=config)
    seconds = time.perf_counter() - start

    # usage_metadata and candidates can both be missing, e.g. when the prompt is blocked.
    usage = r.usage_metadata
    if r.candidates:
        reason = r.candidates[0].finish_reason
        stop_reason = getattr(reason, "name", str(reason)) if reason is not None else "UNKNOWN"
    else:
        block = r.prompt_feedback.block_reason if r.prompt_feedback else None
        stop_reason = f"BLOCKED: {getattr(block, 'name', block)}" if block is not None else "NO_CANDIDATES"

    def count(field: str) -> int:
        return (getattr(usage, field, None) or 0) if usage else 0

    return LLMResult(
        text=r.text or "",
        input_tokens=count("prompt_token_count"),
        output_tokens=count("candidates_token_count"),      # excludes thinking
        thinking_tokens=count("thoughts_token_count"),
        cache_read_tokens=count("cached_content_token_count"),
        seconds=seconds,
        stop_reason=stop_reason,
        truncated=stop_reason == "MAX_TOKENS",
        blocked=stop_reason.startswith("BLOCKED") or stop_reason in BLOCKED_REASONS,
        provider="gemini",
        model=model,
    )
