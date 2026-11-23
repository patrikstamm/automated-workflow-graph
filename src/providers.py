# Kept so existing imports keep working. The provider code now lives in src/llm/.
# New code should use: from src.llm import call, LLMResult
from src.llm import LLMResult
from src.llm import call as _call


def call(model: str, system: str, messages: list[dict]) -> LLMResult:
    """Old signature: 'model' is the provider name ("gemini" or "claude")."""
    return _call(model, system, messages)


__all__ = ["LLMResult", "call"]
