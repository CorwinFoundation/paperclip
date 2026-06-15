---
artifact_type: adr
owner: se
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "Customer-true Track B walker (ARE_WALKER_CUSTOMER_AGENT) replaces the synthetic_v0 ProbeRunFn stub on the production scan path with a walker that drives the bundle's pinned probe families against the customer's agent_endpoint, flips applicability_decisions provenance from synthesized N/A (CASE 1) to customer-supplied (CASE 2), and stays inside the existing tamper-resistance posture (ScopedEgressAllowlist(agent_endpoint_host) on the Prober; ScopedEgressAllowlist([]) deny-all on the Scorer; vault never crossing the IPC). Status PROPOSED pending CTO design co-sign + UW op-seat re-review per the BEAAA-2182 Track A Q-A4 carve-out at api/scan_runtime_factory.py:351-362."
contracts_referenced: ["are-scanner-api-v1@v1.5.0 (no version bump in v0.3; UW op-seat re-review on the same v1.5.0 patch — provenance dial)", "scanner-implementation-supervision-v1@v1.1"]
ticket_refs: ["BEAAA-5414", "BEAAA-5287", "BEAAA-3228", "BEAAA-5353", "BEAAA-5284", "BEAAA-5288", "BEAAA-2182"]
proof_points_cited: ["Canon master tip bec8f80 (BEAAA-5325 ADR-0029, reconciled BEAAA-5353)", "synthetic_v0_probe_run_fn at api/scan_runtime_factory.py:145-176 (bec8f80) — the stub this walker replaces on production-wired runs", "_build_synthetic_applicability at scanners/score_runtime.py:163-186 (bec8f80) — CASE 1 auto-N/A path preserved as fallback, NOT as default, when ARE_WALKER_CUSTOMER_AGENT=1", "BEAAA-2182 Track A note at api/scan_runtime_factory.py:351-362 (bec8f80) — explicit Track B carve-out requiring CTO co-sign + UW op-seat re-review on v1.5.0 patch for the CASE 1 → CASE 2 flip", "supervision contract v1.1 §Required artifacts (merged_sha_on_canon_master_at_close) — landed BEAAA-5288 2026-06-14T17:45Z, gates this ticket's close", "Founder ruling on BEAAA-5353 Decision 2 = A (2026-06-15T06:57Z board comment 1dde393e) sanctioning v0.3"]
version: "0.1"
prior_version: null
self_audit_passed: true
self_audit_note: "Pre-submission self-audit per scanner-engineer-os DECISION SEQUENCE: (1) Deliverable defined: ADR for CTO authoring the customer-true Track B walker design surface BEFORE any code work, gating AC#2-AC#5 on CTO design co-sign. (2) Glossary clean — ARE / IFA / insure (no BMI / ARI / BEAI / guarantee). (3) Classified rule-change per scanner-implementation-supervision-v1@v1.1 — flips applicability_decisions provenance (BEAAA-2182 Q-A4 carve-out names this exact case as rule-change requiring CTO co-sign + UW op-seat re-review on v1.5.0 patch); also touches partial_completion semantics (today hardcoded False at scanners/score_runtime.py:244 — see §Design Q-B4). (4) N=5 calibration spread = 0 expected vs synthetic_v0 baseline only on the synthesized-N/A fallback path (ARE_WALKER_CUSTOMER_AGENT=0); the customer-true path (ARE_WALKER_CUSTOMER_AGENT=1) is a behavior change by construction and is gated by AG-B5 (envelope round-trip via local HTTP echo server smoke). (5) Anchored to are-scanner-api-v1@v1.5.0 (current). NO version bump in v0.3 — the envelope dict shape is byte-identical; only the provenance dial moves. UW op-seat re-review of the v1.5.0 patch is the binding gate per Q-A4 carve-out. (6) Tamper resistance UNCHANGED — Prober ScopedEgressAllowlist(agent_endpoint_host), Scorer ScopedEgressAllowlist([]) deny-all, vault never crosses IPC (Hard Invariants #1, #3, #4, #5 preserved). (7) Wrap-don't-fork — the walker is the orchestration shell; concrete probe families (Inspect / PyRIT / Garak / HolisticBias / BBQ) land as separate rule-execution wrap-tickets, each pinned to a frozen upstream version. v0.3 ships ONE wrap (Inspect Section 1 minimal) for the smoke surface; remaining families queue as follow-ups. (8) Carrier Rule clean. (9) Discrimination invariant — rubric / weights / judge / scoring math unchanged. (10) Merged_sha gate (supervision contract v1.1) acknowledged: this ADR is DRAFT in working tree only; no commit to canon master until CTO design co-sign lands; merge co-sign comes after with branch tip SHA cited."
escalations_noted:
  - "OQ-B1 — Walker symbol name: this ADR proposes the Python callable name `customer_agent_probe_run_fn` (mirroring `synthetic_v0_probe_run_fn`) AND the runtime selector env var `ARE_WALKER_CUSTOMER_AGENT` (1=customer-true, 0=synthetic_v0 fallback). The issue title names the env var as the resolved symbol. CTO confirmation on the Python callable name is requested at the §Q-B1 row."
  - "OQ-B2 — Probe family scope for v0.3: this ADR proposes Inspect (one section, minimal) as the only wrapped family in v0.3 — sufficient to drive ≥1 real dim_id row over the customer endpoint and prove the IPC + tamper-resistance lifecycle holds end-to-end. Remaining families (PyRIT, Garak, HolisticBias, BBQ) are scoped as separate rule-execution wrap-tickets. CTO ruling requested on whether v0.3 ships with 1 family (minimal viable customer-true) or N≥4 (full pillar coverage) at the §Q-B2 row."
  - "OQ-B3 — Applicability decisions provenance: the BEAAA-2182 Q-A4 carve-out names this as the case requiring CTO co-sign + UW op-seat re-review on v1.5.0 patch. This ADR proposes both gates are pre-merge; UW op-seat (afa65747) is added to the request_confirmation chain alongside CTO (88e68019). HoUW binding NOT triggered (no rubric / weights / judge / probe-set canonical change). Founder NOT triggered (no pillar weight / tamper-resistance reduction / covered-peril change)."
  - "OQ-B4 — partial_completion semantics flip: today scanners/score_runtime.py:244 hardcodes False; the customer-true walker must surface True when the customer endpoint fails to cover a fires=True applicability dim. This is part of the rule-change classification. CTO ruling requested on whether the v0.3 envelope projection patches partial_completion in compute_envelope (preferred — single source of truth) vs in the API-edge factory closure."
  - "OQ-B5 — Cascade-close protection: per BEAAA-5353 Decision 3, until the platform-level cascade-close guard ships, the SE + CTO co-signer manually verify no parent cascade-close lands this ticket prematurely. This ADR's close-time checklist (§Rollback / Close) names that manual check + the third-loop escalation path back to BEAAA-5353."
