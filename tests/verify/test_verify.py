# Evaluator: AgentProof baseline + combined report + repair message (DECISIONS.md sections 4 and 5).
import json
import pathlib
import re

import pytest

from src.schema import WorkflowDraft, check_format, normalize
from src.verify import OrderRule, VerifyConfig, repair_message, verify_graph
from tests.schema.examples import CATALOG, EXAMPLES, get

SIX_CHECKS = {"exit_reachability", "reverse_reachability", "dead_ends", "router_shape",
              "human_presence", "tool_declarations"}


def graph_of(data: dict):
    draft = WorkflowDraft.model_validate(data)
    assert check_format(draft, CATALOG) == []      # every test graph is well-formed
    return normalize(draft)


def verify(data, **config):
    return verify_graph(graph_of(data), VerifyConfig(**config))


# ---------- the example graphs ----------

@pytest.mark.parametrize("name", [n for n in EXAMPLES if n != "email_triage"])
def test_clean_examples_pass(name):
    report = verify(get(name))
    assert report.verdict == "PASS"
    assert report.findings == []
    assert {c.check_id for c in report.checks if c.origin == "agentproof"} == SIX_CHECKS
    assert {c.check_id for c in report.checks if c.origin == "ours"} == {"data_flow", "soundness"}


def test_email_triage_dead_end_is_one_finding():
    report = verify(get("email_triage"))
    assert report.verdict == "FAIL"
    assert len(report.findings) == 1
    f = report.findings[0]
    assert (f.origin, f.check_id, f.status) == ("agentproof", "dead_ends", "FAIL")
    assert f.nodes == ["draft_response"]
    assert f.witness == ["start", "classify", "router", "normal_handler", "draft_response"]
    assert f.also_affected == ["normal_handler"]


def test_raw_agentproof_results_are_not_changed_by_merging():
    # The paper reports AgentProof's own results: both checks still fail in the raw data.
    report = verify(get("email_triage"))
    failed = sorted(c.check_id for c in report.checks if c.status == "FAIL")
    assert failed == ["dead_ends", "reverse_reachability"]
    raw = next(c for c in report.checks if c.check_id == "reverse_reachability").raw
    assert set(raw["livelock_nodes"]) == {"normal_handler", "draft_response"}


# ---------- livelock (no dead end to merge into) ----------

LIVELOCK = {
    "name": "stuck_loop",
    "nodes": [
        {"id": "start", "kind": "start"},
        {"id": "pick", "kind": "router"},
        {"id": "work", "kind": "llm", "task": "Work", "join": "any"},
        {"id": "check", "kind": "llm", "task": "Check"},
        {"id": "end", "kind": "end"},
    ],
    "edges": [
        {"source": "start", "target": "pick"},
        {"source": "pick", "target": "end", "condition": "done"},
        {"source": "pick", "target": "work", "condition": "work"},
        {"source": "work", "target": "check"},
        {"source": "check", "target": "work"},
    ],
}


def test_livelock_is_one_finding_for_the_whole_loop():
    report = verify(LIVELOCK)
    assert report.verdict == "FAIL"
    [f] = report.findings
    assert f.check_id == "reverse_reachability"
    assert f.nodes == ["work", "check"]                 # gateway work__join is mapped to "work"
    assert f.witness == ["start", "pick", "work"]


# ---------- human steps ----------

def test_require_human():
    report = verify(get("linear"), require_human=True)
    assert report.verdict == "FAIL"
    assert [f.check_id for f in report.findings] == ["human_presence"]


def test_sensitive_tool_behind_human_passes():
    report = verify(get("parallel"), sensitive_tools=frozenset({"gmail_send"}))
    assert report.verdict == "PASS"
    assert "human_gate_coverage" in {c.check_id for c in report.checks}


def test_sensitive_tool_without_human_fails_with_path():
    report = verify(get("loop"), sensitive_tools=frozenset({"gmail_send"}))
    [f] = report.findings
    assert f.check_id == "human_gate_coverage" and f.nodes == ["send"]
    assert f.witness == ["start", "write_draft", "review", "send"]


# ---------- order rules ----------

