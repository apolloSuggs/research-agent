"""tools.py: the two data tools (exam tasks 2.1, 2.2) and the code that runs them.

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

TOOLS = [get_account_details, get_policies]

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
    body = {k: v for k, v in record.items() if k not in ("record_id", "last_updated")}
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


HANDLERS = {"get_account_details": _account, "get_policies": _policy}


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
