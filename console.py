"""console.py: the Research Console, a local chat page for testing the system the way a customer uses it.

Run:   .venv/bin/python console.py      then open  http://127.0.0.1:8001

You chat as the customer. Nothing is preset: the coordinator greets you, asks for your email, sends a
code to the simulated inbox (right panel), verifies you, asks what reason you were given, and only then
starts the specialists. The "Behind the scenes" lines show what the customer cannot see: tool calls,
the parallel start, code-built reports, the decision, and the citation check.

Test aids, not shortcuts: a card lists the test customers' emails (who you can pretend to be), and
outage switches make the data services fail on purpose (fault injection) for the next message.

Every message calls Claude. It listens only on your own machine (127.0.0.1) because there is no login.
Same look as the Support Console in ../support-agent.
"""

import asyncio
import json
from pathlib import Path

import uvicorn
from starlette.applications import Starlette
from starlette.responses import HTMLResponse, JSONResponse, StreamingResponse
from starlette.routing import Route

from coordinator import Conversation
from mock_data import ACCOUNTS

PAGE = Path(__file__).with_name("console.html")
lock = asyncio.Lock()   # one message at a time: a conversation is a sequence
conversation = Conversation()
faults = {"account": "", "policy": ""}

# What each test customer is good for (a cheat sheet for the tester, not data the agents see).
TRY = {
    "CUST-1001": "Restricted for a payment dispute. The happy path.",
    "CUST-1002": "Active, no flag. Say you were flagged anyway.",
    "CUST-1003": "Flagged for fraud. Say it was a payment dispute.",
    "CUST-1004": "Security hold. Ask four questions in one message.",
    "CUST-1005": "Flagged for a terms violation, which has no policy on file.",
    "CUST-1006": "Closed account with two past flags.",
}


def fault_string():
    return ",".join(f"{k}:{v}" for k, v in faults.items() if v in ("timeout", "blip"))


def state():
    s = conversation.session
    verified = s["verified_customer_id"]
    return {
        "session_id": s["session_id"],
        "verified": {"customer_id": verified, "name": ACCOUNTS[verified]["name"], "at": s["verified_at"]} if verified else None,
        "pending": bool(s["pending_verification"]),
        "inbox": s["outbox"],
        "inquiries": s["inquiries"],
        "faults": faults,
        "test_customers": [{"name": a["name"], "email": a["email"], "try": TRY.get(cid, "")} for cid, a in ACCOUNTS.items()],
    }


async def home(request):
    return HTMLResponse(PAGE.read_text())


async def get_state(request):
    return JSONResponse(state())


async def set_faults(request):
    body = await request.json()
    for k in faults:
        faults[k] = body.get(k) if body.get(k) in ("timeout", "blip") else ""
    conversation.faults = fault_string()
    return JSONResponse(state())


async def reset(request):
    global conversation
    async with lock:
        conversation = Conversation(fault_string())
    return JSONResponse(state())


async def message(request):
    body = await request.json()
    text = (body.get("message") or "").strip()
    if not text:
        return JSONResponse({"error": "Type a message first."}, status_code=400)
    if lock.locked():
        return JSONResponse({"error": "Still working on your last message."}, status_code=409)

    async def events():
        async with lock:
            conversation.faults = fault_string()
            try:
                async for e in conversation.stream(text):
                    yield json.dumps(e) + "\n"
            except Exception as error:   # show the problem on the page instead of a blank screen
                yield json.dumps({"type": "error", "message": f"{type(error).__name__}: {error}"}) + "\n"
            yield json.dumps({"type": "state", "state": state()}) + "\n"

    return StreamingResponse(events(), media_type="application/x-ndjson")


app = Starlette(routes=[
    Route("/", home),
    Route("/api/state", get_state),
    Route("/api/faults", set_faults, methods=["POST"]),
    Route("/api/reset", reset, methods=["POST"]),
    Route("/api/message", message, methods=["POST"]),
])

if __name__ == "__main__":
    print("Research Console: http://127.0.0.1:8001   (Ctrl+C to stop)")
    uvicorn.run(app, host="127.0.0.1", port=8001, log_level="warning")
