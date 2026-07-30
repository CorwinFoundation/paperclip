---
artifact_type: calibration_delta
owner: scanner-engineer-2
persona_audience: internal
canonical_terms_only: true
focus_claim: "Add a pinned Numbat source driver without changing BackBond Scanner scores or contract-v1.0 output."
contracts_referenced: ["are-scanner-api-v1@v1.0", "scanner-implementation-supervision-v1@v1.1"]
ticket_refs: ["BEAAA-19596"]
scanner_version: "0.5.0"
corpus_version: "0.1.0"
probe-set-version: "golden_agents_v1"
rubric_point_id: "none — Passive Monitor ingestion only"
n5_calibration_delta: "0.0000 on every pillar and fixture by scoring-path non-interference"
upstream_eval_version: "github.com/perplexityai/numbat@3d20d782d45001fd3bb200bc5690ce4b9ce0f12b"
cto_cosign_ref: null
self_audit_passed: true
---

# BEAAA-19596 N=5 calibration delta

## BLUF

PASS. This rule-execution change is confined to `tools/are-collector` and adds no scoring, rubric, corpus, probe, model, judge, pricing, or Scanner API import. The BackBond Scanner N=5 delta is therefore `0.0000`; the T6 ruler-specific invariant is also `Δ=0`, demonstrated by five identical focused test transcripts and the golden Numbat fixture's identical canonical event bytes and batch hash on independent runs.

## Reproducibility evidence

Run five times:

```sh
cd tools/are-collector
go test ./internal/collector ./internal/source ./internal/egress
```

All five transcript SHA-256 values:

```text
02e345748cdbc118c2daf7714a51e69537367af707d88f9d0257cfa115947e04
```

Full Collector verification:

```sh
cd tools/are-collector
go test ./...
```

Result: all packages pass.

## Supply-chain pin

- Repository: `https://github.com/perplexityai/numbat`
- Commit: `3d20d782d45001fd3bb200bc5690ce4b9ce0f12b`
- Module tag: `v0.1.1`
- Toolchain: `go1.26.5`, `CGO_ENABLED=0`, `GOOS=linux`, `GOARCH=amd64`, `GOAMD64=v1`
- Command: `go build -trimpath -buildvcs=true -ldflags='-buildid=' -o numbat ./cmd/numbat`
- Approved binary SHA-256: `2e95ac4909db80bfb74aa77f6bed8104173d411a1614bd831e8e65484ef4ecde`
- Container image used for independent verification: `golang:1.26.5@sha256:3aff6657219a4d9c14e27fb1d8976c49c29fddb70ba835014f477e1c70636647`

The candidate does not vendor the Numbat binary, source, or raw records.

## Rollback

Remove Numbat driver registration and revert the dual-source fixture to synthetic-only. The contract-v1.0 receiver and post-bind ingestion paths remain unchanged.
