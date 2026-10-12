"""Test the MCP server the way the Agent SDK will use it: start it as a program and talk over stdio.

Free: no Claude calls. Proves the rules hold INSIDE the server, across separate server processes.
Run:  .venv/bin/python tests/test_mcp_server.py
"""

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import anyio
from mcp.client import Client
from mcp.client.stdio import StdioServerParameters

import session_store
from tools import TOOLS

failures = 0
SID = "test-mcp-session"


def check(ok, label):
    global failures
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        failures += 1


def body(result):
    return json.loads(result.content[0].text)


def server(**env):
    full = {**os.environ, "RETRY_BASE_DELAY": "0.01", **env}
    return Client(StdioServerParameters(command=sys.executable, args=[str(ROOT / "mcp_server.py")], env=full, cwd=str(ROOT)))


async def run():
    session_store.delete(SID)

    print("Listing and the session gate")
    async with server() as c:
        listed = await c.list_tools()
        check({t.name for t in listed.tools} == {t["name"] for t in TOOLS}, "server lists both tools from tools.py")
        r = await c.call_tool("get_account_details", {"customer_id": "CUST-1001"})
        check(r.is_error and body(r)["errorCategory"] == "permission", "E3 no SESSION_ID: account read denied")
        r = await c.call_tool("get_policies", {"flag_type": "payment_dispute"})
        check(not r.is_error and body(r)["source"]["source_url"], "policies need no verification and carry their URL")

    print("\nMulti-inquiry across separate server processes (M1, M2)")
    session_store.verify(SID, "CUST-1001")   # the front door verifies once (code, not a model)
    async with server(SESSION_ID=SID) as c:
        r = await c.call_tool("get_account_details", {"customer_id": "CUST-1001"})
        check(not r.is_error and body(r)["found"], "M1 process 1 reads the verified customer")
        r = await c.call_tool("get_account_details", {"customer_id": "CUST-1003"})
        check(r.is_error and body(r)["errorCategory"] == "permission", "E4 process 1 refuses another customer")
    session_store.add_inquiry(SID, "How do I appeal?", "payment_dispute")
    async with server(SESSION_ID=SID) as c:
        r = await c.call_tool("get_account_details", {"customer_id": "CUST-1001"})
        check(not r.is_error and body(r)["found"], "M2 a NEW process (follow-up question) is still verified")

    print("\nFault injection through the server (no code edits)")
    async with server(SESSION_ID=SID, FAULT_INJECT="account:timeout") as c:
        r = await c.call_tool("get_account_details", {"customer_id": "CUST-1001"})
        b = body(r)
        check(r.is_error and b["errorCategory"] == "transient" and b["isRetryable"] and b["attempts"] == 3, "E5 timeout: is_error set, transient, 3 attempts")
        r = await c.call_tool("get_policies", {"flag_type": "payment_dispute"})
        check(not r.is_error, "E5 the policy tool still works in the same server")
    async with server(SESSION_ID=SID, FAULT_INJECT="account:blip") as c:
        r = await c.call_tool("get_account_details", {"customer_id": "CUST-1001"})
        check(not r.is_error and body(r)["recovered_after"] == 1, "E6 blip recovered inside the server")
    async with server(SESSION_ID=SID, FAULT_INJECT="account:timeout,policy:timeout") as c:
        a = await c.call_tool("get_account_details", {"customer_id": "CUST-1001"})
        p = await c.call_tool("get_policies", {"flag_type": "payment_dispute"})
        check(a.is_error and p.is_error, "E8 both tools fail with structured errors")

    session_store.delete(SID)
    print(f"\n{'All passed.' if not failures else f'{failures} FAILED.'}")


anyio.run(run)
sys.exit(1 if failures else 0)
