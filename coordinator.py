"""coordinator.py: the hub (exam tasks 1.2, 1.3), as a real multi-turn conversation.

The coordinator is the front door: it chats with the customer, verifies them with a one-time code
(front-door tools), works out their question and stated reason, and only then starts the specialists.
Its only built-in tool is Agent. Data access goes through the specialists, and code enforces the rest:
  - scope_hook   who may call which tool
  - gate_hook    no research before verification; writer only when the decision allows; one re-brief
  - MCP server   the verification code and the verified customer live in the session store

Conversation.stream(message) yields events AS THEY HAPPEN (the console shows them live), including
WHICH coordinator reply (message_id) started each specialist: the proof of a parallel start.
Each message resumes the same SDK session, so the coordinator remembers the conversation.
"""

import sys
import uuid
from pathlib import Path

import claude_agent_sdk as sdk
from dotenv import load_dotenv

import session_store
from agents import COORDINATOR_MODEL, COORDINATOR_PROMPT, PREFIX, SERVER, SUBAGENTS
from hooks import FRONT_DOOR, make_report_hooks, scope_hook
from ledger import ReportLedger

ROOT = Path(__file__).parent
load_dotenv(ROOT / ".env.local")   # ANTHROPIC_API_KEY (never printed)


def options(session_id, faults="", ledger=None, resume=None):
    server_env = {"SESSION_ID": session_id}
    if faults:
        server_env["FAULT_INJECT"] = faults
    record_hook, report_hook, gate_hook = make_report_hooks(ledger or ReportLedger(), session_id)
    data_tools = PREFIX + ".*"
    return sdk.ClaudeAgentOptions(
        system_prompt=COORDINATOR_PROMPT,
        model=COORDINATOR_MODEL,
        cwd=str(ROOT),
        tools=["Agent"],              # the ONLY built-in tool. tools=[] would remove Agent too.
        allowed_tools=["Agent"] + sorted(FRONT_DOOR) + [t for d in SUBAGENTS.values() for t in d.tools],
        agents=SUBAGENTS,
        mcp_servers={SERVER: {"type": "stdio", "command": sys.executable,
                              "args": [str(ROOT / "mcp_server.py")], "env": server_env}},
        hooks={
            "PreToolUse": [sdk.HookMatcher(hooks=[scope_hook]),                          # who may call what
                           sdk.HookMatcher(matcher="Agent|Task", hooks=[gate_hook])],    # verified? writer allowed? re-brief limit
            "PostToolUse": [sdk.HookMatcher(matcher=data_tools, hooks=[record_hook]),    # file real results
                            sdk.HookMatcher(matcher="Agent|Task", hooks=[report_hook])], # build the report
            "PostToolUseFailure": [sdk.HookMatcher(matcher=data_tools, hooks=[record_hook])],  # failures too, never silence
        },
        setting_sources=[],           # ignore user/project Claude Code settings
        strict_mcp_config=True,       # only our MCP server, nothing from elsewhere
        env={"CLAUDE_CODE_DISABLE_BACKGROUND_TASKS": "1"},   # subagents run in the foreground
        resume=resume,                # same SDK session every turn: the coordinator remembers the chat
        max_turns=16,
    )


def _text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(p.get("text", "") for p in content if isinstance(p, dict))
    return str(content)


def parallel_start(events):
    """True when both specialists were started from the SAME coordinator reply."""
    by_reply = {}
    for e in events:
        if e["type"] == "spawn":
            by_reply.setdefault(e["message_id"], set()).add(e["to"])
    return any({"account_investigator", "policy_researcher"} <= names for names in by_reply.values())


class Conversation:
    """One customer conversation. The session (verification, inbox, reports) is stored by code."""

    def __init__(self, faults=""):
        self.session_id = f"conv-{uuid.uuid4().hex[:8]}"
        self.sdk_session = None
        self.faults = faults
        session_store.save(session_store.new_session(self.session_id))

    @property
    def session(self):
        return session_store.load(self.session_id)

    async def stream(self, message):
        events, who = [], {}   # who: Agent tool_use_id -> specialist name, to label subagent steps
        ledger = ReportLedger({"question": message})   # a fresh ledger per customer message
        shown = 0

        async for m in sdk.query(prompt=message, options=options(self.session_id, self.faults, ledger, self.sdk_session)):
            batch = []
            parent = getattr(m, "parent_tool_use_id", None)
            label = who.get(parent, "coordinator") if parent else "coordinator"
            if isinstance(m, sdk.AssistantMessage):
                for b in m.content:
                    if isinstance(b, sdk.TextBlock) and b.text.strip():
                        batch.append({"type": "text", "agent": label, "text": b.text})
                    elif isinstance(b, sdk.ToolUseBlock) and b.name in ("Agent", "Task"):
                        who[b.id] = b.input.get("subagent_type", "subagent")
                        batch.append({"type": "spawn", "to": who[b.id], "message_id": m.message_id,
                                      "briefing": b.input.get("prompt", "")})
                    elif isinstance(b, sdk.ToolUseBlock):
                        batch.append({"type": "tool_call", "agent": label, "tool": b.name.removeprefix(PREFIX), "input": b.input})
            elif isinstance(m, sdk.UserMessage) and isinstance(m.content, list):
                for b in m.content:
                    if isinstance(b, sdk.ToolResultBlock) and b.tool_use_id not in who:   # Agent results become reports instead
                        batch.append({"type": "tool_result", "agent": label, "is_error": bool(b.is_error), "content": _text(b.content)})
            elif isinstance(m, sdk.ResultMessage):
                self.sdk_session = m.session_id
                batch.append({"type": "done", "cost_usd": m.total_cost_usd, "turns": m.num_turns,
                              "denials": m.permission_denials, "is_error": m.is_error})
            while shown < len(ledger.reports):   # reports built by the hooks since the last message
                batch.append({"type": "report", "report": ledger.reports[shown]})
                shown += 1
                if ledger.decision()["action"] != "wait":
                    batch.append({"type": "decision", "decision": ledger.decision()})
            for event in batch:
                events.append(event)
                yield event

        researched = bool(ledger.reports)
        coordinator_texts = [e["text"] for e in events if e["type"] == "text" and e["agent"] == "coordinator"]
        reply = ledger.writer_answer or (coordinator_texts[-1] if coordinator_texts else "")
        final = {"type": "final", "source": "report_writer" if ledger.writer_answer else "coordinator",
                 "text": reply, "researched": researched}
        if researched:
            # The answer is the writer's exact text, kept by code; check every citation against the ledger.
            final.update(citations=ledger.check_citations(reply), grounding=ledger.grounding_index(),
                         decision=ledger.decision(), parallel_start=parallel_start(events))
            session_store.add_inquiry(self.session_id, message, ledger.stated_reason())
            session = self.session
            session["reports"].append({"question": message, "reports": ledger.reports, "decision": ledger.decision()})
            session_store.save(session)
        yield final

    async def send(self, message):
        """Same turn, collected into a list (used by main.py and the live tests)."""
        return [e async for e in self.stream(message)]
