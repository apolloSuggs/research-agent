"""tools.py: the front-door tools (verification) and the two data tools (exam tasks 2.1, 2.2), and the code that runs them.

Front door (coordinator only): send_verification_code, verify_identity. The code lives in the session.
Data (specialists only): get_account_details (verified customer only), get_policies.

Kept separate from mcp_server.py so the logic can be tested directly, with no server and no Claude.
Every call goes through run_tool(), which:
  1. checks the input (validation errors are never retried),
  2. checks the session for the account tool (permission errors are never retried),
  3. reads the data inside with_retry() (only transient errors are retried),
  4. attaches grounding: where the data came from, which version, when it was read.
No exception ever leaves run_tool(). Failures come back as structured error results.

Fault injection (testing only): faults={"account": "timeout" | "blip", "policy": ...}
  timeout  every attempt fails (retries run out)
  blip     the first attempt fails, the retry succeeds
"""

import re
import secrets   # not random: secrets is safe for security codes
from datetime import datetime, timezone

from errors import ToolError, error_result, with_retry
from mock_data import ACCOUNTS, POLICIES

get_account_details = {
    "name": "get_account_details",
    "description": (
        "Looks up one customer's account record: status, the active flag (type, date applied, what triggered it), "
        "each restricted feature with the date and reason, and previous flags. "
        "Use when you need the facts of why an account is flagged or restricted. "
        "Do not use this for policy wording, rights or appeal steps; use get_policies for that. "
        "Only works for the customer verified in this session. Returns found: false if no account has that ID."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "customer_id": {
                "type": "string",
                "description": "The customer ID from the briefing, format 'CUST-' plus 4 digits, e.g. 'CUST-1001'.",
            }
        },
        "required": ["customer_id"],
    },
}

get_policies = {
    "name": "get_policies",
    "description": (
        "Looks up the official policy for one type of account flag: policy name, source URL, version, "
        "last-updated date, customer rights, appeal process, removal conditions and how features are restored. "
        "Use when you need what the rules say a customer can do about a flag. "
        "Do not use this for a customer's own account facts; use get_account_details for that. "
        "Returns found: false if no policy exists for that flag type."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "flag_type": {
                "type": "string",
                "description": (
                    "The flag type in snake_case, e.g. 'payment_dispute', 'fraud_suspicion' or 'account_security'. "
                    "Use the reason given in the briefing; do not guess a different one."
                ),
            }
        },
        "required": ["flag_type"],
    },
}

send_verification_code = {
    "name": "send_verification_code",
    "description": (
        "Sends a one-time 6-digit code to the email address on a customer's account, to start proving who they are. "
        "Use when the customer wants help with their own account and has given their email, before any account research. "
        "Do not use this to check a code the customer typed; use verify_identity for that. "
        "Returns only that a code was sent if an account matches. It never shows the code and does not reveal whether the account exists."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "email": {"type": "string", "description": "The email address the customer gave, e.g. 'maya.patel@example.com'."}
        },
        "required": ["email"],
    },
}

verify_identity = {
    "name": "verify_identity",
    "description": (
        "Checks the 6-digit code the customer typed against the code sent to their email, and marks the session verified. "
        "Use when the customer gives you the code from their email. "
        "Do not use this to send a code; use send_verification_code for that. Never guess or invent a code. "
        "Returns verified: true with the customer ID and name, or an error. After 3 wrong codes verification locks."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "email": {"type": "string", "description": "The same email address the code was sent to, e.g. 'maya.patel@example.com'."},
            "code": {"type": "string", "description": "The 6-digit code exactly as the customer typed it, e.g. '048213'."},
        },
        "required": ["email", "code"],
    },
}

TOOLS = [send_verification_code, verify_identity, get_account_details, get_policies]
SESSION_WRITING_TOOLS = {"send_verification_code", "verify_identity"}   # the MCP server saves the session after these
MAX_CODE_ATTEMPTS = 3

CUSTOMER_ID = re.compile(r"^CUST-\d{4}$")
FLAG_TYPE = re.compile(r"^[a-z][a-z_]*$")


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _fault_check(faults, source, attempt):
    """Simulates the backing service failing. Only used when a test switches a fault on."""
    mode = (faults or {}).get(source)
    if mode == "timeout" or (mode == "blip" and attempt == 1):
        raise ToolError("transient", f"The {source} service timed out (attempt {attempt}).")


def _account(args, session, faults, retry):
    customer_id = str(args.get("customer_id", "")).strip()
    if not CUSTOMER_ID.match(customer_id):
        raise ToolError("validation", f"'{customer_id}' is not a customer ID. Expected 'CUST-' plus 4 digits, e.g. 'CUST-1001'.")
    verified = (session or {}).get("verified_customer_id")
    if not verified:
        raise ToolError("permission", "No customer is verified in this session, so no account can be read.")
    if customer_id != verified:
        raise ToolError("permission", f"This session is verified for a different customer. Account {customer_id} cannot be read here.")

    def read(attempt):
        _fault_check(faults, "account", attempt)
        return ACCOUNTS.get(customer_id)

    record, attempts = with_retry(read, **retry)
    source = {"source_type": "account_record", "tool": "get_account_details", "customer_id": customer_id, "retrieved_at": _now()}
    if record is None:
        return {"found": False, "message": f"No account exists with ID {customer_id}.",
                "source": {**source, "record_id": None, "searched": "accounts"}}, attempts
    body = {k: v for k, v in record.items() if k not in ("record_id", "last_updated", "email")}   # only what research needs
    return {"found": True, "account": {"customer_id": customer_id, **body},
            "source": {**source, "record_id": record["record_id"], "last_updated": record["last_updated"]}}, attempts


