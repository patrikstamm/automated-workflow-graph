# Fake provider clients so the LLM tests run offline, without API keys.
# They return real SDK response objects, so field names are checked against the installed SDKs.
from anthropic.types import Message, OutputTokensDetails, TextBlock, Usage
from google.genai import types as G


class FakeAnthropic:
    """Stands in for anthropic.Anthropic. Records the kwargs of messages.create."""
    calls: list[dict] = []
    response: Message | None = None

    def __init__(self, **kwargs):
        self.client_kwargs = kwargs
        self.messages = self

    def create(self, **kwargs):
        FakeAnthropic.calls.append(kwargs)
        return FakeAnthropic.response


def claude_message(text="{}", stop_reason="end_turn", input_tokens=100, output_tokens=50,
                   thinking=0, cache_read=0, cache_write=0) -> Message:
    return Message.model_construct(
        id="msg_test", type="message", role="assistant", model="claude-haiku-5-5",
        content=[TextBlock.model_construct(type="text", text=text, citations=None)],
        stop_reason=stop_reason, stop_sequence=None,
        usage=Usage.model_construct(
            input_tokens=input_tokens, output_tokens=output_tokens,
            cache_read_input_tokens=cache_read, cache_creation_input_tokens=cache_write,
            output_tokens_details=OutputTokensDetails.model_construct(thinking_tokens=thinking),
        ),
    )


class FakeGenai:
    """Stands in for google.genai.Client. Records the kwargs of models.generate_content."""
    calls: list[dict] = []
    response: G.GenerateContentResponse | None = None

    def __init__(self, **kwargs):
        self.models = self

    def generate_content(self, **kwargs):
        FakeGenai.calls.append(kwargs)
        return FakeGenai.response


def gemini_response(text="{}", finish=G.FinishReason.STOP, prompt=100, output=50, thoughts=None,
                    cached=None) -> G.GenerateContentResponse:
    return G.GenerateContentResponse(
        candidates=[G.Candidate(finish_reason=finish, content=G.Content(parts=[G.Part(text=text)]))],
        usage_metadata=G.GenerateContentResponseUsageMetadata(
            prompt_token_count=prompt, candidates_token_count=output,
            thoughts_token_count=thoughts, cached_content_token_count=cached,
        ),
    )
