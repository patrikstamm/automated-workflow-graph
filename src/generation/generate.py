# One LLM call: user request -> LLM -> parsed and format-checked WorkflowDraft.
# generate_draft is the first call of every run (and all of condition A);
# repair_draft is one repair round of condition B. The loop itself is in src/pipeline.py.
from __future__ import annotations

from dataclasses import dataclass

from src.catalog import TOOLS
from src.humanprompt import UserRequest
from src.llm import LLMResult, call, draft_schema
from src.prompt import build_system_prompt, build_user_message
from src.schema import FormatError, ToolSpec, WorkflowDraft, load_draft, specs_from_tools


@dataclass
class GenerationResult:
    draft: WorkflowDraft | None   # None when the answer could not be parsed at all
    errors: list[FormatError]     # empty when the draft is well-formed
    llm: LLMResult
    system: str                   # the exact prompt sent, kept for logging and repair
    messages: list[dict]

    @property
    def ok(self) -> bool:
        return self.draft is not None and not self.errors


def generate_draft(request: UserRequest, provider: str,
                   catalog: dict[str, ToolSpec] | None = None) -> GenerationResult:
    system = build_system_prompt(request)
    messages = [{"role": "user", "content": build_user_message(request)}]
    return _send(provider, system, messages, catalog)


def repair_draft(request: UserRequest, provider: str, previous_answer: str, repair_text: str,
                 catalog: dict[str, ToolSpec] | None = None) -> GenerationResult:
    """One repair round (condition B). Only the latest draft is sent back (DECISIONS.md section 5.2):
    the task, the LLM's previous answer, and the repair message."""
    system = build_system_prompt(request)
    messages = [
        {"role": "user", "content": build_user_message(request)},
        # Both providers reject an empty assistant message.
        {"role": "assistant", "content": previous_answer.strip() or "(no answer)"},
        {"role": "user", "content": repair_text},
    ]
    return _send(provider, system, messages, catalog)


def _send(provider: str, system: str, messages: list[dict],
          catalog: dict[str, ToolSpec] | None) -> GenerationResult:
    result = call(provider, system, messages, schema=draft_schema())

    if result.blocked:
        errors = [FormatError("blocked", f"The provider blocked or refused the request ({result.stop_reason}).")]
        return GenerationResult(None, errors, result, system, messages)

    draft, errors = load_draft(result.text, catalog if catalog is not None else specs_from_tools(TOOLS))
    if result.truncated:
        # Say why the JSON is broken, so the cause is visible in logs and repair prompts.
        errors = [FormatError("truncated", "The answer was cut off at the token limit. Return a shorter workflow.")] + errors
    return GenerationResult(draft, errors, result, system, messages)
