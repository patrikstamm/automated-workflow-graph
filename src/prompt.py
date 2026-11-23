# Builds the full instruction text: the role, the tool list, the JSON format and rules, and the example for LLMs to follow. 
# It also builds the user message, which is just the task description.
from src.catalog import TOOLS_BY_NAME
from src.humanprompt import UserRequest

ROLE = """You design workflows for office tasks.
A workflow is a graph: nodes are steps, edges say which step runs next."""
 
FORMAT_RULES = """Answer with ONLY a JSON object, no explanation, in this format:
{
  "name": "short_snake_case_name",
  "nodes": [
    {"id": "...", "kind": "start" | "end" | "agent" | "tool",
     "tool": "<tool name, only for kind=tool>",
     "task": "<what to do, only for kind=agent>",
     "args": {"<parameter name>": "<value>"},
     "reads": ["<state values this step uses>"],
     "writes": ["<state values this step produces>"]}
  ],
  "edges": [{"from": "<node id>", "to": "<node id>"}]
}
 
Rules:
- Exactly one node with kind "start" and at least one with kind "end".
- Use "tool" nodes only with tools from the list above.
- Every "tool" node needs "args" with a value for each of the tool's parameters.
  To use a value an earlier step wrote, write "$" + its name, e.g. "$summary".
- Use "agent" nodes for thinking steps (extracting, summarising, deciding).
- A step may only read values that an earlier step writes."""
 
EXAMPLE_REQUEST = "Echo the word hello."

EXAMPLE_WORKFLOW = """{
  "name": "echo_hello",
  "nodes": [
    {"id": "start", "kind": "start"},
    {"id": "echo", "kind": "tool", "tool": "test_tool",
     "args": {"text": "hello"},
     "reads": [], "writes": ["echoed"]},
    {"id": "end", "kind": "end"}
  ],
  "edges": [
    {"from": "start", "to": "echo"},
    {"from": "echo", "to": "end"}
  ]
}"""
 
 
def type_name(info: dict) -> str:
    """Make a short type label, e.g. "string" or "list of string"."""
    if info.get("type") == "array":
        return "list of " + type_name(info.get("items", {}))
    return info.get("type", "any")
 
 
def describe_tools(tool_names: list[str]) -> str:
    """Turn the chosen tools into a readable list for the prompt."""
    lines = []
    for name in tool_names:
        tool = TOOLS_BY_NAME[name]
        # tool.args looks like {"sheet": {"type": "string", ...}, ...}
        params = ", ".join(f"{p}: {type_name(info)}" for p, info in tool.args.items())
        lines.append(f"- {tool.name}({params}): {tool.description}")
    return "\n".join(lines)
 
 
def build_system_prompt(request: UserRequest) -> str:
    return (
        f"{ROLE}\n\n"
        f"Tools you may use:\n{describe_tools(request.tools)}\n\n"
        f"{FORMAT_RULES}\n\n"
        f"Example request: {EXAMPLE_REQUEST}\n"
        f"Example answer:\n{EXAMPLE_WORKFLOW}\n\n"
        f"(The example may use tools that are not in your list. "
        f"Only use the tools listed above.)"
    )
 
 
def build_user_message(request: UserRequest) -> str:
    return request.text