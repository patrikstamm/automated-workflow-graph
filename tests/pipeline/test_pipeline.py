# Conditions A and B (DECISIONS.md sections 4 and 5.2), with a scripted fake LLM.
import copy
import json

import pytest

import src.generation.generate as gen_module
from src.catalog import TOOLS_BY_NAME
from src.humanprompt import build_request
from src.llm import LLMResult
from src.pipeline import run_condition_a, run_condition_b
from src.prompt import build_system_prompt
from src.verify import OrderRule, VerifyConfig
from tests.schema.examples import CATALOG, EMAIL_TRIAGE, LINEAR

# The email triage with its dead end fixed: draft_response now continues to the end.
FIXED_TRIAGE = copy.deepcopy(EMAIL_TRIAGE)
FIXED_TRIAGE["edges"].append({"source": "draft_response", "target": "end"})
next(n for n in FIXED_TRIAGE["nodes"] if n["id"] == "end")["join"] = "any"

BROKEN_FORMAT = copy.deepcopy(LINEAR)
BROKEN_FORMAT["nodes"][1]["tool"] = "fax_send"


class ScriptedLLM:
    """Returns the scripted answers in order and records every request."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.requests = []

    def __call__(self, provider, system, messages, schema=None):
        self.requests.append({"provider": provider, "messages": messages})
        answer = self.answers.pop(0)
        if isinstance(answer, LLMResult):
            return answer
        text = answer if isinstance(answer, str) else json.dumps(answer)
        return LLMResult(text=text, input_tokens=100, output_tokens=20, thinking_tokens=5, seconds=0.5,
                         stop_reason="STOP", provider=provider, model="fake-model")


@pytest.fixture
def llm(monkeypatch):
    def install(*answers):
        fake = ScriptedLLM(*answers)
        monkeypatch.setattr(gen_module, "call", fake)
        return fake
    return install


REQUEST = build_request("Triage the incoming email and reply to it.")


def run_b(**config):
    return run_condition_b(REQUEST, "gemini", VerifyConfig(**config), catalog=CATALOG)


# ---------- condition A ----------

def test_a_is_one_call_even_when_it_fails(llm):
    fake = llm(EMAIL_TRIAGE)
    run = run_condition_a(REQUEST, "gemini", catalog=CATALOG)
    assert (run.condition, run.status, len(fake.requests)) == ("A", "FAILED", 1)
    assert run.final.verdict == "FAIL"


def test_a_pass(llm):
    llm(LINEAR)
    assert run_condition_a(REQUEST, "gemini", catalog=CATALOG).status == "PASS"


# ---------- condition B ----------

def test_b_pass_first_time_makes_one_call(llm):
    fake = llm(FIXED_TRIAGE)
    run = run_b()
    assert (run.status, run.repair_rounds, len(fake.requests)) == ("PASS", 0, 1)


def test_b_repairs_a_dead_end(llm):
    fake = llm(EMAIL_TRIAGE, FIXED_TRIAGE)
    run = run_b()
    assert run.status == "PASS" and run.repair_rounds == 1
    assert [r.verdict for r in run.rounds] == ["FAIL", "PASS"]
    # Round 1 resends only the task, the previous answer and the repair message.
    messages = fake.requests[1]["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant", "user"]
    assert json.loads(messages[1]["content"]) == EMAIL_TRIAGE
    assert messages[2]["content"] == run.rounds[0].repair_text
    assert "Dead end" in messages[2]["content"] and "Path: start → classify" in messages[2]["content"]


def test_b_repairs_a_format_error(llm):
    fake = llm(BROKEN_FORMAT, LINEAR)
    run = run_b()
    assert [r.verdict for r in run.rounds] == ["FORMAT_ERROR", "PASS"]
    assert "Format error (unknown_tool)" in fake.requests[1]["messages"][2]["content"]


def test_b_gives_up_after_three_repair_rounds(llm):
    # Four different answers that all still have a dead end.
    variants = []
    for i in range(4):
        v = copy.deepcopy(EMAIL_TRIAGE)
        v["name"] = f"attempt_{i}"
        variants.append(v)
    fake = llm(*variants)
    run = run_b()
    assert (run.status, run.repair_rounds, len(fake.requests)) == ("FAILED", 3, 4)
    assert run.rounds[-1].repair_text == ""          # no message after the last round


def test_b_stops_when_the_answer_does_not_change(llm):
    same_but_reformatted = json.dumps(EMAIL_TRIAGE, indent=4)
    fake = llm(EMAIL_TRIAGE, same_but_reformatted)
    run = run_b()
    assert (run.status, len(fake.requests)) == ("NO_PROGRESS", 2)


def test_b_stops_when_blocked(llm):
    blocked = LLMResult(text="", input_tokens=50, output_tokens=0, seconds=0.1,
                        stop_reason="refusal", blocked=True, provider="claude", model="fake")
    fake = llm(blocked)
    run = run_b()
    assert (run.status, len(fake.requests)) == ("BLOCKED", 1)


def test_b_truncated_answer_is_repaired(llm):
    cut = LLMResult(text='{"name": "x", "nodes": [', input_tokens=100, output_tokens=4000, seconds=9.0,
                    stop_reason="MAX_TOKENS", truncated=True, provider="gemini", model="fake")
    fake = llm(cut, LINEAR)
    run = run_b()
    assert run.status == "PASS"
    assert "Return a shorter workflow" in fake.requests[1]["messages"][2]["content"]


def test_b_inconclusive_is_not_repaired(llm):
    # The order-rule search is stopped by its state bound, so the round is INCONCLUSIVE.
    data = {
        "name": "two_drafts",
        "nodes": [
            {"id": "start", "kind": "start"},
            {"id": "first", "kind": "tool", "tool": "gmail_draft",
             "args": [{"name": "to", "value": "a@example.com"}, {"name": "body", "value": "one"}]},
            {"id": "second", "kind": "tool", "tool": "gmail_draft",
             "args": [{"name": "to", "value": "a@example.com"}, {"name": "body", "value": "two"}]},
            {"id": "note", "kind": "llm", "task": "Take a note"},
            {"id": "send", "kind": "tool", "tool": "gmail_send", "join": "all",
             "args": [{"name": "to", "value": "a@example.com"}, {"name": "subject", "value": "s"},
                      {"name": "body", "value": "b"}]},
            {"id": "end", "kind": "end"},
        ],
        "edges": [
            {"source": "start", "target": "first"}, {"source": "first", "target": "second"},
            {"source": "first", "target": "note"}, {"source": "second", "target": "send"},
            {"source": "note", "target": "send"}, {"source": "send", "target": "end"},
        ],
    }
    fake = llm(data)
    run = run_b(order_rules=(OrderRule("send_after_draft", "tool:gmail_draft -> F tool:gmail_send"),),
                max_states=1)
    assert (run.status, len(fake.requests)) == ("INCONCLUSIVE", 1)


def test_totals_and_to_dict(llm):
    llm(EMAIL_TRIAGE, FIXED_TRIAGE)
    run = run_b()
    t = run.totals()
    assert (t["llm_calls"], t["input_tokens"], t["output_tokens"], t["thinking_tokens"]) == (2, 200, 40, 10)
    assert t["verify_ms"] > 0
    data = json.loads(json.dumps(run.to_dict()))
    assert data["status"] == "PASS" and [r["verdict"] for r in data["rounds"]] == ["FAIL", "PASS"]
    assert data["rounds"][0]["report"]["findings"][0]["check_id"] == "dead_ends"


# ---------- catalog ----------

def test_office_tools_are_in_the_catalog_and_prompt():
    office = {"gmail_search", "gmail_read", "gmail_send", "gmail_label",
              "sheets_read", "sheets_append", "calendar_list", "calendar_create"}
    assert office <= set(TOOLS_BY_NAME)
    system = build_system_prompt(build_request("anything"))
    assert "calendar_create(title: string, time: string, attendees: list of string)" in system
