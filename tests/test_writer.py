"""Offline tests for Phase 4: the writer's packet, the briefing swap, scope, and the citation check.

Free: no Claude calls. Scenario IDs match docs/TEST-SCENARIOS.md (R4, G3).
Run:  .venv/bin/python tests/test_writer.py
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents import PREFIX, SUBAGENTS
from hooks import make_report_hooks, scope_hook
from ledger import ReportLedger
from tools import run_tool

failures = 0
NO_SLEEP = {"sleep": lambda s: None}


def check(ok, label):
    global failures
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        failures += 1


def research(faults=None, customer="CUST-1001", reason="payment_dispute"):
    """A finished research round: both specialists recorded and reported through the real hooks."""
    L = ReportLedger({"question": "Why was my account flagged?", "customer_id": customer, "stated_reason": reason})
    record, report, gate = make_report_hooks(L)
    for aid, agent, tool, args in [("a1", "account_investigator", "get_account_details", {"customer_id": customer}),
                                   ("p1", "policy_researcher", "get_policies", {"flag_type": reason})]:
        asyncio.run(gate({"tool_name": "Agent", "tool_input": {"subagent_type": agent}}, "t", None))
        result, _ = run_tool(tool, args, {"verified_customer_id": customer}, faults, NO_SLEEP)
        asyncio.run(record({"agent_id": aid, "agent_type": agent, "tool_name": PREFIX + tool, "tool_input": args,
                            "hook_event_name": "PostToolUse", "tool_response": [{"type": "text", "text": json.dumps(result)}]}, "t", None))
        asyncio.run(report({"tool_name": "Agent", "tool_input": {"subagent_type": agent},
                            "tool_response": {"agentId": aid, "agentType": agent, "content": [{"type": "text", "text": "My words."}]}}, "t", None))
    return L, gate, report


def start_writer(gate, briefing="Write the answer."):
    return asyncio.run(gate({"tool_name": "Agent", "tool_input": {"subagent_type": "report_writer", "prompt": briefing}}, "t", None))


print("Writer definition and scope (R4)")
w = SUBAGENTS["report_writer"]
check(w.tools == [], "report_writer has no tools")
deny = asyncio.run(scope_hook({"tool_name": PREFIX + "get_policies", "agent_id": "w1", "agent_type": "report_writer"}, "t", None))
check(deny["hookSpecificOutput"]["permissionDecision"] == "deny", "the scope hook denies any data tool to the writer")

print("\nThe briefing swap: code decides what the writer reads")
L, gate, report = research()
out = start_writer(gate, "Summarize: the URL is https://made-up.example.org")
hso = out.get("hookSpecificOutput", {})
check(hso.get("permissionDecision") == "allow" and "updatedInput" in hso, "writer allowed, with updatedInput")
packet = hso["updatedInput"]["prompt"]
check(packet.startswith("RESEARCH PACKET") and "made-up" not in packet, "the coordinator's own text is replaced, not passed on")
check(hso["updatedInput"]["subagent_type"] == "report_writer", "other Agent inputs are kept")
body = json.loads(packet.split("\n", 1)[1])
check(body["verified_customer_id"] == "CUST-1001" and body["stated_reason"] == "payment_dispute", "packet carries the inquiry, set by code")
check("https://policies.example.com/payment-disputes" in packet and "ORD-55120" in packet, "packet carries the exact URL and trigger from the tools")
check("My words." not in packet and all("summary" not in r for r in body["reports"]), "specialists' own words are left out (structured facts only)")
check(body["citable_grounding_ids"] == ["G1", "G2"], "packet lists the citable grounding IDs")

print("\nWriter blocked when research is not finished")
L2, gate2, _ = research(faults={"account": "timeout", "policy": "timeout"})
check(start_writer(gate2)["hookSpecificOutput"]["permissionDecision"] == "deny", "decision 'rebrief': writer denied")

print("\nWriter's answer is kept by code")
asyncio.run(report({"tool_name": "Agent", "tool_input": {"subagent_type": "report_writer"},
                    "tool_response": {"agentId": "w1", "agentType": "report_writer", "content": [{"type": "text", "text": "Answer [G1]."}]}}, "t", None))
check(L.writer_answer == "Answer [G1]." and len(L.reports) == 2, "writer text captured; no report is built for the writer")

print("\nCitation check (G3)")
good = ("Your account was restricted on 09-28-2026 after a chargeback on order ORD-55120 [G1]. "
        "The Payment Dispute Policy v3.2 applies (https://policies.example.com/payment-disputes) [G2]. "
        "Appeal at https://policies.example.com/appeals [G2].")
c = L.check_citations(good)
check(c["ok"] and c["cited"] == ["G1", "G2"] and c["uncited"] == [], "good answer: all citations known, every finding cited")
check(not c["unknown_urls"], "the appeal URL counts: it comes from the policy text the tool returned")
c = L.check_citations("See [G7] and https://policies.example.com/refund-guarantee.")
check(not c["ok"] and c["unknown_ids"] == ["G7"] and c["unknown_urls"] == ["https://policies.example.com/refund-guarantee"],
      "invented ID and invented URL are both caught")
c = L.check_citations("Your account was restricted [G1].")
check(c["ok"] and c["uncited"] == ["G2"], "a finding that is never cited is reported as uncited")

print(f"\n{'All passed.' if not failures else f'{failures} FAILED.'}")
sys.exit(1 if failures else 0)
