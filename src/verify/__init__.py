# Evaluator module: verification of normalized workflow graphs.
# AgentProof's checks run in src/verify/baseline/; our own checks will live in src/verify/ours/ (Step 5).
from src.schema import WorkflowGraph
from src.verify.config import OrderRule, VerifyConfig
from src.verify.repair import repair_message
from src.verify.report import CheckResult, Finding, VerificationReport, build_report


def verify_graph(graph: WorkflowGraph, config: VerifyConfig | None = None) -> VerificationReport:
    from src.verify.baseline import run_agentproof   # imported here: baseline imports report.py
    checks, timings = run_agentproof(graph, config or VerifyConfig())
    return build_report(graph, checks, timings)


__all__ = ["CheckResult", "Finding", "OrderRule", "VerificationReport", "VerifyConfig",
           "build_report", "repair_message", "verify_graph"]
