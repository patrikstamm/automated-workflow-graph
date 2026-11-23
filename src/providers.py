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
    stop_reason: str 

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
 
    return LLMResult(
        text=r.text or "",
        input_tokens=r.usage_metadata.prompt_token_count or 0,
        output_tokens=r.usage_metadata.candidates_token_count or 0,
        seconds=seconds,
        stop_reason=str(r.candidates[0].finish_reason),
    )
#def call_claude(system: str, messages: list[dict]) -> LLMResult:

def call(model: str, system: str, messages: list[dict]) -> LLMResult:
    #if model == "claude":
    #    return call_claude(system, messages)
    if model == "gemini":
        return call_gemini(system, messages)
    raise ValueError(f"Unknown model: {model}")
