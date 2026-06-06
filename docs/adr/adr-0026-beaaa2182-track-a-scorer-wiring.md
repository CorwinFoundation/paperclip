---
artifact_type: adr
owner: cto
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "Track A wires the Scorer subprocess from a no-op sink into a real scoring callee — fork-inherited Rubric is loaded at Scorer spawn, eval_outputs are accumulated in-Scorer, and the §2.2 envelope projection ships back over an IPC return channel with a closed ALLOWED_SCORE_ENVELOPE_FIELDS allow-list mirroring the inbound ALLOWED_ROW_FIELDS pattern; AG-A5 keeps rubric.scoring transitively un-reachable from api/ route handlers so scoring math runs only inside the deny-all egress scope."
contracts_referenced: ["are-scanner-api-v1@v1.2.0 → v1.5.0 (target — see §Open Question OQ-1)", "scanner-implementation-supervision-v1@v1.0"]
ticket_refs: ["BEAAA-2182", "BEAAA-2175", "BEAAA-2132", "BEAAA-2823"]
proof_points_cited: ["BEAAA-2182 CTO design co-sign — wake comment 9840343c (Track A ACCEPTED, Q-A1/Q-A2/Q-A3 resolved)", "BEAAA-2175 PR-B base — api/scan_runtime_factory.py master 52c003e (no-op sink at lines 107-115; synthetic walker yields 4 dim rows at lines 118-149)", "scanners/live_probe_workers.py master 52c003e — ALLOWED_ROW_FIELDS closed allow-list, ScopedEgressAllowlist([]) deny-all in run_scorer, fork-only context", "Prober error sentinel ('err', type, msg) at api/scan_runtime_factory.py::_prober_target master 52c003e (mirrors Q-A2 ACCEPT)"]
version: "0.1"
prior_version: null
self_audit_passed: true
self_audit_note: "Pre-submission self-audit: (1) Deliverable defined in one sentence: ADR for CTO that authors Track A scorer-wiring design AND a merge co-sign request enabling Scanner Engineer to land code behind ARE_SCORER_REAL within Phase 3. (2) Glossary clean — ARE, IFA, insure (no BMI/ARI/BEAI/guarantee). (3) Classified rule-change per scanner-implementation-supervision-v1@v1.0 row 'Scorer / orchestrator IPC contract change' (also 'bin/are-scan CLI behavior change that affects output JSON shape' — N/A since CLI surface unchanged; envelope content meaning changes per BEAAA-2175 lineage). (4) N=5 calibration spread=0 expected on B1/G1/G2/M1/M2/M3 because Scorer is the same callee as CLI offline path under matching seed/scanner_version (AG-A2 binding gate). (5) Anchored to are-scanner-api-v1@v1.2.0 with v1.5.0 target — see OQ-1 deviation from CTO 'v1.3.0' instruction. (6) Tamper resistance — scoring runs inside ScopedEgressAllowlist([]) (deny-all) which is strictly stronger than today's API-edge scoring. (7) Wrap-don't-fork — scanners/score_runtime.py imports rubric.scoring.score; CLI path unchanged; no logic duplication. (8) Carrier Rule clean. (9) Discrimination invariant — no rubric/weight/judge/scoring-math change."
escalations_noted:
  - "OQ-1: CTO design co-sign comment 9840343c stated 'v1.2.1 → v1.3.0'. Current contracts/ tree already has v1.3.0 (telemetry-shim disambiguation, BEAAA-2023) and v1.4.0 (agentic-pilot-readiness, BEAAA-2474) patches. To avoid version collision, this ADR targets v1.5.0. Flagging at merge co-sign for CTO confirmation; will hold the contract-patch publish until CTO confirms target version."
  - "OQ-2: UW op-seat co-sign on contract bump is pending per CTO co-sign comment 9840343c. Bound by Approval-gates row 'CTO + Underwriter co-sign — rule changes that affect the contract output shape'. Surface here; merge gate set in §Rollback."
---

# ADR-0026 — BEAAA-2182 Track A: Scorer wiring (no-op sink → real scoring callee)

## BLUF

