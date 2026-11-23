# Bernstein's conditions (Bernstein 1966), extended to tool side effects (DECISIONS.md section 4, suggestion 3).
# Two steps may run in parallel only if neither writes what the other reads or writes.
# Shared by the verifier (parallel.py) and the improver (Step 7), so both use the same rule.
#
# Effects of a step:
#   - state values: its `reads` and `writes`
#   - tool resources: from the catalog's RESOURCES, e.g. gmail_label writes "gmail:inbox".
#     "{arg}" in a resource name is filled in from the step's argument. If the argument is a "$value"
#     (known only at run time) or missing, it becomes "*" and matches any name: we assume a conflict,
#     but mark it as uncertain.
# Read/read is never a conflict. Tools not in RESOURCES have no resource effects.
from __future__ import annotations

import re
from dataclasses import dataclass

from src.schema.graph import GraphNode

WILDCARD = "*"


@dataclass(frozen=True)
class Effects:
    reads: frozenset[str]            # state values
    writes: frozenset[str]
    reads_res: frozenset[str]        # tool resources, e.g. "sheets:Budget" or "sheets:*"
    writes_res: frozenset[str]


@dataclass(frozen=True)
class Conflict:
    kind: str          # "write_write" or "read_write"
    on: str            # "state" or "resource"
    name: str          # the value or resource; for resources the more specific name of the two
    writer: str        # graph node id of a step that writes it
    other: str         # graph node id of the other step (writes it too, or reads it)
    uncertain: bool    # True if it only matches through a run-time argument ("*")


def effects(node: GraphNode, resources: dict[str, dict[str, list[str]]]) -> Effects:
    reads_res: set[str] = set()
    writes_res: set[str] = set()
    for tool in node.tools:
        spec = resources.get(tool, {})
        reads_res |= {_fill(r, node.metadata.args) for r in spec.get("reads", [])}
        writes_res |= {_fill(w, node.metadata.args) for w in spec.get("writes", [])}
    return Effects(frozenset(node.metadata.reads), frozenset(node.metadata.writes),
                   frozenset(reads_res), frozenset(writes_res))


def conflicts(a: GraphNode, b: GraphNode, resources: dict[str, dict[str, list[str]]]) -> list[Conflict]:
    """Every way in which a and b break Bernstein's conditions; empty if they may run in parallel."""
    ea, eb = effects(a, resources), effects(b, resources)
    found: list[Conflict] = []
    for v in sorted(ea.writes & eb.writes):
        found.append(Conflict("write_write", "state", v, a.id, b.id, False))
    for v in sorted(ea.writes & eb.reads):
        found.append(Conflict("read_write", "state", v, a.id, b.id, False))
    for v in sorted(eb.writes & ea.reads):
        found.append(Conflict("read_write", "state", v, b.id, a.id, False))
    for x, y, kind, writer, other in [
        (ea.writes_res, eb.writes_res, "write_write", a.id, b.id),
        (ea.writes_res, eb.reads_res, "read_write", a.id, b.id),
        (eb.writes_res, ea.reads_res, "read_write", b.id, a.id),
    ]:
        for r in sorted(x):
            for s in sorted(y):
                if _matches(r, s):
                    uncertain = WILDCARD in r or WILDCARD in s
                    found.append(Conflict(kind, "resource", s if WILDCARD in r else r, writer, other, uncertain))
    return found


def _fill(template: str, args: dict[str, str]) -> str:
    def value(m: re.Match) -> str:
        v = args.get(m.group(1), "")
        return WILDCARD if not v or v.startswith("$") else v
    return re.sub(r"\{(\w+)\}", value, template)


def _matches(r: str, s: str) -> bool:
    if WILDCARD not in r and WILDCARD not in s:
        return r == s
    pattern = lambda x: "^" + ".*".join(re.escape(p) for p in x.split(WILDCARD)) + "$"
    return bool(re.match(pattern(r), s) or re.match(pattern(s), r))
