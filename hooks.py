"""hooks.py: rules the SDK runs in code around every tool call (exam tasks 1.5, 2.3).

A hook is a function the SDK calls before (PreToolUse) or after (PostToolUse) a tool runs. It can
allow, deny with a reason the model sees, or add context. A rule in a hook cannot be forgotten the
way a rule in a prompt can.

scope_hook: each agent may call only its own tools.
  Why a hook and not disallowed_tools? disallowed_tools is global: hiding get_policies from the
  coordinator would hide it from the policy researcher too (learned in the support-agent build).
  The SDK tells the hook WHO is calling: agent_type is set inside a subagent and missing on the
  coordinator's own thread.

Phase 3 adds the recorder and report hooks (ledger) and the report_writer gate here.
"""

from agents import PREFIX, SUBAGENTS

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
