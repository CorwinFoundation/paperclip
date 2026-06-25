# BEAAA-5056 — ARE Probe SDK R1 internal build (v0.1)

**BLUF.** Built the R1 ARE Probe SDK to the shelf per Founder
directive `cto-internal-directive-2026-06-13` §3. End-to-end pipeline
works: SDK loads SHA-pinned corpus, runs deterministic refusal probes
against a target HTTP agent, wraps `bin/are-scan` (master v0.4.1), and
emits a v1.0-shaped envelope. 20/20 tests pass including N=5
byte-identical determinism on the fixture target. NO distribution
surface enabled — no public repo, no PyPI publish, no DP onboarding
doc. Shelf-ready. R2 unlocks distribution when the Founder lifts the
comms-freeze.

## Classification

* **Rule classification:** Rule-execution under `contracts/scanner-implementation-supervision-v1.md@v1.0`
  table row "Customer onboarding harness for a new customer archetype"
  (CMO + CBDO awareness, not CTO co-sign). R1 ships under a Founder
  directive; the directive is itself the unblock authority, no CTO
  co-sign required for the R1 implementation.
* **Contract anchor:** `contracts/are-scanner-api-v1.md@v1.0` (envelope
  shape is binding).
* **Founder authority:** `cto-internal-directive-2026-06-13` §3,
  recorded against parent BEAAA-5054. Carries the "BUILD, internal
  only, do not distribute until I lift the freeze" rider.

## Architecture

```
DP-side agent  <----HTTP probes----  are-probe CLI
                                          |
                                          | + SHA-pinned corpus
                                          v
                                    deterministic graders
                                          |
                                          v
                                    eval_results.json (25 dims)
                                          |
                                          v
                                    bin/are-scan (master v0.4.1)
                                          |
                                          v
                                    contract output JSON
                                          |
                                          v
                                    v1.0 envelope (+ probe_sdk block)
                                          |
                                          v
                              local file -> hand-back to IFA out-of-band
```

The SDK does NOT fork rubric, scoring math, judge models, or eval
frameworks. It wraps `bin/are-scan` per the supervision contract's
"wrap don't fork" rule. The only logic that lives inside R1 is:

1. Configuration parsing (`config.py`).
2. SHA-verified corpus loading (`corpus.py`).
3. HTTP probe invocation against the target (`target.py`, urllib stdlib only).
4. Deterministic keyword-based grader for the refusal_robustness family
   (`probes/refusal_robustness.py`). No LLM judge.
5. `eval_results.json` assembly with explicit `source` field per dim
   (`measured` | `dp_attested` | `r1_placeholder`).
6. Subprocess wrap of `bin/are-scan` (`scan_wrapper.py`).
7. Envelope assembly + optional Ed25519 portal CA signature stub
   (`envelope.py`, `signer.py`).

R1 deliberately omits per-provider HTTP adapters (OpenAI / Anthropic /
OpenRouter), LLM-judge probes, DP-signed envelopes, and any external
distribution surface. See `DISTRIBUTION_READINESS_CHECKLIST.md` for the
R2 checklist; each unchecked box becomes a child ticket when the freeze
lifts.

## Files shipped

| Path | Bytes | sha256 |
|------|------:|--------|
| `engineering/probe-sdk/README.md` | see disk | — |
| `engineering/probe-sdk/DISTRIBUTION_READINESS_CHECKLIST.md` | see disk | — |
| `engineering/probe-sdk/pyproject.toml` | see disk | — |
| `engineering/probe-sdk/lockfile.toml` | see disk | — |
| `engineering/probe-sdk/src/are_probe/__init__.py` | — | — |
| `engineering/probe-sdk/src/are_probe/canonical.py` | — | — |
| `engineering/probe-sdk/src/are_probe/config.py` | — | — |
| `engineering/probe-sdk/src/are_probe/corpus.py` | — | — |
| `engineering/probe-sdk/src/are_probe/target.py` | — | — |
| `engineering/probe-sdk/src/are_probe/probes/__init__.py` | — | — |
| `engineering/probe-sdk/src/are_probe/probes/refusal_robustness.py` | — | — |
| `engineering/probe-sdk/src/are_probe/eval_results_builder.py` | — | — |
| `engineering/probe-sdk/src/are_probe/scan_wrapper.py` | — | — |
| `engineering/probe-sdk/src/are_probe/signer.py` | — | — |
| `engineering/probe-sdk/src/are_probe/envelope.py` | — | — |
| `engineering/probe-sdk/src/are_probe/cli.py` | — | — |
| `engineering/probe-sdk/bin/are-probe` (executable) | — | — |
| `engineering/probe-sdk/config/are-probe.yaml.example` | — | — |
| `engineering/probe-sdk/corpus/refusal_robustness_v0.1.jsonl` | — | `ef919312913e9e19197427faf4ba54ade3788e446dcc7fa16960ced92fb2b3a5` |
| `engineering/probe-sdk/fixtures/fixture_target.py` | — | — |
| `engineering/probe-sdk/fixtures/sample_envelope_v0.1.json` | — | `b82ee68ff0719a5f887965a6a3f2e1c13e7a3c1b2736115c11fc6d8e91d2a42c` |
| `engineering/probe-sdk/tools/compute_build_hash.py` | — | — |
| `engineering/probe-sdk/tests/conftest.py` | — | — |
| `engineering/probe-sdk/tests/test_config.py` | — | — |
| `engineering/probe-sdk/tests/test_corpus_sha_pinning.py` | — | — |
| `engineering/probe-sdk/tests/test_envelope_schema.py` | — | — |
| `engineering/probe-sdk/tests/test_refusal_grader.py` | — | — |
| `engineering/probe-sdk/tests/test_n5_determinism.py` | — | — |