Track A replaces `api/scan_runtime_factory.py::_noop_validated_row` with a real `on_validated_row` that accumulates the IPC-validated row stream into an `eval_outputs` dict inside the Scorer subprocess, then projects the §2.2 envelope via `scanners/score_runtime.py::ScoreRuntime.compute_envelope` on `END_OF_SCAN` and ships it back to the API-edge parent over a widened `WorkerChannel` return channel governed by a closed `ALLOWED_SCORE_ENVELOPE_FIELDS` allow-list. Behind `ARE_SCORER_REAL=on`; rollback flips the flag to `off` (≤10 min Tier-1) and the factory falls back to the master `52c003e` `_phase1_pending` envelope. AG-A5 CI check enforces `rubric.scoring` is not importable from `api/`; Scorer-side runtime assertion catches drift in test mode.

## Classification

**Rule-change** per `contracts/scanner-implementation-supervision-v1@v1.0` row "Scorer / orchestrator IPC contract change". CTO design co-sign granted at [BEAAA-2182](/BEAAA/issues/BEAAA-2182) wake comment `9840343c` (2026-06-05). Pre-merge CTO co-sign pending (gates: AG-A2 N=5 spread=0; AG-A5 CI pass; UW op-seat co-sign on contract bump per OQ-1 / OQ-2).

## Decisions (CTO-resolved)

| Q | Decision | Source |
| --- | --- | --- |
| Q-A1 | Pre-load `Rubric` at Scorer spawn via fork-inherited byte-stable instance; no per-scan YAML parse. | CTO comment `9840343c` |
| Q-A2 | Return-channel sentinel is a `("err", type, msg)` tuple mirroring `_prober_target` exactly. | CTO comment `9840343c` |
| Q-A3 | Two-layer AG-A5: CI check is the durable gate + Scorer-startup runtime assertion (`if ARE_TEST_MODE: assert "api.score_runtime" not in sys.modules`) as paranoia layer. | CTO comment `9840343c` |

## Design

### Module layout

| Path | State | Role |
| --- | --- | --- |
| `scanners/score_runtime.py` | **NEW** | Scorer-side `ScoreRuntime` (imports `rubric.scoring.score` + `harness.contract_envelope.to_contract_output`). Mirrors the deleted `api/score_runtime.py` shape; lives in `scanners/` namespace so `api/` route handlers cannot transitively reach `rubric.scoring`. |
| `scanners/live_probe_workers.py` | MODIFIED | Adds `ALLOWED_SCORE_ENVELOPE_FIELDS` closed allow-list (= `ENVELOPE_FIELDS_SECTION_2_2`); adds `serialize_score_envelope` + `validate_inbound_envelope` mirroring the row pair; adds `WorkerChannel.send_score_envelope` / `recv_score_envelope`; extends `run_scorer` to (a) accept `rubric`, `scan_request`, `agent_id`, `run_timestamp`, `invocation_context`, `prior_scan_refs`, `compute_envelope: bool`, (b) accumulate validated rows, (c) on `END_OF_SCAN` invoke `score_runtime.compute_envelope` and push the projected dict (or `("err", type, msg)` tuple) over the return channel. |
| `api/scan_runtime_factory.py` | MODIFIED | Behind `ARE_SCORER_REAL=on`: loads `Rubric.load(rubric_path)` once at runtime construction (parent-side; fork-inherited into Scorer), passes it into `spawn_scorer`, registers per-scan envelope dict from return-channel drain into `runtime._score_envelope_registry[scan_id]`. `_production_score_envelope_factory` closure reads the registry instead of computing. Flag-off path unchanged from master `52c003e` `_phase1_pending` behavior — `api/score_runtime.py` stays deleted; rollback is purely placeholder envelope. |
| `api/score_runtime.py` | DELETED | Module path forbidden by AG-A5. Already deleted in working-tree baseline; this ADR formalizes the deletion. |
| `scripts/check_no_apiedge_score.py` | **NEW** | AG-A5 CI gate: greps `api/**/*.py` for any `from rubric.scoring` / `from api.score_runtime` / `import rubric.scoring` / `import api.score_runtime`; exits non-zero on hit. Lives outside `api/` so it can introspect that surface. |
| `tests/test_beaaa2182_a_scorer_wiring.py` | **NEW** | AG-A1..AG-A6 fixtures. |
| `scripts/beaaa2182_a_calibration.py` | **NEW** | N=5 calibration spread harness against B1/G1/G2/M1/M2/M3 — flag-on vs. CLI offline path under matching seed/scanner_version; spread MUST = 0. |
| `contracts/are-scanner-api-v1-v1.5.0-patch.md` | **NEW (DRAFT)** | Contract bump doc for the Scorer→API IPC surface change. Targets v1.5.0 (OQ-1 deviation from CTO `v1.3.0` direction — flagged at merge co-sign). |