---

# ADR-0030 — BEAAA-5414 Customer-true Track B walker (ARE_WALKER_CUSTOMER_AGENT)

## BLUF

Replace `synthetic_v0_probe_run_fn` on the `ARE_PRODUCTION_SCAN_WIRING=1` path with a customer-true walker (`customer_agent_probe_run_fn`, runtime-selected via `ARE_WALKER_CUSTOMER_AGENT=1`) that drives the bundle's pinned probe families against the customer's `agent_endpoint`, accepts `applicability_decisions` from `scan_request.invocation_context` (CASE 2 provenance) rather than synthesizing N/A from the rubric, and surfaces `partial_completion: True` when the customer endpoint fails to cover a `fires=True` applicability dim within the orchestrator's `MAX_WALL_CLOCK_SECONDS` cap. Synthetic_v0 path stays for `ARE_WALKER_CUSTOMER_AGENT=0` as Tier-1 rollback. Tamper-resistance, IPC allow-lists, vault lifecycle, and the `bin/are-scan` offline CLI are all byte-unchanged. v0.3 scope ships ONE wrapped probe family (Inspect §1 minimal) sufficient to exercise the IPC + envelope round-trip end-to-end; remaining families queue as separate rule-execution wrap-tickets.

## Classification

**Rule-change** per `contracts/scanner-implementation-supervision-v1@v1.1`:

