"""ledger.py: reports built by CODE from real tool results (exam tasks 1.5, 5.3, 5.6).

Why: if a model copies a URL, a version or an error into a report, it can drop or change it.
Here a hook records every real tool result (the ledger), and code builds each specialist's report
from those records. The model's own words are kept only as a labelled 'summary'.

Each report has one shape, every time:
  status          success | partial | failed | no_action
  findings        each with a grounding_id (G1, G2...), the data, and its grounding (source fields)
  error           error_type, retryable, message, attempted, attempts   (or None)
  coverage_note   code-written sentence about a gap (or None)
  partial_data    any findings that did arrive when the report is not a full success

decision() is the coordinator's decision table, in code:
  wait             a specialist has not reported yet
  rebrief          a retryable failure has not had its one re-brief yet: re-brief first, decide after
  write            both sources fine
  write_with_gaps  one source failed (or a finding lacks grounding): write, but state the gap
  stop             both failed and nothing is left to retry: no writer, plain message
Only write and write_with_gaps let the writer start (enforced by gate_hook).
"""

import json

SOURCES = {"account_investigator": "account data", "policy_researcher": "policy data"}
REQUIRED_GROUNDING = {
    ("account_record", True): ("record_id", "last_updated", "retrieved_at"),
    ("policy", True): ("record_id", "source_url", "version", "last_updated", "policy_name", "retrieved_at"),
    ("account_record", False): ("searched", "retrieved_at"),
    ("policy", False): ("searched", "retrieved_at"),
}
MAX_BRIEFINGS = 2   # the first briefing plus ONE re-brief for a retryable failure


def parse_tool_response(response):
    """MCP results arrive as a list of text blocks (or a string). Our tools put JSON in the text."""
    if isinstance(response, dict) and ("found" in response or "errorCategory" in response):
        return response
    if isinstance(response, list):
        text = "".join(b.get("text", "") for b in response if isinstance(b, dict))
    elif isinstance(response, dict) and "content" in response:
        return parse_tool_response(response["content"])
    else:
        text = str(response)
    try:
        return json.loads(text)
    except ValueError:
        # Not our JSON: still a failure the coordinator must hear about, never silence.
        return {"isError": True, "errorCategory": "internal", "isRetryable": False, "message": text[:300], "attempted": "unknown", "attempts": 1}


def _grounded(result):
    source = result.get("source") or {}
    need = REQUIRED_GROUNDING.get((source.get("source_type"), bool(result.get("found"))))
    return bool(need) and all(source.get(k) for k in need)


class ReportLedger:
    def __init__(self):
        self.calls = {}       # agent_id -> [{"tool", "input", "result"}]
        self.reports = []     # finished reports, in order
        self.briefings = {}   # specialist name -> how many times it was started
        self._next_g = 1

    def record(self, agent_id, tool, tool_input, result):
        self.calls.setdefault(agent_id, []).append({"tool": tool, "input": tool_input, "result": result})

    def count_briefing(self, agent_type):
        self.briefings[agent_type] = self.briefings.get(agent_type, 0) + 1
        return self.briefings[agent_type]

    def build_report(self, agent_id, agent_type, summary):
        calls = self.calls.get(agent_id, [])
        errors = [c for c in calls if c["result"].get("isError") or "errorCategory" in c["result"]]
        findings, ungrounded = [], 0
        for c in calls:
            r = c["result"]
            if c in errors:
                continue
            if not _grounded(r):
                ungrounded += 1
                continue
            findings.append({"grounding_id": f"G{self._next_g}", "found": r["found"],
                             "data": r.get("account") or r.get("policy") or {"message": r.get("message")},
                             "grounding": r["source"], "attempts": r.get("attempts", 1),
                             "recovered_after": r.get("recovered_after", 0)})
            self._next_g += 1

        error = None
        if errors:
            e = errors[-1]["result"]
            error = {"error_type": e.get("errorCategory"), "retryable": bool(e.get("isRetryable")),
                     "message": e.get("message"), "attempted": e.get("attempted"), "attempts": e.get("attempts", 1)}

        if not calls:
            status = "no_action"
        elif findings and not errors and not ungrounded:
            status = "success"
        elif findings:
            status = "partial"
        else:
            status = "failed"

        report = {
            "report_id": f"R{len(self.reports) + 1}",
            "agent": agent_type,
            "source": SOURCES.get(agent_type, agent_type),
            "status": status,
            "findings": findings if status == "success" else [],
            "partial_data": findings if status != "success" else [],
            "error": error,
            "coverage_note": self._coverage_note(agent_type, status, error, ungrounded),
            "summary": summary.strip(),   # the specialist's own words: context, never a source of facts
        }
        self.reports.append(report)
        return report

    @staticmethod
    def _coverage_note(agent_type, status, error, ungrounded):
        what = SOURCES.get(agent_type, agent_type)
        if status == "no_action":
            return f"The {agent_type} made no lookup, so the {what} was not checked. Do not state any {what}."
        notes = []
        if error:
            retry = "retryable" if error["retryable"] else "not retryable"
            notes.append(f"The {what} could not be retrieved: {error['message']} ({error['error_type']}, {retry}, "
                         f"{error['attempts']} attempt(s)). Say this was unavailable; do not guess it.")
        if ungrounded:
            notes.append(f"{ungrounded} {what} result(s) came back without a source and were left out.")
        return " ".join(notes) or None

    def latest(self):
        """The most recent report from each specialist (a re-brief replaces the first one)."""
        out = {}
        for r in self.reports:
            if r["agent"] in SOURCES:
                out[r["agent"]] = r
        return out

    def decision(self):
        latest = self.latest()
        missing = [a for a in SOURCES if a not in latest]
        if missing:
            return {"action": "wait", "reason": f"Waiting for: {', '.join(missing)}.", "coverage_notes": [], "rebrief": []}
        failed = [a for a, r in latest.items() if r["status"] in ("failed", "no_action")]
        notes = [r["coverage_note"] for r in latest.values() if r["coverage_note"]]
        rebrief = [a for a in failed if latest[a]["error"] and latest[a]["error"]["retryable"]
                   and self.briefings.get(a, 0) < MAX_BRIEFINGS]
        if rebrief:
            return {"action": "rebrief", "reason": "A retryable failure has not been retried yet. Brief these specialists again, all in ONE reply, then wait.",
                    "coverage_notes": notes, "rebrief": rebrief}
        if len(failed) == len(SOURCES):
            return {"action": "stop", "reason": "Both sources failed and nothing is left to retry. Do not start the writer. Tell the customer plainly what could not be reached.",
                    "coverage_notes": notes, "rebrief": []}
        if failed or notes:
            return {"action": "write_with_gaps", "reason": "Write the answer from what arrived and state every coverage note.",
                    "coverage_notes": notes, "rebrief": rebrief}
        return {"action": "write", "reason": "Both sources succeeded with grounding.", "coverage_notes": [], "rebrief": []}

    def grounding_index(self):
        """grounding_id -> grounding, for checking citations later (Phase 4/5)."""
        return {f["grounding_id"]: f["grounding"] for r in self.reports for f in r["findings"] + r["partial_data"]}
