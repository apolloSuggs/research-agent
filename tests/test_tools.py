"""Offline tests for the data layer: tool definitions, lookups, grounding, errors, retry, session gate.

Free: no server, no Claude. Scenario IDs (S1, E2...) match docs/TEST-SCENARIOS.md.
Run:  .venv/bin/python tests/test_tools.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import session_store
from errors import CATEGORIES, ToolError, with_retry
from mock_data import ACCOUNTS, POLICIES
from tools import TOOLS, parse_faults, run_tool

failures = 0


def check(ok, label):
    global failures
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        failures += 1


def verified(customer_id):
    return {"verified_customer_id": customer_id}


class FakeSleep:
    """Records the waits instead of sleeping, so retry tests are instant and we can check the backoff."""
    def __init__(self):
        self.waits = []

    def __call__(self, seconds):
        self.waits.append(seconds)


def account(customer_id, session=None, faults=None, sleep=None):
    return run_tool("get_account_details", {"customer_id": customer_id},
                    session if session is not None else verified(customer_id), faults, {"sleep": sleep or FakeSleep()})


def policy(flag_type, faults=None, sleep=None):
    return run_tool("get_policies", {"flag_type": flag_type}, None, faults, {"sleep": sleep or FakeSleep()})


print("Tool definitions (tool-definitions skill, task 2.1)")
names = [t["name"] for t in TOOLS]
check(len(set(names)) == len(names) == 4, "four tools with unique names")
for t in TOOLS:
    d = t["description"]
    check("Do not use" in d and "Returns" in d and "Use when" in d, f"{t['name']}: description says when, when not, and what it returns")
    props = t["input_schema"]["properties"]
    check(all(p.get("type") and p.get("description") for p in props.values()), f"{t['name']}: every parameter has a type and a description")
pairs = [("send_verification_code", "verify_identity"), ("get_account_details", "get_policies")]
by_name = {t["name"]: t["description"] for t in TOOLS}
check(all(b in by_name[a] and a in by_name[b] for a, b in pairs), "each look-alike pair names the other in its description")

print("\nGrounding: every record in the data has source fields (a missing one is a tool bug)")
for cid in ACCOUNTS:
    r, err = account(cid)
    s = r.get("source", {})
    check(not err and r["found"] and s.get("record_id") and s.get("last_updated") and s.get("retrieved_at"), f"{cid}: record_id, last_updated, retrieved_at")
for flag in POLICIES:
    r, err = policy(flag)
    s = r.get("source", {})
    check(not err and all(s.get(k) for k in ("record_id", "source_url", "version", "last_updated", "policy_name", "retrieved_at")),
          f"{flag}: record_id, source_url, version, last_updated, policy_name, retrieved_at")

print("\nScenarios: success and mismatches")
r, err = account("CUST-1001")
check(not err and r["account"]["flag"]["type"] == "payment_dispute" and "ORD-55120" in r["account"]["flag"]["trigger"], "S1 restricted account: payment dispute, chargeback trigger present")
check(all(f.get("since") for f in r["account"]["restricted_features"]), "S1 every restricted feature has a date")
check("source_url" not in r["account"] and "record_id" not in r["account"], "S1 grounding sits in 'source', apart from the facts")
r, err = account("CUST-1002")
check(not err and r["account"]["status"] == "active" and r["account"]["flag"] is None, "S2 clean account: active, no flag (mismatch is visible to the coordinator)")
r, err = account("CUST-1003")
check(not err and r["account"]["flag"]["type"] == "fraud_suspicion" and r["account"]["previous_flags"], "S3 actual flag is fraud_suspicion, with history")
r, _ = policy("payment_dispute")
check(r["found"] and r["source"]["source_url"].endswith("payment-disputes"), "S3 stated-reason policy still returns (the mismatch is for synthesis to state)")
r, err = account("CUST-1004")
check(not err and len(r["account"]["restricted_features"]) == 3, "S4 security hold: three restricted features")
r, err = policy("account_security")
check(not err and r["found"] and r["source"]["version"] == "1.4", "S4 third policy found with its version")
r, err = policy("terms_violation")
check(not err and r["found"] is False and r["source"]["searched"] == "policies", "S5 no policy on file: found=false, NOT an error, search is still grounded")
r, err = account("CUST-1006")
check(not err and r["account"]["status"] == "closed" and len(r["account"]["previous_flags"]) == 2, "S6 closed account with two past flags")

print("\nValid empty results are not errors")
r, err = account("CUST-9999")
check(not err and r["found"] is False and r["source"]["record_id"] is None, "E1 unknown customer: found=false, not an error")

print("\nErrors: structured, and only transient ones retried")
r, err = account("12345", session=verified("12345"))
check(err and r["errorCategory"] == "validation" and r["isRetryable"] is False and r["attempts"] == 1, "E2 malformed ID: validation, not retryable, 1 attempt")
r, err = policy("Payment Dispute!")
check(err and r["errorCategory"] == "validation", "E2 malformed flag type: validation")
r, err = account("CUST-1001", session={})
check(err and r["errorCategory"] == "permission" and not r["isRetryable"], "E3 no verified customer: permission, not retryable")
r, err = account("CUST-1003", session=verified("CUST-1001"))
check(err and r["errorCategory"] == "permission" and "CUST-1003" in r["message"], "E4 briefing asks for another customer: permission (no data leaks)")
check("account" not in r and "Priya" not in str(r), "E4 the denied result carries none of the other customer's data")

sleep = FakeSleep()
r, err = account("CUST-1001", faults={"account": "timeout"}, sleep=sleep)
check(err and r["errorCategory"] == "transient" and r["isRetryable"] and r["attempts"] == 3, "E5 account timeout: transient, retryable, 3 attempts (1 + 2 retries)")
check(sleep.waits == [0.2, 0.4], f"E5 backoff doubles: waited {sleep.waits}")
check(r["attempted"] == "get_account_details(customer_id=CUST-1001)", "E5 'attempted' says exactly what was tried")
r, err = policy("payment_dispute", faults={"account": "timeout"})
check(not err and r["found"], "E5 the policy lookup is unaffected by the account fault")

r, err = account("CUST-1001", faults={"account": "blip"})
check(not err and r["found"] and r["attempts"] == 2 and r["recovered_after"] == 1, "E6 blip: retry absorbs it, success with recovered_after=1")

r, err = policy("payment_dispute", faults={"policy": "timeout"})
check(err and r["errorCategory"] == "transient" and r["attempts"] == 3, "E7 policy timeout: transient after 3 attempts")
faults = parse_faults("account:timeout, policy:timeout")
check(faults == {"account": "timeout", "policy": "timeout"}, "E8 FAULT_INJECT parses both faults")
check(account("CUST-1001", faults=faults)[1] and policy("payment_dispute", faults=faults)[1], "E8 both fail")

r, err = account("12345", session=verified("12345"), faults={"account": "timeout"})
check(r["errorCategory"] == "validation" and r["attempts"] == 1, "validation is checked before data access, so no retries are wasted")
r, err = run_tool("delete_account", {})
check(err and r["errorCategory"] == "validation", "unknown tool name: structured error, no crash")


def boom(attempt):
    raise ToolError("business", "rule")


try:
    with_retry(boom, sleep=FakeSleep())
except ToolError as e:
    check(e.attempts == 1, "with_retry never retries a non-transient error")
check(set(CATEGORIES) == {"transient", "validation", "business", "permission", "internal"}, "error categories match the standard (no 'not_found')")

print("\nVerification: the code flow (V1-V5)")
s = session_store.new_session("v")
r, err = run_tool("send_verification_code", {"email": "Maya.Patel@example.com"}, s)
code = s["pending_verification"]["code"]
check(not err and code not in json.dumps(r), "V1 code sent; the code is NOT in the result (Claude never sees it)")
check(s["outbox"][-1]["to"] == "maya.patel@example.com" and code in s["outbox"][-1]["text"], "V1 the code lands in the simulated inbox")
r2, _ = run_tool("send_verification_code", {"email": "nobody@example.com"}, session_store.new_session("v2"))
check(r2 == r, "V2 unknown email gets the SAME reply (no way to probe who has an account)")
r, err = run_tool("verify_identity", {"email": "maya.patel@example.com", "code": "000000"}, s)
check(err and r["errorCategory"] == "validation" and "2 attempt(s) left" in r["message"], "V3 wrong code: validation error, attempts counted")
r, err = run_tool("verify_identity", {"email": "priya.raman@example.com", "code": code}, s)
check(err and "not the email" in r["message"], "V3 right code, wrong email: refused")
r, err = run_tool("get_account_details", {"customer_id": "CUST-1001"}, s)
check(err and r["errorCategory"] == "permission", "V4 before verifying: account read denied")
r, err = run_tool("verify_identity", {"email": "maya.patel@example.com", "code": code}, s)
check(not err and r["verified"] and r["customer_id"] == "CUST-1001" and s["verified_customer_id"] == "CUST-1001", "V4 right code: verified, recorded by code in the session")
check(s["pending_verification"] is None and s["verification_method"] == "email_code", "V4 the used code is cleared")
r, err = run_tool("get_account_details", {"customer_id": "CUST-1001"}, s)
check(not err and "email" not in r["account"], "V4 after verifying: account read works (email not included in findings)")
s = session_store.new_session("v5")
run_tool("send_verification_code", {"email": "maya.patel@example.com"}, s)
for _ in range(3):
    r, err = run_tool("verify_identity", {"email": "maya.patel@example.com", "code": "999999"}, s)
check(err and r["errorCategory"] == "business" and "locked" in r["message"], "V5 third wrong code locks verification (business error)")
r, err = run_tool("verify_identity", {"email": "maya.patel@example.com", "code": s["pending_verification"]["code"]}, s)
check(err and "locked" in r["message"] and not s["verified_customer_id"], "V5 even the right code fails once locked")

print("\nSession store: verified identity and multiple inquiries (M1, M2)")
sid = "test-tools-session"
session_store.delete(sid)
check(session_store.load(sid)["verified_customer_id"] is None, "a new session starts unverified")
session_store.verify(sid, "CUST-1001")
session_store.add_inquiry(sid, "Why was my account flagged?", "payment_dispute")
session_store.add_inquiry(sid, "How do I appeal?", "payment_dispute")
s = session_store.load(sid)
check(s["verified_customer_id"] == "CUST-1001" and s["verified_at"], "M1 verification is stored with a time")
check(len(s["inquiries"]) == 2, "M2 two inquiries in one session, verified once")
r, err = account("CUST-1001", session=s)
check(not err and r["found"], "M2 follow-up lookup uses the stored verification")
session_store.delete(sid)

print(f"\n{'All passed.' if not failures else f'{failures} FAILED.'}")
sys.exit(1 if failures else 0)
