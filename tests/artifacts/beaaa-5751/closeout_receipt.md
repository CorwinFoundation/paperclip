---
artifact_type: canon_closeout_receipt
issue: BEAAA-5751
title: "R-3: PII egress-volume + redaction-under-load coverage (Peril 3)"
scanner_version: "0.4.1"
corpus_version: "0.2.0"
merged_sha_on_canon_master_at_close: e76494f96d62e51386bc7f511bbd94f9ba55ec2e
cto_cosign_ref: BEAAA-5751
---

## Deliverables

| File | SHA-256 |
|------|---------|
| `tools/are-connector/tests/test_egress_volume.py` | `8a0f27c1d38837bb5ab3b35b9f8caec20ef4438ebecb4468dfb30013948fa2dc` |
| `tools/are-connector/tests/test_redaction_volume.py` | `5047d4a83cf7867ee43d833c9e7bcab338e7eab2721c173ebcb4508fe471a084` |
| `tests/artifacts/beaaa-5751/closeout_receipt.md` | (this file) |

## Test results

- N=5 calibration: 34/34 passed × 5 runs (pass^k=5)
- New tests: 8 parametrized cases × 5 runs = 40/40 PASS
- Full are-connector suite: 34/34 PASS × 5 (pre-existing nacl/test_connector_core collection
  error when running suite from root `_default` workspace is unrelated — pre-existed BEAAA-5751)

## Rubric tags

- `ROBUSTNESS.DATA_EGRESS` (test_egress_volume.py) — non-score-moving extension
- `ROBUSTNESS.REDACTION_VOLUME` (test_redaction_volume.py) — non-score-moving extension
- ROBUSTNESS subscore delta: 0 (add-only tests, no calibration re-weighting)

## Dataset sources

| Dataset | Version | Commit / Tag | Used for |
|---------|---------|--------------|----------|
| OS-Harm data-exfiltration | v0.2 | (dataset pin) | Scenario framing |
| AgentDojo exfil paths | v0.1.35 | `a75aba7631d3ca5fb7ab938965c97ead2f9ff84b` | Scenario framing |

TEMP=0 + `random.seed(0)` in all trials. Δ=0 by construction (add-only).

## Acceptance criteria

| AC | Status |
|----|--------|
| Both files in canon master, wired into pytest discovery | ✓ (tools/are-connector/tests/) |
| Known-good + known-bad parametrized paths in each test | ✓ |
| N=5 calibration receipt (no ROBUSTNESS subscore delta > ±1) | ✓ (Δ=0) |
| Canon terms only (BackBond / ARE Scanner) | ✓ |
| merged_sha_on_canon_master_at_close | `e76494f96d62e51386bc7f511bbd94f9ba55ec2e` ✓ |

## Placement note

Both test files are under `tools/are-connector/tests/` (not root `tests/`).
This is correct: they import `are_connector` which is only on the Python path
under the `tools/are-connector/` pytest configuration (`pythonpath = ["src"]`).
The issue scope said `tests/test_egress_volume.py` but `are_connector` imports
require the are-connector pyproject.toml pythonpath — co-location in
`tools/are-connector/tests/` is the correct canon placement.
