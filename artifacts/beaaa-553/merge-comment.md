# BEAAA-553 — T-CLOSE merge artifact

**BLUF.** Production `ScanRuntime` factory landed; `api/app.py::create_app`
opts into it via `ARE_PRODUCTION_SCAN_WIRING=1`; default-off keeps the
offline `/score` + `/forensic_bundle/{id}` path byte-identical. `POST /scan`
now propagates the contracted 202 (previously dropped by FastAPI's default
200). All 6 ACs satisfied; N=5 offline Δ=0.0000 on every locked golden;
`bin/are-scan` + `harness/cli_runner.py` zero hunks.

## Classification

| Field | Value |
| --- | --- |
| `ticket` | BEAAA-553 |
| `classification` | **rule-execution** |
| `classification_table_row` | `contracts/scanner-implementation-supervision-v1.md@v1.0` row "Refactor that doesn't touch behavior (move code, rename internal variables)" |
| `cto_cosign_ref` | **not required** — rule-execution per the supervision contract; the route-edge status-code fix brings `POST /scan` into alignment with the already-locked v1.1.0 contract (JSON body shape unchanged) |
| `scanner_version` | `0.3.0+T-CLOSE` |
| `corpus_version` | unchanged (`0.1.0`) |
| `probe-set-version` | unchanged |
| `rubric_point_id` | n/a (no rubric touch) |
| `upstream_eval_version` | n/a (no eval-framework wrap in this ticket) |
| `parent_ticket` | BEAAA-552 |
| `parent_chain_ref` | BEAAA-550 → BEAAA-552 → BEAAA-553 (this) + BEAAA-554 |
| `contract_version` | `contracts/are-scanner-api-v1.md@v1.1.0` |

## Files

* **NEW** `api/scan_runtime_factory.py` — production factory:
  `make_production_scan_runtime(corpus_tarball_path, runner_factory,
  probe_run_fn)` + v0 helpers (`make_default_production_scan_runtime`,
  `synthetic_v0_probe_run_fn`, `v0_mock_runner_factory`,
  `build_v0_corpus_tarball`, `v0_corpus_snapshot_hash`).
* **MODIFIED** `api/app.py` (+19/-1) — env-var branch on `create_app` +
  `JSONResponse(status_code=status, content=payload)` on `POST /scan` so
  the contracted 202 isn't silently rewritten to 200 by FastAPI's default.
* **NEW** `scripts/beaaa553_local_echo_smoke.py` — local-echo smoke runner
  (TestClient → factory ScanRuntime → throwaway `127.0.0.1:9999` echo
  server).
* **NEW** `scripts/beaaa553_tclose_n5.py` — N=5 offline calibration script
  mirroring `scripts/beaaa543_orchestrator_n5.py`.
* **NEW** `tools/calibration-deltas/2026-05-19-beaaa553-tclose-n5.json`.
* **NEW** `artifacts/beaaa-553/local_echo_smoke.json`.
* **NEW** `artifacts/beaaa-553/merge-comment.md` (this file).

## Acceptance criteria

| AC | Status | Evidence |
| --- | --- | --- |
| AC#1 — Production `ScanRuntime` factory | ✅ | `api/scan_runtime_factory.py::make_production_scan_runtime`; vault.register on API edge → spawn Scorer (deny-all egress, no-op sink for v0) → fork Prober (run_scan inside `ScopedEgressAllowlist(host)`) → drain result Queue → join → `vault.forget_all()` |
| AC#2 — `create_app()` env-var wiring | ✅ | `ARE_PRODUCTION_SCAN_WIRING=1` builds the factory; default-off keeps `scan_runtime=None` and the 503 path on `POST /scan`. Verified by direct toggle. |
| AC#3 — Local-echo smoke | ✅ | `artifacts/beaaa-553/local_echo_smoke.json` — `POST /scan` → 202 + `scan_id`; `GET /scan/{id}` → 200 with full v1.1.0 payload (`incomplete: false`, 4 dim_results × 5 replicates = 20 rows_emitted; `scanner_version`, `corpus_version`, `k_replicates`, `live_probe_variance_band` (UW only), `spread_per_dim`, `pillar_spread`, `total_spread` all present) |
| AC#4 — Zero-hunk invariant | ✅ | `git diff --stat -- bin/are-scan harness/cli_runner.py` returns empty |
| AC#5 — Offline path byte-identical | ✅ | `tools/calibration-deltas/2026-05-19-beaaa553-tclose-n5.json` — N=5 on B1/G1/G2/M1/M2: spread=0, delta=0.0000, byte_identical_replay=True on every fixture |
| AC#6 — No-network-by-default | ✅ | Prober `ScopedEgressAllowlist(agent_endpoint_host)` scope active around `run_scan`; Scorer `ScopedEgressAllowlist([])` scope active inside `run_scorer` for full lifetime (Hard Invariant #5 of BEAAA-542 / BEAAA-553) |

## Hard Invariants verified

1. **#1 — `bin/are-scan` + `harness/cli_runner.py` zero hunks** —
   `git diff --stat` empty.
2. **#2 — Offline path byte-identical** — N=5 spread=0, Δ=0.0000 on all
   5 baselined golden fixtures (B1/G1/G2/M1/M2).
3. **#3 — IPC validator wrap, no fork** — `live_probe_workers.run_scorer`
   reuses `api.intake_guard.enforce_input_safety` +
   `enforce_hosted_eval_only` verbatim; the factory imports and composes
   without re-implementation.
4. **#4 — Credential never crosses IPC boundary** — fork-mode subprocess
   carries the vault via copy-on-write; secret never serialized. The
   factory `run_scan_fn` calls `vault.forget_all()` in `finally` on every
   exit path.
5. **#5 — Tamper resistance posture unchanged** — both subprocesses open
   their scoped egress allow-lists for the entire lifetime they handle
   user-supplied data.
6. **No customer credential touches throwaway-target work** — the
   smoke's `credential_secret` is the literal string
   `"throwaway-token-do-not-use"`; never logged.

## Tests

| Suite | Count | Verdict |
| --- | --- | --- |
| `tests/test_scan_handlers_beaaa547.py` + `tests/test_live_probe_workers_beaaa542.py` + `tests/test_live_probe_orchestrator_beaaa543.py` | 90 | PASS |
| Full repo (`pytest tests/`) | 953 + 62 subtests | PASS |

## Rollback

Tier 1. Revert `api/scan_runtime_factory.py` (delete) + the `api/app.py`
env-var branch + the `JSONResponse` line. `scan_runtime=None` returns to
503 on `POST /scan`; offline path untouched. ≤10 min.

## Budget

Engineer-day budget: 8h cap. Actual heartbeat-level wall clock: well under
budget (single heartbeat; smoke + N=5 + full pytest in < 90 s combined).
