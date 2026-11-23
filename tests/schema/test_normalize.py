# Draft -> normalized graph (DECISIONS.md section 1.4).
from collections import Counter

import pytest

from src.schema import WorkflowDraft, WorkflowGraph, normalize
from tests.schema.examples import EXAMPLES, get


def build(name: str) -> WorkflowGraph:
    return normalize(WorkflowDraft.model_validate(get(name)))


def edge(g: WorkflowGraph, source: str, target: str):
    matches = [e for e in g.edges if e.source == source and e.target == target]
    assert len(matches) == 1, f"expected one edge {source}->{target}, found {len(matches)}"
    return matches[0]


def gateway(g: WorkflowGraph, node_id: str):
    n = g.node(node_id)
    return (n.kind, n.metadata.gateway.role, n.metadata.gateway.logic)


def test_linear_is_unchanged_apart_from_kind_names():
    g = build("linear")
    assert [n.id for n in g.nodes] == ["start", "echo", "end"]
    assert [n.kind for n in g.nodes] == ["entry", "tool", "exit"]
    assert g.entry_id == "start" and g.exit_ids == ["end"]
    assert g.node("echo").tools == ["test_tool"]
    assert g.node("echo").metadata.args == {"text": "hello"}
    assert all(e.kind == "direct" and not e.metadata.loop for e in g.edges)


def test_parallel_split_and_join_are_inserted():
    g = build("parallel")
    assert gateway(g, "start__split") == ("passthrough", "split", "AND")
    assert gateway(g, "summarise__join") == ("passthrough", "join", "AND")
    assert gateway(g, "end__join") == ("passthrough", "join", "XOR")
    assert edge(g, "start", "start__split").kind == "direct"
    assert edge(g, "start__split", "fetch_mail").kind == "parallel"
    assert edge(g, "start__split", "fetch_events").kind == "parallel"
    assert edge(g, "fetch_mail", "summarise__join").kind == "direct"
    assert edge(g, "summarise__join", "summarise").kind == "direct"


def test_branching_human_is_its_own_xor_split():
    g = build("parallel")
    assert gateway(g, "approve") == ("human", "split", "XOR")
    to_send = edge(g, "approve", "send")
    to_end = edge(g, "approve", "end__join")
    assert (to_send.kind, to_send.condition) == ("conditional", "approved")
    assert (to_end.kind, to_end.condition) == ("conditional", "rejected")
    assert "approve__split" not in [n.id for n in g.nodes]


def test_human_with_one_edge_is_not_a_split():
    g = build("email_triage")
    assert g.node("urgent_handler").metadata.gateway is None
    assert edge(g, "urgent_handler", "end").kind == "direct"


def test_loop_edge_out_of_router_stays_conditional():
    g = build("loop")
    back = edge(g, "review", "write_draft__join")
    assert back.kind == "conditional"
    assert back.condition == "needs_changes"
    assert back.metadata.loop is True
    assert [e for e in g.edges if e.metadata.loop] == [back]


def test_vanhatalo_g0_keeps_three_exits_and_marks_the_loop():
    g = build("vanhatalo_g0")
    assert g.exit_ids == ["e1", "e2", "e3"]
    assert gateway(g, "u__join") == ("passthrough", "join", "XOR")
    assert gateway(g, "u") == ("router", "split", "XOR")
    assert gateway(g, "v__split") == ("passthrough", "split", "AND")
    back = edge(g, "w", "u__join")
    assert back.kind == "conditional" and back.metadata.loop is True
    assert sum(e.metadata.loop for e in g.edges) == 1


@pytest.mark.parametrize("name", EXAMPLES)
def test_normalized_graph_follows_vanhatalo_rules(name):
    # Vanhatalo et al. 2.1: only gateways may split or join; the start has one
    # outgoing edge; each end has one incoming edge.
    g = build(name)
    outs = Counter(e.source for e in g.edges)
    ins = Counter(e.target for e in g.edges)
    for n in g.nodes:
        is_gateway = n.metadata.gateway is not None
        if not is_gateway:
            assert outs[n.id] <= 1, f"{n.id} splits without a gateway"
        if not (is_gateway and n.metadata.gateway.role == "join"):
            assert ins[n.id] <= 1, f"{n.id} joins without a join gateway"
    assert outs[g.entry_id] == 1
    for exit_id in g.exit_ids:
        assert ins[exit_id] == 1


def test_origin_path_maps_gateways_back_to_draft_ids():
    g = build("parallel")
    path = ["start", "start__split", "fetch_mail", "summarise__join", "summarise", "approve", "end__join", "end"]
    assert g.origin_path(path) == ["start", "fetch_mail", "summarise", "approve", "end"]


def test_normalized_graph_round_trips_through_json():
    g = build("vanhatalo_g0")
    assert WorkflowGraph.model_validate_json(g.model_dump_json()) == g
