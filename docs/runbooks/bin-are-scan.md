# `bin/are-scan` — operator runbook

**CLI owner:** Scanner Engineer (agent `d8bbc3b5-185f-43ed-bff0-d00323ddbfa3`), per BEAAA-481 ownership handoff from CTO (2026-05-17).
**Supervision contract:** `contracts/scanner-implementation-supervision-v1.md@v1.0`.
**API contract pin:** `contracts/are-scanner-api-v1.md@v1.0`.
**v0.1 design ADR:** `docs/adr-0005-bin-are-scan-cli.md` (authored under CTO name; remains the v0.1 audit-trail anchor).
**Live build:** `scanner_version = 0.3.0`, `rubric_version = 1.0.0`, `corpus_version = 0.1.1`, `contract_version = are-scanner-api-v1@v1.0`.

## What this CLI is

A thin wrapper over the in-agent scoring stack (`harness/cli_runner.py` → `rubric.scoring.score`). Same rubric, same scoring math, same forensic stamps. Lives at `bin/are-scan`; core library is `harness/cli_runner.py`. No probe/judge logic in the CLI itself — duplicating it is the anti-pattern ADR-0005 §"Decision" rejects.

## What this CLI is NOT

- Not a rubric authority. Rubric changes go through the CTO (rule-change per the classification table).
- Not a judge runner today. Phase-1 reads the agent owner's `eval_results.json`; it does not invoke LLM judges. If/when judges are wired in, judge model pins go through CTO co-sign.
- Not a tenant boundary. The CLI assumes its operator is trusted; the agent owner's input directory is treated as adversarial (size caps, path resolution, no shell-out).

## Canonical invocation

```
bin/are-scan --agent <agent-id> --seed <int> [--version <semver>]
             [--corpus <semver>] [--input-dir <path>] [--out <path>]
             [--rubric <path>] [--scored-at <iso8601>]
```

| Flag | Required | Notes |
|---|---|---|
| `--agent` | yes | `^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$` (regex-restricted; the CLI never shells out) |
| `--seed` | yes | Integer; stamped into output for replay |
| `--version` | no | Asserted scanner_version; mismatch with live `SCANNER_VERSION` → exit 2 |
| `--corpus` | no | Corpus semver pin (e.g. `0.1.1`); stamped into output |
| `--input-dir` | no | Default `./inputs/<agent-id>/`; every read is `resolve()`d under it |
| `--out` | no | Default stdout |
| `--rubric` | no | Default `rubric/rubric.yaml` |
| `--scored-at` | no | ISO-8601 override; smoke tests use this to assert byte-identical output |

## Input directory convention

Per ADR-0005 §"Input directory convention" and the API contract Input section:

| File | Required | Shape |
|---|---|---|
| `eval_results.json` | yes | `{P<n>.<m>: {raw, evidence_uri}}` — the only load-bearing input today |
| `system_prompt.txt` | no | UTF-8 text (hashed, never executed) |
| `tool_inventory.json` | no | list of tool objects |
| `policy_attestations.json` | no | object |
| `governance_attestations.json` | no | object |
| `model_versions.json` | no | `{primary, judges[]}` |
| `scanner_invocation_context.json` | no | `{purpose, requested_pillars[]}` |
| `applicability.json` | no | `{applicability_decisions: {P<n>.<m>: {fires, na_justification, na_justification_key}}, policy_version}` |

Every present file is sha256-hashed into `input_file_sha256s`; the snapshot hash covers these, so any input mutation forces a new snapshot.

### `applicability.json` (Option C sidecar, BEAAA-648)

When present, the CLI threads `applicability_decisions` and `policy_version` (→ `applicability_policy_version`) into `rubric.scoring.score()`. Per `rubric.scoring`, any dim with `fires: false` is excluded from its pillar mean and emitted as an N/A entry in `evidence_refs` (`verdict="n/a"`, byte-stable `na_justification` + `na_justification_key`) — Option C re-norm, UW co-signed 2026-05-14. Absence preserves Phase-1 behaviour (no N/A re-norm, byte-identical to v0.2.3 callers).

Sidecar shape (mirrors `artifacts/beaaa-387/<agent>/applicability.json` and the L1 fixture wiring in `tests/test_golden_agents.py::_rerun_score`):

```json
{
  "applicability_decisions": {
    "P2.2": {
      "fires": false,
      "na_justification": "...",
      "na_justification_key": "P2.2.no_uw_labeled_corpus"
    },
    "P3.2": {"fires": false, ...}
  },
  "policy_version": "0.1.0"
}
```

Operator note: the sidecar must be co-located with `eval_results.json` (same `--input-dir`). The applicability policy is owned by the policy bench (BEAAA-198/BEAAA-225); operators do not author it ad-hoc.

## Exit codes — canonical (shipped semantics)

| Exit | Meaning | Operator action |
|---|---|---|
| `0` | Scan clean. JSON on stdout (or `--out`). | Consume `are_total` / `subscores` / `evidence_refs`. |
| `1` | `evidence_refs` incomplete — refuse-to-quote per Underwriter Hard Guarantee #1. | Inspect stderr for the gated dim list; backfill `evidence_uri` on the input dims; rerun. **Do not** synthesise evidence. |
| `2` | Invalid inputs — bad JSON, missing dim, raw out of `[0,4]`, path traversal, oversize file, malformed `agent_id`, scanner-version mismatch, symlink escape from `--input-dir`. **This is also the exit code for tamper rejects** (the inputs-look-malicious bucket). | Inspect stderr; fix the input; do not retry blind. If you suspect tampering rather than honest misformat, capture the input directory before retry. |
| `3` | Scanner internal error — unexpected exception inside `score()` or `run_scan`. | Capture stderr (includes traceback). File a Scanner Engineer ticket; do not retry against the same inputs without engineer eyes. |

