"""session_store.py: facts every agent must be able to trust, kept OUTSIDE every context window.

Why: specialists start blank and never see the coordinator's chat. If "who is verified" lived in a
prompt, a wrong ID in a briefing (a mistake, or injected text) could read another customer's account.
Here CODE writes the session and the MCP tools read it. No model reads or writes this file.

Production would use a database or Redis keyed by session ID. We use one JSON file per session in
.sessions/ (git-ignored), so the state survives new MCP server processes and follow-up questions.
Pattern taken from ../support-agent/session_store.py.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

DIR = Path(__file__).parent / ".sessions"


def new_session(session_id):
    return {
        "session_id": session_id,
        "verified_customer_id": None,
        "verified_at": None,
        "verification_method": None,
        "inquiries": [],   # one entry per question asked in this session (multi-inquiry)
        "reports": [],     # structured reports, filled by the ledger in Phase 3
    }


def _path(session_id):
    safe = "".join(ch for ch in session_id if ch.isalnum() or ch in "-_")
    return DIR / f"{safe}.json"


def load(session_id):
    path = _path(session_id)
    return json.loads(path.read_text()) if path.exists() else new_session(session_id)


def save(session):
    DIR.mkdir(exist_ok=True)
    path = _path(session["session_id"])
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(session, indent=2))
    os.replace(temp, path)  # swap in one step so a reader never sees a half-written file


def delete(session_id):
    _path(session_id).unlink(missing_ok=True)


def verify(session_id, customer_id, method="mock"):
    """MOCK verification. A real system would check a one-time code or a login token first.
    The point here is WHERE the result lives (code-owned state), not how identity is proven."""
    session = load(session_id)
    session.update(verified_customer_id=customer_id, verified_at=_now(), verification_method=method)
    save(session)
    return session


def add_inquiry(session_id, question, stated_reason):
    session = load(session_id)
    session["inquiries"].append({"question": question, "stated_reason": stated_reason, "asked_at": _now()})
    save(session)
    return session


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
