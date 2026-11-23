# Our parallel-conflicts check (DECISIONS.md section 5.3): Bernstein's conditions for steps that
# run at the same time, on state values and on tool resources (suggestion 3).
import pytest

from src.catalog import RESOURCES
from src.schema import ToolSpec, WorkflowDraft, check_format, normalize
from src.verify import VerifyConfig, repair_message, verify_graph
from src.verify.ours.bernstein import conflicts, effects
from src.verify.ours.parallel import parallel_pairs
from tests.schema.examples import CATALOG, EXAMPLES, get

TOOLS = {
    **CATALOG,
    "sheets_append": ToolSpec("sheets_append", {"sheet": "string", "row": "array"}),
    "sheets_read": ToolSpec("sheets_read", {"sheet": "string"}),
    "gmail_label": ToolSpec("gmail_label", {"email_id": "string", "label": "string"}),
}


def graph_of(data):
    draft = WorkflowDraft.model_validate(data)
    assert check_format(draft, TOOLS) == []
    return normalize(draft)


def check(data, **config):
    report = verify_graph(graph_of(data), VerifyConfig(**config))
    result = next(c for c in report.checks if c.check_id == "parallel_conflict")
    return report, result, [f for f in report.findings if f.check_id == "parallel_conflict"]


def llm(node_id, reads=(), writes=(), join=None):
    node = {"id": node_id, "kind": "llm", "task": f"Do {node_id}", "reads": list(reads), "writes": list(writes)}
    if join:
        node["join"] = join
    return node


def append(node_id, sheet):
    reads = [sheet[1:]] if sheet.startswith("$") else []
    return {"id": node_id, "kind": "tool", "tool": "sheets_append", "reads": reads + ["row"],
            "args": [{"name": "sheet", "value": sheet}, {"name": "row", "value": "$row"}]}


def fork(a, b, before=None):
    """start -> prep -> (a || b) -> finish -> end"""
    prep = before or llm("prep")
    return {"name": "fork",
            "nodes": [{"id": "start", "kind": "start"}, prep, a, b, llm("finish", join="all"),
                      {"id": "end", "kind": "end"}],
            "edges": [{"source": "start", "target": "prep"},
                      {"source": "prep", "target": a["id"]}, {"source": "prep", "target": b["id"]},
                      {"source": a["id"], "target": "finish"}, {"source": b["id"], "target": "finish"},
                      {"source": "finish", "target": "end"}]}


ROW = llm("prep", writes=["row", "sheet_name"])


# ---------- the example graphs ----------

@pytest.mark.parametrize("name", EXAMPLES)
def test_examples_have_no_parallel_conflicts(name):
    _, result, findings = check(get(name))
    assert (result.origin, result.status) == ("ours", "PASS") and findings == []


def test_parallel_example_reads_two_different_resources():
    # fetch_mail reads gmail:inbox, fetch_events reads calendar: parallel, but no conflict.
    graph = graph_of(get("parallel"))
    assert [(a, b) for a, b, _ in parallel_pairs(graph)] == [("fetch_mail", "fetch_events")]


def test_vanhatalo_branches_that_never_join_are_parallel():
    assert [(a, b) for a, b, _ in parallel_pairs(graph_of(get("vanhatalo_g0")))] == [("a1", "a2")]


# ---------- state values ----------

def test_both_write_the_same_value():
    report, result, [f] = check(fork(llm("a", writes=["summary"]), llm("b", writes=["summary"])))
    assert report.verdict == "FAIL"
    assert (f.origin, f.status, f.nodes) == ("ours", "FAIL", ["a", "b"])
    assert f.witness == ["start", "prep", "a", "b"]
    assert f.message.startswith('Steps "a" and "b" run in parallel, but both write "summary".')


def test_overwrite_while_reading_is_reported_here():
    # x is written before the split, so data flow is happy; b reads x while a overwrites it.
    data = fork(llm("a", writes=["x"]), llm("b", reads=["x"]), before=llm("prep", writes=["x"]))
    report, _, [f] = check(data)
    assert '"a" writes "x" while "b" reads it' in f.message
    assert [g.check_id for g in report.findings] == ["parallel_conflict"]


def test_read_of_value_only_written_in_sibling_is_left_to_data_flow():
    report, result, findings = check(fork(llm("a", writes=["x"]), llm("b", reads=["x"])))
    assert findings == [] and result.status == "PASS"
    assert [g.check_id for g in report.findings] == ["data_flow"]      # each fault reaches the LLM once


def test_steps_in_sequence_are_not_parallel():
    data = {"name": "seq",
            "nodes": [{"id": "start", "kind": "start"}, llm("a", writes=["x"]), llm("b", writes=["x"]),
                      {"id": "end", "kind": "end"}],
            "edges": [{"source": "start", "target": "a"}, {"source": "a", "target": "b"},
                      {"source": "b", "target": "end"}]}
    assert check(data)[2] == []


def test_loop_around_a_parallel_block_does_not_hide_it():
    data = fork(llm("a", writes=["s"]), llm("b", writes=["s"]))
    data["nodes"][1]["join"] = "any"
    data["nodes"][4] = {"id": "finish", "kind": "router", "join": "all"}
    data["edges"][-1] = {"source": "finish", "target": "end", "condition": "done"}
    data["edges"].append({"source": "finish", "target": "prep", "condition": "again"})
    _, _, [f] = check(data)
    assert f.nodes == ["a", "b"]


# ---------- tool resources ----------

def test_two_appends_to_the_same_sheet():
    _, _, [f] = check(fork(append("a", "Budget"), append("b", "Budget"), before=ROW))
    assert f.status == "FAIL" and 'both change the tool resource "sheets:Budget"' in f.message


def test_appends_to_different_sheets_are_fine():
    assert check(fork(append("a", "Budget"), append("b", "Travel"), before=ROW))[2] == []


def test_run_time_sheet_name_is_inconclusive():
    report, result, [f] = check(fork(append("a", "$sheet_name"), append("b", "Budget"), before=ROW))
    assert result.status == "INCONCLUSIVE" and f.status == "INCONCLUSIVE"
    assert "run time" in f.note and report.verdict == "INCONCLUSIVE"
    assert repair_message(report=report) == ""                       # not sent to repair


def test_label_while_searching_the_inbox():
    label = {"id": "a", "kind": "tool", "tool": "gmail_label",
             "args": [{"name": "email_id", "value": "42"}, {"name": "label", "value": "done"}]}
    search = {"id": "b", "kind": "tool", "tool": "gmail_search", "args": [{"name": "query", "value": "is:unread"}]}
    report, _, [f] = check(fork(label, search))
    assert '"a" changes the tool resource "gmail:inbox" while "b" reads it' in f.message
    text = repair_message(report=report)
    assert "1. Parallel steps get in each other's way: Steps \"a\" and \"b\"" in text
    assert "Path: start → prep → a → b" in text


# ---------- bernstein.py, shared with the improver ----------

def test_bernstein_effects_and_read_read():
    graph = graph_of(fork(append("a", "$sheet_name"), llm("b", reads=["row"]), before=ROW))
    assert effects(graph.node("a"), RESOURCES).writes_res == frozenset({"sheets:*"})
    assert conflicts(graph.node("a"), graph.node("b"), RESOURCES) == []   # both only read "row"
