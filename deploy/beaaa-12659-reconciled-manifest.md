# BEAAA-12659 — Reconciled Scanner Deploy Manifest

- Status: DRAFT (pending CTO close-gate + co-sign; then OPERATOR path, Founder/CEO-gated)
- Date: 2026-07-08
- Owner: Scanner Engineer
- Contract: are-scanner-api-v1@v1.0 (producer-side tamper-resistance)
- Supersedes: manual live-edge snapshot at `35b28a1` (branch beaaa-10945-live-edge-provenance, .git removed on box)

## BLUF
Git-pinned, `.git`-intact deployable that replaces the diverged manual live-edge
snapshot. Built from `master` + the BEAAA-10945 provenance port only (auth is
already on master via EdgeGateway/11867 — NOT re-ported; see ADR-0055).

## Pinned build
- Repo: `CorwinFoundation/are-scanner`
- Branch: `beaaa-12659-reconcile-provenance-on-master`
- Base: `master` @ `a489565a3336564355cf66905b8e7c868fef9259`
- SHA: `<filled at merge — the merge/commit SHA of the reconciled branch>`
- SCANNER_VERSION: `0.4.1`
- Rubric: `1.2.0`, locked_through `2026-08-04`
- Release-gate corpus_version: `0.2.0`
- Contract: `are-scanner-api-v1@v1.0`

## Required env (live edge / pilot|prod profile — runtime refuses to start if any absent)
| Key | Purpose | Custody |
|-----|---------|---------|
| `ARE_RUNTIME_PROFILE` | `pilot` or `prod` | config |
| `ARE_BUNDLE_SIGNING_KEY` | forensic bundle signing | secret |
| `ARE_EVAL_PROVENANCE_KEY` | **NEW (BEAAA-10945)** — HMAC key; hosted runner signs, edge verifies. Absent ⟶ intake verifier inert ⟶ forged hosted-run provenance admitted. Same custody class as bundle signing key; guard added in `api/runtime_guards.py`. | secret |
| `ARE_BUNDLE_STORAGE` / `ARE_BUNDLE_STORAGE_DIR` | durable (non-memory) bundle store | config |
| `ARE_CONNECTOR_PRIVATE_KEY_FILE` | connector key (0600 perms enforced) | secret |
| `ARE_EDGE_GATEWAY` | `on` to activate the D4 revocable key store + D3 rate limit (BEAAA-11867) | config |
| `ARE_API_AUTH_TOKENS_JSON` | legacy flat token→tenant map (only when gateway off) | secret |

Signer↔verifier lockstep: the hosted eval runner MUST sign each `eval_outputs`
dim entry with the SAME `ARE_EVAL_PROVENANCE_KEY` (via `ProvenanceSigner.from_env`)
or the keyed `/score` edge rejects all hosted evidence (422).

## Verification gates (all green on the reconciled branch)
- Golden N=5: `tools/calibration-deltas/2026-07-08-master-vs-beaaa-12659-reconciled.md` — PASS, delta 0.
- Security: `tests/test_beaaa10945_live_edge_provenance.py` 12/12 (forged/absent/wrong-key -> 422; signed -> byte-identical score; custody guard).
- Tenant isolation retained: `test_beaaa546_tenant_isolation_reuse`, `test_scan_handlers_beaaa547`.

## Out of scope (downstream, separate gates)
- Applying to the live VM / provisioning the secrets on the box = OPERATOR path,
  Founder/CEO-gated (BEAAA-10947/10950). Codex is the available operator.
- CI/CD-to-VM wiring = follow-on once this deployable exists.
