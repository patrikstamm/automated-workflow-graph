# Example drafts used by the schema tests (DECISIONS.md section 1.6).
# Each is a plain dict, exactly what an LLM would return as JSON.
import copy

from src.catalog import TOOLS
from src.schema import ToolSpec, specs_from_tools

# Office tools as they will appear in the catalog (Gmail, Sheets, Calendar), plus the real test_tool.
CATALOG: dict[str, ToolSpec] = {
    **specs_from_tools(TOOLS),
    "gmail_search": ToolSpec("gmail_search", {"query": "string"}),
    "gmail_send": ToolSpec("gmail_send", {"to": "string", "subject": "string", "body": "string"}),
    "gmail_draft": ToolSpec("gmail_draft", {"to": "string", "body": "string"}),
    "calendar_list": ToolSpec("calendar_list", {"date": "string"}),
    "calendar_create": ToolSpec("calendar_create", {"title": "string", "time": "string", "attendees": "array"}),
}


def get(name: str) -> dict:
    """Return a fresh copy so tests can modify it."""
    return copy.deepcopy(EXAMPLES[name])


# 1. Linear echo: today's prompt example in the new format.
LINEAR = {
    "name": "echo_hello",
    "nodes": [
        {"id": "start", "kind": "start"},
        {"id": "echo", "kind": "tool", "tool": "test_tool",
         "args": [{"name": "text", "value": "hello"}], "reads": [], "writes": ["echoed"]},
        {"id": "end", "kind": "end"},
    ],
    "edges": [
        {"source": "start", "target": "echo"},
        {"source": "echo", "target": "end"},
    ],
}

# 2. Email triage from the AgentProof paper (Fig. 3): the normal path ends in a dead end.
EMAIL_TRIAGE = {
    "name": "email_triage",
    "nodes": [
        {"id": "start", "kind": "start"},
        {"id": "classify", "kind": "llm", "task": "Classify the email as urgent or normal", "writes": ["intent"]},
        {"id": "router", "kind": "router", "reads": ["intent"]},
        {"id": "urgent_handler", "kind": "human", "task": "Handle the urgent email"},
        {"id": "normal_handler", "kind": "llm", "task": "Write a reply to the normal email", "writes": ["reply_text"]},
        {"id": "draft_response", "kind": "tool", "tool": "gmail_draft",
         "args": [{"name": "to", "value": "sender@example.com"}, {"name": "body", "value": "$reply_text"}],
         "reads": ["reply_text"]},
        {"id": "end", "kind": "end"},
    ],
    "edges": [
        {"source": "start", "target": "classify"},
        {"source": "classify", "target": "router"},
        {"source": "router", "target": "urgent_handler", "condition": "urgent"},
        {"source": "router", "target": "normal_handler", "condition": "normal"},
        {"source": "urgent_handler", "target": "end"},
        {"source": "normal_handler", "target": "draft_response"},
    ],
}

# 3. Parallel fetch + AND-join, then a human approval that branches (the proposal's example).
PARALLEL = {
    "name": "daily_summary",
    "nodes": [
        {"id": "start", "kind": "start"},
        {"id": "fetch_mail", "kind": "tool", "tool": "gmail_search",
         "args": [{"name": "query", "value": "is:unread"}], "writes": ["emails"]},
        {"id": "fetch_events", "kind": "tool", "tool": "calendar_list",
         "args": [{"name": "date", "value": "2026-11-24"}], "writes": ["events"]},
        {"id": "summarise", "kind": "llm", "task": "Summarise the emails and events",
         "join": "all", "reads": ["emails", "events"], "writes": ["summary"]},
        {"id": "approve", "kind": "human", "task": "Approve sending the summary", "reads": ["summary"]},
        {"id": "send", "kind": "tool", "tool": "gmail_send",
         "args": [{"name": "to", "value": "team@example.com"},
                  {"name": "subject", "value": "Daily summary"},
                  {"name": "body", "value": "$summary"}],
         "reads": ["summary"]},
        {"id": "end", "kind": "end", "join": "any"},
    ],
    "edges": [
        {"source": "start", "target": "fetch_mail"},
        {"source": "start", "target": "fetch_events"},
        {"source": "fetch_mail", "target": "summarise"},
        {"source": "fetch_events", "target": "summarise"},
        {"source": "summarise", "target": "approve"},
        {"source": "approve", "target": "send", "condition": "approved"},
        {"source": "approve", "target": "end", "condition": "rejected"},
        {"source": "send", "target": "end"},
    ],
}

