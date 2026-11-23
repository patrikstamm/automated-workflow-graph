# Builds the full instruction text: the role, the tool list, the JSON format and rules, and the example for LLMs to follow. 
# It also builds the user message, which is just the task description.
from src.catalog import TOOLS_BY_NAME
from src.humanprompt import UserRequest

ROLE = """You design workflows for office tasks.
A workflow is a graph: nodes are steps, edges say which step runs next."""
 
# The draft format is defined in src/schema/draft.py (DECISIONS.md section 1.2).
# These rules explain it to the LLM; the answer is also forced to match the JSON schema.
FORMAT_RULES = """Answer with ONLY a JSON object, no explanation, in this format:
{"name": "short_snake_case_name", "nodes": [...], "edges": [{"source": "<node id>", "target": "<node id>"}]}

Node kinds:
- "start": exactly one; where the workflow begins. No incoming edges.
- "end": at least one; where the workflow finishes. No outgoing edges.
- "llm": a thinking step (extract, summarise, decide what to write). Needs "task".
- "tool": calls one tool from the list above. Needs "tool" and "args".
- "human": a person checks or approves something. Needs "task".
- "router": chooses ONE of its outgoing edges based on earlier results.

Node fields:
- "id", and the names in "reads" and "writes", are snake_case: lowercase letters, digits, single underscores.
- "reads": state values the step uses. "writes": state values the step produces.
- Only "tool" nodes have "tool" and "args".

Tool arguments:
- "args" is a list with one {"name": ..., "value": ...} for EVERY parameter of the tool, and no others.
- Every value is a string. To use a state value, the value is exactly "$" + its name, e.g. "$summary",
  and that name must also be in the node's "reads".
- A list parameter is written as a JSON string, e.g. "[\\"a@example.com\\", \\"b@example.com\\"]".

Edges:
- A "router" has 2 or more outgoing edges, each with a different "condition", e.g. "urgent" and "normal".
- A "human" with 2 or more outgoing edges also needs a different "condition" on each, e.g. "approved" and "rejected".
- Any other node with 2 or more outgoing edges runs all of them at the same time; those edges have no condition.
- A node with 2 or more incoming edges must set "join": "all" to wait for every incoming branch,
  or "any" to continue as soon as one arrives. Nodes with one incoming edge have no "join".

Data:
- A step may only read values that are written before it on EVERY path that reaches it."""

EXAMPLE_REQUEST = "Echo the word hello."

EXAMPLE_WORKFLOW = """{
  "name": "echo_hello",
  "nodes": [
    {"id": "start", "kind": "start"},
    {"id": "echo", "kind": "tool", "tool": "test_tool",
     "args": [{"name": "text", "value": "hello"}],
     "reads": [], "writes": ["echoed"]},
    {"id": "end", "kind": "end"}
  ],
  "edges": [
    {"source": "start", "target": "echo"},
    {"source": "echo", "target": "end"}
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