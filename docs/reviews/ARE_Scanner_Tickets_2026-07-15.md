# ARE Scanner — Review Findings as Tickets

Source: code review of `master` @ `7f2dc61` (2026‑07‑14 snapshot). Severity: **HIGH** = fix before live pricing; **MED** = fix before pilot with production wiring; **LOW** = hygiene/robustness. IDs are placeholders (`REV-*`) — renumber to your BEAAA scheme.

Patches attached for the two HIGH items: `F2-quote-fail-closed.patch` (ship-ready) and `F1-admittance-stop-fail-closed.patch` (**proposed — needs UW co-sign**, changes a deliberately-encoded test).

---

## HIGH

### REV-001 (F1) — `ADM.R0.STOP` fails open on missing `scan_autonomy`  · filed **BEAAA-14397** (HeadOfUW)
**Component:** `underwriting/admittance.py`
**Severity:** HIGH — money path, fail-open. **Governance:** touches admittance semantics → UW co-sign required per README approval gates.
**Repro:** Build a score envelope with `are_total ≥ 85`, `measured_dims ≥ 6`, `na_dims ≤ 2`, and `behavioral_integrity.stop_compliance.stat_sig_noncompliance = true`, but **omit** the `scan_autonomy` field. `admittance.evaluate(envelope)` returns `ADM.R8 / AUTO_BIND` instead of `ADM.R0.STOP / REFER_TO_UW`. `tests/test_admittance.py::test_c_read_only_scan_stop_na_unaffected` currently asserts this AUTO_BIND outcome as intended.
**Root cause:** `_r0_stop_knockout` fires only when `scan_autonomy ∈ {autonomous, tool_enabled}`; a missing/None value is treated as read-only-exempt. But a stat-sig STOP non-compliance signal can only be produced by an autonomous/tool scan, so a dropped autonomy marker on a real autonomous scan silently auto-binds a controllability failure — contradicting README lines 48-49.
**Fix (proposed, in patch):** fire the knockout whenever `stop_stat_sig_noncompliance is True` and `scan_autonomy` is not an **explicit** read-only marker (`_READ_ONLY_SCAN_KINDS`); missing/None → fail closed to `REFER_TO_UW`. Also adds a `scan_autonomy` kwarg to `evaluate()` so UW can carry it from the submission record (same pattern as `system_prompt_kind`). Updates `test_c` to use an explicit `"read_only"` marker and adds `test_c_prime` (missing → REFER) and `test_c_dprime` (kwarg override).
**Decision needed:** confirm the read-only marker vocabulary and that flipping `test_c` to `REFER_TO_UW` is the intended semantics.

### REV-002 (F2) — `quote()` prices normally on an unrecognized `admittance_class`  · filed **BEAAA-14396** (CTO)
**Component:** `pricing/quote.py`
**Severity:** HIGH — money path, fail-open. **Governance:** additive fail-closed guard, no scoring-math change; safe to ship.
**Repro:** `quote(score, customer, rates=..., admittance_decision={"admittance_class": "refuse", ...})` (lowercase, or `None`, or any typo) returns a normal full-limit quote with `f_caveat = 1.0` instead of refusing.
**Root cause:** the class dispatch handles `REFUSE`, `REFER_TO_UW`, `CONSTRAINED_BIND`, `AUTO_BIND` but has no `else`; unknown values fall through to the rated path.
**Fix (in patch):** after the REFUSE/REFER handling, `raise ManualReferral` for any class not in `{CONSTRAINED_BIND, AUTO_BIND}`. Adds 4 tests (lowercase-refuse, unknown string, None, and a guard that recognized classes still price). All 85 pricing/admittance tests pass.

---

## MEDIUM

### REV-003 (F3) — live-probe scorer uses the last replicate, not the K-replicate median
`scanners/live_probe_workers.py:750-753` + `score_runtime.py:357`. The orchestrator computes a per-dim median into `ScanResult` but the Scorer accumulates last-write-wins and scores the persisted envelope from that. Nondeterministic agents get two different `are_total` on the two authoritative surfaces. Golden roster is deterministic so the N=5 gate never catches it. **Action:** score the envelope from the same median `ScanResult` uses; add a nondeterministic-agent replay test.