# 4. Loop: a router sends the draft back for rewriting.
LOOP = {
    "name": "review_loop",
    "nodes": [
        {"id": "start", "kind": "start"},
        {"id": "write_draft", "kind": "llm", "task": "Write or rewrite the email draft",
         "join": "any", "writes": ["draft"]},
        {"id": "review", "kind": "router", "reads": ["draft"]},
        {"id": "send", "kind": "tool", "tool": "gmail_send",
         "args": [{"name": "to", "value": "client@example.com"},
                  {"name": "subject", "value": "Proposal"},
                  {"name": "body", "value": "$draft"}],
         "reads": ["draft"]},
        {"id": "end", "kind": "end"},
    ],
    "edges": [
        {"source": "start", "target": "write_draft"},
        {"source": "write_draft", "target": "review"},
        {"source": "review", "target": "write_draft", "condition": "needs_changes"},
        {"source": "review", "target": "send", "condition": "approved"},
        {"source": "send", "target": "end"},
    ],
}

# 5. Human approve/reject with a single end.
HUMAN = {
    "name": "approve_reply",
    "nodes": [
        {"id": "start", "kind": "start"},
        {"id": "write_reply", "kind": "llm", "task": "Write a reply", "writes": ["reply"]},
        {"id": "approve", "kind": "human", "task": "Approve the reply", "reads": ["reply"]},
        {"id": "send", "kind": "tool", "tool": "gmail_send",
         "args": [{"name": "to", "value": "customer@example.com"},
                  {"name": "subject", "value": "Re: your question"},
                  {"name": "body", "value": "$reply"}],
         "reads": ["reply"]},
        {"id": "end", "kind": "end", "join": "any"},
    ],
    "edges": [
        {"source": "start", "target": "write_reply"},
        {"source": "write_reply", "target": "approve"},
        {"source": "approve", "target": "send", "condition": "approved"},
        {"source": "approve", "target": "end", "condition": "rejected"},
        {"source": "send", "target": "end"},
    ],
}

# 6. Vanhatalo et al. (ICSOC 2008), Fig. 10 / our slide G0: three ends, a choice u,
#    a parallel split v, and a loop from w back to u.
VANHATALO_G0 = {
    "name": "vanhatalo_g0",
    "nodes": [
        {"id": "start", "kind": "start"},
        {"id": "u", "kind": "router", "join": "any"},
        {"id": "v", "kind": "llm", "task": "Prepare both parallel tasks"},
        {"id": "a1", "kind": "llm", "task": "Task a1"},
        {"id": "a2", "kind": "llm", "task": "Task a2"},
        {"id": "a3", "kind": "llm", "task": "Task a3"},
        {"id": "w", "kind": "router"},
        {"id": "e1", "kind": "end"},
        {"id": "e2", "kind": "end"},
        {"id": "e3", "kind": "end"},
    ],
    "edges": [
        {"source": "start", "target": "u"},
        {"source": "u", "target": "v", "condition": "parallel"},
        {"source": "u", "target": "a3", "condition": "single"},
        {"source": "v", "target": "a1"},
        {"source": "v", "target": "a2"},
        {"source": "a1", "target": "e1"},
        {"source": "a2", "target": "e2"},
        {"source": "a3", "target": "w"},
        {"source": "w", "target": "e3", "condition": "done"},
        {"source": "w", "target": "u", "condition": "retry"},
    ],
}

EXAMPLES = {
    "linear": LINEAR,
    "email_triage": EMAIL_TRIAGE,
    "parallel": PARALLEL,
    "loop": LOOP,
    "human": HUMAN,
    "vanhatalo_g0": VANHATALO_G0,
}
