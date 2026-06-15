# ARE Scanner

The ARE Scanner is BackBond's 100-point Agent Risk Exposure scoring
engine for production AI agents. Output feeds the Underwriter (pricing)
and Claims systems; every score ships with a complete evidence trail
and a reproducibility guarantee inside ±1 point.

Canon terms used in this repo: **ARE** for the scanner and the score
itself; **BackBond Score** / **BackBond Report** as the external
display surface for the score and the report card.

## Current versions (canon, in-flight on `master`)

- Scanner: `0.4.2` (`SCANNER_VERSION` in `scanners/__init__.py`)
- Rubric: `1.2.0`, locked through `2026-06-30` (`rubric/rubric.yaml`)
- Contract: `are-scanner-api-v1@v1.3.0`
  (`contracts/are-scanner-api-v1.md`; v1.0.x/v1.1.0/v1.2.0/v1.3.0
  cumulative; v1.4.0 + v1.5.0 patch files live alongside the master
  contract — bump on activation per the supervision contract).
- Scoring sheet: 4 pillars × 25 active dimensions
  (OPS=6, BIAS_FAIRNESS=6, ROBUSTNESS=6, GOVERNANCE=7).

## Phase 1 scope

- Pillar weights (Founder-locked 2026-05-12):
  `OPS=25, BIAS_FAIRNESS=25, ROBUSTNESS=25, GOVERNANCE=25`. Pre-rename
  weights (`P1=35, P2=20, P3=30, P4=15`) are retired (BEAAA-470 /
  ADR-0006).
- Deterministic rubric; per-rubric-point named-curve registry
  (ADR-0027); PII dims activated under ADR-0022 / ADR-0025
  (ROBUSTNESS.PII.SCAN_TIME_DETECTION substrate + GOVERNANCE.PII.
  HANDLING_ATTESTATION attestation pair).
- HTML Report Card render (`report_card/`).
- Hosted-eval-only intake; sequential pillar scoring (parallel
  deferred).

## Operating mode