(The lockfile holds the canonical pin set; per-module shas are tracked
inside the `build_hash` derivation in `tools/compute_build_hash.py` so
the on-disk file modes match the canonical layout.)

## Pinned versions (R1)

* `probe_sdk_version`: 0.1.0
* `probe_sdk_release`: R1
* `contract_version`: `are-scanner-api-v1.md@v1.0`
* `scanner_master_expected_version`: 0.4.1 (master at
  `/tmp/are-scanner-master-d801736`)
* Python: 3.11+ required, 3.12.3 verified
* `PyYAML==6.0.1`, `cryptography==41.0.7` — only third-party deps
* Corpus: `refusal_robustness_v0.1` @
  sha256 `ef919312913e9e19197427faf4ba54ade3788e446dcc7fa16960ced92fb2b3a5`
* Signing key (when `--sign` is opted in):
  `cto-attest-v1`, sha256 fp `3ec6c4579ec62902b07ca720295da0bdc1727729acb1899c7145e4b167a22e29`
* `build_hash`:
  `97294bcb488ee33005b3d080c36c531938ed4a9a3669145bb0c00f61f8f22686`
  (sha256 over canonical-json(pinned-deps + module shas + corpus shas)
  computed by `tools/compute_build_hash.py`)

The build_hash is the single anchor an auditor uses to confirm an
envelope was produced by this exact R1 cut.

## Reproducibility evidence

Acceptance criterion: spread = 0 on `are_total` + every pillar
subscore across N=5 runs on the fixture target with the same seed.

Test: `engineering/probe-sdk/tests/test_n5_determinism.py`.

Result on `2026-06-13` against
`/tmp/are-scanner-master-d801736/bin/are-scan` (master v0.4.1):

* All 5 envelopes byte-identical with `freeze_timestamp=True`.
* `are_total` spread = 0 (value: 64).
* OPS spread = 0 (value: 15.625).
* BIAS_FAIRNESS spread = 0 (value: 15.625).
* ROBUSTNESS spread = 0 (value: 17.1875).
* GOVERNANCE spread = 0 (value: 15.625).

Sample envelope at `engineering/probe-sdk/fixtures/sample_envelope_v0.1.json`
(sha256 `b82ee68ff0719a5f887965a6a3f2e1c13e7a3c1b2736115c11fc6d8e91d2a42c`).

Full test suite: 20/20 passing —

```
tests/test_config.py ............. 5
tests/test_corpus_sha_pinning.py .. 2
tests/test_envelope_schema.py ..... 5
tests/test_n5_determinism.py ...... 3 (N=5 are_total, pillar spread, byte equality)
tests/test_refusal_grader.py ...... 5
```

## Envelope schema validation

`engineering/probe-sdk/src/are_probe/envelope.py::assert_v1_output_shape`
verifies every field required by `contracts/are-scanner-api-v1.md@v1.0`
Output section:

* `are_total ∈ [0, 100]`
* `subscores` has OPS / BIAS_FAIRNESS / ROBUSTNESS / GOVERNANCE
* `band ∈ {A,B,C,D}` (accepted as letter or as the master's richer
  `{letter, modifier, ...}` object; envelope normalizes to letter and
  preserves the object under `band_detail` so the Underwriter still
  sees the modifier signal)
* `scanner_version`, `corpus_version`, `model_versions`, `seed`,
  `run_timestamp` all present

Schema check runs on every envelope build; failure raises before any
file is written.

## Distribution-readiness checklist

`engineering/probe-sdk/DISTRIBUTION_READINESS_CHECKLIST.md` is authored
and **not actioned**. It enumerates the gates R2 must clear:

* Distribution surface (repo / PyPI / portal tarball)
* Redistributable corpus subset
* Multi-model client adapters
* DP-signed envelopes + key provisioning
* DP-facing docs
* Telemetry posture decision
* Security review
* Legal review
* CMO + CTO sign-off
* Founder lift of comms-freeze (gating)

Each box becomes a child ticket once the Founder lifts the freeze.

## Founder freeze invariants enforced in code

* `telemetry.enabled: true` in `are-probe.yaml` is **hard-rejected** by
  `config.py` with an explicit reference to the freeze directive. The
  R1 SDK cannot be misconfigured to phone home.
* The SDK ships **only** to `engineering/probe-sdk/` inside the
  workspace; no install metadata points at an external index. PyPI
  upload would require a positive action in R2.
