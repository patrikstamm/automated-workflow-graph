# Normalized graph -> AgentProof (DECISIONS.md section 1.5).
# Tests may import agentproof; only src/ code is limited to src/verify/baseline/.
import pytest
from agentproof import verify
from agentproof.graph.model import graph_from_dict

from src.schema import WorkflowDraft, normalize, to_agentproof_dict
from tests.schema.examples import EXAMPLES, get


def checks_for(name: str) -> tuple[dict[str, dict], object]:
    graph = normalize(WorkflowDraft.model_validate(get(name)))
    report = verify(graph_from_dict(to_agentproof_dict(graph)))
    return {c["check_id"]: c for c in report["structural"]["checks"]}, graph


@pytest.mark.parametrize("name", [n for n in EXAMPLES if n != "email_triage"])
def test_valid_examples_pass_all_six_agentproof_checks(name):
    checks, _ = checks_for(name)
    failed = [cid for cid, c in checks.items() if not c["passed"]]
    assert failed == []


def test_email_triage_dead_end_is_found_with_witness_trace():
    checks, graph = checks_for("email_triage")
    dead = checks["dead_ends"]
    assert not dead["passed"]
    witness = dead["witnesses"]["draft_response"]
    # Same path the AgentProof paper reports, in the LLM's own node ids.
    assert graph.origin_path(witness) == ["start", "classify", "router", "normal_handler", "draft_response"]
    # The dead end and the step leading only to it cannot reach an exit, so
    # reverse_reachability fails on both. Every other check passes.
    assert sorted(cid for cid, c in checks.items() if not c["passed"]) == ["dead_ends", "reverse_reachability"]
    assert set(checks["reverse_reachability"]["witnesses"]) == {"normal_handler", "draft_response"}


def test_router_shape_passes_with_a_loop_edge_out_of_a_router():
    checks, _ = checks_for("loop")
    assert checks["router_shape"]["passed"]


def test_mapping_of_kinds_and_tools():
    data = to_agentproof_dict(normalize(WorkflowDraft.model_validate(get("parallel"))))
    kinds = {n["id"]: n["kind"] for n in data["nodes"]}
    assert kinds["start"] == "entry" and kinds["end"] == "exit"
    assert kinds["start__split"] == kinds["summarise__join"] == kinds["end__join"] == "passthrough"
    assert kinds["approve"] == "human"
    tools = {n["id"]: n["tools"] for n in data["nodes"]}
    assert tools["send"] == ["gmail_send"] and tools["summarise"] == []
    assert data["entry_id"] == "start" and data["exit_ids"] == ["end"]
    assert all("metadata" not in e for e in data["edges"])


def test_node_metadata_survives_agentproof_round_trip():
    data = to_agentproof_dict(normalize(WorkflowDraft.model_validate(get("parallel"))))
    ap_graph = graph_from_dict(data)
    meta = dict(next(n for n in ap_graph.nodes if n.id == "summarise").metadata)
    assert meta["reads"] == ["emails", "events"] and meta["writes"] == ["summary"]
    gate = dict(next(n for n in ap_graph.nodes if n.id == "summarise__join").metadata)
    assert gate["gateway"] == {"role": "join", "logic": "AND"}
