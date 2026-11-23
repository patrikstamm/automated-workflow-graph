import argparse
import json

from src.humanprompt import RequestError, build_request
from src.llm import PROVIDERS, model_for
from src.pipeline import MAX_REPAIR_ROUNDS, run_condition_a, run_condition_b
from src.verify import VerifyConfig


def show_banner(provider: str, repair: bool):
    print("=" * 40)
    print("        WORKFLOW GENERATOR")
    print("=" * 40)
    mode = f"verify + repair (up to {MAX_REPAIR_ROUNDS} rounds)" if repair else "one call, no repair"
    print(f"Provider: {provider} ({model_for(provider)}) | Mode: {mode}")
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
    parser.add_argument("--no-repair", action="store_true",
                        help="condition A: one LLM call, no verification repair rounds")
    parser.add_argument("--require-human", action="store_true",
                        help="the workflow must contain a human step")
    parser.add_argument("--sensitive", action="append", default=[], metavar="TOOL",
                        help="a tool that needs a human approval step before it (repeatable)")
    return parser.parse_args()


def print_run(run):
    for r in run.rounds:
        llm = r.llm
        cached = ", cached" if llm.cached else ""
        print(f"Round {r.n}: {r.verdict} | tokens {llm.input_tokens} in / {llm.output_tokens} out"
              f" / {llm.thinking_tokens} thinking | {llm.seconds:.1f}s{cached}")
        if r.repair_text:
            print("  Sent back to the LLM:")
            print("    " + r.repair_text.replace("\n", "\n    "))

    final = run.final
    print(f"\nResult: {run.status} after {run.repair_rounds} repair round(s)")
    if final.draft is not None:
        print("Workflow draft:")
        print(json.dumps(final.draft.model_dump(exclude_none=True), indent=2))
    else:
        print(f"LLM Response (not a valid draft): {final.llm.text}")
    for e in final.format_errors:
        print(f"  Format error: {e}")
    if final.report:
        for f in final.report.findings:
            note = f" ({f.note})" if f.note else ""
            print(f"  {f.status} [{f.origin}: {f.check_id}] {f.message}{note}")

    t = run.totals()
    print(f"Totals: {t['llm_calls']} LLM call(s), {t['input_tokens']} in / {t['output_tokens']} out"
          f" / {t['thinking_tokens']} thinking tokens | {t['llm_seconds']:.1f}s LLM"
          f" | {t['verify_ms']:.2f} ms verification\n")


def main():
    args = parse_args()
    repair = not args.no_repair
    config = VerifyConfig(require_human=args.require_human, sensitive_tools=frozenset(args.sensitive))
    show_banner(args.provider, repair)

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
            run = (run_condition_b if repair else run_condition_a)(request, args.provider, config)
        except Exception as e:
            print(f"LLM call failed: {type(e).__name__}: {e}\n")
            continue

        print_run(run)


if __name__ == "__main__":
    main()