* The portal CA signer is **opt-in** via `signing.sign: true`. R1
  defaults to unsigned envelopes; the IFA portal counter-signs on
  ingest. DP-signed mode lands in R2.
* No customer naming, carrier naming, or premium quotes anywhere in
  the shipped surface. Carrier Rule held.

## What R1 explicitly does not promise

* R1 does **not** produce a meaningful score on a real customer agent
  outside the refusal-robustness family. Other dims fall back to
  `r1_placeholder` (raw=2.5) or DP-attested raws supplied via
  `--dp-attestations`. The envelope's `uncovered_dimensions` field
  enumerates which dims are placeholder vs. measured, and the
  per-dim `evidence_uri` is prefixed `are-probe://placeholder/` so the
  auditor and Underwriter can refuse-to-quote based on the existing
  contract guarantees (Hard guarantee §2 evidence trail completeness,
  Underwriter §2 uncovered dimensions priced at maximum risk).
* R1 does **not** ship multi-model client adapters. The target HTTP
  shape is generic POST-JSON; a DP wraps their provider call in a
  thin adapter today and gets a first-class adapter in R2.
* R1 does **not** counter-sign at the portal; the portal CA stub is
  local-only. R2 wires the counter-sign into the portal `/ingest`
  surface.

## How to run (internal only)

```bash
# Master scanner must be on disk; SDK invokes it via subprocess.
export ARE_SCAN_BIN=/tmp/are-scanner-master-d801736/bin/are-scan

# (Optional) start the fixture target in another shell for smoke runs.
python3 engineering/probe-sdk/fixtures/fixture_target.py

# Run a probe.
PYTHONPATH=engineering/probe-sdk/src python3 -m are_probe.cli \
    --config engineering/probe-sdk/config/are-probe.yaml.example \
    --target http://127.0.0.1:18077/v1/chat \
    --agent are-probe-r1-smoke \
    --out /tmp/r1-envelope.json
```

## What R2 needs to add

See `DISTRIBUTION_READINESS_CHECKLIST.md`. Highest-leverage items in
order of next ticket priority:

1. Redistributable corpus subset (licensing + signed pack).
2. Multi-model client adapters (OpenAI / Anthropic / OpenRouter).
3. DP-signed envelopes + portal counter-sign on ingest.
4. DP-facing onboarding doc + operations runbook.
5. Vendor scoring snapshot so SDK has no `/tmp/` master dependency.

## Metadata block (per artifact-metadata-schema.md)

* `scanner_version`: `0.4.1` (master, wrapped by R1 SDK)
* `corpus_version`: `0.1.0` (R1 SDK refusal_robustness pack)
* `probe-set-version`: `0.1.0`
* `rubric_point_id`: `P3.1` (Direct prompt-injection resistance);
  corpus also tags fully-qualified
  `ROBUSTNESS.REFUSAL.DONOTANSWER_SMOKE` for R2 evidence-trail use
* `upstream_eval_version`: N/A (R1 ships a stdlib-only deterministic
  grader; no upstream eval-framework wrapped yet)
* `N=5 calibration delta`: spread = 0 on are_total + every pillar
  subscore against fixture target. Captured in
  `tests/test_n5_determinism.py` and replayable via
  `PYTHONPATH=engineering/probe-sdk/src:engineering/probe-sdk/fixtures
  python3 -m pytest engineering/probe-sdk/tests/test_n5_determinism.py -q`
* `cto_cosign_ref`: N/A (rule-execution per supervision contract;
  Founder directive `cto-internal-directive-2026-06-13` §3 is the
  unblock authority)
* `classification_table_row`: "Customer onboarding harness for a new
  customer archetype" — Rule execution
* `escalations_noted`:
  - Master scanner returns `band` as `{letter, display, modifier, ...}`
    object rather than the simple letter the v1.0 contract specifies.
    Envelope assembler accepts both shapes; preserves the master shape
    under `band_detail`. Surfacing for CTO contract-shape clarification
    (envelope continues to ship until clarified — the assembler's
    letter normalization is contract-compatible).
  - Master scanner pinned at `/tmp/are-scanner-master-d801736` — not
    a stable mount across machines. R2 vendor-snapshots the scoring
    engine into the SDK so distribution doesn't depend on `/tmp/`.
  - `rubric_point_id` mapping in R1 uses legacy `P3.1` for refusal-
    robustness; the fully-qualified
    `ROBUSTNESS.REFUSAL.DONOTANSWER_SMOKE` is tagged in the corpus
    pack metadata and surfaces in evidence_refs for R2 dim-mapping
    consistency.

## References

* `contracts/are-scanner-api-v1.md@v1.0` — envelope shape binding.
* `contracts/scanner-implementation-supervision-v1.md@v1.0` — supervision
  contract; "Customer onboarding harness for a new customer archetype"
  classification row.
* Parent: BEAAA-5054 (Founder directive
  cto-internal-directive-2026-06-13 §3).
* `BEAAA-4694` — design-partner umbrella; R1 is the internal precursor
  to the R2 distribute-ready cut.
* `engineering/probe-sdk/DISTRIBUTION_READINESS_CHECKLIST.md` — R2
  prerequisites.
