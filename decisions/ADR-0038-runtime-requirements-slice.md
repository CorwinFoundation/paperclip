---
artifact_type: adr
owner: cto
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "This ADR resolves the unsatisfiable production venv by building it from a new requirements-runtime.txt (the live online-service import closure) that EXCLUDES the four offline-only eval frameworks, eliminating the garak/pyrit datasets conflict without moving any live score or changing the service's runtime behavior."
contracts_referenced: ["are-scanner-api-v1@v1.3.0", "scanner-implementation-supervision-v1@v1.1"]
ticket_refs: ["BEAAA-13329", "BEAAA-12895", "BEAAA-12668"]
proof_points_cited: ["import-closure grep: garak/pyrit/agentdojo/inspect_ai imported only in scanners/{pyrit,agentdojo,inspect}_runner.py (soft try/except ImportError) + tests; garak never imported in Python (promptfoo Node path)", "live modules import with the four frameworks absent (api.app + scanners.live_probe_* + score_runtime + customer_agent_walker + harness.runner/customer_runner/credentials/egress)", "requirements-scanner-regression.txt already documents garak(datasets<2.17) vs pyrit(datasets>=3.0.0) as unsatisfiable", "operator uv/pip resolver proof: clean venv build on bba07f1 fails on garak/pyrit datasets"]
version: 0.1
prior_version: null
self_audit_passed: true
escalations_noted: ["No rubric weight, scoring math, judge model, corpus, band, output-shape, or tamper-resistance change -> no Underwriter co-sign or Founder rubric gate. Rule-execution of this Accepted ADR per scanner-implementation-supervision-v1; PR requests CTO review of the slice contents before merge. Byte-identical-envelope proof on a golden agent attached per Supreme Law even though no live score can move."]
scanner_version: v0.5.0
corpus_version: "0.1.0"
probe_set_version: unchanged
rubric_point_id: N/A (packaging change; no rubric point touched)
n5_calibration_delta: "0 (byte-identical envelope; see Verification)"
cto_cosign_ref: "ADR-0038 Accepted on BEAAA-12895 comment c7e3c803 (CTO design call)"
---

# ADR-0038: split a production runtime-requirements slice out of the full eval-framework release contract

## Status

**Accepted** (CTO design call, BEAAA-12895 comment `c7e3c803`). Implemented as
rule-execution under BEAAA-13329 (Scanner Engineer). Production remains
untouched on the interim `76953a0` runtime until the operator does one clean
cutover to the merged fixed tip.

## Context

`requirements.txt` is unsatisfiable in a single venv. It pins two offline eval
frameworks whose transitive constraints are mutually exclusive:

- `garak==0.10.0` requires `datasets<2.17`
- `pyrit==0.6.0` requires `datasets>=3.0.0`

`garak ⊥ pyrit` on `datasets`. No pin bump resolves the full graph — this is
structural, not incidental (`requirements-scanner-regression.txt` already
records the same conflict and installs only a non-conflicting slice for the
golden regression job). The operator's `uv`/`pip` resolver proof on tip
`bba07f1` confirms a clean production venv is **not buildable** from
`requirements.txt` as pinned. Per the runbook rule this was returned as a
pinning bug rather than hand-fixed on the box. This is the only thing gating
the clean operator cutover for BEAAA-12895 / BEAAA-12668.

## Decision

Build the production venv from a new **`requirements-runtime.txt`** — the
online-service dependency contract derived from the *live* import closure —
that EXCLUDES the four offline-only eval frameworks
(`garak`, `pyrit`, `agentdojo`, `inspect-ai`). `requirements.txt` is retained,
annotated as the full eval-framework **release-contract superset** that is not
co-installable in one venv by design.

### Why excluding the four frameworks is provably safe (import-closure analysis)

