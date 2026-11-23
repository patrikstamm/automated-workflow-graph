# Soundness check (our contribution, DECISIONS.md section 5.3):
# no deadlock and no lack of synchronisation (Vanhatalo, Völzer & Leymann, ICSOC 2007).
#
# Token game on the normalized graph. A token sits on an edge.
#   - entry: puts a token on its outgoing edge
#   - AND-join: fires when every incoming edge has a token; XOR-join and plain steps: when any has one
#   - AND-split and plain steps: put a token on every outgoing edge; XOR-split (router, branching human):
#     on exactly one of them
#   - end steps (and steps with no outgoing edge, which AgentProof reports) take the token away
#   The run is finished when no token is left.
# Faults:
#   - lack of synchronisation: an edge gets a second token (a step runs twice at the same time)
#   - deadlock: no step can fire, but tokens are left (an AND-join waits for a branch that never comes)
#
# Two phases, so the search is only needed where the graph has no simple structure (suggestion 2):
#   1. Fragment reduction: sequence, branch (bond) and loop fragments are decided by pattern and
#      collapsed into a single edge, innermost first, until nothing changes. A branch whose split and
#      join logic do not match is a fault found by pattern.
#   2. Whatever is left (rigid fragments, several end steps) is searched breadth-first over token
#      placements, with the collapsed fragments as single edges. The search is bounded by
#      VerifyConfig.max_states and VerifyConfig.timeout_s; hitting a bound makes the result
#      INCONCLUSIVE, never PASS.
from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass

from src.schema import WorkflowGraph
from src.verify.config import VerifyConfig
from src.verify.report import CheckResult

ORIGIN = "ours"
CHECK_ID = "soundness"


@dataclass
class _Edge:
    source: str
    target: str
    trace: list[str]     # graph nodes inside this (collapsed) edge, in firing order


