"""main.py: run one inquiry from the command line.

  .venv/bin/python main.py --customer CUST-1001 --reason payment_dispute "Why was my account flagged?"
  .venv/bin/python main.py --customer CUST-1001 --reason payment_dispute --faults account:timeout "Why?"

The front door is CODE: it (mock-)verifies the customer and records the inquiry in the session store
before any model runs. Calls Claude, so it costs a little API credit.
"""

import argparse
import asyncio
import json
import uuid

import session_store
from coordinator import parallel_start, run_inquiry


def show(events):
    for e in events:
        t = e["type"]
        if t == "spawn":
            print(f"\n[coordinator -> {e['to']}]  (reply {e['message_id'][-8:]})\n    briefing: {e['briefing']}")
        elif t == "tool_call":
            print(f"   [{e['agent']} calls] {e['tool']}({e['input']})")
        elif t == "tool_result":
            print(f"   [{e['agent']} {'ERROR' if e['is_error'] else 'result'}] {e['content'][:220]}")
        elif t == "text":
            print(f"\n[{e['agent']}] {e['text']}")
        elif t == "done":
            print(f"\n--- done: {e['turns']} turns, ${e['cost_usd'] or 0:.4f}, denials: {len(e['denials'] or [])}")
    print(f"--- parallel start: {'YES' if parallel_start(events) else 'NO'}")


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("question")
    ap.add_argument("--customer", required=True)
    ap.add_argument("--reason", required=True)
    ap.add_argument("--faults", default="")
    ap.add_argument("--session", help="reuse a session ID for a follow-up question")
    ap.add_argument("--json", action="store_true", help="print raw events")
    a = ap.parse_args()

    session_id = a.session or f"run-{uuid.uuid4().hex[:8]}"
    if not a.session:
        session_store.verify(session_id, a.customer)   # mock front door: code verifies, not a model
    session_store.add_inquiry(session_id, a.question, a.reason)
    print(f"session {session_id} · verified {session_store.load(session_id)['verified_customer_id']}")

    events = await run_inquiry(session_id, a.customer, a.reason, a.question, a.faults)
    print(json.dumps(events, indent=2) if a.json else "", end="")
    show(events)


if __name__ == "__main__":
    asyncio.run(main())
