---
artifact_type: calibration_delta
owner: scanner-engineer
persona_audience: internal
canonical_terms_only: true
focus_claim: "Accept pinned Numbat v0.2.0 record-stream families in the Passive Monitor Collector without changing Scanner scores or the API contract."
contracts_referenced: ["are-scanner-api-v1@v1.0", "scanner-implementation-supervision-v1@v1.1"]
ticket_refs: ["BEAAA-19737"]
scanner_version: "0.5.0"
corpus_version: "0.2.0"
probe-set-version: "golden_agents_v1"
rubric_point_id: "none — Passive Monitor ingestion only"
n5_calibration_delta: "0.0000 on every golden agent by scoring-path non-interference"
upstream_eval_version: "github.com/perplexityai/numbat@3d20d782d45001fd3bb200bc5690ce4b9ce0f12b (record schema v0.2.0)"
cto_cosign_ref: null
self_audit_passed: true
---

# BEAAA-19737 N=5 calibration delta

## BLUF

PASS. This rule-execution change is confined to `tools/are-collector/internal/source`. It imports no Scanner scoring, rubric, corpus, probe, judge, pricing, or API-contract package, so the N=5 score delta is `0.0000` by construction.

## Verification

The focused Collector packages passed five independent uncached runs:

```sh
cd tools/are-collector
for i in 1 2 3 4 5; do
  go test -count=1 ./internal/collector ./internal/source ./internal/egress
done
```

The full Collector module also passed:

```sh
go test -count=1 ./...
git diff --check
```

Official-shape tests cover `event`, `finding`, `enforcement`, `indicator`, `scan_summary`, `diagnostic`, an unknown future family, missing identity/time failures, bounded endpoint projection, and the monitor-only enforcement marker. Existing legacy fixtures remain green.

## Safety boundary

Enforcement records are retained only as observations and carry `numbat.enforcement_observed_only=true`. The change does not infer host compliance, enable request-path blocking, or alter Evidence Profile v1 or `are-scanner-api-v1@v1.0` output.
