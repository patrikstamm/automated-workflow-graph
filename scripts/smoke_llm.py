# Live check: one real call per provider, to confirm both accept our draft schema.
# Needs keys in .env (see .env.example). Costs two small requests.
#   docker compose run --rm langgraph-app python scripts/smoke_llm.py
#   docker compose run --rm langgraph-app python scripts/smoke_llm.py claude
# It never prints API keys, only whether each one is set.
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.generation import generate_draft   # noqa: E402  (loads .env through src.llm)
from src.humanprompt import build_request  # noqa: E402
from src.llm import PROVIDERS, model_for   # noqa: E402

KEY_NAMES = {"claude": "ANTHROPIC_API_KEY", "gemini": "GEMINI_API_KEY"}
TASK = "Echo the word hello."


def main() -> int:
    os.environ["LLM_CACHE"] = "off"   # always a real call
    providers = sys.argv[1:] or list(PROVIDERS)
    failures = 0

    for provider in providers:
        print(f"=== {provider} ({model_for(provider)}) ===")
        if not os.getenv(KEY_NAMES[provider]) and not (provider == "gemini" and os.getenv("GOOGLE_API_KEY")):
            print(f"SKIPPED: {KEY_NAMES[provider]} is not set in .env\n")
            continue
        try:
            gen = generate_draft(build_request(TASK), provider)
        except Exception as e:
            failures += 1
            print(f"FAILED: {type(e).__name__}: {e}\n")
            continue

        r = gen.llm
        print(f"Stop reason: {r.stop_reason} | Time: {r.seconds:.1f}s")
        print(f"Tokens: {r.input_tokens} in / {r.output_tokens} out / {r.thinking_tokens} thinking"
              f" / {r.cache_read_tokens} cache read")
        if gen.ok:
            print(f"OK: valid draft with {len(gen.draft.nodes)} nodes and {len(gen.draft.edges)} edges\n")
        else:
            failures += 1
            print("NOT OK:")
            for e in gen.errors:
                print(f"  - {e}")
            print(f"Raw answer: {r.text[:500]}\n")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
