import argparse
import json

from src.generation import generate_draft
from src.humanprompt import RequestError, build_request
from src.llm import PROVIDERS, model_for


def show_banner(provider: str):
    print("=" * 40)
    print("        WORKFLOW GENERATOR")
    print("=" * 40)
    print(f"Provider: {provider} ({model_for(provider)})")
    print("Describe a task and We'll build a workflow for it.")
    print("Type 'quit' to exit.\n")


def get_prompt():
    while True:
        text = input("Your task: ").strip()

        if text == "":
            print("Please type something.\n")
            continue

        return text


def parse_args():
    parser = argparse.ArgumentParser(description="Generate a workflow graph from a task description.")
    parser.add_argument("--provider", choices=PROVIDERS, default="gemini",
                        help="which LLM to use (default: gemini)")
    return parser.parse_args()


def main():
    args = parse_args()
    show_banner(args.provider)

    while True:
        prompt = get_prompt()

        if prompt.lower() == "quit":
            print("Goodbye!")
            break

        print(f"\nPrompt Received: {prompt}\n")

        # Bad input (too long, unknown tools) should not end the session.
        try:
            request = build_request(prompt)
        except RequestError as e:
            print(f"Request rejected: {e}\n")
            continue

        # A failed call (no API key, network error, timeout) should not end the session either.
        try:
            gen = generate_draft(request, args.provider)
        except Exception as e:
            print(f"LLM call failed: {type(e).__name__}: {e}\n")
            continue

        r = gen.llm
        if gen.draft is not None:
            print("Workflow draft:")
            print(json.dumps(gen.draft.model_dump(exclude_none=True), indent=2))
        else:
            print(f"LLM Response (not a valid draft): {r.text}")

        if gen.errors:
            print(f"\nFormat errors ({len(gen.errors)}):")
            for e in gen.errors:
                print(f"  - {e}")
        else:
            print("\nFormat check: OK")

        cached = " | from local cache" if r.cached else ""
        print(f"Tokens: {r.input_tokens} in / {r.output_tokens} out / {r.thinking_tokens} thinking"
              f" | Time: {r.seconds:.1f}s | Stop: {r.stop_reason}{cached}\n")


if __name__ == "__main__":
    main()
