# The JSON schema both providers receive for a workflow draft (DECISIONS.md section 2).
# Built from the one shared Pydantic model, then cleaned of keywords that Gemini's
# response_json_schema does not support. Claude gets exactly the same schema.
from __future__ import annotations

import copy
from typing import Any

from src.schema import WorkflowDraft

# Schema keywords we drop: "default" is not supported by Gemini, "title" only adds tokens.
DROP_KEYWORDS = {"default", "title"}


def draft_schema() -> dict[str, Any]:
    return clean_schema(WorkflowDraft.model_json_schema())


def clean_schema(schema: dict[str, Any]) -> dict[str, Any]:
    return _clean(copy.deepcopy(schema))


def _clean(node: Any) -> Any:
    if isinstance(node, list):
        return [_clean(x) for x in node]
    if not isinstance(node, dict):
        return node
    result = {}
    for key, value in node.items():
        if key in DROP_KEYWORDS:
            continue
        if key in ("properties", "$defs"):
            # Keys here are field/definition names, not keywords: keep them all, clean the values.
            result[key] = {name: _clean(sub) for name, sub in value.items()}
        else:
            result[key] = _clean(value)
    return result
