"""coordinator.py: the hub (exam tasks 1.2, 1.3). Builds the SDK options and runs one inquiry.

The coordinator's only built-in tool is Agent, the tool that starts subagents. Its data access goes
through the specialists, and the MCP server enforces the session's verified customer.

run_inquiry() returns a list of events so tests and the demo page can see exactly what happened,
including WHICH coordinator reply (message_id) started each specialist: the proof of a parallel start.
"""

import sys
from pathlib import Path

import claude_agent_sdk as sdk
from dotenv import load_dotenv

import session_store
from agents import COORDINATOR_MODEL, COORDINATOR_PROMPT, PREFIX, SERVER, SUBAGENTS
from hooks import make_report_hooks, scope_hook
from ledger import ReportLedger

ROOT = Path(__file__).parent
load_dotenv(ROOT / ".env.local")   # ANTHROPIC_API_KEY (never printed)


def options(session_id, faults="", ledger=None):
    server_env = {"SESSION_ID": session_id}
    if faults:
        server_env["FAULT_INJECT"] = faults
    record_hook, report_hook, gate_hook = make_report_hooks(ledger or ReportLedger())
    data_tools = PREFIX + ".*"
    return sdk.ClaudeAgentOptions(
        system_prompt=COORDINATOR_PROMPT,
        model=COORDINATOR_MODEL,
        cwd=str(ROOT),
        tools=["Agent"],              # the ONLY built-in tool. tools=[] would remove Agent too.
        allowed_tools=["Agent"] + [t for d in SUBAGENTS.values() for t in d.tools],
        agents=SUBAGENTS,
        mcp_servers={SERVER: {"type": "stdio", "command": sys.executable,
                              "args": [str(ROOT / "mcp_server.py")], "env": server_env}},
        hooks={
            "PreToolUse": [sdk.HookMatcher(hooks=[scope_hook]),                          # who may call what
                           sdk.HookMatcher(matcher="Agent|Task", hooks=[gate_hook])],    # writer gate, re-brief limit
            "PostToolUse": [sdk.HookMatcher(matcher=data_tools, hooks=[record_hook]),    # file real results
                            sdk.HookMatcher(matcher="Agent|Task", hooks=[report_hook])], # build the report
            "PostToolUseFailure": [sdk.HookMatcher(matcher=data_tools, hooks=[record_hook])],  # failures too, never silence
        },
        setting_sources=[],           # ignore user/project Claude Code settings
        strict_mcp_config=True,       # only our MCP server, nothing from elsewhere
        env={"CLAUDE_CODE_DISABLE_BACKGROUND_TASKS": "1"},   # subagents run in the foreground
        max_turns=12,
    )


def _text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(p.get("text", "") for p in content if isinstance(p, dict))
    return str(content)


def briefing_message(customer_id, stated_reason, question):
    """Facts come from CODE (the verified session), not from the customer's free text."""
    return (f"Verified customer ID: {customer_id}\n"
            f"Reason the customer stated: {stated_reason}\n"
            f"Customer question: {question}")


async def run_inquiry(session_id, customer_id, stated_reason, question, faults=""):
    events, who = [], {}   # who: Agent tool_use_id -> specialist name, to label subagent steps
    ledger = ReportLedger()
    prompt = briefing_message(customer_id, stated_reason, question)
    async for m in sdk.query(prompt=prompt, options=options(session_id, faults, ledger)):
        parent = getattr(m, "parent_tool_use_id", None)
        label = who.get(parent, "coordinator") if parent else "coordinator"
        if isinstance(m, sdk.AssistantMessage):
            for b in m.content:
                if isinstance(b, sdk.TextBlock) and b.text.strip():
                    events.append({"type": "text", "agent": label, "text": b.text})
                elif isinstance(b, sdk.ToolUseBlock) and b.name in ("Agent", "Task"):
                    who[b.id] = b.input.get("subagent_type", "subagent")
                    events.append({"type": "spawn", "to": who[b.id], "message_id": m.message_id,
                                   "briefing": b.input.get("prompt", "")})
                elif isinstance(b, sdk.ToolUseBlock):
                    events.append({"type": "tool_call", "agent": label, "tool": b.name.removeprefix(PREFIX), "input": b.input})
        elif isinstance(m, sdk.UserMessage) and isinstance(m.content, list):
            for b in m.content:
                if isinstance(b, sdk.ToolResultBlock) and parent:
                    events.append({"type": "tool_result", "agent": label, "is_error": bool(b.is_error), "content": _text(b.content)})
        elif isinstance(m, sdk.ResultMessage):
            events.append({"type": "done", "cost_usd": m.total_cost_usd, "turns": m.num_turns,
                           "denials": m.permission_denials, "is_error": m.is_error})
    for report in ledger.reports:
        events.append({"type": "report", "report": report})
    events.append({"type": "decision", "decision": ledger.decision()})
    # Keep the reports in the session so a follow-up question (M3) can see what was already found.
    session = session_store.load(session_id)
    session["reports"].append({"question": question, "reports": ledger.reports, "decision": ledger.decision()})
    session_store.save(session)
    return events


def parallel_start(events):
    """True when both specialists were started from the SAME coordinator reply."""
    spawns = [e for e in events if e["type"] == "spawn"]
    by_reply = {}
    for e in spawns:
        by_reply.setdefault(e["message_id"], set()).add(e["to"])
    return any({"account_investigator", "policy_researcher"} <= names for names in by_reply.values())