| Classification row | This ticket | Notes |
| --- | --- | --- |
| "Implement a probe such that the N=5 score impact exceeds ±1 on any golden agent" | Likely — customer-true vs synthetic_v0 is by construction a different distribution | Gated by AG-B5 smoke |
| "Customer onboarding harness that introduces a new evidence type" | YES — `evidence_uri` now points at real probe artifacts (Inspect log refs) instead of synthetic `https://evidence.local/...` | |
| BEAAA-2182 Track A Q-A4 explicit carve-out | YES — `applicability_decisions` provenance CASE 1 → CASE 2 | Pre-named at `api/scan_runtime_factory.py:351-362` as requiring CTO co-sign + UW op-seat re-review on v1.5.0 patch |

**CTO co-sign + UW operational co-sign are pre-merge gates.** Founder ruling on BEAAA-5353 Decision 2 = A (2026-06-15T06:57Z) sanctions v0.3 SCOPE; design co-sign is still required before code work begins per the issue's AC#1 ordering.

## Prior-attempt failure mode

BEAAA-3228 (loop 1) and BEAAA-5287 (loop 2) were both closed `done` via parent cascade-close without any commit landing on canon master. Auditable absence: `git log master --grep BEAAA-3228` and `git log master --grep BEAAA-5287` against `bec8f80` return zero commits. This ticket's `merged_sha_on_canon_master_at_close` gate (supervision contract v1.1 §Required artifacts) closes that gap. Close-time verification one-liner is named in §Rollback / Close.

## Decisions requested from CTO (pre-code work)

| Q | Proposed | CTO confirmation needed | Default if unspecified |
| --- | --- | --- | --- |
| Q-B1 | Python callable name `customer_agent_probe_run_fn`; env flag `ARE_WALKER_CUSTOMER_AGENT` (1/0). | Walker symbol name | Proposal |
| Q-B2 | v0.3 ships 1 wrapped family (Inspect §1 minimal). Remaining families = follow-up rule-execution tickets. | Scope of v0.3 (1 family vs N≥4) | 1 family |
| Q-B3 | `applicability_decisions` consumed verbatim from `scan_request.invocation_context["applicability_decisions"]` when present; fallback to synthesized N/A when absent. | Provenance contract | Proposal |
| Q-B4 | `partial_completion: True` surfaced from `ScoreRuntime.compute_envelope` (not the API-edge closure) when any `fires=True` dim is uncovered after walker completion. | Where partial_completion is computed | `compute_envelope` |
| Q-B5 | Smoke test = local HTTP echo server bound to a `tests/integration/` ephemeral port; walker drives 1 Inspect §1 probe against it; envelope round-trip asserted byte-stable across 5 replicates. | Smoke surface for AC#4 | Proposal |
| Q-B6 | Synthetic_v0 path REMAINS as Tier-1 rollback (env flag flip ≤10 min). No code deletion. | Rollback posture | Proposal |

## Design

### Module changes