- No human engineers. CTO is a Paperclip agent; all implementation is
  delegated to engineering agents (Scanner Engineer #1..#3) under
  the supervision contract `agents/scanner-implementation-supervision-v1.md`.
- Every rule-change merge carries the `N=5` calibration delta against
  the golden roster (B1/B3/G1/G2/G3 + backstops M1/M2/M3) per the CTO
  Supreme Law (±1 on `are_total` and every pillar subscore).
- Approval gates: any rubric weight change, any scoring-math change,
  any contract field change → Underwriter co-sign required; pillar
  weights / golden moves > ±1 → CEO + Founder ratification.

## Red lines

Snapshot integrity, `forensic_bundle_uri`, tenant isolation, audit log,
pillar weights, dim count, reproducibility variance band, tamper
resistance. Non-negotiable.

## Layout

```
rubric/         # Pillar/dim definitions, weights, calibration curves, scoring engine
api/            # are-scanner-api-v1 surface, scan handlers, score runtime
scanners/       # Pillar/dim probe runners, judges, model_versions registry
harness/        # Bench harness + detector wrappers (LLM Guard, Presidio, AgentDojo)
corpus/         # Probe-pack manifests + corpus snapshots (pinned by sha)
contracts/      # are-scanner-api-v1.md + supervision contracts (canon)
docs/adr/       # Architecture decision records (ADR-0010 .. ADR-0030; ADR-0001..0009 sit one level up at docs/)
report_card/    # HTML render templates + generator
pricing/        # Score → premium translation (Underwriter-facing)
underwriting/   # UW-side helpers + evidence projections
audits/         # Audit-trail outputs + Auditor agent inputs
engineering/    # Probe SDK + engineering scaffolding
infra/          # Dist + sandbox deploy targets
scripts/        # Release-push + ops scripts
tools/          # Calibration deltas + CLI smokes + ops tooling
tests/          # Synthetic fixtures, contract tests, regression suite
bin/            # are-scan CLI + install-git-hooks.sh
inputs/         # Per-agent scan inputs (gitignored by default for customer data)
```

## Local setup after cloning

Install the repo-tracked git hooks once after clone (or after `git pull`
brings new hooks):

```
./bin/install-git-hooks.sh
```

This installs `.githooks/post-commit`, which auto-pushes commits on
allowed agent feature branches (`beaaa-*` and `feature/beaaa-*`) to
`origin`. The hook reads `GITHUB_PAT_ARE_SCANNER` from the environment
via an inline credential helper, so no token is written to disk. To skip
the auto-push for a single commit, include `[skip push]` in the commit
message. The hook also skips during in-flight rebase / cherry-pick /
merge, on detached HEAD, on branches outside the allow-list, and on
guarded branches (`master`, `main`, `release/*`, or branches marked with
`branch.<name>.protected=true` / `protected:` in the branch description).

Release-branch pushes are explicit:

```
BACKBOND_RELEASE_PUSH_APPROVER_ROLE=CTO \
BACKBOND_RELEASE_PUSH_REF=<ticket> \
./scripts/push_release.sh
```

## Test gate

Pull requests and pushes to `master` run `.github/workflows/tests.yml`,
which installs `requirements.txt` plus `requirements-dev.txt` and then
runs `python -m unittest discover -v tests`. Local development should
run the same command before pushing Scanner changes. Calibration
regression on the golden roster runs at the merge gate per
`agents/scanner-implementation-supervision-v1.md`; outputs land under
`tools/calibration-deltas/`.

## Running scans from the CLI

`bin/are-scan` (BEAAA-464) lets you run a scan outside the CTO's agent
run — from CI, from a tunnel, from any internal script. It is a thin
wrapper over the same `rubric.scoring.score` engine the in-agent path
uses; the scoring math is identical (see
`docs/adr-0005-bin-are-scan-cli.md`).

### Quick start

```sh
mkdir -p inputs/G1
cp tests/fixtures/golden_agents/G1/eval_outputs.json inputs/G1/eval_results.json

bin/are-scan --agent G1 --seed 42 --corpus 0.1.1 --out /tmp/G1_scan.json
echo "exit code: $?"            # 0 on a clean scan
jq '.are_total, .band_letter, .subscores' /tmp/G1_scan.json
```

The CLI prints `are_total`, `band_letter`, and per-pillar `subscores`
to the chosen `--out`. Exact values move with `scanner_version` and
`corpus_version`; pin both when comparing across runs.

### Input directory layout

Default `--input-dir` is `./inputs/<agent-id>/`. Only `eval_results.json`
is required; the rest are optional artifacts hashed into the output as
evidence references.

| File                              | Required | Shape                              |
|-----------------------------------|----------|-------------------------------------|
| `eval_results.json`               | yes      | `{P<n>.<m>: {raw, evidence_uri}}`   |
| `system_prompt.txt`               | no       | UTF-8 text                          |
| `tool_inventory.json`             | no       | list of tool objects                |
| `policy_attestations.json`        | no       | object                              |
| `governance_attestations.json`    | no       | object                              |
| `model_versions.json`             | no       | `{primary, judges[]}`               |
| `scanner_invocation_context.json` | no       | `{purpose, requested_pillars[]}`    |

### Exit codes

| Exit | Meaning                                                          |
|------|------------------------------------------------------------------|
| 0    | Scan clean.                                                      |
| 1    | `evidence_refs` incomplete (refuse-to-quote per the Scanner contract Hard guarantee #1). |
| 2    | Invalid inputs (bad JSON, missing dim, raw out of `[0,4]`, path traversal, oversize file, malformed `agent_id`, scanner-version mismatch). |
| 3    | Scanner internal error.                                          |

### Reproducibility guarantee

Per `agents/cto-os.md` Supreme Law and the Scanner contract Hard
guarantee #1: same agent + same `scanner_version` + same
`corpus_version` + same `model_versions` + same seed → `are_total`
within ±1 and every subscore within ±1. With `--scored-at` pinned the
CLI is byte-identical across replicates (spread = 0, strictly stronger
than ±1). Smoke tests live under `tools/are-scan-smoke-*.md`.

### Tamper resistance

The CLI treats all inputs as adversarial: regex-restricted `agent_id`,
no shell-out, no symlink follow-through outside `--input-dir`, 10 MiB
per-file read cap, strict JSON validation. See ADR-0005 §"Tamper-
resistance review" for the full table.

### Contract pin

The CLI emits the contract Output shape per
`contracts/are-scanner-api-v1.md@v1.3.0`. The internal rubric pillar
keys match the contract pillar names 1:1 post-BEAAA-470 (rename per
ADR-0006): `OPS / BIAS_FAIRNESS / ROBUSTNESS / GOVERNANCE`. Dim ids
(`P<n>.<m>`) stay verbatim and are intentionally orthogonal to the
pillar-key namespace. Hard guarantee #3 (envelope `model_versions`
populated) is deferred from the v0 synthetic-walker production path
to the v1 real-adapter wrap-ticket — v0 envelopes legitimately emit
`model_versions: null` until the real-adapter walker lands (BEAAA-5414,
ADR-0030 PROPOSED on master).
