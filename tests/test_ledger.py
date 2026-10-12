"""Offline tests for Phase 3: ledger reports, grounding IDs, coverage notes, decision table, gate.

Free: real tool results (tools.run_tool) are pushed through the real hooks, in the shape the SDK sends.
Scenario IDs match docs/TEST-SCENARIOS.md.
Run:  .venv/bin/python tests/test_ledger.py
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents import PREFIX
from hooks import make_report_hooks
from ledger import ReportLedger, parse_tool_response
from tools import run_tool

failures = 0
NO_SLEEP = {"sleep": lambda s: None}


def check(ok, label):
    global failures
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        failures += 1


def mcp_blocks(result):
    return [{"type": "text", "text": json.dumps(result)}]   # how the SDK hands an MCP result to PostToolUse


def specialist_run(hooks, agent_id, agent_type, tool, args, session=None, faults=None, via_failure=False):
    """Simulate one specialist: its tool call is recorded, then its Agent call finishes."""
    record, report, gate = hooks
    result, is_error = run_tool(tool, args, session, faults, NO_SLEEP)
    data = {"agent_id": agent_id, "agent_type": agent_type, "tool_name": PREFIX + tool, "tool_input": args}
    if via_failure and is_error:
        data.update(hook_event_name="PostToolUseFailure", error=json.dumps(result))
    else:
        data.update(hook_event_name="PostToolUse", tool_response=mcp_blocks(result))
    asyncio.run(record(data, "t", None))
    done = {"tool_name": "Agent", "tool_input": {"subagent_type": agent_type},
            "tool_response": {"agentId": agent_id, "agentType": agent_type, "content": [{"type": "text", "text": "I looked it up."}]}}
    return asyncio.run(report(done, "t", None))


def spawn(hooks, target):
    return asyncio.run(hooks[2]({"tool_name": "Agent", "tool_input": {"subagent_type": target}}, "t", None))


def denied(r):
    return r.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"


V1001 = {"verified_customer_id": "CUST-1001"}

print("S1: both succeed")
L = ReportLedger(); H = make_report_hooks(L)
spawn(H, "account_investigator"); spawn(H, "policy_researcher")
out1 = specialist_run(H, "a1", "account_investigator", "get_account_details", {"customer_id": "CUST-1001"}, V1001)
check(L.decision()["action"] == "wait" and "DECISION" not in out1["hookSpecificOutput"]["additionalContext"], "after one report: decision is 'wait', none sent yet")
out2 = specialist_run(H, "p1", "policy_researcher", "get_policies", {"flag_type": "payment_dispute"})
ctx = out2["hookSpecificOutput"]["additionalContext"]
check(ctx.startswith("STRUCTURED REPORT") and "DECISION (enforced by code)" in ctx, "after both: report and decision go into the coordinator's context")
a, p = L.reports
check(a["status"] == p["status"] == "success", "both reports: success")
check([f["grounding_id"] for f in a["findings"] + p["findings"]] == ["G1", "G2"], "G2 grounding IDs assigned by code: G1, G2")
check(p["findings"][0]["grounding"]["source_url"] == "https://policies.example.com/payment-disputes", "the policy URL in the report is the tool's exact value")
check(a["summary"] == "I looked it up." and "ORD-55120" in json.dumps(a["findings"]), "facts come from findings; the model's words are only the summary")
check(L.decision()["action"] == "write" and not denied(spawn(H, "report_writer")), "decision 'write': the writer may start")
check(set(L.grounding_index()) == {"G1", "G2"}, "grounding index ready for citation checks")

print("\nE5: account fails, policy arrives")
L = ReportLedger(); H = make_report_hooks(L)
spawn(H, "account_investigator"); spawn(H, "policy_researcher")
specialist_run(H, "a1", "account_investigator", "get_account_details", {"customer_id": "CUST-1001"}, V1001, {"account": "timeout"})
specialist_run(H, "p1", "policy_researcher", "get_policies", {"flag_type": "payment_dispute"})
a = L.latest()["account_investigator"]
check(a["status"] == "failed" and a["error"]["error_type"] == "transient" and a["error"]["attempts"] == 3, "account report: failed, transient, 3 attempts")
check("could not be retrieved" in a["coverage_note"] and "retryable" in a["coverage_note"] and "do not guess" in a["coverage_note"], "coverage note written by code: what failed, retryable, do not guess")
d = L.decision()
check(d["action"] == "rebrief" and d["rebrief"] == ["account_investigator"], "decision: re-brief the account investigator first")
check(denied(spawn(H, "report_writer")), "the writer may not start while a re-brief is pending")
check(not denied(spawn(H, "account_investigator")), "one re-brief is allowed")
check(denied(spawn(H, "account_investigator")), "a second re-brief is denied")
L2 = ReportLedger(); H2 = make_report_hooks(L2)
spawn(H2, "account_investigator"); spawn(H2, "policy_researcher"); spawn(H2, "account_investigator")
specialist_run(H2, "a1", "account_investigator", "get_account_details", {"customer_id": "CUST-1001"}, V1001, {"account": "timeout"})
specialist_run(H2, "p1", "policy_researcher", "get_policies", {"flag_type": "payment_dispute"})
check(L2.decision()["action"] == "write_with_gaps" and L2.decision()["rebrief"] == [], "re-brief used up: write with gaps (the gap is stated)")
check(not denied(spawn(H2, "report_writer")), "the writer may start on write_with_gaps")
specialist_run(H, "a2", "account_investigator", "get_account_details", {"customer_id": "CUST-1001"}, V1001)
check(L.latest()["account_investigator"]["status"] == "success" and L.decision()["action"] == "write", "a successful re-brief replaces the failed report: decision 'write'")

print("\nE5 via PostToolUseFailure (the other path an error can take)")
L = ReportLedger(); H = make_report_hooks(L)
specialist_run(H, "a1", "account_investigator", "get_account_details", {"customer_id": "CUST-1001"}, V1001, {"account": "timeout"}, via_failure=True)
check(L.reports[0]["status"] == "failed" and L.reports[0]["error"]["error_type"] == "transient", "an error arriving on the failure hook is still recorded")

print("\nE6: blip absorbed")
L = ReportLedger(); H = make_report_hooks(L)
specialist_run(H, "a1", "account_investigator", "get_account_details", {"customer_id": "CUST-1001"}, V1001, {"account": "blip"})
f = L.reports[0]["findings"][0]
check(L.reports[0]["status"] == "success" and f["recovered_after"] == 1 and L.reports[0]["coverage_note"] is None, "success, recovered_after=1, no gap to report")

print("\nE8: both fail")
L = ReportLedger(); H = make_report_hooks(L)
spawn(H, "account_investigator"); spawn(H, "policy_researcher")
specialist_run(H, "a1", "account_investigator", "get_account_details", {"customer_id": "CUST-1001"}, V1001, {"account": "timeout"})
specialist_run(H, "p1", "policy_researcher", "get_policies", {"flag_type": "payment_dispute"}, faults={"policy": "timeout"})
check(L.decision()["action"] == "rebrief" and set(L.decision()["rebrief"]) == {"account_investigator", "policy_researcher"}, "first: re-brief both (retryable)")
spawn(H, "account_investigator"); spawn(H, "policy_researcher")
specialist_run(H, "a2", "account_investigator", "get_account_details", {"customer_id": "CUST-1001"}, V1001, {"account": "timeout"})
specialist_run(H, "p2", "policy_researcher", "get_policies", {"flag_type": "payment_dispute"}, faults={"policy": "timeout"})
check(L.decision()["action"] == "stop" and len(L.decision()["coverage_notes"]) == 2, "after re-briefs fail too: decision 'stop' with both coverage notes")
r = spawn(H, "report_writer")
check(denied(r) and "Both sources failed" in r["hookSpecificOutput"]["permissionDecisionReason"], "the writer is BLOCKED by the gate hook")

print("\nE1 / S5: valid empty results")
L = ReportLedger(); H = make_report_hooks(L)
specialist_run(H, "a1", "account_investigator", "get_account_details", {"customer_id": "CUST-9999"}, {"verified_customer_id": "CUST-9999"})
specialist_run(H, "p1", "policy_researcher", "get_policies", {"flag_type": "terms_violation"})
check(all(r["status"] == "success" and r["findings"][0]["found"] is False for r in L.reports), "not found = success with a found=false finding (grounded search)")
check(L.decision()["action"] == "write", "decision 'write': the writer must say 'no such account' / 'no policy on file'")

print("\nE4: permission error is not retryable")
L = ReportLedger(); H = make_report_hooks(L)
specialist_run(H, "a1", "account_investigator", "get_account_details", {"customer_id": "CUST-1003"}, V1001)
specialist_run(H, "p1", "policy_researcher", "get_policies", {"flag_type": "payment_dispute"})
check(L.reports[0]["error"]["error_type"] == "permission" and L.decision()["rebrief"] == [], "permission failure: no re-brief offered")

print("\nG2: a result without grounding is downgraded")
L = ReportLedger()
L.record("a1", "get_account_details", {}, {"found": True, "account": {"status": "restricted"}, "source": {"source_type": "account_record"}})
r = L.build_report("a1", "account_investigator", "")
check(r["status"] == "failed" and "without a source" in r["coverage_note"], "only ungrounded data: not success, coverage note says why")
L.record("a2", "get_account_details", {}, run_tool("get_account_details", {"customer_id": "CUST-1001"}, V1001)[0])
L.record("a2", "get_account_details", {}, {"found": True, "account": {}, "source": {}})
r = L.build_report("a2", "account_investigator", "")
check(r["status"] == "partial" and len(r["partial_data"]) == 1 and not r["findings"], "mixed grounded + ungrounded: partial, grounded data kept as partial_data")

print("\nOther")
L = ReportLedger()
check(L.build_report("x", "account_investigator", "Which customer?")["status"] == "no_action", "no tool call: no_action with a coverage note")
check(parse_tool_response("<html>gateway error</html>")["errorCategory"] == "internal", "unreadable tool output becomes a structured internal error, never silence")

print(f"\n{'All passed.' if not failures else f'{failures} FAILED.'}")
sys.exit(1 if failures else 0)