> **Note on the BEAAA-481 issue body:** the v0.1 ownership-handoff ticket body lists exit codes as `1=usage error`, `2=runtime error`, `3=tamper detected`. The **shipped** semantics above (per ADR-0005 and `bin/are-scan:47–50`) supersede that summary. Tamper rejects land in the `2=invalid inputs` bucket because they fail at the input-validation boundary; there is no separate "tamper-detected" exit code today.

## Output shape (operator-visible fields)

Superset of `rubric.scoring.score()`'s response. The contract-stable fields you can rely on:

- `are_total` — alias for `bmi` (legacy internal name); contract callers should read `are_total`.
- `band` — letter band; `band_letter` carries the convenience string (e.g. `"B+"`).
- `subscores` — contract-named pillars (`OPS`, `BIAS_FAIRNESS`, `ROBUSTNESS`, `GOVERNANCE`) post-BEAAA-472.
- `evidence_refs` — flat list, one per dim.
- `uncovered_dimensions` — sorted N/A dim ids.
- `confidence` — deterministic stand-in: `1 − (N/A dim count / total dim count)`.
- `forensic_bundle_uri`, `snapshot_hash`, `scored_at`, `rubric_version`, `scanner_version`, `corpus_version`, `seed`, `model_versions`, `contract_version`, `scan_evidence_age_days`, `pillar_floors`.
- `input_file_sha256s` — `{filename: "sha256:..."}` over every input file the loader touched.
- `cli_meta` — provenance: tool name, issue id, input-dir name, presence flags.

> Post-BEAAA-472 the `subscores_contract_named` bridge view documented in ADR-0005 §"Known gap" is removed at source. Operators reading older snapshots may still see it; treat it as informational and rely on `subscores`.

## Reproducibility check (the N=5 invariant)

The Scanner Engineer runs N=5 on the G1 golden agent before every CLI merge, per `contracts/scanner-implementation-supervision-v1.md@v1.0` §"What the Operational seat guarantees" #2. Operators do not need to run this routinely; if you suspect drift, the smoke is reproducible from a checkout:

```sh
mkdir -p /tmp/are_scan_smoke_n5
cp tests/fixtures/golden_agents/G1/eval_outputs.json \
   /tmp/are_scan_smoke_n5/eval_results.json
SCORED_AT="2026-05-17T12:00:00+00:00"
for i in 0 1 2 3 4; do
  bin/are-scan --agent G1 --seed 42 --corpus 0.1.1 \
    --input-dir /tmp/are_scan_smoke_n5 \
    --scored-at "$SCORED_AT" \
    --out /tmp/are_scan_smoke_n5/run_$i.json
done
sha256sum /tmp/are_scan_smoke_n5/run_*.json
```

Expected: all 5 sha256s identical. Reference smoke: `tools/are-scan-smoke-post-beaaa470.md` (sha256 `24361151…`). Any deviation is a Scanner Engineer ticket.

## Tamper-resistance posture (do not weaken without sign-off)

Per ADR-0005 §"Tamper-resistance review":

- `agent_id` is regex-restricted; the CLI never shells out.
- Every input path is `resolve()`d and asserted under `--input-dir`; symlink escape → exit 2.
- Hard 10 MiB per-file cap on every read.
- `system_prompt.txt` is read as bytes and hashed; never executed by an LLM in Phase-1.
- Out-of-range `raw` values rejected by `_validate_eval_results` per the rubric contract.

Any change that weakens any of the above is a tamper-resistance reduction — Founder + CTO sign-off via ADR, per `scanner-implementation-supervision-v1.md` §"Approval gates".

## Roadmap pointers — v0.2

Scoped on BEAAA-481 (ownership handoff). v0.2 release scope:

| # | Item | Classification | CTO co-sign |
|---|---|---|---|
| 1 | `--output-format {json,ndjson,md}` | Rule-execution | No |
| 2 | `--evidence-bundle <path>` | Rule-execution | No |
| 3 | `--probe-set-version <semver>` pin | Rule-execution | No |
| 4 | `--targets-file <path>` batch mode | Rule-execution | No |

Deferred to separate co-sign rounds:

- **Audit-trail signing** on emitted `scan.json` — rule-change (adds a `signature` block to the envelope); CTO + Underwriter co-sign + ADR + contract version bump.
- **Drop `subscores_contract_named` bridge** — rule-change (output JSON shape); gated on BEAAA-472 landing `done`; CTO co-sign + ADR + contract version bump.

## Escalation

- Scoring drift / unexpected N=5 spread > 0 → Scanner Engineer (file a BEAAA ticket).
- Suspected tamper attempt against a real customer scan → Scanner Engineer + Founders (Founder escalation per `agents/d8bbc3b5-…/AGENTS.md` §"Escalate to Founders when").
- Rubric / weight / pillar-weight question → CTO (rule-change per the classification table); Founders for pillar weights.
- Contract-shape questions for the Underwriter consumer → Scanner Engineer files the CTO + Underwriter co-sign request.

## Audit hook

`bin/are-scan` activity flows through the same forensic stamps the in-agent path uses: `snapshot_hash` covers inputs + seed + corpus; `forensic_bundle_uri` references the persisted bundle; `input_file_sha256s` covers every loaded input. The Auditor's nightly drift check (`contracts/scanner-implementation-supervision-v1.md@v1.0` §"Audit hook") compares CLI-shipped probe wraps against in-agent equivalents to refuse duplicate-logic drift.
