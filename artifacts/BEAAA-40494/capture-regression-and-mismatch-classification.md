# BEAAA-40494 capture regression and 9 October mismatch classification

## Result

`harness/customer_runner.py::_VapiCustomerAgentRunner._parse_response` at base
commit `96a6d9ecf02f429c02f332fdf427747c763090f7` was not byte-preserving. For
each accepted Vapi assistant-content string it applied `content.strip()`, then
returned `" ".join(parts)`. That deleted leading/trailing bytes from each part
and inserted one ASCII space between multiple parts.

The correction preserves every accepted assistant string and concatenates
multiple strings in Vapi `output` order with an empty separator. This is
deterministic and neither inserts nor deletes bytes. Non-assistant object items
remain excluded. A single empty or whitespace-only assistant string is now a
valid captured response and survives unchanged.

## Raw, decoded, captured, persisted proof

The retained regression response body is
`tests/fixtures/vapi_chat_assistant_content_beaaa40494.json`. Its JSON-decoded
assistant content contains nested objects, a quoted brace, escapes, Unicode,
leading/trailing whitespace, and two assistant output items. The focused test
passes the raw response bytes through `complete()`, compares the captured value
to direct JSON decoding, calls `record_starter_verdict()`, and compares the
persisted `reply` to the same expected bytes.

Command:

```text
pytest -q tests/integration/test_vapi_request_construction.py
...........                                                              [100%]
11 passed in 0.45s
```

The fixture's opening brace is present in the raw response, decoded content,
captured return value, and persisted reply. The corrected scanner loses no
brace. This synthetic proof does not establish whether Vapi or the old scanner
lost the opening brace in any historical 9 October exchange.

## 9 October classification

The preserved run is `/var/lib/backbond/scans/vapi-kit-completion-39144/`.
This seat cannot read it:

```text
$ find /var/lib/backbond/scans/vapi-kit-completion-39144 -type f
find: ‘/var/lib/backbond/scans/vapi-kit-completion-39144’: Permission denied
```

Connected issue evidence records persisted responses and aggregate counts, but
does not expose the raw Vapi `POST /chat` response bodies or decoded assistant
fields for the 71 mismatches. Because `strip()` cannot delete a leading `{`
unless whitespace precedes it, the old transformation alone is insufficient to
attribute every mismatch. No brace is prepended and no historical row or label
is modified.

All 71 are therefore classified **fresh execution required for attribution**
from the evidence available to this seat; zero are demonstrated recoverable
from retained raw provider evidence. Counts remain:

| Family | Fresh execution required | Raw-evidence recoverable |
|---|---:|---:|
| consequential_action_confirmation | 15 | 0 |
| customer_defined_risk_pack | 12 | 0 |
| human_escalation_flagging | 20 | 0 |
| p1_3_consistency_golden | 1 | 0 |
| p3_3_secret_exfiltration_rob | 2 | 0 |
| pii_scan_time_detection | 2 | 0 |
| system_prompt_extraction | 1 | 0 |
| p2_3_counterfactual_stability | 2 | 0 |
| p2_4_eval_representation | 7 | 0 |
| p2_5_sentiment_skew_by_attribute | 2 | 0 |
| p2_6_mitigation_evidence | 7 | 0 |
| **Total** | **71** | **0** |

If an authorized operator later exposes retained raw envelopes, the 71 can be
reclassified by comparing raw body → decoded assistant content → persisted
reply. Until then, fresh execution is the only evidence-backed attribution
path.

## Runtime identity and scope

- Scanner version: `0.5.0` (executing manifest `release/backbond-scanner-release.json`)
- Corpus version: `0.2.0` (same manifest)
- Probe-set version: not applicable; capture transport code only
- Rubric point: not applicable; no scoring or rubric behavior changed
- Contract: `are-scanner-api-v1@v1.3.0`
- Classification: rule execution; response transport/persistence correction
- N=5 calibration: not applicable; no scored content, judge, weights, or outputs changed
- Rollback: revert the BEAAA-40494 capture-path commit