### Data flow (flag-on)

```
API edge (parent process):
  make_default_production_scan_runtime()
    ├─ Load Rubric (rubric.yaml parse — parent only; fork-inherited)
    ├─ Construct WorkerChannel with row queue + new envelope return queue
    └─ Wire ScanRuntime.kickoff → run_scan_fn → factory closure reads
       runtime._score_envelope_registry[scan_id]

  Per-scan run_scan_fn:
    ├─ spawn_scorer(channel, rubric=..., scan_request=..., agent_id=...,
    │              run_timestamp=..., invocation_context=...,
    │              prior_scan_refs=..., compute_envelope=True)
    ├─ spawn_prober (unchanged)
    ├─ Wait for orchestrator result_queue.get() (existing path)
    ├─ NEW: drain envelope return queue → registry[scan_id] = envelope dict
    │       (or raise OrchestratorError on ('err', type, msg))
    └─ join + vault.forget_all()

Scorer subprocess (fork-inherited Rubric):
  ScopedEgressAllowlist([])   <-- deny-all (unchanged from master)
    ├─ if ARE_TEST_MODE:
    │    assert "api.score_runtime" not in sys.modules   <-- AG-A5 paranoia
    ├─ Loop: recv_row → validate_inbound (existing) → accumulate eval_outputs
    ├─ END_OF_SCAN:
    │    ├─ try:
    │    │    envelope = ScoreRuntime.compute_envelope(rubric,
    │    │      eval_outputs, scan_request, agent_id, run_timestamp,
    │    │      invocation_context, prior_scan_refs)
    │    │    send_score_envelope(envelope)   <-- ALLOWED_SCORE_ENVELOPE_FIELDS
    │    └─ except BaseException as exc:
    │         send_score_envelope_err(type(exc).__name__, str(exc))
    │         <-- mirrors _prober_target Q-A2 ACCEPT
    └─ return
```

### IPC allow-list — `ALLOWED_SCORE_ENVELOPE_FIELDS`

```python
ALLOWED_SCORE_ENVELOPE_FIELDS: frozenset[str] = frozenset({
    "agent_id", "are_total", "band",
    "confidence_below_adjudication_threshold", "corpus_version",
    "evidence_refs", "model_versions", "partial_completion",
    "prior_scan_refs", "purpose", "rubric_version", "run_timestamp",
    "scanner_version", "seed", "subscores",
    "prior_scan_refs_truncated",  # optional cap sentinel per ADR-0017 §7 Q2
})
```

Mirrors `ALLOWED_ROW_FIELDS` shape: frozenset, validated on both serialize and receive. `validate_inbound_envelope` re-runs the receiver-side check on parent-process drain so the IPC validator catches a misbehaving Scorer (defense in depth, same posture as `validate_inbound` for rows). No free-text trace, no raw probe response body, no opaque bytes — every leaf must be JSON-primitive per `_is_pure_json_value`.

### Return-channel sentinel — tuple mirroring Prober

```python
# Scorer side
try:
    envelope = score_runtime.compute_envelope(...)
    channel.send_score_envelope(("ok", envelope))
except BaseException as exc:
    channel.send_score_envelope(("err", type(exc).__name__, str(exc)))

# Parent side
outcome = channel.recv_score_envelope(timeout=_envelope_drain_timeout())
if outcome[0] == "ok":
    runtime._score_envelope_registry[scan_id] = outcome[1]
else:
    raise OrchestratorError(
        f"scorer subprocess raised {outcome[1]}: {outcome[2]}"
    )
```

