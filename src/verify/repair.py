# Repair message for the LLM (DECISIONS.md section 5): format errors and FAIL findings,
# with witness paths in the LLM's own node ids. INCONCLUSIVE findings are not sent to repair.
from __future__ import annotations

from src.schema import FormatError
from src.verify.report import VerificationReport

LABELS = {
    "dead_ends": "Dead end",
    "reverse_reachability": "No way to an end step",
    "exit_reachability": "Unreachable end step",
    "router_shape": "Router without conditions",
    "human_presence": "Missing human step",
    "human_gate_coverage": "Missing human approval",
    "tool_declarations": "Tool not named",
}


def repair_message(format_errors: list[FormatError] | None = None,
                   report: VerificationReport | None = None) -> str:
    """Empty string when there is nothing to repair."""
    items: list[str] = []
    for e in format_errors or []:
        where = f" at {e.where}" if e.where else ""
        items.append(f"Format error ({e.code}){where}: {e.message}")

    for f in (report.findings if report else []):
        if f.status != "FAIL":
            continue
        label = "Order rule broken" if f.check_id.startswith("order_rule:") else LABELS.get(f.check_id, f.check_id)
        lines = [f"{label}: {f.message}"]
        if f.witness:
            lines.append("   Path: " + " → ".join(f.witness))
        if f.also_affected:
            lines.append("   Also affected: " + ", ".join(f.also_affected))
        items.append("\n".join(lines))

    if not items:
        return ""
    count = f"{len(items)} problem" + ("s" if len(items) != 1 else "")
    body = "\n".join(f"{i}. {text}" for i, text in enumerate(items, 1))
    return f"Your workflow has {count}:\n{body}\nFix these problems and return the full corrected workflow."
