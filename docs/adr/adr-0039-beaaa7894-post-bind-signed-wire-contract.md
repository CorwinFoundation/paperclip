# ADR-0039: Post-Bind Collector → Receiver Signed-Wire Contract (EdDSA per-bind public-key verification)

- Status: Accepted
- Date: 2026-06-23
- Deciders: CTO (decision), Scanner Engineer #3 (implementation)
- Tickets: BEAAA-7894 (decision/gate), BEAAA-7902 (implementation), BEAAA-7915 (canonical merge), BEAAA-7891 (originating security review)
- Implementation merged: `8bd171a` (reachable on `origin/master`)

## Context

The BEAAA-7891 post-bind collector security review found a release-blocking defect (F-01): the collector signed batch JWTs as `alg: EdDSA` with an Ed25519 private key, while the receiver verified only `alg: HS256`. A real collector-produced batch could never pass receiver verification — the collector → receiver path had never round-tripped. Related findings: the release entrypoint routed every `-source` through `AdapterFromFixture` despite documenting real source names (F-02), and absent an explicit private key the collector minted a fresh ephemeral signing identity per run, leaving the receiver no stable key to pin (F-03).

This path carries bound-customer post-bind events that feed `claim_candidate` records (monitor-data-contract-v0.1) toward Claims/Underwriting, so wire integrity and provenance matter.

## Decision

Adopt **EdDSA (Ed25519) with per-bind public-key verification** as the single signed-wire contract.

- **JWT header:** `{"alg":"EdDSA","typ":"JWT","kid":"<bind-key-id>"}`. `kid` is required in non-fixture mode.
- **Receiver:** require `alg == "EdDSA"`; reject `none`/`HS256` (no downgrade, no algorithm confusion). Resolve the Ed25519 public key from a per-bind keyring (`kid -> public key`); unknown or absent `kid` fails closed. Verify the Ed25519 signature over `header_b64 + "." + payload_b64`. All prior checks retained: claim-to-body binding, `exp` expiry, `issued_at` clock-skew, `batch_sha256` recomputation, redaction-conformance. `cryptography` added as a dependency.
- **Collector:** emit `kid`; in non-fixture mode require both the per-bind Ed25519 private key and `-key-id`, failing closed otherwise. Ephemeral keys are permitted only under explicit fixture/smoke mode with a reserved fixture `kid` a production keyring never contains.
- **Entrypoint:** relabeled fixture/local-smoke (`backbond.ai/collector-mode=fixture-smoke`); a real `-source` without a wired adapter errors rather than silently falling through to a fixture. Wiring real Datadog/Splunk/CloudWatch/OTEL adapters is deferred to a separate ticket.

### Why EdDSA over HS256 (the alternative)

1. **Breach posture.** The collector runs customer-side; with asymmetric signing the private key never leaves the customer and BackBond holds only public keys. A receiver/key-store compromise cannot forge customer batches. HS256 would force BackBond to hold a secret capable of minting customer batches.
2. **Non-repudiation** for the claim-candidate evidence chain — a signed batch provably originates from the bound customer's collector.
3. **Lower customer-side churn** (collector already emitted EdDSA) and a **smaller adversarial surface** (one pinned algorithm).

## Consequences

- Tamper resistance increases (asymmetric keys, single pinned algorithm, fail-closed key identity). No Underwriter gate: this is Monitor transport, not `are-scanner-api-v1`; the ARE output envelope is unchanged. N=5 golden calibration drift = 0.
- **Transmit remains Founder/Legal-gated** (BEAAA-7800, plus the BEAAA-7796 adversarial "nothing raw escapes" egress test). This contract makes the path correct for when transmit is later cleared; it does not enable transmit.
- Additive and reversible on the wire (new `kid` header, new receiver verify path). No persisted score, Underwriter contract, or transmit flag is touched.

## Verification

- `cd tools/post-bind-ingest && python3 -m pytest tests/test_receiver.py tests/test_cli.py tests/test_no_score_imports.py tests/test_collector_receiver_e2e.py -q` → 29 passed (includes the collector → `normalize_receiver_events` end-to-end round-trip proving F-01 closed).
- `cd tools/are-collector && go test ./...` → all packages ok.
- N=5 artifact: `tools/calibration-deltas/2026-06-23-beaaa7902-eddsa-signed-wire-n5.md` (max BackBond Score drift 0).
