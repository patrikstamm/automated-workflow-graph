# Our data-flow check (DECISIONS.md section 5.3): every value read is written before it, on every path.
import pytest

from src.schema import WorkflowDraft, check_format, normalize
from src.verify import VerifyConfig, repair_message, verify_graph
from tests.schema.examples import CATALOG, EXAMPLES, get


def llm(node_id, reads=(), writes=(), join=None):
    node = {"id": node_id, "kind": "llm", "task": f"Do {node_id}", "reads": list(reads), "writes": list(writes)}
    if join:
        node["join"] = join
    return node


def flow(nodes, edges, name="flow"):
    data = {"name": name,
            "nodes": [{"id": "start", "kind": "start"}, *nodes, {"id": "end", "kind": "end"}],
            "edges": [{"source": s, "target": t, **({"condition": c} if c else {})} for s, t, c in edges]}
    draft = WorkflowDraft.model_validate(data)
    assert check_format(draft, CATALOG) == []
    return normalize(draft)


def data_flow_findings(graph, **config):
    report = verify_graph(graph, VerifyConfig(**config))
    return report, [f for f in report.findings if f.check_id == "data_flow"]


@pytest.mark.parametrize("name", EXAMPLES)
def test_all_examples_pass_data_flow(name):
    draft = WorkflowDraft.model_validate(get(name))
    report = verify_graph(normalize(draft))
    check = next(c for c in report.checks if c.check_id == "data_flow")
    assert (check.origin, check.status) == ("ours", "PASS")


def test_value_never_written():
    g = flow([llm("send", reads=["summary"])], [("start", "send", None), ("send", "end", None)])
    report, [f] = data_flow_findings(g)
    assert report.verdict == "FAIL"
    assert (f.origin, f.status, f.nodes) == ("ours", "FAIL", ["send"])
    assert f.message == 'Step "send" reads "summary", but no step in the workflow writes it.'
    assert f.witness == ["start", "send"]


def test_input_counts_as_written_at_the_start():
    g = flow([llm("reply", reads=["email"], writes=["answer"])], [("start", "reply", None), ("reply", "end", None)])
    assert data_flow_findings(g)[1] != []
    report, findings = data_flow_findings(g, inputs=frozenset({"email"}))
    assert findings == [] and report.verdict == "PASS"


def test_written_on_only_one_choice_branch():
    g = flow(
        [{"id": "pick", "kind": "router"},
         llm("a", writes=["x"]), llm("b"),
         llm("use", reads=["x"], join="any")],
        [("start", "pick", None), ("pick", "a", "left"), ("pick", "b", "right"),
         ("a", "use", None), ("b", "use", None), ("use", "end", None)])
    _, [f] = data_flow_findings(g)
    assert f.nodes == ["use"]
    assert f.message.endswith("nothing writes it before this step on the path below.")
    assert f.witness == ["start", "pick", "b", "use"]           # the branch that skips the write


def test_written_on_every_choice_branch_passes():
    g = flow(
        [{"id": "pick", "kind": "router"},
         llm("a", writes=["x"]), llm("b", writes=["x"]),
         llm("use", reads=["x"], join="any")],
        [("start", "pick", None), ("pick", "a", "left"), ("pick", "b", "right"),
         ("a", "use", None), ("b", "use", None), ("use", "end", None)])
    assert data_flow_findings(g)[1] == []


def test_read_in_parallel_sibling_branch_fails():
    # "summarise" writes summary while "forward" runs at the same time and reads it.
    g = flow(
        [llm("fetch", writes=["email"]),
         llm("summarise", reads=["email"], writes=["summary"]),
         llm("forward", reads=["summary"]),
         llm("finish", join="all")],
        [("start", "fetch", None), ("fetch", "summarise", None), ("fetch", "forward", None),
         ("summarise", "finish", None), ("forward", "finish", None), ("finish", "end", None)])
    _, [f] = data_flow_findings(g)
    assert f.nodes == ["forward"] and f.witness == ["start", "fetch", "forward"]


def test_and_join_sees_writes_of_all_branches():
    g = flow(
        [llm("split"), llm("a", writes=["x"]), llm("b", writes=["y"]),
         llm("use", reads=["x", "y"], join="all")],
        [("start", "split", None), ("split", "a", None), ("split", "b", None),
         ("a", "use", None), ("b", "use", None), ("use", "end", None)])
    assert data_flow_findings(g)[1] == []


def test_loop_value_only_written_later_in_the_loop():
    # write_draft reads "feedback", which review only writes after the first draft.
    g = flow(
        [llm("write_draft", reads=["feedback"], writes=["draft"], join="any"),
         {"id": "review", "kind": "router", "reads": ["draft"], "writes": ["feedback"]}],
        [("start", "write_draft", None), ("write_draft", "review", None),
         ("review", "write_draft", "redo"), ("review", "end", "ok")])
    _, [f] = data_flow_findings(g)
    assert f.nodes == ["write_draft"] and f.witness == ["start", "write_draft"]


def test_unreachable_reader_is_not_reported_by_data_flow():
    g = flow([llm("lonely", reads=["nothing"])], [("start", "end", None)])
    assert data_flow_findings(g)[1] == []


def test_one_finding_per_missing_value_and_repair_text():
    g = flow([llm("send", reads=["a", "b"])], [("start", "send", None), ("send", "end", None)])
    report, findings = data_flow_findings(g)
    assert [f.message.split('"')[3] for f in findings] == ["a", "b"]
    text = repair_message(report=report)
    assert "Value read before it is written" in text and "Your workflow has 2 problems" in text
    assert "ours.data_flow" in report.timings_ms