TWO_DRAFTS = {
    "name": "two_drafts",
    "nodes": [
        {"id": "start", "kind": "start"},
        {"id": "first", "kind": "tool", "tool": "gmail_draft",
         "args": [{"name": "to", "value": "a@example.com"}, {"name": "body", "value": "one"}]},
        {"id": "second", "kind": "tool", "tool": "gmail_draft",
         "args": [{"name": "to", "value": "a@example.com"}, {"name": "body", "value": "two"}]},
        {"id": "send", "kind": "tool", "tool": "gmail_send",
         "args": [{"name": "to", "value": "a@example.com"}, {"name": "subject", "value": "s"},
                  {"name": "body", "value": "b"}]},
        {"id": "end", "kind": "end"},
    ],
    "edges": [
        {"source": "start", "target": "first"},
        {"source": "first", "target": "second"},
        {"source": "second", "target": "send"},
        {"source": "send", "target": "end"},
    ],
}
SEND_AFTER_DRAFT = OrderRule("send_after_draft", "tool:gmail_draft -> F tool:gmail_send")


def test_order_rule_violation_fails():
    report = verify(TWO_DRAFTS, order_rules=(SEND_AFTER_DRAFT,))
    assert report.verdict == "FAIL"
    [f] = report.findings
    assert (f.origin, f.check_id, f.status) == ("agentproof", "order_rule:send_after_draft", "FAIL")
    assert f.witness == ["start", "first", "second"]
    assert "agentproof.order_rule:send_after_draft" in report.timings_ms


def test_order_rule_through_parallel_branch_is_inconclusive():
    data = json.loads(json.dumps(TWO_DRAFTS))
    # "first" now starts two parallel branches: "second" and "note".
    data["nodes"].insert(3, {"id": "note", "kind": "llm", "task": "Take a note"})
    data["nodes"][3 + 1]["join"] = "all"            # send waits for both branches
    data["edges"] = [
        {"source": "start", "target": "first"},
        {"source": "first", "target": "second"},
        {"source": "first", "target": "note"},
        {"source": "second", "target": "send"},
        {"source": "note", "target": "send"},
        {"source": "send", "target": "end"},
    ]
    report = verify(data, order_rules=(SEND_AFTER_DRAFT,))
    raw = next(c for c in report.checks if c.check_id.startswith("order_rule"))
    assert raw.status == "FAIL"                      # AgentProof's own result is unchanged
    [f] = report.findings
    assert f.status == "INCONCLUSIVE" and "parallel" in f.note
    assert report.verdict == "INCONCLUSIVE"


def test_order_rule_that_holds_passes():
    report = verify(get("loop"), order_rules=(OrderRule("no_drop", "G !tool:drop_table"),))
    assert report.verdict == "PASS"


def test_invalid_order_rule_is_a_config_error():
    with pytest.raises(ValueError, match="not valid AgentProof DSL"):
        verify(get("linear"), order_rules=(OrderRule("bad", "this is not a rule"),))


# ---------- report and repair message ----------

def test_report_is_json_serialisable_and_timed():
    report = verify(get("email_triage"))
    data = json.loads(json.dumps(report.to_dict()))
    assert data["verdict"] == "FAIL"
    assert set(data["ms_by_origin"]) == {"agentproof", "ours"}
    assert data["graph_size"] == {"nodes": 7, "edges": 6, "gateways": 0}
    # parallel: start__split, summarise__join and end__join are inserted gateways
    assert verify(get("parallel")).graph_size == {"nodes": 10, "edges": 11, "gateways": 3}


def test_repair_message_for_dead_end():
    text = repair_message(report=verify(get("email_triage")))
    assert text.startswith("Your workflow has 1 problem:\n1. Dead end:")
    assert "Path: start → classify → router → normal_handler → draft_response" in text
    assert "Also affected: normal_handler" in text
    assert "__" not in text                          # no gateway names reach the LLM


def test_repair_message_skips_inconclusive_and_includes_format_errors():
    from src.schema import FormatError
    report = verify(get("linear"))
    assert repair_message(report=report) == ""
    text = repair_message([FormatError("join_missing", "Needs a join.", "summarise")], report)
    assert "1. Format error (join_missing) at summarise: Needs a join." in text


# ---------- architecture rule (DECISIONS.md section 3) ----------

def test_only_baseline_imports_agentproof():
    src = pathlib.Path(__file__).resolve().parents[2] / "src"
    importers = sorted(
        str(p.relative_to(src)).replace("\\", "/") for p in src.rglob("*.py")
        if re.search(r"^\s*(from|import)\s+agentproof", p.read_text(encoding="utf-8"), re.M)
    )
    assert importers == ["verify/baseline/agentproof.py"]