1. The live API (`api/*`) and the live customer-scan chain
   (`scanners.live_probe_orchestrator/workers`, `score_runtime`,
   `customer_agent_walker`,
   `harness.runner/customer_runner/credentials/egress`) import **none** of
   `garak/pyrit/agentdojo/inspect_ai`. They are imported only by
   `scanners/{pyrit,agentdojo,inspect}_runner.py` (offline runners) plus one
   test. `garak` is never imported in Python at all — it is driven via the
   promptfoo Node CLI subprocess.
2. The one live-adjacent top-level import (`harness/pipeline.py` →
   `pyrit_runner`) is **not** in the live runtime closure — nothing the service
   imports pulls `harness.pipeline`.
3. All three offline runners **soft-import**
   (`try/except ImportError → _AVAILABLE=False`); absence degrades gracefully.

⇒ A production venv without the four frameworks cannot change a live customer
score (the live path never invokes them) and cannot crash module load. Same
git-clean checkout, same bytecode, leaner venv omitting only packages the
production process never imports.

### Two real gaps the slice fixes (surfaced by the closure analysis)

- `fastapi` (+ `uvicorn`) were pinned only in `requirements-dev.txt`, but the
  prod service imports `fastapi` (`api/app.py`) and runs under `uvicorn`
  (`deploy/cto_live_edge_boot.py`) → promoted to first-class runtime pins
  (`fastapi==0.136.1`, `uvicorn==0.46.0`). `starlette`/`pydantic` are left to
  fastapi's transitive constraint (`starlette>=0.46.0`).
- `hugchat` is in the runtime closure via the HuggingChat customer-agent
  adapter in `harness/runner.py` but was unpinned anywhere → pinned
  (`hugchat==0.5.1`; pulls only `requests`/`requests_toolbelt`, no `datasets`).
- The always-on PII substrate (`llm-guard`, `presidio-analyzer`,
  `presidio-anonymizer`, `spacy`, `transformers`) stays in the slice; none of
  them touch `datasets`, so they do not reintroduce the conflict.

## Guardrail against silent regression

BEAAA-13329 adds a CI import-closure guard
(`tests/test_runtime_import_closure_beaaa13329.py`, wired into
`.github/workflows/pr-unit-tests.yml` as `runtime-slice-import-closure`): build
a venv from `requirements-runtime.txt` only, import the live modules, assert
success AND assert the four frameworks are absent. This keeps the slice a true
superset of the live closure going forward. If a live dependency is dropped, the
import fails; if an eval framework leaks back in, the absence assertion fails.

## Decision authority

Scanner architecture + evals tooling + deployment packaging = CTO-unilateral. No
rubric weight / scoring logic / corpus / UW-API / band / output-shape / tamper
change → no Underwriter co-sign and no Founder rubric gate. Rule-execution of
this Accepted ADR per `scanner-implementation-supervision-v1`; the PR requests
CTO review of the slice contents before merge.

## Verification (BEAAA-13329 acceptance)

- `python -m venv` from `requirements-runtime.txt` resolves + installs clean on
  a fresh box (no `datasets` conflict) — exercised by the
  `runtime-slice-import-closure` CI job and the operator venv-build acceptance.
- Import-closure guard test passes in CI.
- Service boots under `uvicorn` from the slice venv; local + public health green
  (operator step on the cutover box).
- One staging/non-prod scan writes a fresh `scan_log` row with non-null
  `forensic_bundle_uri` + `snapshot_hash`.
- Envelope from the slice-built runtime is byte-identical to the interim
  `76953a0` runtime on a golden agent (Supreme Law evidence; N=5 delta = 0 by
  construction — the live scoring path imports none of the removed packages).

## Consequences

- Production installs `requirements-runtime.txt`; CI regression installs
  `requirements-scanner-regression.txt`; dev/test installs `requirements-dev.txt`;
  `requirements.txt` remains the full release-contract superset.
- Offline calibration/CI materializes `garak`/`pyrit`/`agentdojo`/`inspect-ai`
  as per-framework subprocess venvs (non-blocking follow-up the CTO tracks
  separately; not on the cutover critical path).
- Reversible packaging change only. Rollback anchor:
  `/opt/are-scanner-rollback/20260710T165218Z-pre-bba07f1`.
