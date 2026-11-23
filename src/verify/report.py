# Combined verification report (DECISIONS.md sections 4.1 and 5).
# Raw results from each check are kept unchanged in CheckResult.raw, so results can always be
# reported per origin. Findings are our interpretation: one per fault, in the LLM's own node
# ids, with duplicates merged. The verdict follows DECISIONS.md section 5.
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import asdict, dataclass, field
from typing import Literal

from src.schema import WorkflowGraph

Status = Literal["PASS", "FAIL", "INCONCLUSIVE"]

PARALLEL_NOTE = ("The path goes through parallel branches. AgentProof checks parallel branches as if only one "
                 "of them runs, so this may be a false alarm. Not sent to repair.")


@dataclass
class CheckResult:
    origin: str          # "agentproof" or "ours"
    check_id: str
    status: Status       # the check's own result, before our interpretation
    raw: dict            # the check's output, unchanged
    ms: float | None = None


@dataclass
class Finding:
    origin: str
    check_id: str
    status: Status       # FAIL or INCONCLUSIVE
    nodes: list[str]     # the LLM's node ids
    witness: list[str]   # path from the start, in the LLM's node ids
    message: str         # one sentence, ready for a repair prompt
    also_affected: list[str] = field(default_factory=list)
    note: str = ""


@dataclass
class VerificationReport:
    verdict: Status
    checks: list[CheckResult]
    findings: list[Finding]
    timings_ms: dict[str, float]
    graph_size: dict[str, int]

    def ms_by_origin(self) -> dict[str, float]:
        totals: dict[str, float] = defaultdict(float)
        for key, ms in self.timings_ms.items():
            totals[key.split(".", 1)[0]] += ms
        return dict(totals)

    def to_dict(self) -> dict:
        return {**asdict(self), "ms_by_origin": self.ms_by_origin()}


def build_report(graph: WorkflowGraph, checks: list[CheckResult], timings_ms: dict[str, float]) -> VerificationReport:
    findings: list[Finding] = []
    for check in checks:
        if check.status != "PASS":
            findings.extend(_findings_for(graph, check))
    findings = _merge_into_dead_ends(graph, findings)

    statuses = {f.status for f in findings}
    verdict: Status = "FAIL" if "FAIL" in statuses else "INCONCLUSIVE" if "INCONCLUSIVE" in statuses else "PASS"
    size = {
        "nodes": len(graph.nodes),
        "edges": len(graph.edges),
        "gateways": sum(1 for n in graph.nodes if n.kind == "passthrough"),
    }
    return VerificationReport(verdict, checks, findings, timings_ms, size)


# ---------- raw AgentProof output -> findings ----------

def _findings_for(graph: WorkflowGraph, check: CheckResult) -> list[Finding]:
    raw, cid, o = check.raw, check.check_id, check.origin
    origin_of = lambda node_id: graph.node(node_id).metadata.origin_id
    path = lambda p: graph.origin_path(p) if p else []

    if cid == "dead_ends":
        return [Finding(o, cid, "FAIL", [origin_of(n)], path(w),
                        f'Step "{origin_of(n)}" has no outgoing edge, so the workflow gets stuck there.')
                for n, w in raw["witnesses"].items()]

    if cid == "reverse_reachability":
        return _livelock_findings(graph, check)

    if cid == "exit_reachability":
        return [Finding(o, cid, "FAIL", [origin_of(e)], path(raw["witnesses"].get(e)),
                        f'End step "{origin_of(e)}" can never be reached from the start.')
                for e in raw["missing_exits"]]

    if cid == "router_shape":
        return [Finding(o, cid, "FAIL", [origin_of(v["router"])], [],
                        f'Router "{origin_of(v["router"])}" has edges without a condition, to: '
                        + ", ".join(origin_of(b["target"]) for b in v["bad_edges"]) + ".")
                for v in raw["violations"]]

    if cid == "human_presence":
        return [Finding(o, cid, "FAIL", [], [], "The workflow needs a human approval step, but has none.")]

    if cid == "human_gate_coverage":
        return [Finding(o, cid, "FAIL", [origin_of(n)], path(w),
                        f'Tool step "{origin_of(n)}" can be reached without passing a human approval step.')
                for n, w in raw["witnesses"].items()]

    if cid == "tool_declarations":
        return [Finding(o, cid, "FAIL", [origin_of(n)], [], f'Tool step "{origin_of(n)}" does not name a tool.')
                for n in raw["tool_nodes_missing_tools"]]

    if cid.startswith("order_rule:"):
        violation = raw.get("violation_path") or []
        crosses_parallel = any(_is_and_split(graph, n) for n in violation)
        return [Finding(o, cid, "INCONCLUSIVE" if crosses_parallel else "FAIL",
                        [origin_of(violation[-1])] if violation else [], path(violation),
                        f'The order rule "{raw["dsl"]}" is broken on this path.',
                        note=PARALLEL_NOTE if crosses_parallel else "")]

    if cid == "data_flow":
        findings = []
        for m in raw["missing"]:
            step, value = origin_of(m["node"]), m["value"]
            reason = ("nothing writes it before this step on the path below"
                      if m["written_anywhere"] else "no step in the workflow writes it")
            findings.append(Finding(o, cid, "FAIL", [step], path(m["witness"]),
                                    f'Step "{step}" reads "{value}", but {reason}.'))
        return findings

    # Unknown check (e.g. a newer AgentProof): report it rather than drop it.
    return [Finding(o, cid, "FAIL", [], [], f'Check "{cid}" failed.')]


