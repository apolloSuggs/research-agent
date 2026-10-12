"""mock_data.py: the "database" both specialists read (our own version of the course data, not a copy).

In production this would be an accounts service and a policy service. Here it is two dicts.
Every record carries the fields grounding needs (record ID, last-updated date, and for policies the
source URL and version), so code can attach provenance to each finding. The model never types them.

Each customer is here to drive at least one scenario in docs/TEST-SCENARIOS.md. In the console you
"become" one by giving their email; a verification code then lands in the simulated inbox.
Dates are MM-DD-YYYY. URLs use example.com, a domain reserved for examples.
"""

ACCOUNTS = {
    # S1 happy path: restricted for a payment dispute, both lookups succeed.
    "CUST-1001": {
        "record_id": "ACC-1001",
        "name": "Maya Patel",
        "email": "maya.patel@example.com",
        "status": "restricted",
        "flag": {
            "type": "payment_dispute",
            "applied_on": "09-28-2026",
            "trigger": "Chargeback filed on order ORD-55120 ($184.20)",
        },
        "restricted_features": [
            {"feature": "instant_payouts", "since": "09-28-2026", "reason": "Funds held while the dispute is open"},
            {"feature": "international_transfers", "since": "09-28-2026", "reason": "Outgoing transfers paused during dispute review"},
        ],
        "previous_flags": [],
        "last_updated": "09-28-2026",
    },
    # S2 mismatch: active with no flag, but the customer says "payment dispute".
    "CUST-1002": {
        "record_id": "ACC-1002",
        "name": "Daniel Brooks",
        "email": "daniel.brooks@example.com",
        "status": "active",
        "flag": None,
        "restricted_features": [],
        "previous_flags": [],
        "last_updated": "07-02-2026",
    },
    # S3 reason mismatch (reflection question 3): customer says payment dispute, account says fraud suspicion.
    "CUST-1003": {
        "record_id": "ACC-1003",
        "name": "Priya Raman",
        "email": "priya.raman@example.com",
        "status": "restricted",
        "flag": {
            "type": "fraud_suspicion",
            "applied_on": "10-02-2026",
            "trigger": "Three card changes within 24 hours, then a login from a new device",
        },
        "restricted_features": [
            {"feature": "card_payments", "since": "10-02-2026", "reason": "Blocked until identity is re-confirmed"},
            {"feature": "withdrawals", "since": "10-02-2026", "reason": "Held during fraud review"},
        ],
        "previous_flags": [
            {"type": "payment_dispute", "applied_on": "03-14-2025", "resolved_on": "04-02-2025"},
        ],
        "last_updated": "10-02-2026",
    },
    # S4 third policy + complex question: security hold after a suspicious password reset.
    "CUST-1004": {
        "record_id": "ACC-1004",
        "name": "Tomas Silva",
        "email": "tomas.silva@example.com",
        "status": "restricted",
        "flag": {
            "type": "account_security",
            "applied_on": "10-09-2026",
            "trigger": "Password reset requested from an unrecognized country",
        },
        "restricted_features": [
            {"feature": "outgoing_payments", "since": "10-09-2026", "reason": "Paused until the owner confirms the reset"},
            {"feature": "profile_changes", "since": "10-09-2026", "reason": "Locked to stop account takeover"},
            {"feature": "api_access", "since": "10-10-2026", "reason": "Keys revoked after the reset"},
        ],
        "previous_flags": [],
        "last_updated": "10-10-2026",
    },
    # S5 policy gap: flagged for a type that has NO policy on file (valid empty policy result).
    "CUST-1005": {
        "record_id": "ACC-1005",
        "name": "Grace Kim",
        "email": "grace.kim@example.com",
        "status": "restricted",
        "flag": {
            "type": "terms_violation",
            "applied_on": "09-15-2026",
            "trigger": "Listing reported for prohibited items",
        },
        "restricted_features": [
            {"feature": "new_listings", "since": "09-15-2026", "reason": "Listing creation paused during review"},
        ],
        "previous_flags": [],
        "last_updated": "09-16-2026",
    },
    # S6 edge status: closed account with a history of flags.
    "CUST-1006": {
        "record_id": "ACC-1006",
        "name": "Leo Novak",
        "email": "leo.novak@example.com",
        "status": "closed",
        "flag": None,
        "restricted_features": [],
        "previous_flags": [
            {"type": "payment_dispute", "applied_on": "11-03-2024", "resolved_on": "12-01-2024"},
            {"type": "fraud_suspicion", "applied_on": "06-20-2025", "resolved_on": "08-01-2026"},
        ],
        "closed_on": "08-01-2026",
        "last_updated": "08-01-2026",
    },
}

POLICIES = {
    "payment_dispute": {
        "record_id": "POL-PD",
        "name": "Payment Dispute Policy",
        "source_url": "https://policies.example.com/payment-disputes",
        "version": "3.2",
        "last_updated": "08-15-2026",
        "customer_rights": [
            "See which order and which dispute caused the restriction",
            "Keep using features that are not listed as restricted",
            "Get a decision within 10 business days of the dispute closing",
        ],
        "appeal_process": "Submit an appeal at https://policies.example.com/appeals with the order ID and proof of delivery or refund. Reviewed within 5 business days.",
        "removal_conditions": "The flag is removed when the card issuer closes the dispute in the merchant's favor, or the customer refunds the order.",
        "feature_restoration": "Restricted features return within 24 hours of the flag being removed.",
    },
    "fraud_suspicion": {
        "record_id": "POL-FS",
        "name": "Fraud Review Policy",
        "source_url": "https://policies.example.com/fraud-review",
        "version": "2.1",
        "last_updated": "06-30-2026",
        "customer_rights": [
            "Be told which activity triggered the review",
            "Re-confirm identity to speed up the review",
            "Withdraw held funds once the review clears",
        ],
        "appeal_process": "Re-confirm identity in the security center, then request a manual review at https://policies.example.com/appeals. Reviewed within 7 business days.",
        "removal_conditions": "Removed when identity is re-confirmed and no further suspicious activity is found within 14 days.",
        "feature_restoration": "Card payments return at once after re-confirmation; withdrawals after the review clears.",
    },
    "account_security": {
        "record_id": "POL-AS",
        "name": "Account Security Hold Policy",
        "source_url": "https://policies.example.com/security-holds",
        "version": "1.4",
        "last_updated": "09-01-2026",
        "customer_rights": [
            "Confirm or reject the reset that caused the hold",
            "Have API keys reissued after the hold is lifted",
        ],
        "appeal_process": "No appeal needed: confirm the reset from the registered email, or contact security support to reject it.",
        "removal_conditions": "Lifted when the owner confirms the reset from the registered email, or after a security support call.",
        "feature_restoration": "Payments and profile changes return at once; new API keys must be created by the owner.",
    },
}
