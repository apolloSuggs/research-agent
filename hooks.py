"""hooks.py: rules the SDK runs in code around every tool call (exam tasks 1.5, 2.3).

A hook is a function the SDK calls before (PreToolUse) or after (PostToolUse) a tool runs. It can
allow, deny with a reason the model sees, or add context. A rule in a hook cannot be forgotten the
way a rule in a prompt can.

scope_hook: each agent may call only its own tools.
  Why a hook and not disallowed_tools? disallowed_tools is global: hiding get_policies from the
  coordinator would hide it from the policy researcher too (learned in the support-agent build).
  The SDK tells the hook WHO is calling: agent_type is set inside a subagent and missing on the
  coordinator's own thread.

make_report_hooks(ledger), Phase 3: the "code gate" that sits beside the coordinator.
  record_hook   PostToolUse (+ PostToolUseFailure) on our data tools: files each REAL result in the ledger
  report_hook   PostToolUse on Agent: when a specialist finishes, code builds its report and adds it
                (and, once both are in, the decision) to the coordinator's context
  gate_hook     PreToolUse on Agent: blocks report_writer when the decision is "stop", and allows at
                most one re-brief per specialist
"""

import json

from agents import PREFIX, SUBAGENTS
from ledger import MAX_BRIEFINGS, parse_tool_response

COORDINATOR_TOOLS = {"Agent", "Task"}   # the SDK accepts "Agent"; the guide and older versions call it "Task"


def caller_of(input_data):
    return input_data.get("agent_type") if input_data.get("agent_id") else "coordinator"


def allowed_tools(caller):
    if caller in SUBAGENTS:
        return set(SUBAGENTS[caller].tools)
    if caller == "coordinator":
        return COORDINATOR_TOOLS
    return set()   # an unknown agent gets nothing: deny by default


def _deny(reason):
    return {"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason,
    }}


async def scope_hook(input_data, tool_use_id, context):
    tool = input_data["tool_name"]
    caller = caller_of(input_data)
    if tool not in allowed_tools(caller):
        name = tool.removeprefix(PREFIX)
        return _deny(f"{name} is not available to the {caller}. Only the right specialist may use it.")
    if tool in COORDINATOR_TOOLS:
        target = (input_data.get("tool_input") or {}).get("subagent_type")
        if target not in SUBAGENTS:
            return _deny(f"There is no specialist called '{target}'. Use one of: {', '.join(SUBAGENTS)}.")
    return {}


def make_report_hooks(ledger):

    async def record_hook(input_data, tool_use_id, context):
        if input_data.get("agent_id") and input_data["tool_name"].startswith(PREFIX):
            if input_data.get("hook_event_name") == "PostToolUseFailure":
                result = parse_tool_response(input_data.get("error", ""))
            else:
                result = parse_tool_response(input_data.get("tool_response"))
            ledger.record(input_data["agent_id"], input_data["tool_name"].removeprefix(PREFIX),
                          input_data.get("tool_input", {}), result)
        return {}

    async def report_hook(input_data, tool_use_id, context):
        response = input_data.get("tool_response")
        try:
            body = json.loads(response) if isinstance(response, str) else dict(response)
        except (ValueError, TypeError):
            return {}
        agent_id = body.get("agentId")
        if not agent_id:
            return {}
        agent_type = body.get("agentType") or (input_data.get("tool_input") or {}).get("subagent_type")
        if agent_type not in ("account_investigator", "policy_researcher"):
            return {}
        summary = " ".join(b.get("text", "") for b in body.get("content", []) if isinstance(b, dict))
        report = ledger.build_report(agent_id, agent_type, summary)
        text = "STRUCTURED REPORT (built by the system from the real tool results):\n" + json.dumps(report)
        decision = ledger.decision()
        if decision["action"] != "wait":
            text += "\n\nDECISION (enforced by code):\n" + json.dumps(decision)
        return {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": text}}

    async def gate_hook(input_data, tool_use_id, context):
        if input_data["tool_name"] not in COORDINATOR_TOOLS or input_data.get("agent_id"):
            return {}
        target = (input_data.get("tool_input") or {}).get("subagent_type")
        if target == "report_writer":
            action = ledger.decision()["action"]
            if action == "stop":
                return _deny("Both sources failed, so there is nothing to write from. Tell the customer plainly what "
                             "could not be reached and that it can be tried again.")
            if action in ("wait", "rebrief"):
                return _deny(f"The research is not finished (decision: {action}). Follow the DECISION first.")
        if target in ("account_investigator", "policy_researcher"):
            if ledger.briefings.get(target, 0) >= MAX_BRIEFINGS:
                return _deny(f"{target} was already re-briefed once. Report the gap instead of trying again.")
            ledger.count_briefing(target)
        return {}

    return record_hook, report_hook, gate_hook
