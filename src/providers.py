# Providers for LLMs (Large Language Models) like Gemini and Claude. 
import os
import time
from dotenv import load_dotenv
from dataclasses import dataclass

load_dotenv()  # Load environment variables from .env file

# CLAUDE_MODEL = "claude-haiku-5-5" # skip this for it need billing
GEMINI_MODEL = "gemini-3.5-flash"
MAX_TOKENS = 4000

@dataclass
class LLMResult:
    text: str
    input_tokens: int
    output_tokens: int
    seconds: float
    stop_reason: str   # the provider's own reason, as text, e.g. "STOP" or "MAX_TOKENS"
    truncated: bool = False   # True when the output hit the token limit (provider-neutral flag)

def call_gemini(system: str, messages: list[dict]) -> LLMResult:
    from google import genai
    from google.genai import types
    # print("call_gemini received system length:", len(system))
    client = genai.Client(http_options=types.HttpOptions(timeout=120_000)) # quit when no response in 2 minutes
 
    # Gemini calls the assistant role "model" and wraps text in "parts".
    contents = [
        {
            "role": "model" if m["role"] == "assistant" else "user",
            "parts": [{"text": m["content"]}],
        }
        for m in messages
    ]
 
    start = time.perf_counter()
    r = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=contents,
        config=types.GenerateContentConfig(
            system_instruction=system,
            max_output_tokens=MAX_TOKENS,
            response_mime_type="application/json",
        ),
    )
    seconds = time.perf_counter() - start

    # usage_metadata and candidates can both be missing, e.g. when the prompt is blocked.
    usage = r.usage_metadata
    if r.candidates:
        reason = r.candidates[0].finish_reason
        stop_reason = getattr(reason, "name", str(reason)) if reason is not None else "UNKNOWN"
    else:
        block = r.prompt_feedback.block_reason if r.prompt_feedback else None
        stop_reason = f"BLOCKED: {getattr(block, 'name', block)}" if block is not None else "NO_CANDIDATES"

    return LLMResult(
        text=r.text or "",
        input_tokens=(usage.prompt_token_count or 0) if usage else 0,
        output_tokens=(usage.candidates_token_count or 0) if usage else 0,
        seconds=seconds,
        stop_reason=stop_reason,
        truncated=stop_reason == "MAX_TOKENS",
    )
#def call_claude(system: str, messages: list[dict]) -> LLMResult:

def call(model: str, system: str, messages: list[dict]) -> LLMResult:
    #if model == "claude":
    #    return call_claude(system, messages)
    if model == "gemini":
        return call_gemini(system, messages)
    raise ValueError(f"Unknown model: {model}")
