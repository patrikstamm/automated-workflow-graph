# Evaluator module: verification of normalized workflow graphs.
# AgentProof's checks run in src/verify/baseline/; our own checks in src/verify/ours/.
from src.schema import WorkflowGraph
from src.verify.config import OrderRule, VerifyConfig
from src.verify.repair import repair_message
from src.verify.report import CheckResult, Finding, VerificationReport, build_report


def verify_graph(graph: WorkflowGraph, config: VerifyConfig | None = None) -> VerificationReport:
    # Imported here: both packages import report.py, which imports this package.
    from src.verify.baseline import run_agentproof
    from src.verify.ours import run_ours
    config = config or VerifyConfig()
    checks, timings = run_agentproof(graph, config)
    our_checks, our_timings = run_ours(graph, config)
    return build_report(graph, checks + our_checks, {**timings, **our_timings})


__all__ = ["CheckResult", "Finding", "OrderRule", "VerificationReport", "VerifyConfig",
           "build_report", "repair_message", "verify_graph"]
