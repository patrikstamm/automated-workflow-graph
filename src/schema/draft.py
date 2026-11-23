# Draft workflow format: what the LLM writes (DECISIONS.md sections 1.2 and 1.3).
# The draft has no gateway nodes. normalize.py turns it into the normalized graph.
from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

NodeKind = Literal["start", "end", "llm", "tool", "human", "router"]
JoinKind = Literal["all", "any"]

# snake_case without double underscores, so our "<id>__join" / "<id>__split" names never collide.
NAME_PATTERN = re.compile(r"^[a-z][a-z0-9]*(_[a-z0-9]+)*$")
# An argument value that is exactly "$name" refers to the state value "name".
REF_PATTERN = re.compile(r"^\$([a-z][a-z0-9]*(?:_[a-z0-9]+)*)$")

BRANCHING_KINDS = ("router", "human")   # these may choose one branch (XOR-split)


# ---------- The model both LLMs fill in ----------

class Arg(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    value: str          # always a string; lists are written as a JSON string


class DraftNode(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    kind: NodeKind
    task: str | None = None           # llm and human nodes
    tool: str | None = None           # tool nodes
    args: list[Arg] = Field(default_factory=list)   # tool nodes
    reads: list[str] = Field(default_factory=list)
    writes: list[str] = Field(default_factory=list)
    join: JoinKind | None = None      # required when the node has more than one incoming edge


class DraftEdge(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str
    target: str
    condition: str | None = None      # only on edges out of a branching router/human


class WorkflowDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    nodes: list[DraftNode]
    edges: list[DraftEdge]


# ---------- Tool catalog view used by the checks ----------

@dataclass(frozen=True)
class ToolSpec:
    name: str
    params: dict[str, str | None]     # parameter name -> JSON type ("string", "array", ...); None = any


def specs_from_tools(tools) -> dict[str, ToolSpec]:
    """Build ToolSpecs from LangChain @tool objects (src/catalog.py TOOLS)."""
    return {
        t.name: ToolSpec(t.name, {p: info.get("type") for p, info in t.args.items()})
        for t in tools
    }


# ---------- Format errors ----------

@dataclass(frozen=True)
class FormatError:
    code: str       # short machine-readable name, e.g. "join_missing"
    message: str    # sentence that can go straight into a repair prompt
    where: str = "" # node id, "source->target", or field path

    def __str__(self) -> str:
        return f"[{self.code}] {self.where}: {self.message}" if self.where else f"[{self.code}] {self.message}"


def load_draft(text: str, catalog: dict[str, ToolSpec]) -> tuple[WorkflowDraft | None, list[FormatError]]:
    """Parse the LLM's text and run all format checks. Returns (draft, errors)."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        return None, [FormatError("invalid_json", f"Response is not valid JSON: {e}")]

    try:
        draft = WorkflowDraft.model_validate(data)
    except ValidationError as e:
        return None, [
            FormatError("schema", err["msg"], ".".join(str(p) for p in err["loc"]))
            for err in e.errors()
        ]

    return draft, check_format(draft, catalog)


def check_format(draft: WorkflowDraft, catalog: dict[str, ToolSpec]) -> list[FormatError]:
    """All checks from DECISIONS.md 1.3. Returns every error found, not just the first,
    so one repair round can fix them all. An empty list means the draft is well-formed."""
    errors: list[FormatError] = []
    err = lambda code, msg, where="": errors.append(FormatError(code, msg, where))

    # --- ids ---
    ids: set[str] = set()
    for n in draft.nodes:
        if not NAME_PATTERN.match(n.id):
            err("bad_id", "Node ids must be snake_case (lowercase, digits, single underscores).", n.id)
        if n.id in ids:
            err("duplicate_id", "Node id is used more than once.", n.id)
        ids.add(n.id)

    # --- edges ---
    out_edges: dict[str, list[DraftEdge]] = defaultdict(list)
    in_edges: dict[str, list[DraftEdge]] = defaultdict(list)
    seen_edges: set[tuple[str, str]] = set()
    for e in draft.edges:
        where = f"{e.source}->{e.target}"
        if e.source not in ids or e.target not in ids:
            missing = [x for x in (e.source, e.target) if x not in ids]
            err("unknown_edge_endpoint", f"Edge refers to unknown node(s): {', '.join(missing)}.", where)
            continue
        if (e.source, e.target) in seen_edges:
            err("duplicate_edge", "The same edge appears more than once.", where)
            continue
        seen_edges.add((e.source, e.target))
        out_edges[e.source].append(e)
        in_edges[e.target].append(e)

    # --- start and end ---
    starts = [n for n in draft.nodes if n.kind == "start"]
    if len(starts) != 1:
        err("start_count", f"There must be exactly one start node, found {len(starts)}.")
    for s in starts:
        if in_edges[s.id]:
            err("start_has_incoming", "The start node must not have incoming edges.", s.id)
    ends = [n for n in draft.nodes if n.kind == "end"]
    if not ends:
        err("no_end", "There must be at least one end node.")
    for n in ends:
        if out_edges[n.id]:
            err("end_has_outgoing", "End nodes must not have outgoing edges.", n.id)

    # --- per node ---
    for n in draft.nodes:
        outs, ins = out_edges[n.id], in_edges[n.id]

        # task
        if n.kind in ("llm", "human") and not (n.task and n.task.strip()):
            err("missing_task", f"A {n.kind} node needs a non-empty task.", n.id)

        # tool fields only on tool nodes
        if n.kind != "tool" and (n.tool is not None or n.args):
            err("unexpected_field", "Only tool nodes may have 'tool' and 'args'.", n.id)

        if n.kind == "tool":
            errors.extend(_check_tool_node(n, catalog))

        # state value names
        for name in n.reads + n.writes:
            if not NAME_PATTERN.match(name):
                err("bad_state_name", f"State value '{name}' must be snake_case.", n.id)

        # "$x" references must be declared in reads
        for a in n.args:
            m = REF_PATTERN.match(a.value)
            if m and m.group(1) not in n.reads:
                err("ref_not_in_reads", f"Argument '{a.name}' uses ${m.group(1)} but '{m.group(1)}' is not in reads.", n.id)

        # branching and conditions
        branches = n.kind == "router" or (n.kind == "human" and len(outs) > 1)
        if n.kind == "router" and len(outs) < 2:
            err("router_needs_branches", f"A router needs at least 2 outgoing edges, found {len(outs)}.", n.id)
        if branches:
            conditions = [e.condition.strip() if e.condition else "" for e in outs]
            for e, c in zip(outs, conditions):
                if not c:
                    err("missing_condition", "Every edge out of a branching node needs a condition.", f"{e.source}->{e.target}")
            dupes = sorted({c for c in conditions if c and conditions.count(c) > 1})
            for c in dupes:
                err("duplicate_condition", f"Condition '{c}' is used on more than one edge.", n.id)
        else:
            for e in outs:
                if e.condition:
                    err("unexpected_condition", "Only edges out of a branching router or human may have a condition.", f"{e.source}->{e.target}")

        # joins
        if len(ins) > 1 and n.join is None:
            err("join_missing", f"Node has {len(ins)} incoming edges, so 'join' must be \"all\" or \"any\".", n.id)
        if len(ins) <= 1 and n.join is not None:
            err("join_unexpected", "'join' is only allowed on nodes with more than one incoming edge.", n.id)

    return errors


def _check_tool_node(n: DraftNode, catalog: dict[str, ToolSpec]) -> list[FormatError]:
    errors: list[FormatError] = []
    if not n.tool:
        return [FormatError("missing_tool", "A tool node needs a 'tool' name.", n.id)]
    spec = catalog.get(n.tool)
    if spec is None:
        return [FormatError("unknown_tool", f"Tool '{n.tool}' is not in the catalog.", n.id)]

    given = [a.name for a in n.args]
    missing = sorted(set(spec.params) - set(given))
    extra = sorted(set(given) - set(spec.params))
    repeated = sorted({g for g in given if given.count(g) > 1})
    if missing or extra or repeated:
        parts = []
        if missing:
            parts.append(f"missing {', '.join(missing)}")
        if extra:
            parts.append(f"unknown {', '.join(extra)}")
        if repeated:
            parts.append(f"repeated {', '.join(repeated)}")
        errors.append(FormatError("args_mismatch", f"Arguments for '{n.tool}' do not match its parameters ({'; '.join(parts)}).", n.id))

    for a in n.args:
        typ = spec.params.get(a.name)
        if a.name in spec.params and not REF_PATTERN.match(a.value) and not _value_matches(a.value, typ):
            errors.append(FormatError("arg_type", f"Argument '{a.name}' must be of type {typ}; got {a.value!r}.", n.id))
    return errors


def _value_matches(value: str, typ: str | None) -> bool:
    """Check a literal string value against a JSON type from the catalog."""
    if typ in (None, "string"):
        return True
    if typ == "boolean":
        return value in ("true", "false")
    try:
        if typ == "integer":
            int(value)
            return True
        if typ == "number":
            float(value)
            return True
        if typ == "array":
            return isinstance(json.loads(value), list)
        if typ == "object":
            return isinstance(json.loads(value), dict)
    except (ValueError, json.JSONDecodeError):
        return False
    return True