| Path | State | Role |
| --- | --- | --- |
| `api/scan_runtime_factory.py` | MODIFIED | Adds `customer_agent_probe_run_fn`, `customer_agent_http_runner_factory`, `ARE_WALKER_CUSTOMER_AGENT` selector env var. `make_default_production_scan_runtime` selects walker + runner factory based on env flag; synthetic_v0 path byte-unchanged when flag=0. |
| `scanners/score_runtime.py` | MODIFIED | `compute_envelope` accepts new kwarg `walker_partial_completion: bool = False`; when True, the projected envelope's `partial_completion` field is True regardless of CLI projection (the rescan_handler's pre-existing patch path is unchanged). |
| `scanners/customer_agent_walker.py` | **NEW** | Walker shell — iterates the bundle's pinned probe families, drives them against the customer endpoint via the runner_factory-built ModelRunner, yields `(dim_id, partial_row)` tuples that match the ProbeRunFn protocol. Each yielded row carries `evidence_uri` pointing at the upstream eval framework's log artifact (e.g., Inspect's session log JSON URI) and `evidence_sha256` computed over the raw response payload. |
| `scanners/wraps/inspect_section1_v0.py` | **NEW (v0.3)** | First wrapped probe family for v0.3 — wraps Inspect's Section 1 "behavioral safety" suite (minimal, ONE eval task) and emits a `(dim_id, partial_row)` tuple per probe. Inspect version pinned in module docstring + tests. |
| `harness/customer_runner.py` | **NEW** | `customer_agent_http_runner_factory` — minimal HTTP POST runner that binds the credential from the vault into an `Authorization` header, sends the prompt as JSON body, returns the response text. Adversarial-input bounds preserved (size caps, no shell-out, no symlink follow). |
| `tests/integration/test_walker_customer_agent_v0_3.py` | **NEW** | AG-B1..AG-B5 fixture tests + AG-B5 smoke (local HTTP echo server, walker round-trip, envelope assertions). Smoke surface satisfies issue AC#4. |
| `tests/integration/__init__.py` | **NEW** | Module marker for the new integration tests directory. |
| `scripts/beaaa5414_track_b_calibration.py` | **NEW** | N=5 calibration spread harness for the synthetic_v0 fallback path (`ARE_WALKER_CUSTOMER_AGENT=0`) — proves the synthetic path is byte-stable across the change. |
| `tools/calibration-deltas/2026-06-15-beaaa5414-track-b-walker-n5.md` | **NEW** | N=5 calibration delta report. |
| `docs/adr/adr-0030-beaaa5414-customer-true-track-b-walker.md` | **NEW (THIS FILE)** | Design ADR. |

### Walker contract (Python signature)

```python
def customer_agent_probe_run_fn(runner: ModelRunner) -> Iterable[tuple[str, dict[str, Any]]]:
    """Customer-true ProbeRunFn — drives the bundle's pinned probe families
    against the customer's agent endpoint via `runner` and yields one
    (dim_id, partial_row) tuple per real probe response.

    The walker is selected on the production scan path when
    ``ARE_WALKER_CUSTOMER_AGENT=1`` is set on the API-edge process; the
    default (unset / `0`) preserves the existing `synthetic_v0_probe_run_fn`
    behavior byte-for-byte (Tier-1 rollback).

    Tamper-resistance invariants (preserved, NOT relaxed):
    * Runs inside the Prober subprocess's ScopedEgressAllowlist(agent_endpoint_host)
      scope — every probe request is bound to the customer's endpoint host.
    * Scorer's deny-all ScopedEgressAllowlist([]) is untouched.
    * The vault is fork-inherited; no credential crosses the IPC.
    * IPC rows pass through the existing closed ALLOWED_ROW_FIELDS allow-list
      and the existing intake_guard validators (provenance + shape-safety).

    For each yielded row, `evidence_uri` points at the upstream eval
    framework's log artifact (e.g., Inspect session log URI) and
    `evidence_sha256` is the canonical hash over the raw response payload
    (NOT over the dim_id — distinguishes from the synthetic_v0 walker's
    `sha256(dim_id)` rule).
    """
```

### `ARE_WALKER_CUSTOMER_AGENT` selector

`make_default_production_scan_runtime()` reads `ARE_WALKER_CUSTOMER_AGENT` (default `0`) at runtime construction:

| Flag | Walker | Runner factory | Applicability decisions provenance |
| --- | --- | --- | --- |
| `0` (default) | `synthetic_v0_probe_run_fn` | `v0_mock_runner_factory` | CASE 1 (auto-N/A) |
| `1` | `customer_agent_probe_run_fn` | `customer_agent_http_runner_factory` | CASE 2 (consumed from `scan_request.invocation_context["applicability_decisions"]`); fallback to CASE 1 when absent |

Tier-1 rollback = flip flag to `0` (≤10 min). No code deletion. Tier-2 = PR revert ≤2 h.

### Tamper-resistance assertion (re-stated)

Reading the existing factory at `api/scan_runtime_factory.py:230-273`, the `_prober_target` opens `ScopedEgressAllowlist(agent_endpoint_host)` around the `run_scan` call. The customer-true walker is invoked **inside** that scope (it's the body of the `probe_run_fn` callable that `run_scan` consumes). The customer endpoint is the only host the walker can talk to; any other outbound is denied at the transport layer. This is the same invariant the v0 synthetic walker honored — synthetic_v0 just chose not to talk to anything; customer_agent does talk, but only to the allow-listed host. **Tamper-resistance posture is not relaxed.**

### Why the v1.5.0 contract does NOT bump

The §2.2 envelope dict shape is byte-identical:

* `agent_id` — unchanged.
* `are_total`, `subscores`, `band` — same scoring callee (`rubric.scoring.score`).
* `evidence_refs`, `evidence_uri`, `evidence_sha256` — same field shape; only the value content moves from synthetic strings to real upstream log URIs.
* `partial_completion` — same field; only its value can flip from False to True under the customer-true path (today hardcoded False at `scanners/score_runtime.py:244`).
* `confidence_below_adjudication_threshold` — unchanged.
* `model_versions`, `corpus_version`, `rubric_version`, `scanner_version`, `seed`, `run_timestamp` — unchanged.
* `prior_scan_refs` — unchanged.
* `purpose` — unchanged.

**The provenance dial (`applicability_decisions` source: synthesized vs customer-supplied) is the v1.5.0 patch's UW op-seat re-review surface** per the BEAAA-2182 Q-A4 carve-out. The patch document itself gets an addendum line; no version bump.

## Acceptance Gates

* **AG-B1** — Customer-true walker yields real probe rows under `ARE_WALKER_CUSTOMER_AGENT=1` against a local HTTP echo server fixture (proves the runner_factory + walker round-trip).
* **AG-B2** — Synthetic_v0 fallback under `ARE_WALKER_CUSTOMER_AGENT=0` is byte-identical to the bec8f80 baseline (Tier-1 rollback proof).
* **AG-B3** — `applicability_decisions` CASE 2 path: when `scan_request.invocation_context["applicability_decisions"]` is provided, `ScoreRuntime.compute_envelope` consumes them verbatim; auto-N/A is NOT invoked.
* **AG-B4** — `partial_completion: True` surfaces in the envelope when a `fires=True` applicability dim is uncovered by the walker.
* **AG-B5** — Smoke test under `tests/integration/test_walker_customer_agent_v0_3.py` exercises end-to-end (HTTP echo server → walker → IPC rows → Scorer → envelope) across 5 replicates; envelope round-trip is byte-stable across replicates under a fixed seed.
* **AG-B6** — N=5 calibration spread on synthetic_v0 fallback path (`ARE_WALKER_CUSTOMER_AGENT=0`) against bec8f80 baseline: spread MUST = 0 (rollback determinism gate). The harness `scripts/beaaa5414_track_b_calibration.py` MUST execute under `ARE_PRODUCTION_SCAN_WIRING=1` against the real `api/scan_runtime_factory.make_default_production_scan_runtime` path — NOT a monkey-patched in-process variant. The harness includes an explicit emission-path guard that raises `RuntimeError` when `os.getenv("ARE_PRODUCTION_SCAN_WIRING") != "1"`. (CTO amendment 1 per BEAAA-5418 — addresses the BEAAA-2316 B2 calibration blindspot where monkey-patched `pab.FIXTURE_CONFIG` produced false spread=0.)
* **AG-B7** — Credential-bytes IPC audit (customer-true path only). The smoke test loads a known fixture token (`AGB7_FIXTURE_TOKEN`) into the vault, runs the walker through `customer_agent_http_runner_factory(agent_endpoint, secret)` against the local HTTP echo server, and asserts that NO byte sequence of the fixture token appears in any IPC frame on `WorkerChannel` (Prober↔Scorer). Implementation: `WorkerChannel.send_row` / `recv_row` are instrumented for the test run; every frame's serialized bytes are scanned with `assert fixture_token_bytes not in frame_bytes`. Failure mode: any hit raises `CredentialLeakError` and fails the test. (CTO amendment 2 per BEAAA-5418 — makes the Q-A4 carve-out's tamper-resistance leg testable rather than asserted from code-reading alone; same audit-gap pattern as ADR-0029 / BEAAA-5325.)

AG-B6 + AG-B7 are the binding rule-execution-discipline gates; AG-B1..AG-B5 are the rule-change-acceptance gates.

## Amendments (CTO design co-sign, BEAAA-5418 2026-06-15)

CTO disposition on Q-B1..Q-B6 = **ACCEPT_WITH_AMENDMENTS** via relay child BEAAA-5418 (parent BEAAA-5414 actor-boundary). Amendments folded into §Acceptance Gates AG-B6 (updated) + AG-B7 (new) above. Two carry-along acceptance conditions (Q-B2, Q-B5) are recorded here for downstream artifacts.

* **Q-B2 acceptance condition (carry-along).** v0.3 ships ONE wrapped probe family (Inspect §1 minimal). The v0.3 close packet and any UW-facing artifact MUST state verbatim: *"customer-true Track B walker shipped; pillar coverage held off pending follow-on wrap-tickets (PyRIT, Garak, HolisticBias, BBQ)."* Discrimination invariant is by-design partial under v0.3 customer-true runs; `partial_completion: True` will surface routinely. Intentional, not a defect.
* **Q-B5 documentation note (carry-along).** AG-B5's byte-stable envelope across 5 replicates against the deterministic local HTTP echo server proves **IPC + scaffolding round-trip stability**, NOT score reproducibility against a real customer agent (non-deterministic by construction). UW reads AG-B5 as IPC-frame-stability evidence; AG-B6 (now under emission-path guard) covers rollback determinism.

Both amendments do NOT change the Q-B1..Q-B6 dispositions and do NOT require re-co-sign. The provenance dial gate (UW op-seat re-review on v1.5.0 patch) remains the open pre-commit gate.

## Rollback / Close

### Pre-merge (CTO design co-sign)

1. SE drafts this ADR (PROPOSED status); request_confirmation opened on BEAAA-5414 targeting CTO with this ADR's path + walker symbol + Q-B1..Q-B6 decisions.
2. CTO accepts (or amends) design via request_confirmation.
3. UW op-seat re-reviews provenance dial on v1.5.0 patch via the request_confirmation chain alongside CTO.
4. Both accepts land before any commit on the working branch beyond this ADR.

### Pre-merge (CTO merge co-sign)

1. SE commits ADR + walker scaffold + smoke test + N=5 calibration on branch `beaaa-5414-track-b-walker` (off `bec8f80`).
2. SE pushes branch + opens second request_confirmation on BEAAA-5414 with branch tip SHA cited.
3. CTO runs `git merge-base --is-ancestor <branch-tip-sha> master` in the canonical workspace (`/mnt/paperclipdata/dot-paperclip/instances/default/companies/59f8876e-e729-4dda-98f9-1317c2b50492/projects/are-scanner`) — wait, this verification happens AFTER merge. The pre-merge variant is `git rev-list --left-right --count master...<branch>` to confirm linear-ahead.
4. CTO co-signs merge; SE fast-forwards canon master to the branch tip.

### Close (merged_sha gate, supervision contract v1.1)

The closing comment on BEAAA-5414 MUST include all of:

```
merged_sha_on_canon_master_at_close: <sha>
verified_via: cd /mnt/paperclipdata/dot-paperclip/instances/default/companies/59f8876e-e729-4dda-98f9-1317c2b50492/projects/are-scanner && git merge-base --is-ancestor <sha> master && echo "ANCESTOR"
@CTO co-sign on merged_sha verification.
```

If the verification one-liner does NOT print `ANCESTOR`, the close is invalid and the ticket reverts to `in_progress`. The Auditor's nightly pass surfaces `merged_sha_on_canon_master_at_close` reachability failures at Founder-tier (Supreme-Law adjacent).

### Cascade-close protection (manual until platform guard ships)

Per BEAAA-5353 Decision 3, until the platform-level cascade-close guard lands, the SE (me) + CTO co-signer (88e68019) manually verify no parent cascade-close fires on BEAAA-5414 with the merged_sha artifact missing. If a cascade-close happens, immediately PATCH BEAAA-5414 back to `in_progress`, surface as a third loop on BEAAA-5353, and re-cite the artifact_required declaration in the issue body.

## Metadata (issue close artifact preview)

* `scanner_version`: 0.4.2 (no bump in v0.3; this ticket doesn't touch SCANNER_VERSION)
* `corpus_version`: 0.1.0 (unchanged from bec8f80 baseline)
* `probe-set-version`: 1.0.0 (Inspect §1 minimal pin recorded in `scanners/wraps/inspect_section1_v0.py` module docstring)
* `rubric_point_id`: ALL (the walker spans the full active rubric; specific dim coverage depends on Inspect §1 family + customer-supplied applicability_decisions)
* `N=5 calibration delta`: synthetic_v0 fallback path spread = 0 (AG-B6 binding gate); customer-true path is non-determinism by construction (real probe outputs) — recorded as `n/a — customer-true path; rollback path spread = 0`
* `upstream_eval_version`: Inspect <pinned in §wrap module> (v0.3 scope)
* `cto_cosign_ref`: <populated at close — design co-sign req_conf id + merge co-sign req_conf id>
* `uw_cosign_ref`: <populated at close — UW op-seat re-review req_conf id on v1.5.0 patch provenance dial>
* `merged_sha_on_canon_master_at_close`: <populated at close — branch tip merged into master>

## References

* Issue: BEAAA-5414 (this ticket; third attempt with merged_sha gate enforced)
* Prior attempts: BEAAA-3228 (loop 1, cascade-closed without merge), BEAAA-5287 (loop 2, cascade-closed without merge)
* Parent escalation: BEAAA-5353 (Founder ruling on Decision 2 = A 2026-06-15T06:57Z)
* Supervision contract: `contracts/scanner-implementation-supervision-v1.md` v1.1 (BEAAA-5288 2026-06-14T17:45Z)
* BEAAA-2182 Track A: the Q-A4 carve-out at `api/scan_runtime_factory.py:351-362` names this case as rule-change requiring CTO co-sign + UW op-seat re-review

## Footer — BEAAA-5485 SSRF gap closure (2026-06-15)

The customer-runner tamper-resistance posture promised in §Design line 115 of this ADR ("The customer endpoint is the only host the walker can talk to; any other outbound is denied at the transport layer.") was broken in implementation. `harness/customer_runner.py` issued HTTP via `urllib.request.urlopen` — a transport the `ScopedEgressAllowlist`'s `httpx`-based `_AllowlistTransport` does not intercept — so a 302 `Location: http://127.0.0.1/` was followed verbatim from inside `ScopedEgressAllowlist(<declared-host>)`. SSRF gap; the §6 promise on `contracts/are-scanner-api-v1@v1.3.0` did not hold on the Track B walker path.

**Closed by BEAAA-5485** (CTO-unilateral, security-posture restoration row of `contracts/scanner-implementation-supervision-v1.md@v1.0`).

* Fix commit on the BEAAA-5485 PR branch: replaces `urllib.request.urlopen` with `scope.client(follow_redirects=True)` so the allowlist's transport intercepts both the initial request and every redirect re-issue. Concrete commit SHA recorded in the BEAAA-5485 PR description (`branch beaaa-5485-ssrf-fix` against master at branch-off; tip SHA captured in the cmt `cto_cosign_ref` block at close).
* Regression test: `tests/integration/test_customer_runner_egress.py` — bounds the contract going forward. Pre-fix run fails with "DID NOT RAISE"; post-fix run passes with the sentinel-on-127.0.0.1 flag staying `False`.
* N=5 calibration delta vs master: `tools/calibration-deltas/2026-06-15-beaaa5485-ssrf-fix-n5.json`. Δ = 0 by construction (`rubric.scoring.score` does not import `harness.customer_runner`) and empirically (master vs fix score-function output is byte-identical modulo timestamp). Supreme Law ±1 satisfied.
* Posture invariant after the fix: any non-allow-listed host (initial request OR redirect target) raises `EgressNotAllowed` at the transport layer; the runner surfaces this as `RunnerError` to the walker without leaking the refused target.

Note: the BEAAA-5485 ticket body references "ADR 0006 (egress containment)"; ADR-0006 in this repo is the pillar-name reconciliation ADR and has no egress content. The Track B walker tamper-resistance posture is the §Design line 115 claim above, so this ADR (ADR-0030) is the semantically correct closing-record location. Discrepancy with the ticket text flagged to CTO in the BEAAA-5485 disposition comment for ruling.
