# Test scenarios: the master map

Every scenario the system must handle, the data that drives it, and where it is proven.
**Offline** tests are free (no Claude calls). **Live** tests call Claude (Phase 5).
Status: ✅ passing · ⏳ waiting for its phase.

Customers live in `mock_data.py`. Verification is a mock in `session_store.py` (architect's decision: the course has no verification step).

## The cast
| ID | Name | Status | Flag | Built for |
|---|---|---|---|---|
| CUST-1001 | Maya Patel | restricted | payment_dispute (chargeback on ORD-55120) | Happy path |
| CUST-1002 | Daniel Brooks | active | none | "I was flagged" but nothing is there |
| CUST-1003 | Priya Raman | restricted | fraud_suspicion (+ an old resolved payment dispute) | Stated reason differs from real flag |
| CUST-1004 | Tomas Silva | restricted | account_security (3 features) | Third policy, complex question |
| CUST-1005 | Grace Kim | restricted | terms_violation (**no policy on file**) | Policy gap |
| CUST-1006 | Leo Novak | closed | none now, two past flags | Edge status |
| CUST-9999 | (none) | | | Unknown ID |

Policies: `payment_dispute` (v3.2), `fraud_suspicion` (v2.1), `account_security` (v1.4). No policy for `terms_violation`.

## How to run a scenario yourself
Open the Research Console (`.venv/bin/python console.py`, http://127.0.0.1:8001) and chat as the customer. Nothing is preset: give the test customer's email when asked, read the code from the simulated inbox, type it back, say what reason you were given, and ask your question. Outage switches set the faults for the next message.

## V: Verification (front door)
| ID | Scenario | Expected | Status |
|---|---|---|---|
| V1 | Give a test customer's email | Code sent to the simulated inbox; the code never appears in a tool result | ✅ offline + live |
| V2 | Give an unknown email | Same reply as a real one (no probing who has an account); no code arrives | ✅ offline |
| V3 | Wrong code / wrong email | Validation error, attempts counted | ✅ offline |
| V4 | Right code | Session verified by code; account reads now allowed | ✅ offline + live |
| V5 | Three wrong codes | Locked (business error); even the right code fails after | ✅ offline |
| V6 | Research before verifying | Gate hook denies both specialists | ✅ offline |
| V7 | "Hello" | Greeting only, no tools | ✅ live |

## S: Success and mismatch
| ID | Scenario | Input (customer, stated reason) | Expected | Offline | Live |
|---|---|---|---|---|---|
| S1 | Restricted, both succeed | 1001, payment_dispute | Answer cites the policy URL and version, the chargeback on ORD-55120, each restricted feature with its date | ✅ data + grounding | ✅ P4 (citations ok, all findings cited) |
| S2 | Course mismatch | 1002, payment_dispute | Says the account is active with no flag; does not describe a dispute that is not there | ✅ | ⏳ P5 |
| S3 | Reason mismatch | 1003, payment_dispute | States both: customer said payment dispute, account shows fraud suspicion; cites both sources | ✅ | ✅ P4 (mismatch named, fraud policy gap stated) |
| S4 | Security hold | 1004, account_security | Three features with dates, security policy v1.4 | ✅ | ⏳ P5 |
| S5 | Policy gap | 1005, terms_violation | Account facts given; says no policy is on file; no invented rights; does NOT say "try again" | ✅ | ✅ P4 (after a prompt fix) |
| S6 | Closed account | 1006, payment_dispute | Says the account is closed, lists past flags with dates | ✅ | ⏳ P5 |

## E: Errors (contain, then report)
| ID | Scenario | Setup | Expected | Offline | Live |
|---|---|---|---|---|---|
| E1 | Unknown customer | 9999 | `found: false`, NOT an error; answer says no such account | ✅ | ⏳ P5 |
| E2 | Malformed input | ID `12345` | `validation`, not retryable, 1 attempt, no retries wasted | ✅ | |
| E3 | Nobody verified | no session | `permission`, account never read | ✅ | |
| E4 | Wrong customer in briefing | verified 1001, asks 1003 | `permission`; no data from 1003 leaks | ✅ | ⏳ P2 |
| E5 | Account service down | `FAULT_INJECT=account:timeout` | 3 attempts, backoff 0.2s then 0.4s, `transient` + retryable; decision `rebrief` (once), then `write_with_gaps`; policy still arrives; answer states the gap | ✅ | ✅ P3 + P4 (writer states the gap, cites only [G1]) |
| E6 | Brief blip | `account:blip` | Retry absorbs it: success with `recovered_after: 1`; coordinator never sees an error | ✅ (+ ledger) | ⏳ P5 |
| E7 | Policy service down | `policy:timeout` | Account facts still given; answer says the policy could not be reached | ✅ | ⏳ P5 |
| E8 | Both down | `account:timeout,policy:timeout` | Decision `rebrief` both in ONE reply, then `stop`; plain message; `report_writer` blocked by the gate hook | ✅ tools + gate | ✅ P3 (4 reports, stop, no writer) |

## R: Routing (hub-and-spoke)
| ID | Scenario | Expected | When |
|---|---|---|---|
| R1 | Parallel start | Both specialists started in the SAME coordinator reply | ✅ P2 live (same `message_id`, 3 of 3 runs) |
| R2 | Tool scope | Each specialist can call only its own tool; a cross call is denied by the scope hook | ✅ P2 offline (`tests/test_hooks.py`); live: 0 denials, no cross calls |
| R3 | Full briefing | Each briefing contains the customer ID / stated reason and the return format | ✅ P2 live |
| R4 | Writer only after research | `report_writer` gets the code-built packet (briefing replaced by `updatedInput`), never raw chat or specialists' words; no tools | ✅ P4 offline + live |

## G: Grounding
| ID | Scenario | Expected | When |
|---|---|---|---|
| G1 | Every record has source fields | record_id, last_updated, retrieved_at; policies also URL, version, name | ✅ |
| G2 | Ledger assigns `grounding_id`; ungrounded data is left out (failed or partial + coverage note) | ✅ P3 (`tests/test_ledger.py`) |
| G3 | Every claim in the answer maps to a `grounding_id`; URLs quoted from it | ✅ P4 `check_citations`: unknown IDs/URLs caught offline; 5 live answers ok=True |

## M: Multi-inquiry and session state
| ID | Scenario | Expected | When |
|---|---|---|---|
| M1 | Verify once | Verification stored with a time, outside every prompt | ✅ |
| M2 | Follow-up question | A new server process in the same session is still verified; inquiries are logged | ✅ |
| M3 | Follow-up reuses reports | Reports and decision saved to the session after each inquiry ✅ P3. Second question answered with the same verified customer | ⏳ P5 live |

## C: Complex question
| ID | Scenario | Expected | When |
|---|---|---|---|
| C1 | Four parts in one message | 1004: why flagged, which features, my rights, how to appeal. All four answered from research data with sources | ⏳ P5 |

## Live runs so far
- 10-11-2026, Phase 2 (research summary only, no writer yet): S1 ✅, S3 ✅ (mismatch and missing fraud policy named), E5 ✅ (structured transient error, 3 attempts, policy still arrived, gap stated). About $0.02 each.
- 10-11-2026, Phase 3 (ledger + decision table + gate): E5 ✅ (rebrief once, then write_with_gaps, [G1] cited), E8 ✅ (rebrief both in one reply, then stop, no writer). $0.03 each.
- 10-12-2026, Phase 4 (report writer): S1, S3, S5, E5 ✅, citation check ok on all. S5 first said "try again" for a found=false policy; fixed in the writer prompt (rule 4). About $0.05 each.

## How to run
```
.venv/bin/python tests/test_tools.py        # data layer, errors, retry, grounding, session
.venv/bin/python tests/test_mcp_server.py   # same rules through the real MCP server, across processes
.venv/bin/python tests/test_hooks.py        # scope hook, parallel-start check
.venv/bin/python tests/test_ledger.py       # reports, grounding IDs, coverage notes, decision table, gate
.venv/bin/python tests/test_writer.py       # writer packet, briefing swap, citation check
.venv/bin/python tests/run_all.py           # ALL offline suites, one line each (-v for every check)
.venv/bin/python main.py --customer CUST-1001 --reason payment_dispute "Why was my account flagged?"   # live, costs credit
```