### REV-004 (F4) — inverted pillar ordering in the live-probe path
`scanners/wraps/inspect_section1_v0.py:149-185`, `scanners/live_probe_orchestrator.py:173-174`. Comments/logic assume `P1=GOV, P2=ROB, P3=BIAS, P4=OPS` — the inverse of `rubric.yaml`. Under `ARE_WALKER_CUSTOMER_AGENT=1` every live pillar subscore measures the wrong peril; fully-qualified dims (`ROBUSTNESS.TOOL_MISUSE.*`, PII dims) drop out of `pillar_spread` because `_pillar_of` only matches `P1..P4`. **Action:** reconcile against `rubric.yaml`; extend `_pillar_of` to resolve named dims.

### REV-005 (F10) — ~20% of PR #71 text cases are byte-identical duplicates
`tools/text_case_materializer.py` (`_stable_select`, `_garak_case`). `sha256(case_id) % len(fixtures)` distributes more registry cases than fixtures, so 113 target-bound rows → only 90 distinct prompt+oracle pairs. Non-independent evidence over-weights a single probe in ROBUSTNESS scoring and overstates distinct coverage. **Action:** fail (or warn+dedup) when distinct cases collapse to one fixture; don't count collisions toward distinct coverage.

### REV-006 (F5) — SSRF hardening missing at the API boundary (defended in depth today)
`api/scan_handlers.py:858-870` (`agent_endpoint.host`) and `:1199-1205` (`webhook.url`) validate syntax only. The egress layer (`harness/egress.py:_is_forbidden_resolved_ip`) does block private/loopback/metadata IPs and is wired in, so no live SSRF — but the webhook path has no range check and is currently unwired. **Action:** replicate `intake_guard` scheme/host discipline at the API boundary; range-check `webhook.url` before wiring delivery.

### REV-007 (F7) — shared sqlite connection written from multiple threads without a lock
`api/scan_log.py:182-184` (`check_same_thread=False`), `scan_handlers.py:529-571`. Background full-scan daemon + inline request threads both call `append_scan`; `_state_lock` guards only `self._results`. Concurrent `execute()` can fail an audit-log write (a red line). **Action:** serialize scan-log writes with a dedicated lock or a per-thread connection.

### REV-008 (F8) — background full-scan thread swallows all exceptions with no log
`api/scan_handlers.py:512-525`. `except Exception` records a generic failed result and emits no LOGGER line, so an `append_scan` failure on the background path is silent. **Action:** log at error level (without leaking credentials) before marking failed.

### REV-009 (F9) — JCS float encoding violates RFC 8785 for `1e-6 ≤ |x| < 1e-4`
`api/jcs.py:124-153` falls back to `repr()`; `1e-05 → "1e-5"` where RFC 8785 requires `"0.00001"`. External re-canonicalizers compute a different `snapshot_hash`. **Action:** implement the ECMAScript `Number::toString` shortest-round-trip rules for the sub-1e-4 range, or document the divergence and stop claiming cross-consumer recomputability.

### REV-010 (F11) — score() accepts unvalidated caller-supplied N/A decisions (inflation path)
`rubric/scoring.py:213-250`, `harness/cli_runner.py:375-383`. A caller can mark its worst dims `fires:false` (via a `scanner_invocation_context.json` flag) and inflate `are_total` ~8 points, traced only by a `coverage_caveat`. Counterpart bug: on the normal path the sidecar is silently discarded (F19). **Action:** validate `applicability_decisions` against `applicability_policy.yaml` + `active_dim_ids`; surface a diagnostic when a sidecar is ignored.

### REV-011 (F12) — CI gate fail semantics differ GitLab vs GitHub for exit 75
`ci_plugin/templates/.gitlab-ci.yml:31-34`, `ci_plugin/gate.py:133-166`. Exit 75 greens GitLab but reds GitHub; returned on transport failure, poll timeout, and missing score/threshold — so a blocked egress or unconfigured band silently passes on GitLab. `client.py:39-41` also raises uncaught `JSONDecodeError` on a non-JSON 200. **Action:** unify the two surfaces' outage semantics; classify non-JSON responses.

