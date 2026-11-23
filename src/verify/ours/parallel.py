# Parallel-conflicts check (our contribution, DECISIONS.md section 5.3):
# two steps that can run at the same time must satisfy Bernstein's conditions (bernstein.py),
# for state values and for tool resources.
#
# Which steps run in parallel (structural rule): x and y are parallel if different branches of the
# same AND-split reach them, and neither can reach the other. Loop edges are left out of "reach", so a
# loop around a parallel block does not hide it. The rule is exact for sound graphs; on a graph that
# fails the soundness check the result is best effort.
#
# A step reading a value that a parallel branch writes is already reported by the data-flow check when
# the value is not written before the split; here it is only reported when the value IS written before
# (so the parallel branch overwrites it while it is being read). Each fault reaches the LLM once.
#
# A conflict that only exists through a run-time argument ("$sheet") is INCONCLUSIVE, not FAIL.
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import asdict

from src.schema import WorkflowGraph
from src.verify.config import VerifyConfig
from src.verify.ours.bernstein import conflicts
from src.verify.ours.data_flow import available_before
from src.verify.report import CheckResult

ORIGIN = "ours"
CHECK_ID = "parallel_conflict"
STEP_KINDS = {"llm", "tool", "human", "router"}


def check_parallel(graph: WorkflowGraph, config: VerifyConfig,
                   resources: dict[str, dict[str, list[str]]] | None = None) -> CheckResult:
    if resources is None:
        from src.catalog import RESOURCES   # imported here: the catalog loads LangChain
        resources = RESOURCES
    available = available_before(graph, config)
    pairs = []
    for a, b, split in parallel_pairs(graph):
        found = [c for c in conflicts(graph.node(a), graph.node(b), resources)
                 if not (c.on == "state" and c.kind == "read_write" and c.name not in available[c.other])]
        if found:
            pairs.append({"a": a, "b": b, "split": split,
                          "conflicts": [asdict(c) for c in found],
                          "witness": _witness(graph, split, a, b)})

    certain = any(not c["uncertain"] for p in pairs for c in p["conflicts"])
    status = "FAIL" if certain else "INCONCLUSIVE" if pairs else "PASS"
    return CheckResult(ORIGIN, CHECK_ID, status, {"pairs": pairs})


def parallel_pairs(graph: WorkflowGraph) -> list[tuple[str, str, str]]:
    """(a, b, split) for every pair of steps that can run in parallel, a before b in graph order.
    `split` is the first AND-split (in graph order) whose branches separate them."""
    order = {n.id: i for i, n in enumerate(graph.nodes)}
    succs: dict[str, list[str]] = defaultdict(list)
    for e in graph.edges:
        if not e.metadata.loop:
            succs[e.source].append(e.target)
    reach = {n.id: _reach(n.id, succs) for n in graph.nodes}     # nodes reachable from n, n included
    steps = {n.id for n in graph.nodes if n.kind in STEP_KINDS}

    found: dict[tuple[str, str], str] = {}
    for s in graph.nodes:
        gate = s.metadata.gateway
        if gate is None or gate.role != "split" or gate.logic != "AND":
            continue
        branches = [reach[t] & steps for t in succs[s.id]]
        for i in range(len(branches)):
            for j in range(i + 1, len(branches)):
                for x in branches[i]:
                    for y in branches[j]:
                        if x == y or y in reach[x] or x in reach[y]:
                            continue
                        key = (x, y) if order[x] < order[y] else (y, x)
                        found.setdefault(key, s.id)
    return sorted(((a, b, s) for (a, b), s in found.items()), key=lambda p: (order[p[0]], order[p[1]]))


def _reach(start: str, succs: dict[str, list[str]]) -> set[str]:
    seen, queue = {start}, deque([start])
    while queue:
        for m in succs[queue.popleft()]:
            if m not in seen:
                seen.add(m)
                queue.append(m)
    return seen


def _path(graph: WorkflowGraph, source: str, target: str) -> list[str]:
    succs: dict[str, list[str]] = defaultdict(list)
    for e in graph.edges:
        succs[e.source].append(e.target)
    parent: dict[str, str | None] = {source: None}
    queue = deque([source])
    while queue:
        n = queue.popleft()
        if n == target:
            path = []
            while n is not None:
                path.append(n)
                n = parent[n]
            return path[::-1]
        for m in succs[n]:
            if m not in parent:
                parent[m] = n
                queue.append(m)
    return [target]


def _witness(graph: WorkflowGraph, split: str, a: str, b: str) -> list[str]:
    """From the start to the split, then down one branch to a, then down the other to b."""
    return _path(graph, graph.entry_id, split) + _path(graph, split, a)[1:] + _path(graph, split, b)[1:]
