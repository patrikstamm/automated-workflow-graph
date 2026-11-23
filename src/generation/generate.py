# One generation call: user request -> LLM -> parsed and format-checked WorkflowDraft.
# This is evaluation condition A ("LLM only"): exactly one call, no retries (DECISIONS.md section 4).
# Repair rounds for format errors and verification faults belong to condition B.
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
    result = call(provider, system, messages, schema=draft_schema())

    if result.blocked:
        errors = [FormatError("blocked", f"The provider blocked or refused the request ({result.stop_reason}).")]
        return GenerationResult(None, errors, result, system, messages)

    draft, errors = load_draft(result.text, catalog if catalog is not None else specs_from_tools(TOOLS))
    if result.truncated:
        # Say why the JSON is broken, so the cause is visible in logs and repair prompts.
        errors = [FormatError("truncated", "The answer was cut off at the token limit.")] + errors
    return GenerationResult(draft, errors, result, system, messages)
