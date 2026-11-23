# Provider schema export and the record/replay cache.
import json

import pytest

from src.llm import LLMResult
from src.llm import replay
from src.llm.schema import draft_schema
from src.schema import WorkflowDraft

# Keywords Gemini's response_json_schema accepts (google-genai 3.6.1 docs), plus "$schema"-style keys.
GEMINI_KEYWORDS = {"$id", "$defs", "$ref", "$anchor", "type", "format", "title", "description", "enum",
                   "items", "prefixItems", "minItems", "maxItems", "minimum", "maximum", "anyOf", "oneOf",
                   "properties", "additionalProperties", "required", "propertyOrdering"}


def walk(node, in_names=False):
    """Yield every schema object (skipping the name level of properties/$defs)."""
    if isinstance(node, list):
        for x in node:
            yield from walk(x)
    elif isinstance(node, dict):
        if in_names:
            for sub in node.values():
                yield from walk(sub)
            return
        yield node
        for k, v in node.items():
            yield from walk(v, in_names=k in ("properties", "$defs"))


def test_schema_only_uses_keywords_gemini_supports():
    for obj in walk(draft_schema()):
        unsupported = set(obj) - GEMINI_KEYWORDS
        assert not unsupported, unsupported


def test_ref_stands_alone():
    # Gemini: "If $ref is set on a sub-schema, no other properties ... may be set."
    for obj in walk(draft_schema()):
        if "$ref" in obj:
            assert all(k.startswith("$") for k in obj), obj


def test_field_names_survive_cleaning():
    node = draft_schema()["$defs"]["DraftNode"]["properties"]
    assert set(node) == set(WorkflowDraft.model_json_schema()["$defs"]["DraftNode"]["properties"])
    assert draft_schema()["$defs"]["DraftNode"]["additionalProperties"] is False


def test_schema_is_stable():
    assert json.dumps(draft_schema(), sort_keys=True) == json.dumps(draft_schema(), sort_keys=True)


# ---------- replay ----------

class Sender:
    def __init__(self, result):
        self.result, self.count = result, 0

    def __call__(self, *args):
        self.count += 1
        return self.result


ARGS = ("gemini", "gemini-3.5-flash", "SYSTEM", [{"role": "user", "content": "x"}], {"type": "object"})


@pytest.fixture
def cache(monkeypatch, tmp_path):
    monkeypatch.setenv("LLM_CACHE_DIR", str(tmp_path))
    return tmp_path


def good():
    return LLMResult(text='{"a": 1}', input_tokens=10, output_tokens=5, seconds=1.0, stop_reason="STOP")


def test_off_always_calls(monkeypatch, cache):
    monkeypatch.setenv("LLM_CACHE", "off")
    send = Sender(good())
    replay.cached_call(*ARGS, send)
    replay.cached_call(*ARGS, send)
    assert send.count == 2 and not list(cache.iterdir())


def test_record_saves_then_reuses(monkeypatch, cache):
    monkeypatch.setenv("LLM_CACHE", "record")
    send = Sender(good())
    first = replay.cached_call(*ARGS, send)
    second = replay.cached_call(*ARGS, send)
    assert send.count == 1
    assert not first.cached and second.cached
    assert second.text == first.text and second.input_tokens == 10


def test_record_does_not_save_failed_answers(monkeypatch, cache):
    monkeypatch.setenv("LLM_CACHE", "record")
    bad = good()
    bad.truncated = True
    send = Sender(bad)
    replay.cached_call(*ARGS, send)
    replay.cached_call(*ARGS, send)
    assert send.count == 2


def test_replay_miss_and_hit(monkeypatch, cache):
    monkeypatch.setenv("LLM_CACHE", "replay")
    with pytest.raises(replay.ReplayMiss):
        replay.cached_call(*ARGS, Sender(good()))
    monkeypatch.setenv("LLM_CACHE", "record")
    replay.cached_call(*ARGS, Sender(good()))
    monkeypatch.setenv("LLM_CACHE", "replay")
    send = Sender(good())
    assert replay.cached_call(*ARGS, send).cached and send.count == 0


def test_key_changes_with_any_input():
    base = replay.request_key(*ARGS)
    assert replay.request_key("claude", *ARGS[1:]) != base
    assert replay.request_key(*ARGS[:2], "OTHER", *ARGS[3:]) != base
    assert replay.request_key(*ARGS[:4], None) != base


def test_bad_mode(monkeypatch):
    monkeypatch.setenv("LLM_CACHE", "sometimes")
    with pytest.raises(ValueError):
        replay.mode()
