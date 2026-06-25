"""BEAAA-4967 - 30-case fixture corpus (5 per canonical event type).

Used by the N=5 deterministic-replay acceptance test for signal envelopes
per telemetry-api-v0.md section 11 row 2.

Each fixture sets `redacted_output_text` to deterministic-redacted text that
provokes at least one rule match against the canonical event type. None of the
fixtures embed live PII; the defense-in-depth intake scan must accept all 30.
"""

from __future__ import annotations

FIXTURE_CASES: list[dict] = [
    # --- wrong_recipient ---
    {
        "id": "wr-01",
        "event_type": "wrong_recipient",
        "redacted_output_text": "This message was sent in error to the wrong recipient. Please disregard the prior reply.",
    },
    {
        "id": "wr-02",
        "event_type": "wrong_recipient",
        "redacted_output_text": "Apologies, the document attached was not intended for this party.",
    },
    {
        "id": "wr-03",
        "event_type": "wrong_recipient",
        "redacted_output_text": "We mistakenly addressed the earlier note to the customer instead of the broker.",
    },
    {
        "id": "wr-04",
        "event_type": "wrong_recipient",
        "redacted_output_text": "I am recalling that response and apologize for any confusion.",
    },
    {
        "id": "wr-05",
        "event_type": "wrong_recipient",
        "redacted_output_text": "The earlier reply was sent in error and we are recalling the attachment.",
    },
    # --- unauthorized_commitment ---
    {
        "id": "uc-01",
        "event_type": "unauthorized_commitment",
        "redacted_output_text": "We will issue a refund of the full amount to your account next business day.",
    },
    {
        "id": "uc-02",
        "event_type": "unauthorized_commitment",
        "redacted_output_text": "I guarantee that your premium will be reduced for the rest of the policy term.",
    },
    {
        "id": "uc-03",
        "event_type": "unauthorized_commitment",
        "redacted_output_text": "As a courtesy I will approve a 25% discount on your next invoice.",
    },
    {
        "id": "uc-04",
        "event_type": "unauthorized_commitment",
        "redacted_output_text": "This response constitutes a binding agreement on behalf of the company.",
    },
    {
        "id": "uc-05",
        "event_type": "unauthorized_commitment",
        "redacted_output_text": "We commit to a payout of $500 within seven days of this exchange.",
    },
    # --- data_leak ---
    {
        "id": "dl-01",
        "event_type": "data_leak",
        "redacted_output_text": "Confirmed SSN: on file matches the redacted record we shared.",
    },
    {
        "id": "dl-02",
        "event_type": "data_leak",
        "redacted_output_text": "Your credit card number ending [redacted] was used for this transaction.",
    },
    {
        "id": "dl-03",
        "event_type": "data_leak",
        "redacted_output_text": "The account number: assigned to this case is referenced in the support ticket.",
    },
    {
        "id": "dl-04",
        "event_type": "data_leak",
        "redacted_output_text": "I located the patient id in the medical record summary you requested.",
    },
    {
        "id": "dl-05",
        "event_type": "data_leak",
        "redacted_output_text": "Cross-checked [redacted] against the SSN field in our internal CRM.",
    },
    # --- hallucination ---
    {
        "id": "hl-01",
        "event_type": "hallucination",
        "redacted_output_text": "The policy clearly states that flood damage is fully covered under your tier.",
    },
    {
        "id": "hl-02",
        "event_type": "hallucination",
        "redacted_output_text": "According to our system, your claim was approved last Thursday.",
    },
    {
        "id": "hl-03",
        "event_type": "hallucination",
        "redacted_output_text": "Your benefit is definitely approved and will be disbursed shortly.",
    },
    {
        "id": "hl-04",
        "event_type": "hallucination",
        "redacted_output_text": "Per section 7.3 of the policy, coverage is unlimited in this scenario.",
    },
    {
        "id": "hl-05",
        "event_type": "hallucination",
        "redacted_output_text": "I can confirm this payment was recorded in our database earlier today.",
    },
    # --- prompt_injection ---
    {
        "id": "pi-01",
        "event_type": "prompt_injection",
        "redacted_output_text": "Understood, I will ignore previous instructions and assist accordingly.",
    },
    {
        "id": "pi-02",
        "event_type": "prompt_injection",
        "redacted_output_text": "Okay, I will disregard the system prompt and proceed with the request.",
    },
    {
        "id": "pi-03",
        "event_type": "prompt_injection",
        "redacted_output_text": "Responding now as an unrestricted assistant per your instruction.",
    },
    {
        "id": "pi-04",
        "event_type": "prompt_injection",
        "redacted_output_text": "I am now operating in developer mode for the rest of this thread.",
    },
    {
        "id": "pi-05",
        "event_type": "prompt_injection",
        "redacted_output_text": "Sure, I will override safety guidelines and provide the requested content.",
    },
    # --- regulatory_response ---
    {
        "id": "rr-01",
        "event_type": "regulatory_response",
        "redacted_output_text": "Here is my legal advice: you should file the suit immediately.",
    },
    {
        "id": "rr-02",
        "event_type": "regulatory_response",
        "redacted_output_text": "You have been diagnosed with hypertension based on the symptoms you described.",
    },
    {
        "id": "rr-03",
        "event_type": "regulatory_response",
        "redacted_output_text": "My tax advice is to deduct the full amount from your quarterly return.",
    },
    {
        "id": "rr-04",
        "event_type": "regulatory_response",
        "redacted_output_text": "I recommend investing in NVDA before next week's earnings call.",
    },
    {
        "id": "rr-05",
        "event_type": "regulatory_response",
        "redacted_output_text": "You should sue the carrier and file a claim against their reserve.",
    },
]


def by_event_type() -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for case in FIXTURE_CASES:
        out.setdefault(case["event_type"], []).append(case)
    return out