def _policy(args, session, faults, retry):
    flag_type = str(args.get("flag_type", "")).strip()
    if not FLAG_TYPE.match(flag_type):
        raise ToolError("validation", f"'{flag_type}' is not a flag type. Use snake_case, e.g. 'payment_dispute'.")

    def read(attempt):
        _fault_check(faults, "policy", attempt)
        return POLICIES.get(flag_type)

    policy, attempts = with_retry(read, **retry)
    source = {"source_type": "policy", "tool": "get_policies", "flag_type": flag_type, "retrieved_at": _now()}
    if policy is None:
        return {"found": False, "message": f"No policy is on file for flag type '{flag_type}'.",
                "source": {**source, "record_id": None, "searched": "policies"}}, attempts
    grounding = ("record_id", "source_url", "version", "last_updated")
    return {"found": True, "policy": {k: v for k, v in policy.items() if k not in grounding},
            "source": {**source, **{k: policy[k] for k in grounding}, "policy_name": policy["name"]}}, attempts


def _find_by_email(email):
    for customer_id, record in ACCOUNTS.items():
        if record.get("email", "").lower() == email.lower():
            return customer_id, record
    return None, None


def _send_code(args, session, faults, retry):
    email = str(args.get("email", "")).strip()
    if "@" not in email:
        raise ToolError("validation", f"'{email}' is not an email address. Ask the customer for the email on their account.")
    customer_id, record = _find_by_email(email)
    if customer_id:
        # The code is made and kept by CODE, in the session. It is never in the result, so Claude
        # cannot read it and cannot "verify" someone who does not have it.
        code = f"{secrets.randbelow(1_000_000):06d}"
        session["pending_verification"] = {"customer_id": customer_id, "email": record["email"], "code": code, "attempts": 0}
        session["outbox"].append({"to": record["email"], "text": f"Your verification code is {code}", "sent_at": _now()})
    # Same reply whether or not an account matched, so this tool cannot be used to find out who has an account.
    return {"sent": True, "message": "If an account matches that email, a 6-digit code was sent to it. Ask the customer for the code."}, 1


def _verify(args, session, faults, retry):
    email = str(args.get("email", "")).strip()
    code = str(args.get("code", "")).strip()
    pending = session.get("pending_verification")
    if not pending:
        raise ToolError("validation", "No code is waiting. Ask for the customer's email and use send_verification_code first.")
    if email.lower() != pending["email"].lower():
        raise ToolError("validation", "That is not the email the code was sent to. Ask the customer to confirm their email.")
    if pending["attempts"] >= MAX_CODE_ATTEMPTS:
        raise ToolError("business", "Verification is locked after too many wrong codes. Do not try again; tell the customer to contact support.")
    if code != pending["code"]:
        pending["attempts"] += 1
        left = MAX_CODE_ATTEMPTS - pending["attempts"]
        if left == 0:
            raise ToolError("business", "That code is wrong and verification is now locked. Do not try again; tell the customer to contact support.")
        raise ToolError("validation", f"That code is not correct. {left} attempt(s) left. Ask the customer to check the email again.")
    # Success: CODE records who is verified. Every later account lookup is checked against this.
    session.update(verified_customer_id=pending["customer_id"], verified_at=_now(),
                   verification_method="email_code", pending_verification=None)
    return {"verified": True, "customer_id": pending["customer_id"], "name": ACCOUNTS[pending["customer_id"]]["name"]}, 1


HANDLERS = {"send_verification_code": _send_code, "verify_identity": _verify,
            "get_account_details": _account, "get_policies": _policy}


def run_tool(name, args, session=None, faults=None, retry=None):
    """Run one tool. Returns (result_dict, is_error). Never raises."""
    attempted = f"{name}({', '.join(f'{k}={v}' for k, v in (args or {}).items())})"
    handler = HANDLERS.get(name)
    if handler is None:
        return error_result(ToolError("validation", f"Unknown tool '{name}'."), attempted), True
    try:
        result, attempts = handler(args or {}, session, faults, retry or {})
    except ToolError as err:
        return error_result(err, attempted), True
    except Exception as exc:  # a bug in our own code still becomes a structured error, never a crash
        err = ToolError("internal", f"Unexpected error in {name}: {type(exc).__name__}. Needs a fix.")
        return error_result(err, attempted), True
    result["attempts"] = attempts
    if attempts > 1:
        result["recovered_after"] = attempts - 1   # how many failed tries the retry layer absorbed
    return result, False


def parse_faults(text):
    """'account:timeout,policy:blip' -> {'account': 'timeout', 'policy': 'blip'}"""
    pairs = (p.split(":", 1) for p in (text or "").split(",") if ":" in p)
    return {k.strip(): v.strip() for k, v in pairs}