class _Reduced:
    """The normalized graph with collapsed fragments as single edges."""

    def __init__(self, graph: WorkflowGraph):
        self.graph = graph
        self.edges: dict[int, _Edge] = {}
        self.outs: dict[str, list[int]] = defaultdict(list)
        self.ins: dict[str, list[int]] = defaultdict(list)
        self._next = 0
        for e in graph.edges:
            self.add(e.source, e.target, [])
        self.counts = {"sequence": 0, "bond": 0, "loop": 0}
        self.faults: list[dict] = []

    def add(self, source: str, target: str, trace: list[str]) -> int:
        eid = self._next
        self._next += 1
        self.edges[eid] = _Edge(source, target, trace)
        self.outs[source].append(eid)
        self.ins[target].append(eid)
        return eid

    def remove(self, eid: int) -> _Edge:
        e = self.edges.pop(eid)
        self.outs[e.source].remove(eid)
        self.ins[e.target].remove(eid)
        return e

    def split_logic(self, node_id: str) -> str:
        gate = self.graph.node(node_id).metadata.gateway
        return gate.logic if gate is not None and gate.role == "split" else "AND"

    def join_logic(self, node_id: str) -> str:
        gate = self.graph.node(node_id).metadata.gateway
        return gate.logic if gate is not None and gate.role == "join" else "XOR"

    # ---------- phase 1: reduction rules ----------

    def reduce(self) -> None:
        changed = True
        while changed:
            changed = self._sequence() | self._bond() | self._loop()

    def _sequence(self) -> bool:
        """A step with one incoming and one outgoing edge: u -> n -> v becomes u -> v."""
        changed = False
        for n in [node.id for node in self.graph.nodes]:
            if len(self.ins[n]) != 1 or len(self.outs[n]) != 1:
                continue
            first, second = self.edges[self.ins[n][0]], self.edges[self.outs[n][0]]
            if first.source == n or second.target == n:
                continue                                   # a step that loops onto itself
            self.remove(self.ins[n][0])
            self.remove(self.outs[n][0])
            self.add(first.source, second.target, first.trace + [n] + second.trace)
            self.counts["sequence"] += 1
            changed = True
        return changed

    def _bond(self) -> bool:
        """Several edges from the same split to the same join become one edge."""
        changed = False
        for u in [node.id for node in self.graph.nodes]:
            by_target: dict[str, list[int]] = defaultdict(list)
            for eid in self.outs[u]:
                by_target[self.edges[eid].target].append(eid)
            for v, group in by_target.items():
                if len(group) < 2 or v == u:
                    continue
                split, join = self.split_logic(u), self.join_logic(v)
                branches = [self.edges[eid].trace for eid in group]
                prefix = self._path_to(u)
                if prefix is not None and split == "AND" and join != "AND":
                    # every branch reaches the XOR-join, so the step after it runs once per branch
                    self._fault("lack_of_sync", v, u, prefix + branches[0] + [v] + branches[1] + [v])
                elif prefix is not None and split != "AND" and join == "AND":
                    # only one branch runs, so the AND-join waits forever for the others
                    self._fault("deadlock", v, u, prefix + branches[0] + [v])
                for eid in group:
                    self.remove(eid)
                trace = [n for b in branches for n in b] if split == "AND" else branches[0]
                self.add(u, v, trace)
                self.counts["bond"] += 1
                changed = True
        return changed

    def _loop(self) -> bool:
        """A join j whose only edge goes to a split s, and an edge back from s to j: a do-while loop.
        XOR-join + XOR-split is sound; the back edge is removed."""
        changed = False
        for s in [node.id for node in self.graph.nodes]:
            if len(self.ins[s]) != 1 or len(self.outs[s]) < 2:
                continue
            forward = self.edges[self.ins[s][0]]
            j = forward.source
            if j == s or len(self.outs[j]) != 1 or len(self.ins[j]) < 2:
                continue
            back = next((eid for eid in self.outs[s] if self.edges[eid].target == j), None)
            if back is None:
                continue
            prefix = self._path_to(j)
            body = forward.trace + [s] + self.edges[back].trace
            if prefix is not None and self.join_logic(j) == "AND":
                self._fault("deadlock", j, s, prefix, loop=True)   # the loop's own token can never arrive first
            elif prefix is not None and self.split_logic(s) == "AND":
                self._fault("lack_of_sync", j, s, prefix + body + [j], loop=True)
            self.remove(back)
            self.counts["loop"] += 1
            changed = True
        return changed

    def _fault(self, kind: str, node: str, split: str, witness: list[str], loop: bool = False) -> None:
        if not any(f["kind"] == kind and f["node"] == node for f in self.faults):
            self.faults.append({"kind": kind, "node": node, "split": split,
                                "found_by": "pattern", "loop": loop, "witness": witness})

    def _path_to(self, target: str) -> list[str] | None:
        """Shortest path from the entry in the original graph; None if unreachable
        (unreachable steps are AgentProof's finding, not ours)."""
        succs = defaultdict(list)
        for e in self.graph.edges:
            succs[e.source].append(e.target)
        parent: dict[str, str | None] = {self.graph.entry_id: None}
        queue = deque([self.graph.entry_id])
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
        return None

    def reachable(self) -> set[str]:
        seen, queue = {self.graph.entry_id}, deque([self.graph.entry_id])
        while queue:
            for eid in self.outs[queue.popleft()]:
                t = self.edges[eid].target
                if t not in seen:
                    seen.add(t)
                    queue.append(t)
        return seen

    # ---------- phase 2: bounded state search ----------

    def search(self, max_states: int, timeout_s: float) -> tuple[int, dict | None]:
        """Breadth-first search over token placements. Returns (states explored, limit hit or None)."""
        entry = self.graph.entry_id
        initial = frozenset(self.outs[entry])
        fired0 = [entry] + [n for eid in self.outs[entry] for n in self.edges[eid].trace]
        parent: dict[frozenset, tuple[frozenset | None, list[str]]] = {initial: (None, fired0)}
        queue = deque([initial])
        deadline = time.perf_counter() + timeout_s
        explored = 0

        while queue:
            if explored % 256 == 0 and time.perf_counter() > deadline:
                return explored, {"bound": "timeout_s", "value": timeout_s, "states": explored}
            state = queue.popleft()
            explored += 1
            enabled = False
            for n in sorted({self.edges[eid].target for eid in state}):
                for consume in self._consumable(n, state):
                    enabled = True
                    for produce in self._producible(n):
                        rest = state - consume
                        fired = [n] + [x for eid in produce for x in self.edges[eid].trace]
                        doubled = next((eid for eid in produce if eid in rest), None)
                        if doubled is not None:
                            culprit = self._join_before(self.edges[doubled].source)
                            self._search_fault("lack_of_sync", culprit,
                                               self._firing_order(parent, state) + fired)
                            continue
                        new = rest | produce
                        if new not in parent:
                            parent[new] = (state, fired)
                            if len(parent) > max_states:
                                return explored, {"bound": "max_states", "value": max_states,
                                                  "states": explored}
                            queue.append(new)
            if not enabled and state:
                for n in sorted({self.edges[eid].target for eid in state}):
                    self._search_fault("deadlock", n, self._firing_order(parent, state) + [n])
        return explored, None

    def _consumable(self, n: str, state: frozenset) -> list[frozenset]:
        marked = [eid for eid in self.ins[n] if eid in state]
        if self.join_logic(n) == "AND":
            return [frozenset(self.ins[n])] if len(marked) == len(self.ins[n]) else []
        return [frozenset({eid}) for eid in marked]

    def _producible(self, n: str) -> list[frozenset]:
        outs = self.outs[n]
        if self.split_logic(n) == "XOR":
            return [frozenset({eid}) for eid in outs]
        return [frozenset(outs)]                           # empty for end steps: the token is taken away

    def _join_before(self, node_id: str) -> str:
        """The step that runs twice: walk back from where the second token was put to the nearest join."""
        seen = set()
        while len(self.ins[node_id]) == 1 and node_id not in seen:
            seen.add(node_id)
            node_id = self.edges[self.ins[node_id][0]].source
        return node_id

    def _search_fault(self, kind: str, node: str, witness: list[str]) -> None:
        if not any(f["kind"] == kind and f["node"] == node for f in self.faults):
            self.faults.append({"kind": kind, "node": node, "split": None,
                                "found_by": "search", "loop": False, "witness": witness})

    @staticmethod
    def _firing_order(parent: dict, state: frozenset) -> list[str]:
        parts = []
        while state is not None:
            state, fired = parent[state]
            parts.append(fired)
        return [n for fired in reversed(parts) for n in fired]


def check_soundness(graph: WorkflowGraph, config: VerifyConfig) -> CheckResult:
    reduced = _Reduced(graph)
    reduced.reduce()

    # Fully reduced: only the entry and one end step are left, joined by one edge.
    reachable = reduced.reachable()
    trivial = len(reachable) <= 2 and all(not reduced.outs[n] for n in reachable - {graph.entry_id})
    states, limit = (0, None) if trivial else reduced.search(config.max_states, config.timeout_s)

    raw = {
        "faults": reduced.faults,
        "patterns": dict(reduced.counts),
        "searched": not trivial,
        "search_size": {"nodes": 0 if trivial else len(reachable),
                        "edges": 0 if trivial else sum(len(reduced.outs[n]) for n in reachable)},
        "states": states,
        "limit": limit,
    }
    status = "FAIL" if reduced.faults else "INCONCLUSIVE" if limit else "PASS"
    return CheckResult(ORIGIN, CHECK_ID, status, raw)
