# Token game on the normalized graph, shared by the soundness and order-rule checks
# (DECISIONS.md section 5.3). A token sits on an edge; a state is the set of edges that carry one.
#   - entry: puts a token on its outgoing edge
#   - AND-join: fires when every incoming edge has a token; XOR-join and plain steps: when any has one
#   - AND-split and plain steps: put a token on every outgoing edge; XOR-split (router, branching human):
#     on exactly one of them
#   - end steps (and steps with no outgoing edge) take the token away
# The soundness check first collapses simple fragments into single edges, so an edge can stand for
# several steps: their ids are kept in `trace`, in firing order.
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from src.schema import WorkflowGraph


@dataclass
class Edge:
    source: str
    target: str
    trace: list[str]     # graph nodes inside this (collapsed) edge, in firing order


@dataclass(frozen=True)
class Move:
    node: str                  # the node that fires
    consumed: frozenset[int]   # edges it takes a token from
    produced: frozenset[int]   # edges it puts a token on (one edge for an XOR-split, none for an end step)
    fired: list[str]           # node, then the steps inside the produced edges


class TokenNet:
    def __init__(self, graph: WorkflowGraph):
        self.graph = graph
        self.edges: dict[int, Edge] = {}
        self.outs: dict[str, list[int]] = defaultdict(list)
        self.ins: dict[str, list[int]] = defaultdict(list)
        self._next = 0
        for e in graph.edges:               # edge id i is graph.edges[i] until something is collapsed
            self.add(e.source, e.target, [])

    def add(self, source: str, target: str, trace: list[str]) -> int:
        eid = self._next
        self._next += 1
        self.edges[eid] = Edge(source, target, trace)
        self.outs[source].append(eid)
        self.ins[target].append(eid)
        return eid

    def remove(self, eid: int) -> Edge:
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

    def initial(self) -> tuple[frozenset[int], list[str]]:
        """The state after the entry fires, and what fired."""
        entry = self.graph.entry_id
        outs = self.outs[entry]
        return frozenset(outs), [entry] + [n for eid in outs for n in self.edges[eid].trace]

    def moves(self, state: frozenset[int]) -> list[Move]:
        """Every way one node can fire in this state, in a fixed order."""
        result = []
        for n in sorted({self.edges[eid].target for eid in state}):
            for consumed in self._consumable(n, state):
                for produced in self._producible(n):
                    fired = [n] + [x for eid in sorted(produced) for x in self.edges[eid].trace]
                    result.append(Move(n, consumed, produced, fired))
        return result

    def _consumable(self, n: str, state: frozenset[int]) -> list[frozenset[int]]:
        marked = [eid for eid in self.ins[n] if eid in state]
        if self.join_logic(n) == "AND":
            return [frozenset(self.ins[n])] if len(marked) == len(self.ins[n]) else []
        return [frozenset({eid}) for eid in marked]

    def _producible(self, n: str) -> list[frozenset[int]]:
        outs = self.outs[n]
        if self.split_logic(n) == "XOR":
            return [frozenset({eid}) for eid in outs]
        return [frozenset(outs)]           # empty for end steps: the token is taken away


def firing_order(parent: dict, state) -> list[str]:
    """Rebuild what fired from the initial state to `state`; parent[s] = (previous state, fired)."""
    parts = []
    while state is not None:
        state, fired = parent[state]
        parts.append(fired)
    return [n for fired in reversed(parts) for n in fired]
