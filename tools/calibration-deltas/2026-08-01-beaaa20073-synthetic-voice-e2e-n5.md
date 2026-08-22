---
artifact_id: "BEAAA-20073-synthetic-voice-e2e-n5"
artifact_type: "calibration_delta"
persona_audience: "internal"
scanner_version: "0.5.0"
corpus_version: "0.2.0"
probe-set-version: "golden_agents_v1"
rubric_point_id: "n/a - Passive Monitor harness; no Scanner rubric point changed"
N=5_calibration_delta: "0 total; 0 every subscore; five E2E runs PASS with identical assertions/counts"
cto_cosign_ref: "n/a - rule execution"
contract_version: "contracts/are-scanner-api-v1.md@v1.3.0"
ticket: "BEAAA-20073"
---

# BEAAA-20073 synthetic voice E2E N=5 receipt

BLUF: the metadata-only synthetic voice harness exercises the existing Collector, contract-v1.0 receiver, post-bind ingestion, and Log Tracker interfaces without changing Scanner scoring or emitting transcript/audio/call content.

Classification: rule execution under `contracts/scanner-implementation-supervision-v1.md`, row "Customer onboarding harness for a new customer archetype." The harness uses the existing source-driver evidence type and does not change Collector core, contract output shape, scoring, judges, rubric, or tamper-resistance posture.

Verification:

- `go test ./internal/source ./internal/collector ./internal/egress ./cmd/synthetic-voice-source` - PASS.
- `PYTHONPATH=tools/post-bind-ingest/src:tools/log-tracker/src python3 -m pytest -q tools/post-bind-ingest/tests/test_synthetic_voice_e2e.py tools/post-bind-ingest/tests/test_passive_log_reader_e2e.py` - 2 passed.
- Five independent invocations of `run_synthetic_voice_e2e.py` - five PASS with identical report digest `45589921a2588674c7e5370a1ed5e832dd308676b69d632073b5d60acdfa66fc`; each emitted 3 signed sequential batches, 3 normalized determinations, and 3 Log Tracker rows; the recursive scan measured 331 nodes and zero violations on every run.
- R3 value-boundary probes reject charset-legal North American and E.164 phone numbers, transcript references, and caller-like identifiers with the explicit `prohibited voice content value` reason before batch signing; the E2E injection battery carries the same cases.

N=5 delta: zero. Candidate files do not touch Scanner score, rubric, corpus, probe, judge, model, seed, or golden inputs. The five runs additionally prove deterministic structural results across signing, chaining, replay rejection, tamper rejection, closed producer pins, the production continuity gate, determination persistence with commitment hashes, and measured recursive no-content rejection.

FAL audit: pass for this internal engineering artifact. Focus supports continuous evidence; alignment introduces no external promise; linkage cites the BackBond Scanner contract and Passive Monitor boundary. Carrier Rule clean. No external customer or platform named.

Rollback: revert the BEAAA-20073 candidate commit. Existing Collector, receiver, passive-log E2E, and Log Tracker paths remain unchanged.
