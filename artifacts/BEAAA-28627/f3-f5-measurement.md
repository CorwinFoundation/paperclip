---
artifact_type: verification
owner: scanner-engineer
persona_audience: internal
ticket_refs: [BEAAA-28627, BEAAA-29119]
scanner_version: unchanged
corpus_version: unchanged
probe-set-version: unchanged
rubric_point_id: none
n5_calibration_delta: "N/A; reconciler-only test change outside scoring paths"
cto_cosign_ref: "N/A; rule-execution smoke tests for an existing operation"
contract_ref: "are-scanner-api-v1@v1.3.0"
---

# BEAAA-28627 combined F3 + F5 measurement

BLUF: The focused reconciliation slice passes while preserving fail-closed evidence attribution. F3 widens SHA discovery to the matched issue neighbourhood; F5 records only approval-shaped board comments that name the landing SHA and never admits a non-QA agent through that path.

Classification: rule execution under `scanner-implementation-supervision-v1@v2.1`, table row “Smoke test for an existing operation.” No scoring, rubric, corpus, judge, contract output, or tamper-resistance behavior changes.

## Command

```text
python3 -m pytest -q tests/test_canon_landing_reconciler.py -k 'board_approval_without_a_named_sha or non_qa_agent_cannot_take_the_unattributed_board_path or sha_search_widens_to_hit_neighbourhood or unattributed_board_approval'
```

## Result

```text
.....                                                                    [100%]
5 passed, 195 deselected in 0.81s
```

## Discriminators

- F3: a SHA-search hit expands to its neighbourhood and finds the QA approval on the canonical gate.
- F5 named-SHA guard: an approval-shaped board comment without a SHA is not recorded as evidence for the landing.
- F5 author guard: a non-QA agent cannot enter the unattributed board/user path, even when its approval-shaped comment names the candidate SHA.
- Existing board-path handling remains fail-closed: the comment is surfaced as a separate finding and does not satisfy QA.
