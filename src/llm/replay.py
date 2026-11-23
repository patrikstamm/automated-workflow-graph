# Record/replay cache for LLM calls (DECISIONS.md section 2: keep costs low).
#   LLM_CACHE=off     always call the provider (default)
#   LLM_CACHE=record  reuse a saved answer if there is one, otherwise call and save it
#   LLM_CACHE=replay  only use saved answers; a missing one is an error (guarantees zero API cost)
# Answers are saved as JSON in LLM_CACHE_DIR (default .llm_cache/, which git and Docker ignore).
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict
from pathlib import Path
from typing import Callable

from src.llm.base import LLMResult

MODES = ("off", "record", "replay")


class ReplayMiss(RuntimeError):
    """LLM_CACHE=replay and no saved answer exists for this request."""


def mode() -> str:
    m = os.getenv("LLM_CACHE", "off").strip().lower()
    if m not in MODES:
        raise ValueError(f"LLM_CACHE must be one of {', '.join(MODES)}, got {m!r}.")
    return m


def cache_dir() -> Path:
    return Path(os.getenv("LLM_CACHE_DIR", ".llm_cache"))


def request_key(provider: str, model: str, system: str, messages: list[dict], schema: dict | None) -> str:
    payload = json.dumps(
        {"provider": provider, "model": model, "system": system, "messages": messages, "schema": schema},
        sort_keys=True, ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def cached_call(provider: str, model: str, system: str, messages: list[dict], schema: dict | None,
                send: Callable[..., LLMResult]) -> LLMResult:
    m = mode()
    if m == "off":
        return send(provider, model, system, messages, schema)

    path = cache_dir() / f"{request_key(provider, model, system, messages, schema)}.json"
    if path.exists():
        saved = LLMResult(**json.loads(path.read_text(encoding="utf-8")))
        saved.cached = True
        return saved
    if m == "replay":
        raise ReplayMiss(f"No saved answer for this {provider} request (LLM_CACHE=replay). Run with LLM_CACHE=record first.")

    result = send(provider, model, system, messages, schema)
    # Don't save failed answers, so a retry in record mode really calls the provider again.
    if not (result.blocked or result.truncated):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(result), ensure_ascii=False, indent=2), encoding="utf-8")
    return result