### REV-012 (F13) — report card crashes on N/A dims (`raw: None`)
`report_card/diff.py:42,74`, `reproducibility.py:50`, `render.py:625-627`. `float(entry.get("raw", 0))` raises `TypeError` when `raw` is present-but-None (the N/A shape); `_compute_display_weighted` also averages over all dims instead of measured-only, diverging from the score JSON. **Action:** guard None → treat as N/A; normalize over measured dims to match the engine.

### REV-013 (F14) — demo service auth fails open unless `DEMO_ENVIRONMENT=public-demo`
`demo-real-agent/app.py:262-264,363-366`, Dockerfile defaults to `container`. Any manual `docker run` exposes an unauthenticated endpoint spending real judge budget. **Action:** require a token whenever one is configured regardless of environment; default-deny.

### REV-014 (F15) — flagship `tests.yml` installs an unco-installable dep graph via legacy resolver
`.github/workflows/tests.yml:36`. `requirements.txt` says (in caps) it is not co-installable (garak vs pyrit `datasets` conflict); the legacy resolver last-wins-resolves it. **Action:** run the suite in the per-framework venvs (ADR-0056) instead of one forced environment.

### REV-015 (F16) — N=5 calibration gate silently shrinks as golden agents flip to "pending"
`scripts/beaaa6190_v05_rc_n5.py:59-66`, `tests/test_golden_agents.py:116`. 3/9 golden agents are already `pending_baseline_scan` and skipped; no minimum live-roster count is enforced. **Action:** assert a minimum live golden count; fail the gate if the roster drops below it.

### REV-016 (F17) — post-commit auto-push PAT helper is not host-scoped
`.githooks/post-commit:95-96`. The inline credential helper answers any credential request for any URL with `GITHUB_PAT_ARE_SCANNER`; a re-pointed `origin` exfiltrates the PAT on the next commit. **Action:** pin `host=github.com` / the expected remote URL in the helper.

### REV-017 (F18) — zero GitHub Actions pinned by SHA
All 10 workflows use mutable tags; jobs hold `packages: write` + keyless-cosign `id-token: write` and SSH deploy creds. **Action:** pin every `uses:` to a full commit SHA.

---

## LOW (batch)

- **F19** — applicability sidecar silently discarded on the normal CLI path (`harness/cli_runner.py:375-383`); no diagnostic. Pair with REV-010.
- **F20** — dim-count contradiction: `rubric.yaml:3` & `README.md:26` say 25, `rubric.yaml:326` says 27 (actual). Add a code invariant asserting the active-dim count.
- **F21** — `bin/are-scan:183-188` promises a partial result on refuse-to-quote but emits none (`result` never assigned).
- **F22** — snapshot-hash TOCTOU: `eval_results.json` read twice (`cli_runner.py:229-238`); rescan `snapshot_hash` = `sha256("rescan|{scan_id}")` binds no rows.
- **F23** — audit-spine ledger append has no lock + O(n²) re-read (`scanners/canary_anti_gaming.py:258-299`); concurrent writers → false tamper alarm.
- **F24** — empty probe-item lists silently score a dim `raw=0.0` (`agentdojo_runner.py`, `pyrit_runner.py`, `inspect_runner.py`); a loader misconfig looks like a catastrophic agent failure.
- **F25** — malformed JSON body → HTTP 500 + spurious error log instead of 4xx (`api/app.py:272,323,366`).
- **F26** — `openapi.yaml` omits the live routes (`/scanner/v1/scans`, `/rescans`, `/sla`).
- **F27** — runtime openai pin drift: `requirements-runtime.txt:18` (1.54.4) vs `requirements.txt:87` (1.58.1) despite an in-file parity claim.
- **F28** — `are_total` banker's rounding at band boundaries (`rubric/scoring.py:312`); specify half-up or half-even explicitly.
- **F29** — live third-party key committed: `scripts/beaaa334_wall_regress.py:57` (publicly-observable widget key; rotate + move to env). No other real secrets found.
- **F30** — README documents `unittest discover` but CI runs `pytest` (`tests.yml:46`); the documented command fails because `conftest.py` never loads. `cryptography==41.0.7` (dev pin) has known CVEs.
- **F31** — newest "verification" tests are self-referential artifact pins (`test_beaaa14142_*`, `test_beaaa14134_*`); fine as regression pins, not tamper evidence.
