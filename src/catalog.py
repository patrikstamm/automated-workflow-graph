# this is for cataloging the tools that are available to the agent.  
# Each tool is a function that is decorated with @tool, which adds metadata to the function.  
# The metadata includes the name of the tool, a description of what it does, and the input and output types.  
# The tools are then added to a list called TOOLS, which is used by the agent to look up tools by name.
#
# The office tools below are stubs: generation and verification only read their names and
# parameters. They are not executed until mock tools exist (DECISIONS.md section 7).


from langchain_core.tools import tool

# ---------- Test Tool ----------
@tool 
def test_tool(text: str) -> str:
    """A test tool that echoes the input."""
    return f"Test tool received: {text}"

# ---------- Gmail ----------
 
@tool
def gmail_search(query: str) -> list[dict]:
    """Find emails matching a search query."""
    raise NotImplementedError
 
 
@tool
def gmail_read(email_id: str) -> dict:
    """Open one email and get its full content."""
    raise NotImplementedError
 
 
@tool
def gmail_send(to: str, subject: str, body: str) -> str:
    """Send an email."""
    raise NotImplementedError
 
 
@tool
def gmail_label(email_id: str, label: str) -> str:
    """Add a label to an email."""
    raise NotImplementedError
 
 
# ---------- Sheets ----------
 
@tool
def sheets_read(sheet: str) -> list[list[str]]:
    """Read all rows of a spreadsheet."""
    raise NotImplementedError
 
 
@tool
def sheets_append(sheet: str, row: list[str]) -> str:
    """Add one row to a spreadsheet."""
    raise NotImplementedError
 
 
# ---------- Calendar ----------
 
@tool
def calendar_list(date: str) -> list[dict]:
    """List calendar events on a date (YYYY-MM-DD)."""
    raise NotImplementedError
 
 
@tool
def calendar_create(title: str, time: str, attendees: list[str]) -> str:
    """Create a calendar event."""
    raise NotImplementedError
 
 
# ---------- Lists other files use ----------
 
TOOLS = [
    test_tool, #remove this later, it's just for testing
    gmail_search, gmail_read, gmail_send, gmail_label,
    sheets_read, sheets_append,
    calendar_list, calendar_create,
]
 
# Look up a tool by its name: TOOLS_BY_NAME["sheets_append"]
TOOLS_BY_NAME = {t.name: t for t in TOOLS}
 
RESOURCES = {
    "test_tool":       {"reads": [],                 "writes": []}, # FOR TESTING
    "gmail_search":    {"reads": ["gmail:inbox"],    "writes": []},
    "gmail_read":      {"reads": ["gmail:inbox"],    "writes": []},
    "gmail_send":      {"reads": [],                 "writes": ["gmail:sent"]},
    "gmail_label":     {"reads": [],                 "writes": ["gmail:inbox"]},
    "sheets_read":     {"reads": ["sheets:{sheet}"], "writes": []},
    "sheets_append":   {"reads": [],                 "writes": ["sheets:{sheet}"]},
    "calendar_list":   {"reads": ["calendar"],       "writes": []},
    "calendar_create": {"reads": [],                 "writes": ["calendar"]},
}
