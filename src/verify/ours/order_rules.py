# Exact order-rule check (our contribution, DECISIONS.md section 5.3).
# Same rule language and the same automata as AgentProof's temporal DSL (agentproof.monitor.ltl), so a
# task's rules are written once and both checkers can be compared. Differences, all on purpose:
#   - finished runs: a rule that is still waiting (e.g. "deploy -> F approve" after deploy) when the run
#     ends is a violation. AgentProof's static check misses this.
#   - parallel branches run in every interleaving (token game, tokens.py) instead of as alternatives.
#   - only the LLM's own steps (llm, tool, human, router) are events; start, end and the inserted gateways
#     are invisible, so they do not count towards k in "F[<=k]".
#   - "decision:x" matches a router or human step that takes the branch with condition "x"
#     (in AgentProof's static check it never matches).
#   - "a U (b U c)" is checked as written (AgentProof treats "(b U c)" as one atom that never matches).
# Only runs that finish are checked; a loop that can never be left is AgentProof's livelock finding.
# The search uses the same bounds as soundness; hitting one makes the result INCONCLUSIVE.
from __future__ import annotations

import re
import time
from collections import deque
from dataclasses import dataclass

from src.schema import WorkflowGraph
from src.verify.config import OrderRule, VerifyConfig
from src.verify.ours.tokens import Move, TokenNet, firing_order
from src.verify.report import CheckResult

ORIGIN = "ours"
STEP_KINDS = {"llm", "tool", "human", "router"}
TAGS = {"tool": "tool", "llm": "llm_step", "human": "human", "router": "router"}


# ---------- events and atoms (as agentproof.monitor.ltl._event_matches_predicate) ----------

def _matches(atom: str, event: dict) -> bool:
    if atom.startswith("tool:"):
        return event.get("tool_name", "") == atom[5:]
    if atom.startswith("action:"):
        return event.get("action_type", "") == atom[7:]
    if atom.startswith("decision:"):
        return event.get("decision", "") == atom[9:]
    return atom in event.get("tags", ())


def _event(graph: WorkflowGraph, move: Move) -> dict:
    node = graph.node(move.node)
    event = {"action_type": node.kind, "tags": (TAGS[node.kind],)}
    if node.kind == "tool" and node.tools:
        event["tool_name"] = node.tools[0]
    gate = node.metadata.gateway
    if gate is not None and gate.role == "split" and gate.logic == "XOR" and len(move.produced) == 1:
        event["decision"] = graph.edges[next(iter(move.produced))].condition   # unreduced net: id = edge index
    return event


# ---------- monitors (one per rule shape; states must be hashable) ----------

VIOLATED, DONE = "violated", "done"


@dataclass(frozen=True)
class Forbidden:            # G !a
    a: str
    initial = 0
    def step(self, s, ev): return 1 if s == 1 or _matches(self.a, ev) else 0
    def violated(self, s): return s == 1
    def ok_at_end(self, s): return s == 0


@dataclass(frozen=True)
class Response:             # a -> F b   (a second a before b is also a violation, as in AgentProof)
    a: str
    b: str
    initial = 0
    def step(self, s, ev):
        a, b = _matches(self.a, ev), _matches(self.b, ev)
        if s == 2:
            return 2
        if s == 0:
            return 1 if a and not b else 0
        return 0 if b else 2 if a else 1
    def violated(self, s): return s == 2
    def ok_at_end(self, s): return s == 0


@dataclass(frozen=True)
class Bounded:              # a -> F[<=k] b
    a: str
    b: str
    k: int
    initial = 0
    def step(self, s, ev):
        a, b = _matches(self.a, ev), _matches(self.b, ev)
        if s == self.k + 1:
            return s
        if s == 0:
            return 1 if a and not b else 0
        if b:
            return 0
        return self.k + 1 if s >= self.k else s + 1
    def violated(self, s): return s == self.k + 1
    def ok_at_end(self, s): return s == 0


@dataclass(frozen=True)
class Chain:                # a -> F b -> F c ...
    steps: tuple[str, ...]
    initial = 0
    def step(self, s, ev):
        n = len(self.steps)
        if s == n:
            return n
        if s == 0:
            return 1 if _matches(self.steps[0], ev) else 0
        if _matches(self.steps[s], ev):
            return 0 if s + 1 >= n else s + 1
        return n if _matches(self.steps[0], ev) else s
    def violated(self, s): return s == len(self.steps)
    def ok_at_end(self, s): return s == 0


@dataclass(frozen=True)
class Until:                # a U b, or nested a U (b U c): a's, then b's, then c must come
    phases: tuple[str, ...]  # e.g. ("a", "b", "c"); the last one ends the rule
    @property
    def initial(self): return frozenset({0})
    def step(self, s, ev):
        if s in (VIOLATED, DONE):
            return s
        if _matches(self.phases[-1], ev):
            return DONE
        nxt = frozenset(k for i in s for k in range(i, len(self.phases) - 1) if _matches(self.phases[k], ev))
        return nxt or VIOLATED
    def violated(self, s): return s == VIOLATED
    def ok_at_end(self, s): return s == DONE


