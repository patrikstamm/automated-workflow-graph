# Our own checks (origin "ours"), kept apart from AgentProof's (DECISIONS.md section 4.1).
# They must not import agentproof.
from __future__ import annotations

import time

from src.schema import WorkflowGraph
from src.verify.config import VerifyConfig
from src.verify.ours.data_flow import check_data_flow
from src.verify.ours.soundness import check_soundness
from src.verify.ours.order_rules import check_order_rules
from src.verify.ours.parallel import check_parallel
from src.verify.report import CheckResult

CHECKS = (check_data_flow, check_soundness, check_parallel, check_order_rules)


def run_ours(graph: WorkflowGraph, config: VerifyConfig) -> tuple[list[CheckResult], dict[str, float]]:
    results: list[CheckResult] = []
    timings: dict[str, float] = {}
    for check in CHECKS:
        start = time.perf_counter()
        result = check(graph, config)
        if isinstance(result, list):              # one result per order rule, each timed by the check
            for r in result:
                timings[f"ours.{r.check_id}"] = r.ms
            results.extend(result)
            continue
        result.ms = (time.perf_counter() - start) * 1000
        timings[f"ours.{result.check_id}"] = result.ms
        results.append(result)
    return results, timings


__all__ = ["run_ours"]
