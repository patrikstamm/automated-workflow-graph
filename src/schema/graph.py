# Normalized workflow graph (DECISIONS.md section 1.4).
# A superset of AgentProof's dict format: same node/edge fields and kind names,
# plus our own metadata (gateways, reads/writes, args, loop flags).
# All checks and the improver work on this layer, never on the draft.
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# AgentProof's node and edge kinds (agentproof.graph.model.NodeKind / EdgeKind).
GraphNodeKind = Literal["entry", "exit", "tool", "llm", "router", "human", "passthrough"]
GraphEdgeKind = Literal["direct", "conditional", "parallel"]
# AgentProof also has a "loop" edge kind. We do not use it: loop edges keep their kind and
# get metadata.loop = True instead, because AgentProof requires every edge out of a router
# to be "conditional".


class Gateway(BaseModel):
    role: Literal["split", "join"]
    logic: Literal["AND", "XOR", "OR"]


class NodeMeta(BaseModel):
    origin_id: str                       # draft node this came from (gateways: the node they belong to)
    task: str | None = None
    args: dict[str, str] = Field(default_factory=dict)
    reads: list[str] = Field(default_factory=list)
    writes: list[str] = Field(default_factory=list)
    gateway: Gateway | None = None       # set on inserted gateways and on branching router/human nodes


class GraphNode(BaseModel):
    id: str
    kind: GraphNodeKind
    label: str = ""
    tools: list[str] = Field(default_factory=list)   # non-empty only for tool nodes
    metadata: NodeMeta


class EdgeMeta(BaseModel):
    loop: bool = False                   # True for back edges found by DFS from the entry


class GraphEdge(BaseModel):
    source: str
    target: str
    kind: GraphEdgeKind = "direct"
    condition: str = ""
    metadata: EdgeMeta = Field(default_factory=EdgeMeta)


class WorkflowGraph(BaseModel):
    name: str
    framework: str = "llm_draft"
    entry_id: str
    exit_ids: list[str]
    nodes: list[GraphNode]
    edges: list[GraphEdge]

    def node(self, node_id: str) -> GraphNode:
        for n in self.nodes:
            if n.id == node_id:
                return n
        raise KeyError(node_id)

    def origin_path(self, path: list[str]) -> list[str]:
        """Translate a witness path over this graph back to the LLM's draft node ids.
        Gateways map to the node they belong to; consecutive repeats are collapsed.
        e.g. [start, start__split, fetch_mail] -> [start, fetch_mail]"""
        result: list[str] = []
        for node_id in path:
            origin = self.node(node_id).metadata.origin_id
            if not result or result[-1] != origin:
                result.append(origin)
        return result
