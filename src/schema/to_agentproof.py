# Normalized graph -> AgentProof's dict format (DECISIONS.md section 1.5).
# Returns a plain dict in the shape of agentproof.graph.model.graph_to_dict().
# This file does not import agentproof (DECISIONS.md section 3: only src/verify/baseline/ may);
# the evaluator turns the dict into an AgentGraph with agentproof's graph_from_dict().
from __future__ import annotations

from typing import Any

from src.schema.graph import WorkflowGraph


def to_agentproof_dict(graph: WorkflowGraph) -> dict[str, Any]:
    """Our normalized graph already uses AgentProof's kind names, so this mostly
    drops what AgentProof does not read. Edge metadata (the loop flag) is left out
    because graph_from_dict ignores it."""
    return {
        "name": graph.name,
        "framework": graph.framework,
        "entry_id": graph.entry_id,
        "exit_ids": list(graph.exit_ids),
        "nodes": [
            {
                "id": n.id,
                "kind": n.kind,
                "label": n.label,
                "tools": list(n.tools),
                "metadata": n.metadata.model_dump(exclude_none=True),
            }
            for n in graph.nodes
        ],
        "edges": [
            {"source": e.source, "target": e.target, "kind": e.kind, "condition": e.condition}
            for e in graph.edges
        ],
    }