def _livelock_findings(graph: WorkflowGraph, check: CheckResult) -> list[Finding]:
    """One finding per group of connected steps that cannot reach an end step."""
    witnesses: dict[str, list[str]] = check.raw["witnesses"]
    stuck = set(witnesses)
    groups = _connected_groups(graph, stuck)
    findings = []
    for group in groups:
        entry = min(group, key=lambda n: (len(witnesses[n]), n))   # the step reached first
        nodes = _unique(graph.node(n).metadata.origin_id for n in sorted(group, key=lambda n: (len(witnesses[n]), n)))
        findings.append(Finding(
            check.origin, check.check_id, "FAIL", nodes, graph.origin_path(witnesses[entry]),
            "These steps can never reach an end step (endless loop or no way out): " + ", ".join(nodes) + ".",
        ))
    return findings


def _merge_into_dead_ends(graph: WorkflowGraph, findings: list[Finding]) -> list[Finding]:
    """A dead end is also reported by reverse_reachability (the dead end and every step that
    only leads to it). Fold those steps into the dead-end finding, so the LLM sees each fault once."""
    dead = [f for f in findings if f.check_id == "dead_ends"]
    if not dead:
        return findings
    dead_ids = {f.nodes[0] for f in dead}
    successors = defaultdict(list)
    for e in graph.edges:
        successors[graph.node(e.source).metadata.origin_id].append(graph.node(e.target).metadata.origin_id)

    result = []
    for f in findings:
        if f.check_id != "reverse_reachability":
            result.append(f)
            continue
        remaining = []
        for node in f.nodes:
            target = _first_reachable(node, dead_ids, successors)
            if target is None:
                remaining.append(node)
            else:
                owner = next(d for d in dead if d.nodes[0] == target)
                if node != target and node not in owner.also_affected:
                    owner.also_affected.append(node)
        if remaining:   # a real livelock is left over
            f.nodes = remaining
            f.message = ("These steps can never reach an end step (endless loop or no way out): "
                         + ", ".join(remaining) + ".")
            result.append(f)
    return result


# ---------- helpers ----------

def _is_and_split(graph: WorkflowGraph, node_id: str) -> bool:
    gate = graph.node(node_id).metadata.gateway
    return gate is not None and gate.role == "split" and gate.logic == "AND"


def _connected_groups(graph: WorkflowGraph, nodes: set[str]) -> list[set[str]]:
    neighbours = defaultdict(set)
    for e in graph.edges:
        if e.source in nodes and e.target in nodes:
            neighbours[e.source].add(e.target)
            neighbours[e.target].add(e.source)
    groups, seen = [], set()
    for start in sorted(nodes):
        if start in seen:
            continue
        group, queue = set(), deque([start])
        while queue:
            n = queue.popleft()
            if n in group:
                continue
            group.add(n)
            queue.extend(neighbours[n] - group)
        seen |= group
        groups.append(group)
    return groups


def _first_reachable(start: str, targets: set[str], successors: dict[str, list[str]]) -> str | None:
    queue, seen = deque([start]), {start}
    while queue:
        n = queue.popleft()
        if n in targets:
            return n
        for m in successors[n]:
            if m not in seen:
                seen.add(m)
                queue.append(m)
    return None


def _unique(items) -> list[str]:
    out: list[str] = []
    for x in items:
        if x not in out:
            out.append(x)
    return out
