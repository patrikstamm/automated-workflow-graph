# Provider-neutral LLM interface (DECISIONS.md section 2).
# Everything outside src/llm/ calls call(); nothing else may import a provider SDK.
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()  # API keys come from .env (never committed)

PROVIDERS = ("claude", "gemini")
DEFAULT_MODELS = {"claude": "claude-haiku-5-5", "gemini": "gemini-3.5-flash"}
MAX_TOKENS = 4000
TIMEOUT_SECONDS = 120


@dataclass
class LLMResult:
    text: str
    input_tokens: int             # prompt tokens, including any read from the provider's cache
    output_tokens: int            # visible answer tokens, excluding thinking
    seconds: float
    stop_reason: str              # the provider's own reason, e.g. "end_turn", "STOP", "MAX_TOKENS"
    truncated: bool = False       # output hit the token limit; the JSON is likely incomplete
    blocked: bool = False         # the provider refused or blocked the request
    thinking_tokens: int = 0      # billed reasoning tokens (billed output = output_tokens + thinking_tokens)
    cache_read_tokens: int = 0    # prompt tokens served from the provider's prompt cache
    cache_write_tokens: int = 0   # prompt tokens written to the provider's prompt cache
    provider: str = ""
    model: str = ""
    cached: bool = False          # served from our local record/replay cache (no API call, no cost)


def model_for(provider: str) -> str:
    """Model id for a provider. Override with CLAUDE_MODEL / GEMINI_MODEL in .env."""
    return os.getenv(f"{provider.upper()}_MODEL") or DEFAULT_MODELS[provider]


def call(provider: str, system: str, messages: list[dict], schema: dict | None = None) -> LLMResult:
    """Send one request. messages: [{"role": "user" | "assistant", "content": str}, ...].
    schema: a JSON schema the answer must follow (see src/llm/schema.py), or None for plain JSON."""
    if provider not in PROVIDERS:
        raise ValueError(f"Unknown provider: {provider!r}. Use one of {', '.join(PROVIDERS)}.")
    from src.llm import replay   # imported here so tests can switch cache modes per call
    return replay.cached_call(provider, model_for(provider), system, messages, schema, _dispatch)


def _dispatch(provider: str, model: str, system: str, messages: list[dict], schema: dict | None) -> LLMResult:
    if provider == "claude":
        from src.llm.claude import call_claude
        return call_claude(model, system, messages, schema)
    from src.llm.gemini import call_gemini
    return call_gemini(model, system, messages, schema)
