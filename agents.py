"""agents.py: who the agents are (exam tasks 1.2, 1.3, 2.3).

A subagent in the Agent SDK is an AgentDefinition: a description (when the coordinator should use it),
a prompt (how it behaves) and a tool list (what it may touch). The coordinator starts one with the
Agent tool. A subagent starts BLANK: it sees its own prompt plus the briefing, nothing else.

The specialists do not retry. Retries already happen in code inside the tool (errors.py). If the model
also retried, a timeout could turn into 9 attempts and the report would not match reality.
"""

import claude_agent_sdk as sdk

SERVER = "data"                       # the name we give our MCP server in the options
PREFIX = f"mcp__{SERVER}__"           # the SDK names MCP tools mcp__<server>__<tool>

COORDINATOR_MODEL = "claude-sonnet-5-5"
SPECIALIST_MODEL = "claude-haiku-5-5"   # Haiku 5.5: short, single-tool jobs, the small model is enough

_SPECIALIST_RULES = (
    "Call your tool exactly once, with the value from the briefing. Do not retry: retries already happen "
    "inside the tool. Then reply in 2-4 plain sentences: what the tool returned, or, if it returned an error, "
    "its errorCategory, whether it is retryable, and its message. The system attaches the exact tool result "
    "to your report, so do not copy URLs, versions or dates into JSON. Never guess or add facts the tool did "
    "not return. You cannot talk to the customer, so never ask questions."
)

SUBAGENTS = {
    "account_investigator": sdk.AgentDefinition(
        description="Reads one verified customer's account record: status, flag, trigger, restricted features, past flags.",
        prompt=(
            "You are the account investigator. Your briefing gives a customer ID. Use get_account_details "
            "for that ID only. " + _SPECIALIST_RULES
        ),
        tools=[PREFIX + "get_account_details"],
        model=SPECIALIST_MODEL,
        background=False,   # the SDK would otherwise run it in the background; we wait for the report
    ),
    "policy_researcher": sdk.AgentDefinition(
        description="Reads the official policy for one flag type: rights, appeal process, removal and restoration, with its URL and version.",
        prompt=(
            "You are the policy researcher. Your briefing gives a flag type (the reason the customer stated). "
            "Use get_policies for that flag type only. " + _SPECIALIST_RULES
        ),
        tools=[PREFIX + "get_policies"],
        model=SPECIALIST_MODEL,
        background=False,
    ),
}

# Phase 2 version. Phase 3 adds structured reports, Phase 4 hands synthesis to report_writer.
COORDINATOR_PROMPT = (
    "You are the research coordinator for account-flag questions. You never look anything up yourself: "
    "you have no data tools. The system message gives you the VERIFIED customer ID, the reason the customer "
    "stated, and their question.\n"
    "1) The two lookups do not depend on each other. Start BOTH in the SAME reply, with two Agent calls: "
    "account_investigator and policy_researcher.\n"
    "2) A specialist starts with NOTHING: it cannot see this conversation. Every Agent prompt must be a full "
    "briefing: the goal, the exact value to look up (customer ID for the account investigator, the stated "
    "flag type in snake_case for the policy researcher), and what to report back. Never write 'the customer above'.\n"
    "3) Each specialist's answer comes back with a STRUCTURED REPORT built by the system from the real tool "
    "results, and once both are in, a DECISION enforced by code. Take every fact, date, URL and version from "
    "the reports' findings (cite the grounding_id, e.g. [G1]), never from a specialist's summary or memory.\n"
    "4) Follow the DECISION's action. 'rebrief': brief every specialist listed in 'rebrief' again, all in ONE "
    "reply, with the same full briefing, then wait for the new DECISION. 'stop': both sources failed and nothing "
    "is left to retry: say plainly what could not be reached and that it can be tried again, and write nothing "
    "else. 'write' or 'write_with_gaps': write a short research summary from the findings, state every "
    "coverage note as a gap, and point out any mismatch between the stated reason and the account's flag. "
    "Do not answer from your own knowledge."
)
