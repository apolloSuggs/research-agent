"""Offline tests for the scope hook (scenario R2) and the parallel-start check (R1 logic).

Free: calls the hook functions directly with the same input shape the SDK sends.
Run:  .venv/bin/python tests/test_hooks.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents import PREFIX
from coordinator import parallel_start
from hooks import scope_hook

failures = 0


def check(ok, label):
    global failures
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        failures += 1


def call(tool, agent_type=None, tool_input=None):
    data = {"tool_name": tool, "tool_input": tool_input or {}}
    if agent_type:
        data.update(agent_id="a1", agent_type=agent_type)
    return asyncio.run(scope_hook(data, "t1", None))


def denied(result):
    return result.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"


ACCT, POL = PREFIX + "get_account_details", PREFIX + "get_policies"

print("Scope hook (R2)")
check(not denied(call(ACCT, "account_investigator")), "account_investigator may call get_account_details")
check(denied(call(POL, "account_investigator")), "account_investigator may NOT call get_policies")
check(not denied(call(POL, "policy_researcher")), "policy_researcher may call get_policies")
check(denied(call(ACCT, "policy_researcher")), "policy_researcher may NOT call get_account_details")
check(denied(call(ACCT)), "the coordinator may NOT call data tools itself")
check(not denied(call("Agent", tool_input={"subagent_type": "policy_researcher"})), "the coordinator may start a known specialist")
check(denied(call("Agent", tool_input={"subagent_type": "general-purpose"})), "the coordinator may NOT start an unknown agent")
check(denied(call("Agent", "account_investigator", {"subagent_type": "policy_researcher"})), "a specialist may NOT start other agents")
check(denied(call(ACCT, "mystery_agent")), "an unknown agent gets nothing (deny by default)")
r = call(POL, "account_investigator")
check("not available to the account_investigator" in r["hookSpecificOutput"]["permissionDecisionReason"], "the denial reason tells the model why")

print("\nParallel-start check (R1 logic)")
same = [{"type": "spawn", "to": "account_investigator", "message_id": "m1"}, {"type": "spawn", "to": "policy_researcher", "message_id": "m1"}]
apart = [{"type": "spawn", "to": "account_investigator", "message_id": "m1"}, {"type": "spawn", "to": "policy_researcher", "message_id": "m2"}]
check(parallel_start(same), "both started in one reply counts as parallel")
check(not parallel_start(apart), "started in two replies does NOT count")

print(f"\n{'All passed.' if not failures else f'{failures} FAILED.'}")
sys.exit(1 if failures else 0)
