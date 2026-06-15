# ARE Scanner v0.1

ARE Scanner. 100-point governance and pricing engine for production AI systems.

## Phase 1 Scope (through end of May 2026)

- 4 pillars × 24 dimensions deterministic rubric (locked through 2026-04-28)
- Pillar weights (rubric 1.0.0, Founder-locked 2026-05-12): `OPS=25, BIAS_FAIRNESS=25, ROBUSTNESS=25, GOVERNANCE=25` (post-rename names per BEAAA-470 / ADR-0006; was `P1=35, P2=20, P3=30, P4=15` in v0.1.x draft)
- BEAAA-21 v0.2 API contract to the Underwriter (`/score`, `/forensic_bundle`)
- HTML-only Report Card render (BEAAA-13 rev 4 template)
- Hosted-eval-only intake (no on-prem connector this phase)
- Sequential pillar scoring (parallel deferred)

## Operating Mode

Mode A endorsed by CEO 2026-04-22 (BEAAA-29):
- Solo CTO + Claude Code execution
- Zero Phase 1 human hires
- Mode A→B trigger gate live (any §9.6 risk yellow → fire Staff ML Eval hire within 5 business days)

## Layout

```
rubric/         # Pillar/dim definitions, weights, scoring functions (WS1)
api/            # BEAAA-21 v0.2 OpenAPI surface (WS2)
policy/         # BEAAA-22 data-handling enforcement (WS3)
report_card/    # HTML render templates + generator (WS4)
tests/          # Synthetic fixtures, contract tests
```

## Red Lines

Snapshot integrity, `forensic_bundle_uri`, tenant isolation, audit log, pillar weights, dim count. Non-negotiable in any mode.

## Local setup after cloning

Install the repo-tracked git hooks once after clone (or after `git pull`
brings new hooks):

```
./bin/install-git-hooks.sh
```

This installs `.githooks/post-commit`, which auto-pushes each commit on
the current branch to `origin`. The hook reads `GITHUB_PAT_ARE_SCANNER`
from the environment via an inline credential helper, so no token is
written to disk. To skip the auto-push for a single commit, include
`[skip push]` in the commit message. The hook also skips during
in-flight rebase / cherry-pick / merge and on detached HEAD.

## Test Gate

Pull requests and pushes to `master` run `.github/workflows/tests.yml`,
which installs `requirements.txt` plus `requirements-dev.txt` and then
runs `python -m unittest discover -v tests`. Local development should use
the same command before pushing Scanner changes.

## Running scans from the CLI

`bin/are-scan` (BEAAA-464) lets you run a scan outside the CTO's agent
run — from CI, from a tunnel, from any internal script. It is a thin
wrapper over the same `rubric.scoring.score` engine the in-agent path
uses; the scoring math is identical (see `docs/adr-0005-bin-are-scan-cli.md`).

### Quick start

```sh
mkdir -p inputs/G1
cp tests/fixtures/golden_agents/G1/eval_outputs.json inputs/G1/eval_results.json

bin/are-scan --agent G1 --seed 42 --corpus 0.1.1 --out /tmp/G1_scan.json
echo "exit code: $?"            # 0 on a clean scan
jq '.are_total, .band_letter, .subscores' /tmp/G1_scan.json
# 84
# "B+"
# {"OPS": 20.3125, "BIAS_FAIRNESS": 19.2708, "ROBUSTNESS": 22.9167, "GOVERNANCE": 21.875}
```

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
| 2    | Invalid inputs (bad JSON, missing dim, raw out of [0,4], path traversal, oversize file, malformed `agent_id`, scanner-version mismatch). |
| 3    | Scanner internal error.                                          |

### Reproducibility guarantee

Per `agents/cto-os.md` Supreme Law and the Scanner contract Hard
guarantee #1: same agent + same `scanner_version` + same `corpus_version`
+ same `model_versions` + same seed → `are_total` within ±1 and every
subscore within ±1. With `--scored-at` pinned the CLI is byte-identical
across replicates (spread = 0, strictly stronger than ±1). Smoke tests:
`tools/are-scan-smoke-post-beaaa470.md` (current — post-rename) +
`tools/are-scan-smoke-2026-05-17.md` (pre-rename baseline, retained).

### Tamper resistance

The CLI treats all inputs as adversarial: regex-restricted `agent_id`,
no shell-out, no symlink follow-through outside `--input-dir`, 10 MiB
per-file read cap, strict JSON validation. See ADR-0005 §"Tamper-
resistance review" for the full table.

### Contract pin

The CLI emits the contract Output shape per
`contracts/are-scanner-api-v1.md@v1.0`. The internal rubric pillar keys
match the contract's pillar names 1:1 post-BEAAA-470 (rename per
ADR-0006): `OPS / BIAS_FAIRNESS / ROBUSTNESS / GOVERNANCE`. The
ADR-0005 `subscores_contract_named` bridge view is therefore removed
— `subscores` is contract-named at the source. Dim ids (`P<n>.<m>`)
stay verbatim and are intentionally orthogonal to the pillar-key
namespace.
