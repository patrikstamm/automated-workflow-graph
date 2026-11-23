# Draft -> normalized graph (DECISIONS.md section 1.4).
# Inserts explicit gateway nodes so every split and join is visible, the way
# Vanhatalo et al.'s workflow graphs require, and marks loop edges.
from __future__ import annotations

from collections import defaultdict

from src.schema.draft import DraftEdge, WorkflowDraft
from src.schema.graph import EdgeMeta, Gateway, GraphEdge, GraphNode, NodeMeta, WorkflowGraph

KIND_MAP = {"start": "entry", "end": "exit", "llm": "llm", "tool": "tool", "human": "human", "router": "router"}


def normalize(draft: WorkflowDraft) -> WorkflowGraph:
    """Build the normalized graph. Call only on a draft that passed check_format()."""
    out_edges: dict[str, list[DraftEdge]] = defaultdict(list)
    in_edges: dict[str, list[DraftEdge]] = defaultdict(list)
    for e in draft.edges:
        out_edges[e.source].append(e)
        in_edges[e.target].append(e)

    # Which nodes need which gateways.
    xor_split = {n.id for n in draft.nodes if n.kind in ("router", "human") and len(out_edges[n.id]) > 1}
    and_split = {n.id for n in draft.nodes if n.id not in xor_split and len(out_edges[n.id]) > 1}
    joined = {n.id for n in draft.nodes if len(in_edges[n.id]) > 1}

    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []

    for n in draft.nodes:
        # Join gateway in front of a node with several incoming edges.
        if n.id in joined:
            logic = "AND" if n.join == "all" else "XOR"
            nodes.append(_gateway(f"{n.id}__join", n.id, "join", logic))
            edges.append(GraphEdge(source=f"{n.id}__join", target=n.id))

        nodes.append(GraphNode(
            id=n.id,
            kind=KIND_MAP[n.kind],
            label=n.id,
            tools=[n.tool] if n.kind == "tool" and n.tool else [],
            metadata=NodeMeta(
                origin_id=n.id,
                task=n.task,
                args={a.name: a.value for a in n.args},
                reads=list(n.reads),
                writes=list(n.writes),
                # A branching router/human is itself the XOR-split; no extra node.
                gateway=Gateway(role="split", logic="XOR") if n.id in xor_split else None,
            ),
        ))

        # AND-split gateway after a node that starts several parallel branches.
        if n.id in and_split:
            nodes.append(_gateway(f"{n.id}__split", n.id, "split", "AND"))
            edges.append(GraphEdge(source=n.id, target=f"{n.id}__split"))

    for e in draft.edges:
        source = f"{e.source}__split" if e.source in and_split else e.source
        target = f"{e.target}__join" if e.target in joined else e.target
        if e.source in xor_split:
            edges.append(GraphEdge(source=source, target=target, kind="conditional", condition=e.condition or ""))
        elif e.source in and_split:
            edges.append(GraphEdge(source=source, target=target, kind="parallel"))
        else:
            edges.append(GraphEdge(source=source, target=target))

    entry_id = next(n.id for n in draft.nodes if n.kind == "start")
    graph = WorkflowGraph(
        name=draft.name,
        entry_id=entry_id,
        exit_ids=[n.id for n in draft.nodes if n.kind == "end"],
        nodes=nodes,
        edges=edges,
    )
    _mark_loops(graph)
    return graph


def _gateway(node_id: str, origin: str, role: str, logic: str) -> GraphNode:
    return GraphNode(
        id=node_id,
        kind="passthrough",   # all inserted gateways are passthrough; their logic lives in metadata
        label=node_id,
        metadata=NodeMeta(origin_id=origin, gateway=Gateway(role=role, logic=logic)),
    )


def _mark_loops(graph: WorkflowGraph) -> None:
    """Depth-first search from the entry; an edge back to a node still on the
    search stack closes a loop and gets metadata.loop = True. Edges are explored
    in list order, so the result is deterministic."""
    outgoing: dict[str, list[int]] = defaultdict(list)
    for i, e in enumerate(graph.edges):
        outgoing[e.source].append(i)

    WHITE, GREY, BLACK = 0, 1, 2
    colour: dict[str, int] = defaultdict(int)
    stack: list[tuple[str, int]] = [(graph.entry_id, 0)]
    colour[graph.entry_id] = GREY
    while stack:
        node_id, pos = stack[-1]
        if pos < len(outgoing[node_id]):
            stack[-1] = (node_id, pos + 1)
            edge = graph.edges[outgoing[node_id][pos]]
            if colour[edge.target] == GREY:
                edge.metadata = EdgeMeta(loop=True)
            elif colour[edge.target] == WHITE:
                colour[edge.target] = GREY
                stack.append((edge.target, 0))
        else:
            colour[node_id] = BLACK
            stack.pop()
