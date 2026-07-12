---
artifact_type: report
owner: scanner_engineer
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "The live BackBond Scanner edge image gives operators an immutable scanner runtime while preserving score-path calibration gates."
contracts_referenced: ["are-scanner-api-v1@v1.0", "scanner-implementation-supervision-v1@v1.1"]
ticket_refs: ["BEAAA-13361", "BEAAA-13358", "BEAAA-13545"]
proof_points_cited: ["ADR-0057 v2.0 PR #54 ab457c95a7fe8ff2294d6bc185aa905ed85876a7"]
version: 0.1
prior_version: null
self_audit_passed: true
escalations_noted: ["Initial close was blocked on BEAAA-13548; local-board comment 9435e734-0b93-4367-a393-6ed72bfb70cc reconciled the active loop-stability N=5 close gate."]
---

# BEAAA-13361 Image-Parity Delta Record

**BLUF:** implementation and image verification passed; BEAAA-13548 reconciled the active loop-stability N=5 close gate, so BEAAA-13361 is closable once the shipped code SHA is reachable from canonical master.

## Classification

- `scanner-implementation-supervision-v1@v1.1` row: smoke test / internal runtime plumbing for an existing operation.
- Classification: rule-execution.
- Contract anchor: `are-scanner-api-v1@v1.0`.
- Contract output shape: unchanged.
- Scoring math, rubric weights, judge models, probe corpus: unchanged.
- Tamper-resistance posture: unchanged.

## Implementation Pins

- `scanner_version`: unchanged by this ticket.
- `corpus_version`: unchanged by this ticket.
- `probe-set-version`: unchanged by this ticket.
- `rubric_point_id`: none; image parity only.
- `cto_cosign_ref`: not required for the implementation classification.
- `image_tag_verified`: `backbond-scanner-live:beaaa-13361`.
- `image_revision_label`: superseded at close by the committed master-reachable SHA.

## Verification

| Check | Result |
|---|---|
| `docker build --build-arg GIT_SHA=$(git rev-parse HEAD) -t backbond-scanner-live:beaaa-13361 .` | pass |
| Dockerfile build guard `python scripts/verify_runtime_import_closure.py` | pass; live modules import and `garak` / `pyrit` / `agentdojo` / `inspect_ai` absent |
| In-image guard `docker run --rm --entrypoint python backbond-scanner-live:beaaa-13361 scripts/verify_runtime_import_closure.py` | pass |
| Image revision label inspect | pass; label equals `833c3b3bacec40bcf67b10e9a462bb6185ce31e3` |
| Compose config render `docker compose -f infra/live-edge/docker-compose.local.yml config` | pass |
| Container boot smoke with temp `/var/lib/are-scanner` mount and `GET /openapi.json` | pass; HTTP 200 |

## N=5 Gate

Initial command:

```bash
python3 scripts/beaaa6190_v05_rc_n5.py
```

Result:

```text
rubric.scoring.ScoringError: missing dim scores: ['ROBUSTNESS.LOOP_STABILITY.BUDGET_GUARD']
```

Disposition: this is an existing branch calibration-gate blocker, not caused by BEAAA-13361. The image-parity patch does not touch the score path, but Scanner Engineer close discipline still requires a resolved N=5 record or CTO disposition before `done`.

Reconciled gate, per local-board comment `9435e734-0b93-4367-a393-6ed72bfb70cc`:

| Evidence | Result |
|---|---|
| `tools/calibration-deltas/2026-07-11-beaaa13548-image-parity-close-gate.md` | pass; active loop-stability packet satisfies the BEAAA-13361 close gate |
| `tools/calibration-deltas/2026-07-11-rubric-1.3.0-vs-1.4.0-loop-stability-robustness-active.md` | pass; current active-dimension N=5 packet |
| `python3 scripts/beaaa13389_loop_stability_activation_delta.py` | pass |
| `python3 -m unittest tests.test_loop_stability_activation_beaaa13540` | pass; 2 tests |
| `python3 scripts/beaaa6190_v05_rc_n5.py` | expected fail; obsolete v0.5 RC gate missing `ROBUSTNESS.LOOP_STABILITY.BUDGET_GUARD` fixture scores and not the current close gate |

Disposition: BEAAA-13361 remains rule-execution/no-score-path. `scanner_version`, `corpus_version`, and `probe-set-version` are unchanged; `rubric_point_id` is none for the image work. Loop-stability movement is covered separately by BEAAA-13413 / approval `9d1a6d35-6c10-449d-bcf9-b2792e94fa6d`.

## FAL / Brand

- Audience: internal.
- Brand Backbone gate: not required.
- Canonical terms: pass except grandfathered contract/file identifiers.
- Carrier Rule: no carrier mention.
