# AgentProof's checks, run unchanged (DECISIONS.md sections 3 and 4.1).
# This is the ONLY file in src/ that imports agentproof. It returns AgentProof's raw
# results; interpreting them (findings, merging, verdict) happens in src/verify/report.py.
from __future__ import annotations

import time

from agentproof.graph.model import graph_from_dict
from agentproof.monitor.ltl import MonitorRuleSpec, compile_monitor_rule
from agentproof.verify import check_temporal_property, run_structural_checks

from src.schema import WorkflowGraph, to_agentproof_dict
from src.verify.config import VerifyConfig
from src.verify.report import CheckResult

ORIGIN = "agentproof"


def run_agentproof(graph: WorkflowGraph, config: VerifyConfig) -> tuple[list[CheckResult], dict[str, float]]:
    """Run AgentProof's structural and policy checks and its static order-rule check.
    Returns (one CheckResult per check, timings in ms)."""
    ap_graph = graph_from_dict(to_agentproof_dict(graph))
    results: list[CheckResult] = []
    timings: dict[str, float] = {}

    # Six structural checks (+ human-gate coverage when sensitive tools are given).
    start = time.perf_counter()
    structural = run_structural_checks(
        ap_graph,
        require_human=config.require_human,
        sensitive_tools=set(config.sensitive_tools) or None,
    )
    timings["agentproof.structural"] = (time.perf_counter() - start) * 1000
    for check in structural["checks"]:
        results.append(CheckResult(ORIGIN, check["check_id"], "PASS" if check["passed"] else "FAIL", check))

    # Order rules: AgentProof's static graph x DFA product check, one rule at a time.
    for rule in config.order_rules:
        try:
            compiled = compile_monitor_rule(MonitorRuleSpec(rule_id=rule.rule_id, dsl=rule.dsl, on_violation="block"))
        except Exception as e:   # a bad rule is a configuration mistake, not an LLM fault
            raise ValueError(f"Order rule {rule.rule_id!r} ({rule.dsl!r}) is not valid AgentProof DSL: {e}") from e
        start = time.perf_counter()
        outcome = check_temporal_property(ap_graph, compiled)
        ms = (time.perf_counter() - start) * 1000
        check_id = f"order_rule:{rule.rule_id}"
        timings[f"agentproof.{check_id}"] = ms
        raw = {**outcome, "dsl": rule.dsl}
        results.append(CheckResult(ORIGIN, check_id, "FAIL" if outcome["violated"] else "PASS", raw, ms))

    return results, timings
