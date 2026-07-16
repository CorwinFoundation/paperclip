# ADR-0055 — BEAAA-12659 Reconcile diverged scanner lineages onto master

- Status: PROPOSED (CTO co-sign required before any merge — security-critical port)
- Date: 2026-07-08
- Owner: Scanner Engineer
- Supervision: CTO co-sign REQUIRED per `contracts/scanner-implementation-supervision-v1.md`
- Contract: `are-scanner-api-v1@v1.0` (producer-side tamper-resistance); no contract output-shape change
- Branch: `beaaa-12659-reconcile-provenance-on-master` (off `master` @ `a489565`)

## BLUF

The production edge (srv1749248) runs `35b28a1`, the HEAD of feature branch
`beaaa-10945-live-edge-provenance` — **19 commits ahead of `master`, 96 behind**,
with `.git` removed on the box. Investigation of all 19 divergent commits against
current `master` shows the divergence is **almost entirely already-on-master or
superseded**. The one genuinely net-new, deploy-relevant surface absent from
`master` is **hosted-runner provenance signing (BEAAA-10945 / SEC-6)**. The
security/auth/tenant-isolation lineage the ticket flagged as "must-port"
(BEAAA-10906 read-path auth + tenant isolation, BEAAA-10920 SEC-1/2/3/6) has
**already been re-implemented on master by a newer, stronger mechanism**
(`EdgeGateway` key store BEAAA-11867 + `_authenticated_tenant_id` / uw-entitlement
BEAAA-11858 + BEAAA-546 tenant isolation). **Porting the branch's `api/auth.py`
would REGRESS master** (static in-memory token registry ⟶ replacing a mutable,
revocable key store). We therefore port **provenance only** onto a fresh
master-based branch and produce a `.git`-intact, git-pinned deployable.

## The 19 commits — classification (all accounted for)

Buckets: **(a)** already-equivalent / superseded on master; **(b)** must-port
(net-new, deploy-relevant, absent from master); **(c)** obsolete / drop for this
reconciliation (a master-based deployable does not re-adopt branch-only scoring
experiments master's 96-commit line did not take — re-adoption, if ever wanted,
is a separate scoring decision for the CTO, out of scope here).

Evidence method: for each commit, checked whether its primary source payload
(not artifacts) exists on `master` (`git cat-file`), and diffed the security
surface (`git diff master <branch> -- <file>`) against merge-base `d331006`.

