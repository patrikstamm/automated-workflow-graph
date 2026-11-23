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
SEARCH_LIMIT_NOTE = ("A search bound was hit, so the property is neither proved nor disproved. "
                     "Not sent to repair.")
RUNTIME_ARG_NOTE = ("The conflict depends on an argument known only at run time (a \"$value\"), so it may be "
                    "a false alarm. Not sent to repair.")


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
    agentproof_verdict: Status = "PASS"   # the verdict from AgentProof's checks alone (suggestion 1)

    def ms_by_origin(self) -> dict[str, float]:
        totals: dict[str, float] = defaultdict(float)
        for key, ms in self.timings_ms.items():
            totals[key.split(".", 1)[0]] += ms
        return dict(totals)

    def to_dict(self) -> dict:
        return {**asdict(self), "ms_by_origin": self.ms_by_origin()}


def build_report(graph: WorkflowGraph, checks: list[CheckResult], timings_ms: dict[str, float]) -> VerificationReport:
    # Order rules: our exact check decides (DECISIONS.md section 5). AgentProof's result for the same rule
    # stays in `checks` unchanged, and still counts towards the AgentProof-only verdict.
    ours_rules = {c.check_id for c in checks if c.origin == "ours" and c.check_id.startswith("order_rule:")}
    decides = [c for c in checks
               if not (c.origin == "agentproof" and c.check_id in ours_rules)]
    findings = _findings(graph, decides)
    agentproof_findings = _findings(graph, [c for c in checks if c.origin == "agentproof"])

    size = {
        "nodes": len(graph.nodes),
        "edges": len(graph.edges),
        "gateways": sum(1 for n in graph.nodes if n.kind == "passthrough"),
    }
    return VerificationReport(_verdict(findings), checks, findings, timings_ms, size,
                              agentproof_verdict=_verdict(agentproof_findings))


def _findings(graph: WorkflowGraph, checks: list[CheckResult]) -> list[Finding]:
    findings: list[Finding] = []
    for check in checks:
        if check.status != "PASS":
            findings.extend(_findings_for(graph, check))
    return _merge_into_dead_ends(graph, findings)


def _verdict(findings: list[Finding]) -> Status:
    statuses = {f.status for f in findings}
    return "FAIL" if "FAIL" in statuses else "INCONCLUSIVE" if "INCONCLUSIVE" in statuses else "PASS"


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

    if cid.startswith("order_rule:") and o == "ours":
        findings = []
        if raw["violated"]:
            witness = path(raw["witness"])
            if raw["when"] == "at_end":
                message = f'The order rule "{raw["dsl"]}" is still not met when the workflow ends on this path.'
                nodes = []
            else:
                message = f'The order rule "{raw["dsl"]}" is broken at step "{witness[-1]}" on this path.'
                nodes = [witness[-1]]
            findings.append(Finding(o, cid, "FAIL", nodes, witness, message))
        if raw["limit"]:
            limit = raw["limit"]
            findings.append(Finding(o, cid, "INCONCLUSIVE", [], [],
                                    f'The check of order rule "{raw["dsl"]}" stopped at its limit '
                                    f'({limit["bound"]} = {limit["value"]}) after {limit["states"]} states.',
                                    note=SEARCH_LIMIT_NOTE))
        return findings

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

    if cid == "soundness":
        findings = []
        for fault in raw["faults"]:
            step = origin_of(fault["node"])
            split = origin_of(fault["split"]) if fault["split"] else ""
            if fault["kind"] == "lack_of_sync" and fault["loop"]:
                message = (f'Step "{split}" starts parallel branches and one of them loops back to "{step}", '
                           f'so "{step}" runs again while the rest of the workflow goes on. '
                           f'Use a router to choose between looping back and going on.')
            elif fault["kind"] == "lack_of_sync":
                message = (f'Step "{step}" is reached by several parallel branches but does not wait for '
                           f'all of them ("join": "all"), so it runs more than once.')
            elif fault["loop"]:
                message = (f'Step "{step}" waits for all its incoming branches ("join": "all"), but one of them '
                           f'only comes back from the loop at "{split}", so it can never run the first time. '
                           f'Use "join": "any".')
            else:
                message = (f'Step "{step}" waits for all its incoming branches ("join": "all"), but not '
                           f'all of them can arrive, so the workflow gets stuck there. '
                           f'Use "join": "any" if only one of them runs.')
            findings.append(Finding(o, cid, "FAIL", [step], path(fault["witness"]), message))
        if raw["limit"]:
            limit = raw["limit"]
            findings.append(Finding(o, cid, "INCONCLUSIVE", [], [],
                                    f'The soundness search stopped at its limit ({limit["bound"]} = '
                                    f'{limit["value"]}) after {limit["states"]} states.',
                                    note=SEARCH_LIMIT_NOTE))
        return findings

    if cid == "parallel_conflict":
        findings = []
        for pair in raw["pairs"]:
            certain = [c for c in pair["conflicts"] if not c["uncertain"]]
            shown = certain or pair["conflicts"]
            a, b = origin_of(pair["a"]), origin_of(pair["b"])
            reasons = _unique(_conflict_text(c, origin_of) for c in shown)
            findings.append(Finding(
                o, cid, "FAIL" if certain else "INCONCLUSIVE", [a, b], path(pair["witness"]),
                f'Steps "{a}" and "{b}" run in parallel, but {"; ".join(reasons)}. Run them one after the '
                f'other, or change them so they do not use the same value or tool resource.',
                note="" if certain else RUNTIME_ARG_NOTE))
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

def _conflict_text(c: dict, origin_of) -> str:
    writer, other, name = origin_of(c["writer"]), origin_of(c["other"]), c["name"]
    if c["on"] == "state":
        return (f'both write "{name}"' if c["kind"] == "write_write"
                else f'"{writer}" writes "{name}" while "{other}" reads it')
    return (f'both change the tool resource "{name}"' if c["kind"] == "write_write"
            else f'"{writer}" changes the tool resource "{name}" while "{other}" reads it')


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