Q-A2 ACCEPT: same shape as `_prober_target` outcome tuple. Receiver-side allow-list applies to `outcome[1]` when `outcome[0] == "ok"`; error tuple is type-checked separately.

### AG-A5 enforcement (two-layer per Q-A3 ACCEPT)

**Layer 1 — CI check (durable gate):**

```bash
# scripts/check_no_apiedge_score.py
forbidden_patterns = (
    r"^\s*from\s+rubric\.scoring\s+import",
    r"^\s*import\s+rubric\.scoring",
    r"^\s*from\s+api\.score_runtime\s+import",
    r"^\s*import\s+api\.score_runtime",
)
# Scan api/**/*.py; exit 1 on any match.
```

**Layer 2 — Scorer startup runtime assertion (paranoia):**

```python
# scanners/live_probe_workers.py::run_scorer entry
if os.environ.get("ARE_TEST_MODE") == "1":
    assert "api.score_runtime" not in sys.modules, (
        "AG-A5 violation: api.score_runtime imported by parent before "
        "fork (fork-inherited sys.modules); scoring callee transitively "
        "reachable from API edge."
    )
```

The runtime assertion runs at Scorer entry, BEFORE the deny-all egress scope is opened (so the assertion error doesn't get swallowed). It only fires in test mode (`ARE_TEST_MODE=1`) to avoid prod latency.

## Acceptance Gates (AG-A1..AG-A6)

| Gate | What | Threshold |
| --- | --- | --- |
| **AG-A1** | Real `on_validated_row` accumulates rows in Scorer and yields the same `eval_outputs` dict the CLI offline path would assemble | Byte-equal `eval_outputs` shape across N=5 replicates |
| **AG-A2** | N=5 spread on B1/G1/G2/M1/M2/M3 between flag-on rescan envelope and CLI offline scan output | Spread = 0 on `are_total` and every subscore (CTO Supreme Law reproducibility ±1, tightened to 0 because same callee) |
| **AG-A3** | `ALLOWED_SCORE_ENVELOPE_FIELDS` allow-list rejects every non-listed field at both serialize and receive sides | 100% reject on synthesized off-list field tests; closed-set property test |
| **AG-A4** | Return-channel sentinel `("err", type, msg)` surfaces as `OrchestratorError("scorer subprocess raised TypeName: message")` on Scorer-raised exception | Synthesized Scorer raise produces the expected parent-side `OrchestratorError` shape |
| **AG-A5** | `scripts/check_no_apiedge_score.py` finds zero matches in `api/**/*.py` AND Scorer startup assertion holds in test mode | CI exit 0; assertion does not fire on `pytest tests/` |
| **AG-A6** | Flag-off rollback: setting `ARE_SCORER_REAL=off` produces the master `52c003e` `_phase1_pending` envelope byte-equally | Byte-equal envelope diff against master `52c003e` artifact under flag-off |

AG-A2 is the binding merge gate per `contracts/scanner-implementation-supervision-v1@v1.0` Supreme Law. Spread > 0 on any golden = halt merge, re-classify as rule-change-with-content-delta, escalate.

## Hard Invariants — Status

| Invariant | Status under Track A |
| --- | --- |
| #1 Offline path untouched | UNCHANGED — `bin/are-scan` + `harness/cli_runner.py` byte-identical. Scorer path is a NEW caller of the same `rubric.scoring.score` callee (wrap-don't-fork). |
| #3 IPC validators wrapped not forked | STRENGTHENED — return channel reuses the same `_is_pure_json_value` + closed allow-list pattern as `serialize_measurement_row`. |
| #4 Credential never crosses IPC | UNCHANGED — vault stays parent-process; envelope return-channel carries no credential-typed field (allow-list closed). |
| #5 Tamper resistance | STRENGTHENED — scoring now runs inside `ScopedEgressAllowlist([])` (deny-all) inside Scorer subprocess; previously ran on API edge with no egress scope. |

## Rollback

| Tier | Action | RTO | Trigger |
| --- | --- | --- | --- |
| 1 | `ARE_SCORER_REAL=off` env flip | ≤10 min | AG-A2 N=5 spread > 0 in prod; OrchestratorError rate > 5% on Scorer return-channel drain |
| 2 | PR revert | ≤2 h | Tier-1 doesn't recover (e.g. flag-off path itself broken) |
| 3 | Parent flag `ARE_RESCANS_MEASURED=off` per ADR-0017 §6 | ≤30 min | Full rescan-path disable for any persistence-semantics regression |

Flag-off path = master `52c003e` `_phase1_pending` envelope (placeholder; AC-5 reads "structurally true, not customer-true"). Acceptable rollback posture because Track B is the AC-1/AC-2 customer-true gate; Track A is the scoring-callee plumbing.

## Open Questions (flagged for CTO at merge co-sign)

### OQ-1 — Contract version target

CTO design co-sign comment `9840343c` stated `v1.2.1 → v1.3.0`. Current `contracts/` directory already holds:

- `contracts/are-scanner-api-v1-v1.3.0-patch.md` — Path B telemetry-shim disambiguation (BEAAA-2023, CTO-authored, Founder-ruling-derived).
- `contracts/are-scanner-api-v1-v1.4.0-patch.md` — Agentic-pilot-readiness output surface (BEAAA-2474, CTO-authored DRAFT pending UW op-seat co-sign).

To avoid version collision, this ADR targets **v1.5.0**. Flagging deviation at merge co-sign; will hold `contracts/are-scanner-api-v1-v1.5.0-patch.md` publish until CTO confirms target version (or instructs alternative — e.g., addendum to v1.3.0).

### OQ-2 — UW op-seat co-sign on contract bump

Per CTO design co-sign comment `9840343c` — `uw_cosign_ref` is "pending pre-merge (contract bump v1.2.1 → v1.3.0)". Approval-gates row "CTO + Underwriter co-sign — rule changes that affect the contract output shape" governs. Surface to UW op-seat at the merge co-sign step (after AG-A2 N=5 spread=0 passes locally so UW has the calibration delta in hand).

## Metadata pre-bake (Supreme Law §All implementation actions ship with metadata)

- `scanner_version`: target `0.3.11` (per CTO co-sign comment)
- `corpus_version`: `0.1.0` (unchanged in Track A — synthetic walker still emits 4 dims; Track B is the corpus-bump ticket)
- `probe-set-version`: unchanged (no probe touch in Track A)
- `rubric_point_id` range affected: **none** (no rubric edit; Scorer scoring is the same `rubric.scoring.score` callee CLI uses)
- `N=5 calibration delta`: pending — see AG-A2; `scripts/beaaa2182_a_calibration.py` produces the JSON+MD pair before merge co-sign request
- `cto_cosign_ref` (design): [BEAAA-2182](/BEAAA/issues/BEAAA-2182) comment `9840343c` (2026-06-05, CTO ACCEPTED)
- `cto_cosign_ref` (merge): pending — request raised at end of this heartbeat
- `houw_cosign_ref` (Track A): not required per CTO ("pure IPC plumbing, no probe-set/judge/rubric touch")
- `uw_cosign_ref`: pending — OQ-2

## Why this exists

Without Track A, the BEAAA-2175 PR-B `_production_score_envelope_factory` that fills `_phase1_pending` with real values **runs the scoring callee on the API edge** — outside the Scorer's deny-all egress scope. The tamper-resistance posture in `contracts/are-scanner-api-v1@§Hard-guarantees(CTO) #5` weakens if the agent-under-scan can ever route adversarial content into the scoring engine's import graph. Moving the scoring callee into the Scorer subprocess (fork-inherited Rubric; ScopedEgressAllowlist([]); IPC allow-list governing both directions) closes that surface. Track A is the load-bearing prerequisite for parent [BEAAA-2132](/BEAAA/issues/BEAAA-2132) AC-1 / AC-2 to be customer-true rather than structurally true; Track B builds on top once Track A merges.
