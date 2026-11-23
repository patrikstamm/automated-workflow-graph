from src.catalog import TOOLS
from src.providers import call
from src.humanprompt import UserRequest, build_request
from src.prompt import build_system_prompt

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
        request = build_request(prompt)                              
        system = build_system_prompt(request) 
        
        # Call the LLM with the user's prompt
        result = call(model="gemini", system=system, messages=[{"role": "user", "content": prompt}])
        print(f"LLM Response: {result.text}\n")
        print(f"Tokens: {result.input_tokens} in / {result.output_tokens} out | Time: {result.seconds:.1f}s")


if __name__ == "__main__":
    main()