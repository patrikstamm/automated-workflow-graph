# Our exact order-rule check (DECISIONS.md section 5.3): AgentProof's rule language, checked on
# finished runs and on every interleaving of parallel branches. Our result decides the verdict.
import pytest

from src.schema import WorkflowDraft, check_format, normalize
from src.verify import OrderRule, VerifyConfig, repair_message, verify_graph
from src.verify.ours.order_rules import Both, Chain, Until, parse
from tests.schema.examples import CATALOG, get


def graph_of(data):
    draft = WorkflowDraft.model_validate(data)
    assert check_format(draft, CATALOG) == []
    return normalize(draft)


def verify(data, dsl, **config):
    report = verify_graph(graph_of(data), VerifyConfig(order_rules=(OrderRule("r", dsl),), **config))
    ours = next(c for c in report.checks if c.origin == "ours" and c.check_id == "order_rule:r")
    agentproof = next(c for c in report.checks if c.origin == "agentproof" and c.check_id == "order_rule:r")
    return report, ours, agentproof


def tool(node_id, name, join=None):
    args = {"gmail_draft": [("to", "a@example.com"), ("body", "b")],
            "gmail_send": [("to", "a@example.com"), ("subject", "s"), ("body", "b")],
            "test_tool": [("text", "hi")]}[name]
    node = {"id": node_id, "kind": "tool", "tool": name, "args": [{"name": k, "value": v} for k, v in args]}
    return {**node, "join": join} if join else node


def flow(nodes, edges):
    return {"name": "flow", "nodes": [{"id": "start", "kind": "start"}, *nodes, {"id": "end", "kind": "end"}],
            "edges": [{"source": s, "target": t, **({"condition": c} if c else {})} for s, t, c in edges]}


DRAFT_THEN_END = flow([tool("draft", "gmail_draft")], [("start", "draft", None), ("draft", "end", None)])
SEND_AFTER_DRAFT = "tool:gmail_draft -> F tool:gmail_send"


# ---------- AgentProof's two limits ----------

def test_rule_still_open_at_the_end_fails():
    report, ours, agentproof = verify(DRAFT_THEN_END, SEND_AFTER_DRAFT)
    assert agentproof.status == "PASS"                   # AgentProof misses it
    assert (ours.status, ours.raw["when"]) == ("FAIL", "at_end")
    [f] = report.findings
    assert f.origin == "ours" and f.witness == ["start", "draft", "end"]
    assert f.message == f'The order rule "{SEND_AFTER_DRAFT}" is still not met when the workflow ends on this path.'
    assert (report.verdict, report.agentproof_verdict) == ("FAIL", "PASS")


def test_rule_met_before_the_end_passes():
    data = flow([tool("draft", "gmail_draft"), tool("send", "gmail_send")],
                [("start", "draft", None), ("draft", "send", None), ("send", "end", None)])
    assert verify(data, SEND_AFTER_DRAFT)[1].status == "PASS"


def test_parallel_branches_are_interleaved():
    # draft and send run in parallel: in one interleaving send comes first, so the draft is never sent.
    data = flow([{"id": "fork", "kind": "llm", "task": "Prepare"}, tool("draft", "gmail_draft"),
                 tool("send", "gmail_send"), {"id": "finish", "kind": "llm", "task": "Finish", "join": "all"}],
                [("start", "fork", None), ("fork", "draft", None), ("fork", "send", None),
                 ("draft", "finish", None), ("send", "finish", None), ("finish", "end", None)])
    report, ours, _ = verify(data, SEND_AFTER_DRAFT)
    assert ours.raw["when"] == "at_end"
    assert report.findings[0].witness == ["start", "fork", "send", "draft", "finish", "end"]


# ---------- differences from AgentProof, on purpose ----------

def test_decision_atoms_match_the_branch_taken():
    # In HUMAN, "approved" leads to send; AgentProof's static check never matches decision atoms.
    report, ours, agentproof = verify(get("human"), "decision:approved -> F tool:gmail_send")
    assert ours.status == "PASS" and agentproof.status == "PASS"
    data = get("human")
    data["edges"] = [{"source": "start", "target": "write_reply"}, {"source": "write_reply", "target": "approve"},
                     {"source": "approve", "target": "end", "condition": "approved"},
                     {"source": "approve", "target": "send", "condition": "rejected"},
                     {"source": "send", "target": "end"}]
    report, ours, agentproof = verify(data, "decision:approved -> F tool:gmail_send")
    assert ours.status == "FAIL" and agentproof.status == "PASS"
    assert report.findings[0].witness == ["start", "write_reply", "approve", "end"]


