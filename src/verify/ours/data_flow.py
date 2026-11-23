# Data-flow check (our contribution, DECISIONS.md section 5.3):
# every value a step reads must be written before it, on every path that reaches it.
#
# A forward "must" analysis on the normalized graph:
#   - IN(entry) = the configured inputs
#   - a node with one predecessor gets that predecessor's OUT
#   - an XOR-join (one branch ran) gets the values written on EVERY incoming branch (intersection)
#   - an AND-join (all branches ran) gets the values written on ANY incoming branch (union)
#   - OUT(n) = IN(n) + the values n writes
# A value written in a parallel sibling branch therefore only counts after the join.
# Loops are handled by starting every node at "all values" and iterating until nothing changes.
from __future__ import annotations

from collections import defaultdict, deque

from src.schema import WorkflowGraph
from src.verify.config import VerifyConfig
from src.verify.report import CheckResult

ORIGIN = "ours"
CHECK_ID = "data_flow"


def check_data_flow(graph: WorkflowGraph, config: VerifyConfig) -> CheckResult:
    preds: dict[str, list[str]] = defaultdict(list)
    succs: dict[str, list[str]] = defaultdict(list)
    for e in graph.edges:
        preds[e.target].append(e.source)
        succs[e.source].append(e.target)

    nodes = {n.id: n for n in graph.nodes}
    inputs = set(config.inputs)
    universe = inputs | {v for n in graph.nodes for v in n.metadata.writes}

    # Start every node at "everything" (the top of the lattice) and shrink to the fixpoint.
    out: dict[str, set[str]] = {n: set(universe) for n in nodes}
    incoming: dict[str, set[str]] = {n: set(universe) for n in nodes}
    queue = deque(n.id for n in graph.nodes)
    queued = set(queue)
    while queue:
        node_id = queue.popleft()
        queued.discard(node_id)
        node = nodes[node_id]
        if node_id == graph.entry_id:
            new_in = set(inputs)
        elif not preds[node_id]:
            new_in = set(universe)        # unreachable: says nothing (AgentProof reports reachability)
        elif len(preds[node_id]) == 1:
            new_in = set(out[preds[node_id][0]])
        else:
            gate = node.metadata.gateway
            sets = [out[p] for p in preds[node_id]]
            and_join = gate is not None and gate.role == "join" and gate.logic == "AND"
            new_in = set().union(*sets) if and_join else set.intersection(*sets)
        new_out = new_in | set(node.metadata.writes)
        incoming[node_id] = new_in
        if new_out != out[node_id]:
            out[node_id] = new_out
            for s in succs[node_id]:
                if s not in queued:
                    queue.append(s)
                    queued.add(s)

    reachable = _reachable(graph.entry_id, succs)
    missing = []
    for n in graph.nodes:
        if n.id not in reachable:
            continue                      # unreachable steps are AgentProof's finding, not a data-flow one
        for value in n.metadata.reads:
            if value not in incoming[n.id]:
                missing.append({
                    "node": n.id,
                    "value": value,
                    "written_anywhere": value in universe,
                    "witness": _witness(graph, succs, out, n.id, value),
                })

    return CheckResult(ORIGIN, CHECK_ID, "FAIL" if missing else "PASS", {"missing": missing})


def _reachable(entry: str, succs: dict[str, list[str]]) -> set[str]:
    seen, queue = {entry}, deque([entry])
    while queue:
        for s in succs[queue.popleft()]:
            if s not in seen:
                seen.add(s)
                queue.append(s)
    return seen


def _witness(graph: WorkflowGraph, succs: dict[str, list[str]], out: dict[str, set[str]],
             target: str, value: str) -> list[str]:
    """Shortest path from the entry to `target` through nodes after which `value` is
    still not guaranteed to be written."""
    parent: dict[str, str | None] = {graph.entry_id: None}
    queue = deque([graph.entry_id])
    while queue:
        n = queue.popleft()
        if n == target:
            path = []
            while n is not None:
                path.append(n)
                n = parent[n]
            return path[::-1]
        if value in out[n]:
            continue                      # past this node the value is written: not part of a witness
        for s in succs[n]:
            if s not in parent:
                parent[s] = n
                queue.append(s)
    return [target]
