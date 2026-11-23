from src.providers import call
from src.humanprompt import RequestError, build_request
from src.prompt import build_system_prompt, build_user_message

def show_banner():
    print("=" * 40)
    print("        WORKFLOW GENERATOR")
    print("=" * 40)
    print("Describe a task and We'll build a workflow for it.")
    print("Type 'quit' to exit.\n")


def get_prompt():
    while True:
        text = input("Your task: ").strip()

        if text == "":
            print("Please type something.\n")
            continue

        return text


def main():
    show_banner()

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

        system = build_system_prompt(request)
        user_message = build_user_message(request)  # the cleaned-up text, not the raw input

        # A failed call (no API key, network error, timeout) should not end the session either.
        try:
            result = call(model="gemini", system=system, messages=[{"role": "user", "content": user_message}])
        except Exception as e:
            print(f"LLM call failed: {type(e).__name__}: {e}\n")
            continue

        print(f"LLM Response: {result.text}\n")
        print(f"Tokens: {result.input_tokens} in / {result.output_tokens} out | Time: {result.seconds:.1f}s")
        if result.truncated:
            print(f"WARNING: the response was cut off ({result.stop_reason}); the JSON is likely incomplete.")
        print()


if __name__ == "__main__":
    main()