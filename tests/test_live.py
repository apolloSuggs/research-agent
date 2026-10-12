"""Live course checks: the full system, through the real front door, with Claude. COSTS money (about $1 total).

Each scenario plays the customer: asks for help with their email, reads the code from the simulated inbox
(like a person reading their email), types it back with their question, then checks the answer for the
facts the course requires. Scenario IDs match docs/TEST-SCENARIOS.md.

Run:  .venv/bin/python tests/test_live.py          (all)
      .venv/bin/python tests/test_live.py S1 E5    (some)
"""

import asyncio
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from coordinator import Conversation

failures, total_cost = 0, 0.0
INTERNAL = ["flag type", "policy type", "transient", "grounding", "research packet", "ledger", "payment_dispute",
            "fraud_suspicion", "account_security", "found: false", "retrying it"]


def check(ok, label):
    global failures
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        failures += 1


def has(text, *needles):
    t = text.lower().replace("‑", "-")
    return all(n.lower() in t for n in needles)


async def conversation(email, question, faults=""):
    """Returns (final event, all events of the research turn, the conversation)."""
    global total_cost
    c = Conversation(faults)
    events = await c.send(f"Hi, I need help with my account. My email is {email}.")
    events += await c.send(c.session["outbox"][-1]["text"].split()[-1] if c.session["outbox"] else "000000")
    turn = await c.send(question)
    events += turn
    total_cost += sum(e.get("cost_usd") or 0 for e in events if e["type"] == "done")
    final = next(e for e in reversed(turn) if e["type"] == "final")
    return final, turn, c


def common(final, label_research=True):
    if label_research:
        check(final["researched"], "research ran (after verification)")
        check(final.get("parallel_start"), "both specialists started in the same reply")
        check(final["citations"]["ok"], f"every citation and URL checks out against the ledger {final['citations']['cited']}")
    leaked = [w for w in INTERNAL if w in final["text"].lower()]
    check(not leaked, f"no internal words reach the customer {leaked or ''}")


async def s1():
    print("\nS1  Both succeed (course check: URL, trigger, exact features with dates)")
    f, _, c = await conversation("maya.patel@example.com",
        "I was told it's a payment dispute. Why was my account flagged and what is restricted?")
    common(f)
    check(c.session["verified_customer_id"] == "CUST-1001", "verified through the emailed code")
    check(has(f["text"], "https://policies.example.com/payment-disputes"), "policy URL in the answer")
    check(has(f["text"], "ORD-55120") and has(f["text"], "chargeback"), "trigger: chargeback on ORD-55120")
    check(has(f["text"], "instant payouts") and has(f["text"], "international transfers") and has(f["text"], "09-28-2026"),
          "both restricted features with their date")
    check(f["source"] == "report_writer" and f["decision"]["action"] == "write", "decision write, answer from the report writer")


async def s3():
    print("\nS3  Stated reason differs from the real flag")
    f, _, _ = await conversation("priya.raman@example.com",
        "They said my account was flagged for a payment dispute. Why?")
    common(f)
    check(has(f["text"], "fraud"), "the answer names the real flag (fraud)")
    check(has(f["text"], "payment dispute"), "and the reason the customer gave")


async def s2():
    print("\nS2  Customer says flagged, account is clean")
    f, _, _ = await conversation("daniel.brooks@example.com",
        "My account was flagged for a payment dispute. What happened?")
    common(f)
    check(has(f["text"], "active") and (has(f["text"], "no flag") or has(f["text"], "not flagged") or has(f["text"], "isn't flagged")
          or has(f["text"], "no active flag") or has(f["text"], "doesn't show")), "says the account is active with no flag")


async def e5():
    print("\nE5  Account lookup fails (course check: policy still arrives, gap stated)")
    f, turn, _ = await conversation("maya.patel@example.com",
        "I was told it's a payment dispute. Why was my account flagged?", faults="account:timeout")
    common(f)
    reports = [e["report"] for e in turn if e["type"] == "report"]
    check(any(r["agent"] == "account_investigator" and r["status"] == "failed" and r["error"]["retryable"] for r in reports),
          "account report: failed, retryable (contained, not raised)")
    check(sum(1 for r in reports if r["agent"] == "account_investigator") == 2, "re-briefed exactly once")
    check(f["decision"]["action"] == "write_with_gaps", "decision write_with_gaps")
    check(has(f["text"], "https://policies.example.com/payment-disputes"), "policy result still in the answer")
    check(has(f["text"], "couldn't") or has(f["text"], "could not") or has(f["text"], "unable"), "the answer states the account gap")
    check(not has(f["text"], "ORD-55120"), "no account facts were guessed")


async def e8():
    print("\nE8  Both fail: stop, no writer")
    f, turn, _ = await conversation("maya.patel@example.com",
        "I was told it's a payment dispute. Why was my account flagged?", faults="account:timeout,policy:timeout")
    check(f["decision"]["action"] == "stop", "decision stop after one re-brief each")
    check(not any(e["type"] == "spawn" and e["to"] == "report_writer" for e in turn) or f["source"] == "coordinator",
          "the report writer never produced the answer")
    common(f, label_research=False)


async def c1():
    print("\nC1  Four questions in one message (course check: every part answered from the research)")
    f, _, _ = await conversation("tomas.silva@example.com",
        "I was told it's a security hold. Why was my account flagged, which features are affected, "
        "what are my rights, and how do I appeal?")
    common(f)
    check(has(f["text"], "password reset"), "why: the password reset trigger")
    check(has(f["text"], "outgoing payments") and has(f["text"], "profile changes") and has(f["text"], "api"), "which features: all three")
    check(has(f["text"], "api keys reissued") or has(f["text"], "reissued") or has(f["text"], "confirm or reject"), "rights: from the policy")
    check(has(f["text"], "registered email") or has(f["text"], "security support"), "appeal/removal steps: from the policy")
    check(has(f["text"], "https://policies.example.com/security-holds"), "policy URL in the answer")


SCENARIOS = {"S1": s1, "S2": s2, "S3": s3, "E5": e5, "E8": e8, "C1": c1}


async def main():
    chosen = [a.upper() for a in sys.argv[1:]] or list(SCENARIOS)
    for name in chosen:
        await SCENARIOS[name]()
    print(f"\n{'All passed.' if not failures else f'{failures} FAILED.'}  Cost: ${total_cost:.2f}")


asyncio.run(main())
sys.exit(1 if failures else 0)