| # | SHA | Ticket / subject | Bucket | Evidence |
|---|-----|------------------|:------:|----------|
| 1 | cd17f14 | 8645 public exposure collector v0 | **c** | `scanners/public_exposure/*` absent on master; 0 tree hits — branch-only experiment not adopted |
| 2 | 3fef6f7 | 8720 validate scored evidence refs | **a** | `harness/contract_envelope.py` present on master (superseded by later evidence work) |
| 3 | e8394515 | 8880 align scanner v1 scan route | **a** | `tests/test_beaaa8880_scanner_v1_routes.py` + `api/openapi.yaml` present on master |
| 4 | dbf33e2 | 8881 canonical quote decision graph | **a** | `underwriting/automatic_decision.py` present on master |
| 5 | 796b1a2 | 8882 runtime production guards | **a** | `api/runtime_guards.py` present on master (base file; provenance guard added separately — see #19) |
| 6 | bf54c5b | 9342 harden receiver container path | **a** | `tools/post-bind-ingest/.../normalizer.py` present on master |
| 7 | 8a97fdc | 10195 repair P2.2 evidence URIs | **a** | `harness/contract_envelope.py` present on master |
| 8 | 0a468498 | 10483 add OSS evidence pack runner | **a** | `harness/oss_evidence_pack.py` + `scripts/oss_evidence_pack_v1.py` present on master |
| 9 | 4b6547f | 10488 repair golden N5 corpus gate | **a** | `scripts/release_gate_n5.py` + `tests/test_release_gate_n5_beaaa10187.py` present on master |
| 10 | 070d6ef | 10485 grounded-answer evidence module | **a** | `fixtures/support_contact_center/grounded_answer_v1.jsonl` present on master |
| 11 | f30084f | 10458 enforce fail-closed live egress | **a** | `harness/release_gate.py` + `scripts/check_v1_release_gate.py` present on master |
| 12 | e3c6f65 | 10641 support/voice action-authority fixtures | **c** | `fixtures/support_voice_action_authority_v1/*` absent; 0 tree hits — branch-only fixture pack |
| 13 | 0da283a | 10642 support voice transcript evidence | **a** | `fixtures/support_contact_center/support_voice_transcript_v1.jsonl` present on master |
| 14 | 9f88bc1 | 10643 support/voice four-pillar packet | **c** | `fixtures/support_voice_four_pillar_integration_v1/*` absent; 0 tree hits |
| 15 | 6a7e73f | 10674 authorized support-agent packet lane | **c** | `tools/support-agent-packet-lane/*` + runbook absent; 0 tree hits |
| 16 | b1f8006 | 10906 read-path auth + tenant isolation + audience gate | **a (superseded)** | `api/auth.py` absent on master **by design**: master re-implemented via `EdgeGateway` (11867) + `_authenticated_tenant_id`/`_uw_audience_entitled` (11858) + BEAAA-546 tenant isolation. Porting would regress. See "Why auth is NOT ported". |
| 17 | 886ab25 | 10920 close live authz regression SEC-1/2/3/6 | **a (superseded) + (b) provenance seed** | SEC-1/2/3/6 auth invariants enforced on master by the 11867/11858 edge (401-on-missing-bearer, credential-derived tenant, uw gate). The commit ALSO seeded the intake **provenance verifier** in `intake_guard.py`/`handlers.py` — that half is net-new and is ported (see #19). |
| 18 | 4fcfb8f | 10920 correct N=5 delta-doc header | **c** | Cosmetic delta-doc header fix on a branch-only artifact; drop. |
| 19 | 35b28a1 | 10945 hosted-runner provenance signing + key custody | **b (MUST-PORT)** | `provenance_sig` absent everywhere on master (`git grep` = 0). Net-new SEC-6 producer/verifier/custody surface. This is the reconciliation payload. |

Summary: **(a) 10 superseded/present · (b) 1 must-port (provenance, spanning #17 verifier-seed + #19) · (c) 5 obsolete/drop** (4 branch-only experiments + 1 cosmetic).

## Why the auth lineage is NOT ported (the load-bearing decision)

`master` HEAD `api/app.py` already enforces the BEAAA-10906/10920 invariants:
- **401 on missing/invalid bearer** — `_bearer_token()` → `authorization_bearer_required`.
- **tenant derived from credential, not `X-Tenant-Id`** — `_authenticated_tenant_id()` → `EdgeGateway.resolve_tenant(token)`; `_tenant_context()` rejects a header that disagrees with the auth context (409).
- **revocation** — the `EdgeGateway` key store resolves a revoked/expired key to 401 within cache-TTL, **no process restart** (strictly stronger than the branch's static in-memory `PrincipalResolver`).
- **uw-audience role gate** — `_uw_audience_entitled()` (BEAAA-11858).
- **tenant isolation** — `tests/test_beaaa546_tenant_isolation_reuse.py`, `tests/test_scan_handlers_beaaa547.py`, `tests/test_app.py` (all green on the reconciled branch).

Porting `api/auth.py` on top of this would replace a mutable revocable key store
with a static token map — a security regression. **Decision: drop `api/auth.py`;
keep master's edge auth.** SEC-1/2/3/6 parity is retained by master's mechanism.

## What IS ported (provenance — bucket b)

Additive, off the scoring path (`provenance_sig` never enters `engine_inputs` —
`handlers` projects only `raw`/`evidence_uri`), so golden N=5 delta is 0 by
construction and no rubric/scoring/band/output-shape/judge change occurs.

1. `api/intake_guard.py` — `ProvenanceSigner` / `EvalProvenanceVerifier` /
   `sign_eval_provenance` / `provenance_preimage` (HMAC-SHA256 over
   `dim_id|runner|eval_run_id`). Master == merge-base for this file, so ported
   wholesale (additive; existing `enforce_hosted_eval_only`/`enforce_input_safety`/
   `IntakeError` preserved, new `provenance_verifier` kwarg optional).
2. `api/runtime_guards.py` — one net-new guard: `ARE_EVAL_PROVENANCE_KEY`
   required in pilot/prod (same custody class as `ARE_BUNDLE_SIGNING_KEY`).
3. `api/handlers.py` — `score_handler` gains optional `provenance_verifier`,
   threaded to `enforce_hosted_eval_only`.
4. `api/app.py` — `create_app(provenance_verifier=...)` + `app.state` wired from
   `EvalProvenanceVerifier.from_env()`; passed into the `/score` route.
5. `scripts/beaaa2139_cutover_preflight.py` — provenance custody preflight
   (master == merge-base; ported wholesale).
6. `tests/test_beaaa10945_live_edge_provenance.py` — **rewritten** onto master's
   `X-Tenant-Id` edge (dropped the superseded `PrincipalResolver` coupling);
   `tests/test_runtime_guards_beaaa8882.py` fixtures updated to supply the new
   prod-required key + a positive missing-key guard test.

## Proof (attached)

- **Golden N=5:** `tools/calibration-deltas/2026-07-08-master-vs-beaaa-12659-reconciled.md`
  — **PASS, Δ0** on total and every pillar across the 8/9 baselined roster
  (B2 excluded per BEAAA-10199). (`.md` front-matter carries the known stale
  BEAAA-10453 auto-template boilerplate; the table + the reconciliation note
  are the truth.)
- **Security parity:** `tests/test_beaaa10945_live_edge_provenance.py` — 12/12
  pass: forged / absent / wrong-key `provenance_sig` on the keyed edge → **422**;
  correctly-signed → 200 with **byte-identical** `are_total`/`band`; pilot/prod
  refuses to start without `ARE_EVAL_PROVENANCE_KEY`. Tenant isolation retained
  (`test_beaaa546_*`, `test_scan_handlers_beaaa547`). 137 tests green on the
  affected+adjacent surface.
- **Deploy manifest:** `deploy/beaaa-12659-reconciled-manifest.md`.

## Consequences

- Production can be redeployed from a `.git`-intact, master-pinned checkout of the
  reconciled branch → scores cite a clean git SHA (Supreme-Law reproducibility).
- The manual live-edge snapshot at `35b28a1` is retired once this lands.
- **OUT of scope (downstream gates):** applying to the VM / provisioning secrets
  is the OPERATOR path (Founder/CEO-gated per BEAAA-10947/10950); CI/CD-to-VM is a
  follow-on. Bucket-(c) re-adoption, if ever wanted, is a separate CTO scoring call.

## Rollback

Pure repo work on a new branch; nothing touches the live VM. Abandoning the branch
is a no-op for production.
