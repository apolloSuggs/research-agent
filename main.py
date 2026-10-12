"""main.py: chat with the research system in the terminal (the console is the nicer way: console.py).

  .venv/bin/python main.py                       # normal
  .venv/bin/python main.py --faults account:timeout

Type as the customer. When you give a test customer's email (see mock_data.py, e.g.
maya.patel@example.com), the verification code appears here as [inbox]. Empty line quits.
Every message calls Claude, so it costs a little API credit.
"""

import argparse
import asyncio

from coordinator import Conversation


def show(events):
    for e in events:
        t = e["type"]
        if t == "spawn":
            print(f"   [coordinator -> {e['to']}]  (reply {e['message_id'][-8:]})")
        elif t == "tool_call":
            print(f"   [{e['agent']} calls] {e['tool']}({e['input']})")
        elif t == "tool_result":
            print(f"   [{e['agent']} {'ERROR' if e['is_error'] else 'result'}] {e['content'][:200]}")
        elif t == "report":
            r = e["report"]
            ids = [f["grounding_id"] for f in r["findings"] + r["partial_data"]]
            print(f"   [report {r['report_id']}] {r['agent']}: {r['status']} grounding={ids}"
                  + (f" error={r['error']['error_type']}" if r["error"] else ""))
        elif t == "decision":
            print(f"   [decision] {e['decision']['action']}")
        elif t == "done":
            print(f"   [done] ${e['cost_usd'] or 0:.4f}")
        elif t == "final":
            print(f"\n[{e['source']}] {e['text']}")
            if e["researched"]:
                c = e["citations"]
                print(f"\n   [citation check] ok={c['ok']} cited={c['cited']} parallel start={e['parallel_start']}")


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--faults", default="", help='testing only, e.g. "account:timeout"')
    a = ap.parse_args()
    conversation = Conversation(a.faults)
    print(f"Research agent (session {conversation.session_id}). Type as the customer. Empty line quits.")
    seen = 0
    while (message := input("\nYou: ").strip()):
        show(await conversation.send(message))
        outbox = conversation.session["outbox"]
        for mail in outbox[seen:]:
            print(f"\n[inbox for {mail['to']}] {mail['text']}")
        seen = len(outbox)


if __name__ == "__main__":
    asyncio.run(main())
