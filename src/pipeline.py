# Evaluation conditions (DECISIONS.md sections 4 and 5.2).
#   A: one LLM call; format errors or failed checks make the run fail.
#   B: A + verification and repair, up to 3 repair rounds.
#   C: B + optimisation (added in Step 8).
# Every round is recorded, so runs can be stored and compared later.
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Literal

from src.generation import GenerationResult, generate_draft, repair_draft
from src.humanprompt import UserRequest
from src.llm import LLMResult
from src.schema import FormatError, ToolSpec, WorkflowDraft, WorkflowGraph, normalize
from src.verify import VerificationReport, VerifyConfig, repair_message, verify_graph

MAX_REPAIR_ROUNDS = 3

RunStatus = Literal["PASS", "INCONCLUSIVE", "FAILED", "NO_PROGRESS", "BLOCKED"]


@dataclass
class Round:
    n: int                                   # 0 = first draft, 1-3 = repair rounds
    llm: LLMResult
    format_errors: list[FormatError]
    draft: WorkflowDraft | None
    graph: WorkflowGraph | None              # None when the draft had format errors
    report: VerificationReport | None        # None when the draft had format errors
    repair_text: str = ""                    # the repair message sent after this round, if any

    @property
    def verdict(self) -> str:
        """FORMAT_ERROR, BLOCKED, or the verification verdict (PASS / FAIL / INCONCLUSIVE)."""
        if self.llm.blocked:
            return "BLOCKED"
        if self.format_errors or self.report is None:
            return "FORMAT_ERROR"
        return self.report.verdict


@dataclass
class RunResult:
    condition: str                           # "A" or "B"
    provider: str
    model: str
    status: RunStatus
    rounds: list[Round] = field(default_factory=list)

    @property
    def final(self) -> Round:
        return self.rounds[-1]

    @property
    def repair_rounds(self) -> int:
        return len(self.rounds) - 1

    def totals(self) -> dict:
        llms = [r.llm for r in self.rounds]
        return {
            "llm_calls": sum(not x.cached for x in llms),
            "cached_calls": sum(x.cached for x in llms),
            "input_tokens": sum(x.input_tokens for x in llms),
            "output_tokens": sum(x.output_tokens for x in llms),
            "thinking_tokens": sum(x.thinking_tokens for x in llms),
            "llm_seconds": sum(x.seconds for x in llms),
            "verify_ms": sum(sum(r.report.timings_ms.values()) for r in self.rounds if r.report),
        }

    def to_dict(self) -> dict:
        """Summary for logging and storage (full drafts and reports included)."""
        return {
            "condition": self.condition, "provider": self.provider, "model": self.model,
            "status": self.status, "repair_rounds": self.repair_rounds, "totals": self.totals(),
            "rounds": [
                {
                    "n": r.n, "verdict": r.verdict, "stop_reason": r.llm.stop_reason,
                    "input_tokens": r.llm.input_tokens, "output_tokens": r.llm.output_tokens,
                    "thinking_tokens": r.llm.thinking_tokens, "seconds": r.llm.seconds, "cached": r.llm.cached,
                    "format_errors": [vars(e) for e in r.format_errors],
                    "report": r.report.to_dict() if r.report else None,
                    "draft": r.draft.model_dump(exclude_none=True) if r.draft else None,
                    "repair_text": r.repair_text,
                }
                for r in self.rounds
            ],
        }


def run_condition_a(request: UserRequest, provider: str, config: VerifyConfig | None = None,
                    catalog: dict[str, ToolSpec] | None = None) -> RunResult:
    first = _check(0, generate_draft(request, provider, catalog), config)
    return RunResult("A", provider, first.llm.model, _final_status(first), [first])


def run_condition_b(request: UserRequest, provider: str, config: VerifyConfig | None = None,
                    catalog: dict[str, ToolSpec] | None = None,
                    max_repair_rounds: int = MAX_REPAIR_ROUNDS) -> RunResult:
    rounds = [_check(0, generate_draft(request, provider, catalog), config)]

    while True:
        current = rounds[-1]
        if current.verdict in ("PASS", "INCONCLUSIVE", "BLOCKED"):
            status = _final_status(current)        # INCONCLUSIVE is exported with a warning, not repaired
            break
        if current.n >= max_repair_rounds:
            status = "FAILED"
            break
        current.repair_text = repair_message(current.format_errors, current.report)
        if not current.repair_text:                # nothing the LLM could act on
            status = "FAILED"
            break

        nxt = _check(current.n + 1,
                     repair_draft(request, provider, current.llm.text, current.repair_text, catalog), config)
        rounds.append(nxt)
        if _same_answer(current.llm.text, nxt.llm.text):
            status = "NO_PROGRESS"
            break

    return RunResult("B", provider, rounds[0].llm.model, status, rounds)


# ---------- helpers ----------

def _check(n: int, gen: GenerationResult, config: VerifyConfig | None) -> Round:
    if not gen.ok:
        return Round(n, gen.llm, gen.errors, gen.draft, None, None)
    graph = normalize(gen.draft)
    return Round(n, gen.llm, [], gen.draft, graph, verify_graph(graph, config))


def _final_status(r: Round) -> RunStatus:
    return {"PASS": "PASS", "INCONCLUSIVE": "INCONCLUSIVE", "BLOCKED": "BLOCKED"}.get(r.verdict, "FAILED")


def _same_answer(a: str, b: str) -> bool:
    """Compare as JSON when both parse (ignores spacing and key order), otherwise as text."""
    try:
        return json.dumps(json.loads(a), sort_keys=True) == json.dumps(json.loads(b), sort_keys=True)
    except (json.JSONDecodeError, TypeError):
        return a.strip() == b.strip()
