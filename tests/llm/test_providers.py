# Claude and Gemini adapters, offline (fake clients built from real SDK types).
import anthropic
import google.genai as genai
import pytest
from google.genai import types as G

from src.llm import call, draft_schema, model_for
from src.llm.claude import to_claude_messages
from tests.llm.fakes import FakeAnthropic, FakeGenai, claude_message, gemini_response

MESSAGES = [{"role": "user", "content": "Echo hello"}]


@pytest.fixture(autouse=True)
def fakes(monkeypatch):
    monkeypatch.setenv("LLM_CACHE", "off")
    monkeypatch.delenv("CLAUDE_MODEL", raising=False)
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    monkeypatch.setattr(anthropic, "Anthropic", FakeAnthropic)
    monkeypatch.setattr(genai, "Client", FakeGenai)
    FakeAnthropic.calls, FakeGenai.calls = [], []


# ---------- Claude ----------

def test_claude_request_shape():
    FakeAnthropic.response = claude_message('{"a": 1}')
    schema = draft_schema()
    call("claude", "SYSTEM", MESSAGES, schema=schema)
    kw = FakeAnthropic.calls[0]
    assert kw["model"] == "claude-haiku-5-5"
    assert kw["system"] == "SYSTEM"
    assert kw["max_tokens"] == 4000
    assert kw["messages"] == MESSAGES
    assert kw["output_config"] == {"format": {"type": "json_schema", "schema": schema}}
    assert "temperature" not in kw


def test_claude_without_schema_sends_no_output_config():
    FakeAnthropic.response = claude_message()
    call("claude", "S", MESSAGES)
    assert "output_config" not in FakeAnthropic.calls[0]


def test_claude_result_normalises_tokens():
    FakeAnthropic.response = claude_message('{"a": 1}', input_tokens=80, output_tokens=60,
                                            thinking=20, cache_read=15, cache_write=5)
    r = call("claude", "S", MESSAGES)
    assert r.text == '{"a": 1}'
    assert r.input_tokens == 80 + 15 + 5      # cached prompt tokens included, like Gemini
    assert r.output_tokens == 40              # visible part only
    assert r.thinking_tokens == 20
    assert (r.cache_read_tokens, r.cache_write_tokens) == (15, 5)
    assert (r.provider, r.model, r.stop_reason) == ("claude", "claude-haiku-5-5", "end_turn")
    assert not r.truncated and not r.blocked


@pytest.mark.parametrize("reason, truncated, blocked", [
    ("max_tokens", True, False), ("refusal", False, True), ("end_turn", False, False)])
def test_claude_stop_reasons(reason, truncated, blocked):
    FakeAnthropic.response = claude_message(stop_reason=reason)
    r = call("claude", "S", MESSAGES)
    assert (r.truncated, r.blocked) == (truncated, blocked)


def test_claude_messages_are_merged_and_checked():
    msgs = [{"role": "user", "content": "a"}, {"role": "user", "content": "b"},
            {"role": "assistant", "content": "c"}, {"role": "user", "content": "d"}]
    assert to_claude_messages(msgs) == [
        {"role": "user", "content": "a\n\nb"}, {"role": "assistant", "content": "c"},
        {"role": "user", "content": "d"}]
    with pytest.raises(ValueError):
        to_claude_messages([{"role": "assistant", "content": "x"}])
    with pytest.raises(ValueError):
        to_claude_messages([])


# ---------- Gemini ----------

def test_gemini_request_shape():
    FakeGenai.response = gemini_response()
    schema = draft_schema()
    call("gemini", "SYSTEM", MESSAGES + [{"role": "assistant", "content": "x"}], schema=schema)
    kw = FakeGenai.calls[0]
    assert kw["model"] == "gemini-3.5-flash"
    assert kw["contents"][1]["role"] == "model"
    cfg = kw["config"]
    assert cfg.system_instruction == "SYSTEM"
    assert cfg.response_mime_type == "application/json"
    assert cfg.response_json_schema == schema
    assert cfg.response_schema is None
    assert cfg.temperature is None            # provider default (DECISIONS.md section 2)


def test_gemini_result_normalises_tokens():
    FakeGenai.response = gemini_response('{"a": 1}', prompt=90, output=40, thoughts=30, cached=10)
    r = call("gemini", "S", MESSAGES)
    assert r.text == '{"a": 1}'
    assert (r.input_tokens, r.output_tokens, r.thinking_tokens, r.cache_read_tokens) == (90, 40, 30, 10)
    assert (r.provider, r.stop_reason) == ("gemini", "STOP")


def test_gemini_truncated_and_blocked():
    FakeGenai.response = gemini_response(finish=G.FinishReason.MAX_TOKENS)
    assert call("gemini", "S", MESSAGES).truncated
    FakeGenai.response = gemini_response(finish=G.FinishReason.SAFETY)
    assert call("gemini", "S", MESSAGES).blocked
    FakeGenai.response = G.GenerateContentResponse(
        prompt_feedback=G.GenerateContentResponsePromptFeedback(block_reason=G.BlockedReason.SAFETY))
    r = call("gemini", "S", MESSAGES)
    assert r.blocked and r.text == "" and r.stop_reason == "BLOCKED: SAFETY"


# ---------- shared ----------

def test_unknown_provider():
    with pytest.raises(ValueError):
        call("gpt", "S", MESSAGES)


def test_model_override_from_env(monkeypatch):
    monkeypatch.setenv("CLAUDE_MODEL", "claude-other")
    assert model_for("claude") == "claude-other"
    assert model_for("gemini") == "gemini-3.5-flash"


def test_old_providers_import_still_works():
    from src.providers import LLMResult, call as old_call
    FakeGenai.response = gemini_response('{"a": 1}')
    r = old_call(model="gemini", system="S", messages=MESSAGES)
    assert isinstance(r, LLMResult) and r.text == '{"a": 1}'
