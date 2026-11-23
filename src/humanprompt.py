# Human prompt parsing and validation
# It cleans and checks the user's input: removes extra spaces, rejects empty or too-long text, checks the tool names exist, and packs it all into a UserRequest
from dataclasses import dataclass
 
from src.catalog import TOOLS_BY_NAME
 
MAX_LENGTH = 2000  # characters
 
 
@dataclass
class UserRequest:
    text: str          # the task description, cleaned up
    tools: list[str]   # tool names the LLM is allowed to use
 
 
class RequestError(ValueError):
    """Raised when the human's input can't be used."""
 
 
def build_request(text: str, tools: list[str] | None = None) -> UserRequest:
    # Collapse extra spaces and line breaks into single spaces.
    text = " ".join(text.split())
 
    if not text:
        raise RequestError("The task description is empty.")
 
    if len(text) > MAX_LENGTH:
        raise RequestError(
            f"The task description is too long ({len(text)} characters, "
            f"max {MAX_LENGTH})."
        )
 
    # No tools picked means "let the LLM choose from all of them".
    if not tools:
        tools = list(TOOLS_BY_NAME)   # all tool names
 
    unknown = [t for t in tools if t not in TOOLS_BY_NAME]
    if unknown:
        raise RequestError(f"Unknown tools: {', '.join(unknown)}")
 
    return UserRequest(text=text, tools=tools)