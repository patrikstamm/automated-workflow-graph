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


if __name__ == "__main__":
    main()