def test_gateways_do_not_count_as_steps_in_a_bound():
    # draft -> send, where send has a join gateway in front of it (a router can also skip the draft).
    data = flow([{"id": "pick", "kind": "router"}, tool("draft", "gmail_draft"), tool("send", "gmail_send", join="any")],
                [("start", "pick", None), ("pick", "draft", "with_draft"), ("pick", "send", "direct"),
                 ("draft", "send", None), ("send", "end", None)])
    report, ours, agentproof = verify(data, "tool:gmail_draft -> F[<=1] tool:gmail_send")
    assert agentproof.status == "FAIL"                   # AgentProof counts send__join as a step
    assert ours.status == "PASS" and report.verdict == "PASS"


def test_start_and_end_are_not_events():
    report, ours, agentproof = verify(get("linear"), "G !action:entry")
    assert agentproof.status == "FAIL" and ours.status == "PASS"


def test_nested_until():
    rule = "action:llm U (action:tool U action:human)"
    assert parse(rule) == Until(("action:llm", "action:tool", "action:human"))
    ok = flow([{"id": "think", "kind": "llm", "task": "Think"}, tool("run", "test_tool"),
               {"id": "check", "kind": "human", "task": "Check"}],
              [("start", "think", None), ("think", "run", None), ("run", "check", None), ("check", "end", None)])
    assert verify(ok, rule)[1].status == "PASS"
    bad = flow([tool("run", "test_tool"), {"id": "think", "kind": "llm", "task": "Think"},
                {"id": "check", "kind": "human", "task": "Check"}],
               [("start", "run", None), ("run", "think", None), ("think", "check", None), ("check", "end", None)])
    report, ours, _ = verify(bad, rule)
    assert (ours.status, ours.raw["when"]) == ("FAIL", "during")
    assert report.findings[0].nodes == ["think"]         # an llm step after the tool phase started


# ---------- the rule shapes ----------

def test_forbidden_in_a_loop():
    report, ours, _ = verify(get("loop"), "G !tool:gmail_send")
    assert ours.status == "FAIL"
    [f] = report.findings
    assert f.nodes == ["send"] and f.witness == ["start", "write_draft", "review", "send"]
    assert 'is broken at step "send"' in f.message


def test_chain_and_combinations():
    assert parse("a -> F b -> F c") == Chain(("a", "b", "c"))
    assert isinstance(parse("(G !a) AND (b -> F c)"), Both)
    assert verify(get("parallel"), "tool:gmail_search -> F action:human -> F tool:gmail_send")[1].status == "FAIL"
    either = "(G !tool:gmail_send) OR (decision:approved -> F tool:gmail_send)"
    assert verify(get("parallel"), either)[1].status == "PASS"
    both = "(G !tool:gmail_send) AND (decision:approved -> F tool:gmail_send)"
    assert verify(get("parallel"), both)[1].status == "FAIL"


def test_invalid_rule_is_rejected():
    with pytest.raises(ValueError, match="not a supported order rule"):
        parse("this is not a rule")


# ---------- bounds and repair ----------

def test_hitting_the_state_bound_is_inconclusive():
    report, ours, _ = verify(get("parallel"), SEND_AFTER_DRAFT, max_states=1)
    assert ours.status == "INCONCLUSIVE" and ours.raw["limit"]["bound"] == "max_states"
    [f] = report.findings
    assert f.status == "INCONCLUSIVE" and "bound was hit" in f.note
    assert report.verdict == "INCONCLUSIVE" and repair_message(report=report) == ""


def test_repair_message_for_open_rule():
    report, _, _ = verify(DRAFT_THEN_END, SEND_AFTER_DRAFT)
    text = repair_message(report=report)
    assert "1. Order rule broken: The order rule" in text and "Path: start → draft → end" in text