@dataclass(frozen=True)
class Both:                 # (x) AND (y)
    left: object
    right: object
    @property
    def initial(self): return (self.left.initial, self.right.initial)
    def step(self, s, ev): return (self.left.step(s[0], ev), self.right.step(s[1], ev))
    def violated(self, s): return self.left.violated(s[0]) or self.right.violated(s[1])
    def ok_at_end(self, s): return self.left.ok_at_end(s[0]) and self.right.ok_at_end(s[1])


@dataclass(frozen=True)
class Either:               # (x) OR (y)
    left: object
    right: object
    @property
    def initial(self): return (self.left.initial, self.right.initial)
    def step(self, s, ev): return (self.left.step(s[0], ev), self.right.step(s[1], ev))
    def violated(self, s): return self.left.violated(s[0]) and self.right.violated(s[1])
    def ok_at_end(self, s): return self.left.ok_at_end(s[0]) or self.right.ok_at_end(s[1])


# ---------- parser (the grammar of agentproof.monitor.ltl._parse_dsl) ----------

def parse(dsl: str):
    text = " ".join(dsl.split())
    for op, cls in (("AND", Both), ("OR", Either)):
        m = re.fullmatch(r"\((.+)\)\s+" + op + r"\s+\((.+)\)", text)
        if m:
            return cls(parse(m.group(1)), parse(m.group(2)))
    if m := re.fullmatch(r"G\s+!\s*([^\s]+)", text):
        return Forbidden(m.group(1))
    if m := re.fullmatch(r"([^\s]+)\s*->\s*F\[<=(\d+)\]\s+([^\s]+)", text):
        return Bounded(m.group(1), m.group(3), int(m.group(2)))
    if re.fullmatch(r"([^\s]+)(\s*->\s*F\s+[^\s]+){2,}", text):
        return Chain(tuple(re.split(r"\s*->\s*F\s+", text)))
    if m := re.fullmatch(r"([^\s]+)\s*->\s*F\s+([^\s]+)", text):
        return Response(m.group(1), m.group(2))
    if m := re.fullmatch(r"([^\s]+)\s+U\s+\((.+)\)", text):
        inner = parse(m.group(2))
        if isinstance(inner, Until):
            return Until((m.group(1), *inner.phases))
    if m := re.fullmatch(r"([^\s]+)\s+U\s+([^\s]+)", text):
        return Until((m.group(1), m.group(2)))
    raise ValueError(f"not a supported order rule: {dsl}")


# ---------- the check ----------

def check_order_rules(graph: WorkflowGraph, config: VerifyConfig) -> list[CheckResult]:
    results = []
    for rule in config.order_rules:
        start = time.perf_counter()
        raw = _check_rule(graph, rule, config)
        status = "FAIL" if raw["violated"] else "INCONCLUSIVE" if raw["limit"] else "PASS"
        results.append(CheckResult(ORIGIN, f"order_rule:{rule.rule_id}", status, raw,
                                   (time.perf_counter() - start) * 1000))
    return results


def _check_rule(graph: WorkflowGraph, rule: OrderRule, config: VerifyConfig) -> dict:
    monitor = parse(rule.dsl)
    net = TokenNet(graph)
    marking, fired0 = net.initial()
    initial = (marking, monitor.initial)
    parent: dict = {initial: (None, fired0)}
    queue = deque([initial])
    deadline = time.perf_counter() + config.timeout_s
    explored = 0
    result = {"dsl": rule.dsl, "violated": False, "when": None, "witness": [], "states": 0, "limit": None}

    while queue:
        if explored % 256 == 0 and time.perf_counter() > deadline:
            result["limit"] = {"bound": "timeout_s", "value": config.timeout_s, "states": explored}
            break
        current = queue.popleft()
        explored += 1
        tokens, state = current
        for move in net.moves(tokens):
            rest = tokens - move.consumed
            if move.produced & rest:
                continue                          # lack of synchronisation: the soundness check reports it
            new_tokens = rest | move.produced
            new_state = state
            if graph.node(move.node).kind in STEP_KINDS:
                new_state = monitor.step(state, _event(graph, move))
            finished = not new_tokens
            if monitor.violated(new_state) or (finished and not monitor.ok_at_end(new_state)):
                result.update(violated=True, when="during" if monitor.violated(new_state) else "at_end",
                              witness=firing_order(parent, current) + move.fired, states=explored)
                return result
            nxt = (new_tokens, new_state)
            if not finished and nxt not in parent:
                parent[nxt] = (current, move.fired)
                if len(parent) > config.max_states:
                    result["limit"] = {"bound": "max_states", "value": config.max_states, "states": explored}
                    result["states"] = explored
                    return result
                queue.append(nxt)
    result["states"] = explored
    return result
