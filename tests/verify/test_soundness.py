# Our soundness check (DECISIONS.md section 5.3): no deadlock and no lack of synchronisation.
# Simple fragments are decided by pattern; only what is left is searched, within bounds.
import pytest

from src.schema import WorkflowDraft, check_format, normalize
from src.verify import VerifyConfig, repair_message, verify_graph
from tests.schema.examples import CATALOG, get


def graph_of(data):
    draft = WorkflowDraft.model_validate(data)
    assert check_format(draft, CATALOG) == []
    return normalize(draft)


def soundness(data, **config):
    report = verify_graph(graph_of(data), VerifyConfig(**config))
    check = next(c for c in report.checks if c.check_id == "soundness")
    return report, check, [f for f in report.findings if f.check_id == "soundness"]


def llm(node_id, join=None):
    node = {"id": node_id, "kind": "llm", "task": f"Do {node_id}"}
    if join:
        node["join"] = join
    return node


def flow(nodes, edges, extra_ends=()):
    ends = [{"id": "end", "kind": "end"}, *({"id": e, "kind": "end"} for e in extra_ends)]
    return {"name": "flow",
            "nodes": [{"id": "start", "kind": "start"}, *nodes, *ends],
            "edges": [{"source": s, "target": t, **({"condition": c} if c else {})} for s, t, c in edges]}


# ---------- the example graphs ----------

@pytest.mark.parametrize("name", ["linear", "parallel", "loop", "human"])
def test_structured_examples_are_decided_by_pattern(name):
    _, check, findings = soundness(get(name))
    assert (check.origin, check.status) == ("ours", "PASS") and findings == []
    assert check.raw["searched"] is False and check.raw["states"] == 0


def test_pattern_counts_are_recorded():
    _, check, _ = soundness(get("parallel"))
    assert check.raw["patterns"] == {"sequence": 8, "bond": 2, "loop": 0}
    assert soundness(get("loop"))[1].raw["patterns"]["loop"] == 1


def test_vanhatalo_g0_needs_the_search_and_is_sound():
    # Three end steps and a loop that leaves from a different step than it enters: a rigid fragment.
    _, check, findings = soundness(get("vanhatalo_g0"))
    assert check.status == "PASS" and findings == []
    assert check.raw["searched"] is True and check.raw["states"] == 10
    assert check.raw["search_size"] == {"nodes": 8, "edges": 8}


def test_dead_end_is_left_to_agentproof():
    # The dead end takes the token away, so soundness passes; AgentProof's dead_ends reports it.
    report, check, findings = soundness(get("email_triage"))
    assert check.status == "PASS" and findings == []
    assert [f.check_id for f in report.findings] == ["dead_ends"]


# ---------- faults found by pattern ----------

PARALLEL_TO_ANY = flow(
    [llm("fetch"), llm("a"), llm("b"), llm("finish", join="any")],
    [("start", "fetch", None), ("fetch", "a", None), ("fetch", "b", None),
     ("a", "finish", None), ("b", "finish", None), ("finish", "end", None)])

CHOICE_TO_ALL = flow(
    [{"id": "pick", "kind": "router"}, llm("a"), llm("b"), llm("finish", join="all")],
    [("start", "pick", None), ("pick", "a", "left"), ("pick", "b", "right"),
     ("a", "finish", None), ("b", "finish", None), ("finish", "end", None)])


def test_parallel_branches_into_any_join_is_lack_of_sync():
    report, check, [f] = soundness(PARALLEL_TO_ANY)
    assert report.verdict == "FAIL"
    assert check.raw["faults"][0]["found_by"] == "pattern" and check.raw["searched"] is False
    assert (f.origin, f.status, f.nodes) == ("ours", "FAIL", ["finish"])
    assert f.witness == ["start", "fetch", "a", "finish", "b", "finish"]   # finish runs twice
    assert "runs more than once" in f.message


def test_choice_into_all_join_is_deadlock():
    _, check, [f] = soundness(CHOICE_TO_ALL)
    assert check.raw["faults"][0]["found_by"] == "pattern"
    assert f.nodes == ["finish"] and f.witness == ["start", "pick", "a", "finish"]
    assert "gets stuck" in f.message and '"join": "any"' in f.message


def test_parallel_split_in_a_loop_is_lack_of_sync():
    data = flow([llm("work", join="any"), llm("check")],
                [("start", "work", None), ("work", "check", None),
                 ("check", "work", None), ("check", "end", None)])
    _, check, [f] = soundness(data)
    assert check.raw["faults"][0]["loop"] is True
    assert f.nodes == ["work"] and f.witness == ["start", "work", "check", "work"]
    assert "Use a router" in f.message


def test_all_join_at_loop_start_is_deadlock():
    data = flow([llm("work", join="all"), {"id": "check", "kind": "router"}],
                [("start", "work", None), ("work", "check", None),
                 ("check", "work", "again"), ("check", "end", "done")])
    _, _, [f] = soundness(data)
    assert f.nodes == ["work"] and "can never run the first time" in f.message


# ---------- faults found by the search (rigid fragment) ----------

def rigid(join):
    # v starts two branches; the a1 branch chooses between m and its own end step e1, a2 always goes to m.
    return flow(
        [llm("v"), llm("a1"), llm("a2"), {"id": "r", "kind": "router"}, llm("m", join=join)],
        [("start", "v", None), ("v", "a1", None), ("v", "a2", None), ("a1", "r", None),
         ("r", "m", "x"), ("r", "e1", "y"), ("a2", "m", None), ("m", "end", None)],
        extra_ends=["e1"])


def test_rigid_fragment_lack_of_sync_found_by_search():
    _, check, [f] = soundness(rigid("any"))
    assert check.raw["searched"] is True and check.raw["faults"][0]["found_by"] == "search"
    assert f.nodes == ["m"] and f.witness == ["start", "v", "a1", "a2", "m", "r", "m"]


def test_rigid_fragment_deadlock_found_by_search():
    _, check, [f] = soundness(rigid("all"))
    assert check.raw["faults"][0]["found_by"] == "search"
    assert f.nodes == ["m"] and f.witness == ["start", "v", "a1", "a2", "r", "e1", "m"]


def test_repair_message_for_soundness():
    report, _, _ = soundness(PARALLEL_TO_ANY)
    text = repair_message(report=report)
    assert "1. Branches do not fit together: Step \"finish\"" in text
    assert "Path: start → fetch → a → finish → b → finish" in text


# ---------- bounds (DECISIONS.md section 4, suggestion 2) ----------

@pytest.mark.parametrize("config, bound", [({"max_states": 2}, "max_states"), ({"timeout_s": 0}, "timeout_s")])
def test_hitting_a_bound_is_inconclusive_never_pass(config, bound):
    report, check, [f] = soundness(get("vanhatalo_g0"), **config)
    assert check.status == "INCONCLUSIVE" and check.raw["limit"]["bound"] == bound
    assert f.status == "INCONCLUSIVE" and "bound was hit" in f.note
    assert report.verdict == "INCONCLUSIVE"
    assert repair_message(report=report) == ""            # not sent to repair


def test_pattern_only_graph_never_hits_a_bound():
    _, check, _ = soundness(get("parallel"), max_states=1, timeout_s=0)
    assert check.status == "PASS" and check.raw["limit"] is None


def test_default_bounds():
    assert (VerifyConfig().max_states, VerifyConfig().timeout_s) == (10_000, 2.0)
