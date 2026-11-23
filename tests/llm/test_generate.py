# generate_draft (condition A: one call, no retries) and the prompt's own example.
import json

import pytest

import src.generation.generate as gen_module
from src.generation import generate_draft
from src.humanprompt import build_request
from src.llm import LLMResult
from src.prompt import EXAMPLE_WORKFLOW, build_system_prompt
from src.schema import load_draft
from tests.schema.examples import CATALOG, LINEAR


def fake_call(text, **flags):
    calls = []

    def call(provider, system, messages, schema=None):
        calls.append({"provider": provider, "system": system, "messages": messages, "schema": schema})
        return LLMResult(text=text, input_tokens=1, output_tokens=1, seconds=0.0,
                         stop_reason=flags.pop("stop_reason", "STOP"), **flags)

    return call, calls


@pytest.fixture
def request_():
    return build_request("Echo the word hello.")


def test_valid_answer_gives_draft_and_one_call(monkeypatch, request_):
    call, calls = fake_call(json.dumps(LINEAR))
    monkeypatch.setattr(gen_module, "call", call)
    g = generate_draft(request_, "claude")
    assert g.ok and g.draft.name == "echo_hello"
    assert len(calls) == 1
    assert calls[0]["provider"] == "claude"
    assert calls[0]["schema"]["$defs"]["DraftNode"]           # schema is always sent
    assert calls[0]["messages"] == [{"role": "user", "content": "Echo the word hello."}]
    assert g.system == calls[0]["system"]


def test_format_errors_are_returned_not_retried(monkeypatch, request_):
    broken = json.loads(json.dumps(LINEAR))
    broken["nodes"][1]["tool"] = "fax_send"
    call, calls = fake_call(json.dumps(broken))
    monkeypatch.setattr(gen_module, "call", call)
    g = generate_draft(request_, "gemini")
    assert not g.ok and [e.code for e in g.errors] == ["unknown_tool"]
    assert len(calls) == 1


def test_truncated_answer(monkeypatch, request_):
    call, _ = fake_call('{"name": "x", "nodes": [', truncated=True, stop_reason="MAX_TOKENS")
    monkeypatch.setattr(gen_module, "call", call)
    g = generate_draft(request_, "gemini")
    assert g.draft is None
    assert [e.code for e in g.errors][:2] == ["truncated", "invalid_json"]


def test_blocked_answer(monkeypatch, request_):
    call, _ = fake_call("", blocked=True, stop_reason="refusal")
    monkeypatch.setattr(gen_module, "call", call)
    g = generate_draft(request_, "claude")
    assert g.draft is None and [e.code for e in g.errors] == ["blocked"]


def test_prompt_example_is_a_valid_draft():
    # The example the LLM copies must pass our own format checks (the old one was broken JSON).
    draft, errors = load_draft(EXAMPLE_WORKFLOW, CATALOG)
    assert draft is not None and errors == []


def test_prompt_describes_the_new_format(request_):
    system = build_system_prompt(request_)
    for phrase in ('"join": "all"', '"condition"', '"router"', '"human"', '"$summary"', "test_tool(text: string)"):
        assert phrase in system, phrase
    assert '"from"' not in system and '"agent"' not in system   # old format is gone
