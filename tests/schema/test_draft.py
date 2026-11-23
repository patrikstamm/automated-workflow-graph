# Format checks on the draft (DECISIONS.md section 1.3).
import json

import pytest

from src.schema import WorkflowDraft, check_format, load_draft
from tests.schema.examples import CATALOG, EXAMPLES, get


def codes(data: dict) -> list[str]:
    draft, errors = load_draft(json.dumps(data), CATALOG)
    return [e.code for e in errors]


@pytest.mark.parametrize("name", EXAMPLES)
def test_valid_examples_have_no_format_errors(name):
    assert codes(get(name)) == []


def test_json_schema_has_no_recursion():
    # Both providers need a flat schema (DECISIONS.md 1.2): no definition may refer to itself
    # or back to the top-level model.
    schema = WorkflowDraft.model_json_schema()
    assert "#/$defs/WorkflowDraft" not in json.dumps(schema)
    for name, definition in schema.get("$defs", {}).items():
        assert f"#/$defs/{name}" not in json.dumps(definition), name


def test_invalid_json():
    _, errors = load_draft("{not json", CATALOG)
    assert [e.code for e in errors] == ["invalid_json"]


def test_unknown_field_is_a_schema_error():
    data = get("linear")
    data["nodes"][1]["colour"] = "blue"
    assert codes(data) == ["schema"]


def test_errors_are_all_reported_at_once():
    data = get("linear")
    data["nodes"][1]["tool"] = "fax_send"        # unknown tool
    data["nodes"].append({"id": "end", "kind": "end"})  # duplicate id
    assert set(codes(data)) >= {"unknown_tool", "duplicate_id"}


def test_errors_carry_location_and_readable_message():
    data = get("parallel")
    del data["nodes"][3]["join"]
    _, errors = load_draft(json.dumps(data), CATALOG)
    assert len(errors) == 1
    assert errors[0].where == "summarise"
    assert "join" in str(errors[0])


# ---- The eight malformed drafts from DECISIONS.md 1.6, plus the other rules in 1.3 ----

def _duplicate_id(d):            d["nodes"].append({"id": "echo", "kind": "llm", "task": "x"})
def _unknown_tool(d):            d["nodes"][1]["tool"] = "fax_send"
def _wrong_args(d):              d["nodes"][1]["args"] = [{"name": "txt", "value": "hello"}]
def _router_edge_no_condition(d): del d["edges"][2]["condition"]
def _missing_join(d):            del d["nodes"][3]["join"]
def _ref_not_in_reads(d):        d["nodes"][5]["reads"] = []
def _two_starts(d):              d["nodes"].append({"id": "start_two", "kind": "start"})
def _end_has_outgoing(d):        d["edges"].append({"source": "end", "target": "echo"})
def _bad_id(d):                  d["nodes"][1]["id"] = "Echo__1"
def _unknown_endpoint(d):        d["edges"].append({"source": "echo", "target": "nowhere"})
def _duplicate_edge(d):          d["edges"].append({"source": "echo", "target": "end"})
def _no_end(d):                  d["nodes"][2]["kind"] = "llm"; d["nodes"][2]["task"] = "x"
def _start_has_incoming(d):      d["edges"].append({"source": "echo", "target": "start"})
def _missing_task(d):            d["nodes"][1]["task"] = "  "
def _missing_tool(d):            d["nodes"][1]["tool"] = None
def _unexpected_field(d):        d["nodes"][1]["tool"] = "gmail_search"
def _arg_type(d):
    d["nodes"][1] = {"id": "echo", "kind": "tool", "tool": "calendar_create",
                     "args": [{"name": "title", "value": "Sync"}, {"name": "time", "value": "10:00"},
                              {"name": "attendees", "value": "a@x.com"}]}
def _router_one_branch(d):       d["edges"] = [e for e in d["edges"] if e.get("condition") != "normal"]
def _duplicate_condition(d):     d["edges"][3]["condition"] = "urgent"
def _unexpected_condition(d):    d["edges"][0]["condition"] = "always"
def _join_unexpected(d):         d["nodes"][1]["join"] = "all"
def _bad_state_name(d):          d["nodes"][1]["writes"] = ["Echoed Text"]


MALFORMED = [
    # (example, change, expected error code)
    ("linear", _duplicate_id, "duplicate_id"),
    ("linear", _unknown_tool, "unknown_tool"),
    ("linear", _wrong_args, "args_mismatch"),
    ("email_triage", _router_edge_no_condition, "missing_condition"),
    ("parallel", _missing_join, "join_missing"),
    ("parallel", _ref_not_in_reads, "ref_not_in_reads"),
    ("linear", _two_starts, "start_count"),
    ("linear", _end_has_outgoing, "end_has_outgoing"),
    ("linear", _bad_id, "bad_id"),
    ("linear", _unknown_endpoint, "unknown_edge_endpoint"),
    ("linear", _duplicate_edge, "duplicate_edge"),
    ("linear", _no_end, "no_end"),
    ("linear", _start_has_incoming, "start_has_incoming"),
    ("email_triage", _missing_task, "missing_task"),
    ("linear", _missing_tool, "missing_tool"),
    ("email_triage", _unexpected_field, "unexpected_field"),
    ("linear", _arg_type, "arg_type"),
    ("email_triage", _router_one_branch, "router_needs_branches"),
    ("email_triage", _duplicate_condition, "duplicate_condition"),
    ("linear", _unexpected_condition, "unexpected_condition"),
    ("linear", _join_unexpected, "join_unexpected"),
    ("linear", _bad_state_name, "bad_state_name"),
]


@pytest.mark.parametrize("example, change, expected", MALFORMED, ids=[m[2] for m in MALFORMED])
def test_malformed_draft_gives_specific_error(example, change, expected):
    data = get(example)
    change(data)
    assert expected in codes(data)


def test_reference_check_ignores_literal_dollar_text():
    data = get("linear")
    data["nodes"][1]["args"] = [{"name": "text", "value": "costs $5"}]
    assert codes(data) == []


def test_check_format_works_on_model_directly():
    draft = WorkflowDraft.model_validate(get("loop"))
    assert check_format(draft, CATALOG) == []
