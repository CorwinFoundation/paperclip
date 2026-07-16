# ARE Scanner — Code Review

**Reviewed:** `development/scanner/are-scanner-demo-real-90-pr` @ `master` commit `7f2dc61` (merge of PR #71 "feat/demo-real-90-execution", 2026‑07‑14). Snapshot exported from `git archive HEAD`, so uncommitted working-tree changes in that checkout are **not** covered.
**Scope:** full Python tree (~155k LOC, 611 files) plus `rubric.yaml`, `admittance_rules.yaml`, CI workflows, git hooks, Dockerfiles, and requirements pins. Reviewed by subsystem, with the highest-severity findings re-verified against source.
**Method:** five parallel subsystem passes (scoring engine, API/security, execution/tooling incl. the new PR #71 code, downstream money path, tests/CI/hygiene). Top money-path and security findings independently re-checked line-by-line.

---

## Executive summary

This is a serious, well-engineered codebase. The **deterministic scoring core is genuinely strong**: `rubric/scoring.py`, `bands.py`, and the calibration-curve registry are small, well-specified, and enforce their stated red lines (weights sum to 100, raw ∈ [0,4], missing-dim raises, corpus pinned). Tests around the core are real — `test_bands.py` pins all 12 band boundaries to exact values, `test_scoring.py` asserts exact BMI values, `test_golden_agents.py` does byte-identical comparison against locked expected scores — and the N=5 calibration gate actually runs on every push/PR and exits non-zero on drift. The suite runs clean locally (2,387 / 2,424 pass; the 16 failures are all environmental — missing `.git`, optional eval deps). No `assertTrue(True)`, no hidden xfails, no tests mocking the scorer they claim to test.

**The risk is not in the core — it is at the seams.** The most dangerous findings are all integration mismatches that no single module's invariants (and no golden-roster replay, which is deterministic end-to-end and therefore blind to them) would ever catch. Two of them sit directly on the money path and **fail open** — the direction that underprices or over-binds risk rather than erroring. Those are the ones to fix before this wiring feeds live pricing.

Severity counts: **2 high (money-path fail-open), ~10 medium, ~12 low.** No confirmed critical (the one candidate — SSRF — is defended in depth at the egress layer; see F5).

---

## High severity — fix before live pricing

### F1. `ADM.R0.STOP` fails open when `scan_autonomy` is missing → a stat-sig STOP failure can still AUTO_BIND at Preferred-A
`underwriting/admittance.py:494-504` (predicate), `:228-230` (accessor), `:663-705` (`evaluate`)

```python
def _r0_stop_knockout(ctx):
    return (ctx["scan_autonomy"] in _AUTONOMOUS_SCAN_KINDS      # {"autonomous","tool_enabled"}
            and ctx["stop_stat_sig_noncompliance"] is True)
```

`scan_autonomy` is read **only** from the score envelope (`_scan_autonomy → score_or_report.get("scan_autonomy")`, returns `None` if absent). Unlike `system_prompt_kind` / `insured_agent_ref`, which get explicit `evaluate()` kwargs precisely because "the Scanner contract does not promise them," there is **no caller-supplied override** for `scan_autonomy`, and a missing value (`None ∉ _AUTONOMOUS_SCAN_KINDS`) is silently treated as "read-only scan → exempt," so the knockout never fires and the candidate falls through to `R8` AUTO_BIND.

**Failure scenario:** an envelope carrying `behavioral_integrity.stop_compliance.stat_sig_noncompliance: true` (only producible by an autonomous/tool scan) but with `scan_autonomy` dropped/renamed/never stamped by the assembling layer, scoring ≥85, **auto-binds at Preferred-A** — the exact outcome README lines 48-49 promise cannot happen ("a stat-sig STOP failure on an autonomous/tool scan hard-blocks Preferred-A auto-bind"). This inverts the fail-closed discipline the same file applies to missing provenance (`ADM.R0.FACT`). When `scan_autonomy` *is* present the README claim checks out — `R0.STOP` correctly precedes R7/R8 and routes `REFER_TO_UW`. **Verified.** `tests/test_admittance.py:450-457` actually encodes the buggy behavior as expected.
**Fix:** treat missing `scan_autonomy` on a STOP-noncompliant envelope as fail-closed (route `REFER_TO_UW`), or require it as an evaluated field like the other UW-carried metadata.

### F2. `quote()` silently prices on an unrecognized `admittance_class` — no fail-closed default
`pricing/quote.py:587-622, 679-691`

The class dispatch handles `REFUSE` (raise `Ineligible`), `REFER_TO_UW` (raise `ManualReferral`), `CONSTRAINED_BIND`, and `AUTO_BIND` — but there is **no `else`**. Any other value (`None`, lowercase `"refuse"`, a typo, a corrupted serialized intake record — and `_decision_as_dict` explicitly accepts raw dicts "from serialized intake records") falls straight through to the full rated path with `f_caveat = 1.0`, no exclusion endorsement, and no `admittance` block in the breakdown.

**Failure scenario:** a `REFUSE` decision persisted with a lowercase class field is re-quoted → a formally declined agent receives a normal premium. The report card already defends against exactly this (`report_card/render.py:980` normalizes with `.upper()`); the money path does not. **Verified.**
**Fix:** add a terminal `else: raise ManualReferral(...)` (or `Ineligible`) so any unknown class fails closed.

---

## Medium severity

### F3. Scoring engine: live-probe scorer uses the *last* replicate's raw, not the K-replicate median
`scanners/live_probe_workers.py:750-753`, `scanners/live_probe_orchestrator.py:557-614`, `scanners/score_runtime.py:357`
The orchestrator computes the per-dim **median** across K replicates into `ScanResult.dim_results`, but the Scorer accumulates `accumulated[dim_id] = entry` ("last-write-wins on duplicate dim_id") and scores the persisted §2.2 envelope from that dict. For a nondeterministic agent the two authoritative surfaces (`ScanResult` vs. persisted envelope) can report different `are_total` — the envelope score being an arbitrary endpoint sample. Deterministic agents (incl. the entire golden roster) are unaffected, which is why the N=5 gate never catches it. *Reported by subsystem reviewer; plausible and worth the team confirming against a live/nondeterministic run.*

### F4. Scoring engine: two modules encode a **wrong pillar ordering**, so live probes score the wrong peril
`scanners/wraps/inspect_section1_v0.py:149-185`, `scanners/live_probe_orchestrator.py:173-174`
Both carry the comment/assumption `P1=GOV/OPS, P2=ROB/BIAS, P3=BIAS/ROB, P4=OPS/GOV` — the inverse of `rubric.yaml` (`P1=OPS, P2=BIAS_FAIRNESS, P3=ROBUSTNESS, P4=GOVERNANCE`). Under `ARE_WALKER_CUSTOMER_AGENT=1`, a jailbreak-refusal probe scores into OPS "hallucination," a truthfulness miss into ROBUSTNESS "prompt-injection," etc. — every live pillar subscore measures a different peril than its label. Relatedly, `_pillar_of` only recognizes `P1..P4` prefixes, so the five fully-qualified dims (`ROBUSTNESS.TOOL_MISUSE.*`, the two PII dims) map to `""` and drop out of the UW-facing `pillar_spread`. *Reported; the taxonomy inversion is concrete in the source and should be reconciled against `rubric.yaml` before the customer-agent walker is used for real scores.*

### F5. API: SSRF surface on `agent_endpoint.host` / `webhook.url` — validated for syntax only (defended in depth, but the front door is open)
`api/scan_handlers.py:858-870` (`_validate_agent_endpoint`), `:1199-1205` (webhook URL)
The API layer validates the host against a hostname regex only — no private/loopback/link-local/metadata-range check, and no forced `https://` (in contrast to `intake_guard.enforce_input_safety`, which does both). **Mitigating:** I verified the real block exists one layer down — `harness/egress.py:_is_forbidden_resolved_ip` (`:324-333`) rejects private/loopback/link-local/multicast/unspecified/reserved resolved IPs, and it is actually wired into `ScopedEgressAllowlist` scoping (`:623-638`), which the runtime factory opens around every `run_scan`. So `169.254.169.254` / `localhost` are caught at egress-scope creation. The finding is therefore **not** an exploitable SSRF today, but the API surface relies entirely on that downstream guard and the webhook path (`_fire_webhook`) has no range check and is currently unwired — the moment a poster is injected it is exploitable.
**Fix:** replicate the `intake_guard` scheme/host discipline at the API boundary (defense in depth), and range-check `webhook.url` before wiring delivery.

### F6. API: async full-scan concurrency lease released before the scan finishes → `max_concurrent` cap not enforced
`api/app.py:316-340`, `api/scan_handlers.py:439-466, 512-525`
For full-tier scans, `ScanRuntime.kickoff` spawns a daemon thread and returns `queued`/202 immediately; the `finally` block releases the `ConcurrencyLease` at once, so the D3 max-concurrent cap never bounds the background work it is meant to bound. The app comment ("runtime executes the scan inline") is false under production scan wiring. *Reported.*

### F7. API: one shared sqlite connection (`check_same_thread=False`) written from background + inline threads without a lock
`api/scan_log.py:182-184`, `api/scan_handlers.py:529-571`
`_state_lock` guards `self._results` but not `append_scan`. Concurrent `execute()` on a single `Connection` from the full-scan daemon and an inline smoke-tier request can raise "cannot start a transaction within a transaction" / cursor errors — failing an **audit-log write**, a stated red line. *Reported.*

### F8. API: background full-scan thread swallows all exceptions with no log line — incl. audit-write failures
`api/scan_handlers.py:512-525`
`except Exception as exc:` records a generic `failed` result and emits **no** `LOGGER` call, so an `append_scan` failure on the background path produces zero operator signal. *Reported.*

### F9. API: JCS float encoding violates RFC 8785 for `1e-6 ≤ |x| < 1e-4`
`api/jcs.py:124-153`
Falls back to Python `repr()`, whose exponential threshold (`<1e-4`) differs from ECMAScript `Number::toString` (`<1e-6`): `1e-05 → "1e-5"` where RFC 8785 requires `"0.00001"`. Internal write/read is self-consistent, but an external verifier re-canonicalizing the JSON computes a different `snapshot_hash`, weakening the "any consumer can recompute the hash byte-for-byte" guarantee. *Reported; empirically demonstrated by the reviewer.*

### F10. Execution: ~20% of the new PR #71 "text cases" materialize to byte-identical prompt+oracle pairs → non-independent evidence
`tools/text_case_materializer.py` (`_stable_select` `:670-674`, `_garak_case` `:498-513`)
`index = sha256(case_id) % len(fixture_rows)` distributes more registry cases than there are backing fixtures, so distinct `case_id`s collapse to the same prompt **and** the same expected contract. Confirmed empirically by the reviewer: 113 target-bound rows → only **90 distinct** payload+oracle pairs (e.g. `P3.3-ROB-05/06/10` all resolve to fixture `ex-007`; four garak DAN cases emit one identical prompt). Three "separate" secret-exfil tests that are the same probe feed three non-independent signals into ROBUSTNESS scoring, over-weighting one probe and overstating distinct coverage. This is the most material integrity issue in PR #71 and should be fixed before these cases feed underwriting scores.

### F11. Money-path applicability: `score()` accepts arbitrary caller-supplied N/A decisions; CLI lets scanned-agent inputs invoke them (score-inflation path)
`rubric/scoring.py:213-250`, `harness/cli_runner.py:175-181, 375-383`
`score()` never validates `applicability_decisions` against `applicability_policy.yaml` or `active_dim_ids`; a dim marked `fires:false` is skipped before its raw is consulted and the pillar mean re-normalizes upward. The activation gate (`scanner_invocation_context.json` with `applicability_mode: legacy_replay`) is itself a caller-supplied file. An agent owner marking their two worst dims N/A can inflate `are_total` by ~8 points with only a `coverage_caveat` diagnostic as trace. *Reported.* (Counterpart bug F19: on the normal path the sidecar is silently discarded.)

### F12. CI plugin gate has opposite fail semantics on GitLab vs GitHub for the same exit code
`ci_plugin/templates/.gitlab-ci.yml:31-34`, `ci_plugin/gate.py:133-166`, GitHub `action.yml`
Exit 75 is returned on transport failure after retries, on poll timeout, **and** whenever `score`/`threshold` are both absent — and GitLab `allow_failure.exit_codes:[75]` greens the pipeline, while GitHub Actions treats a 75 container exit as a **failed** step. So a blocked egress to `api.backbond.ai` (or a vendor with no policy band) yields a green build on GitLab (documented "never red on outage" choice, but it means network-error→pass) and a red build on GitHub — the two surfaces disagree. Also `client.py:39-41`: a 200 with non-JSON body raises uncaught `JSONDecodeError`. *Reported.*

### F13. Report card crashes on N/A dims (`raw: None`) and mis-normalizes partial pillars
`report_card/diff.py:42,74`, `report_card/reproducibility.py:50`, `report_card/render.py:625-627`
`float(entry.get("raw", 0))` does not default when the key exists with value `None` — which is exactly how N/A dims are emitted (`render.py:573-575`). Quarterly-diff, reproducibility, and contract-v1.0 display-weight re-render all raise `TypeError` on any scan containing one N/A dim; and `_compute_display_weighted` averages over *all* rubric dims while the engine normalizes over measured dims only, producing a rendered-vs-score-JSON mismatch. *Reported.*

### F14. Demo service auth fails open unless `DEMO_ENVIRONMENT == "public-demo"` — but the image defaults to `"container"`
`demo-real-agent/app.py:262-264, 363-366`, `demo-real-agent/Dockerfile`
No token configured → every `POST /api/runs` is authorized, and the token-required check runs only for `public-demo`. The sanctioned `deploy.sh` path forces `public-demo` + a 32-char token (safe), but any manual `docker run` / staging / port-forward exposes an unauthenticated endpoint that spends real judge budget against a live agent. *Reported.*

### F15. Tests/CI: flagship `tests.yml` gate installs a knowingly-unco-installable dependency graph via the legacy resolver
`.github/workflows/tests.yml:36`
`pip install --use-deprecated=legacy-resolver -r requirements.txt -r requirements-dev.txt`, where `requirements.txt` itself states (in caps, twice) it is "NOT CO-INSTALLABLE IN ONE VENV BY DESIGN" (garak needs `datasets<2.17`, pyrit needs `datasets>=3.0.0`). The full suite runs in an environment pip could never legitimately produce; a pin reorder silently flips which `datasets` wins. The clean per-framework-venv design (ADR-0056) exists but the flagship gate bypasses it. *Reported.*

### F16. Tests/CI: N=5 calibration gate silently shrinks as golden fixtures flip to "pending"
`scripts/beaaa6190_v05_rc_n5.py:59-66`, `tests/test_golden_agents.py:116`
Agents with `status != "live"` are skipped by both the gate and the golden test (which only asserts `len(live_ids) > 0`). **3 of 9** golden agents (B2, B3, G3) are already `pending_baseline_scan`; no minimum live-roster count is enforced, so a PR that breaks B-slot scoring and flips those agents to pending still passes the gate. *Reported.*

### F17. Tests/CI: post-commit auto-push credential helper is not host-scoped
`.githooks/post-commit:95-96`
The inline helper answers **any** credential request for **any** URL with `GITHUB_PAT_ARE_SCANNER` (escaping is correct — the token never hits argv/disk). An edited/typo'd/re-pointed `origin` (any HTTPS host) exfiltrates the PAT on the next commit to a `beaaa-*` branch, automatically. Should pin `host=github.com` / the expected remote. Branch-guard logic itself is sound. *Reported.*

### F18. Tests/CI: zero GitHub Actions pinned by SHA, in a repo whose product claim is tamper resistance
all 10 workflows
Every action uses a mutable tag (`checkout@v4`, `cosign-installer@v3`, `build-push-action@v6`, …). A compromised upstream tag runs in jobs holding `packages: write` + keyless-cosign `id-token: write` (`cd.yml:71-73`) and SSH deploy creds (`real-agent-demo.yml:100-110`) — it could sign and push a tampered image with a valid cosign signature. Positives: no `pull_request_target` anywhere; deploy jobs gated off PR events. *Reported.*

---

## Low severity (selected)

- **F19. Applicability sidecar silently discarded on the normal CLI path** (`harness/cli_runner.py:375-383`) — a legitimate N/A submission gets exit 2 with no hint the sidecar was ignored. Counterpart to F11.
- **F20. Dim-count contradiction in canonical artifacts** — `rubric.yaml:3` and `README.md:26` say "25 active dimensions"; `rubric.yaml:326` says "27 active dims (6+6+8+7)". Actual is 27 (P3.5/P3.6 activated). No code invariant enforces a dim count, so the drift is undetectable. **Verified.** Any downstream consumer hard-coding 25 mis-normalizes coverage.
- **F21. `bin/are-scan` refuse-to-quote path emits no partial result** despite an in-code comment promising it does (`bin/are-scan:183-188`) — `result` is never assigned. **Verified as dead intent.**
- **F22. Snapshot-hash TOCTOU** — `eval_results.json` is read once for the SHA and again for scoring (`harness/cli_runner.py:229-238`); a swap between reads yields an output attesting to different bytes than were scored. Rescan-path `snapshot_hash` is `sha256("rescan|{scan_id}")` — commits to no probe rows.
- **F23. Audit-spine ledger append has no locking + re-reads the whole file per append** (`scanners/canary_anti_gaming.py:258-299`) — two concurrent writers fork the hash chain → **false tamper alarm** + O(n²) cost.
- **F24. Empty probe-item lists silently score a dim raw=0.0** (`scanners/agentdojo_runner.py`, `pyrit_runner.py`, `inspect_runner.py`) — a loader misconfig masquerades as a catastrophic, bind-blocking agent failure with no diagnostic.
- **F25. Malformed JSON body → HTTP 500 + spurious error log** instead of 4xx (`api/app.py:272,323,366`).
- **F26. `openapi.yaml` omits the live primary routes** (`/scanner/v1/scans`, `/rescans`, `/sla`) while documenting a `deprecated` `/scan` (`api/openapi.yaml` vs `api/app.py:342-419`).
- **F27. Runtime pins drift from the release contract while claiming parity** — `requirements-runtime.txt:18 openai==1.54.4` vs `requirements.txt:87 openai==1.58.1`; the production edge image runs a different SDK than calibration recorded against.
- **F28. `are_total` uses banker's rounding at band boundaries** (`rubric/scoring.py:312 int(round(...))`) — `84.5→84`, `85.5→86`; unspecified half-even behavior at exact-.5 letter cuts.
- **F29. Live third-party API key committed** — `scripts/beaaa334_wall_regress.py:57 CHATLAB_APIKEY="17a2cb19-..."` (a publicly-observable widget key, low impact, but a hardcoded third-party credential used to probe a live endpoint). Full-repo secret sweep found nothing else real — all other AKIA/xoxb/PRIVATE-KEY hits are synthetic fixtures.
- **F30. README documents a test command that doesn't work** — claims `python -m unittest discover -v tests`; CI runs `pytest` (`tests.yml:46`). Under unittest discovery `conftest.py` never loads → spurious failures for anyone reproducing the documented gate. `cryptography==41.0.7` (dev/CI pin) has known CVEs fixed in 42.x.
- **F31. "Verification" tests for the newest tickets are self-referential** — `test_beaaa14142_contract_anchor.py` / `test_beaaa14134_retell_reissue.py` subprocess committed verifier scripts whose expected SHAs live in the same repo as the artifacts; regenerate both in one commit and the proof passes. Fine as regression pins; not tamper evidence.

---

## What's good (so the fixes don't obscure it)

- Deterministic core (`rubric/`, `bands.py`, calibration curves): small, well-specified, red lines enforced, byte-identical replay tests are real.
- Tenant isolation on `/score`, `/forensic_bundle`, `/quote`, `GET /scan/{id}` is consistently fail-closed (cross-tenant → 404, not a 403 existence oracle); bundle IDs are opaque random handles; HMAC signing uses constant-time compares.
- Egress allowlist genuinely blocks private/loopback/metadata ranges at resolve time (see F5).
- Pricing uses `Decimal`/`ROUND_HALF_UP`; revenue-band boundaries match the documented convention; unmatched limit tiers → `ManualReferral` (safe).
- Report card escapes all scan-derived text through `html.escape` — I found **no XSS**.
- PR #71 materialization is deterministic and evidence writes are atomic (temp + `replace`); partial failures land as `unresolved`, never silently as passes.
- License/infra code is fail-closed (Ed25519 with key-id pinning, clock-skew handling, hardened systemd unit, loopback-only edge); no hardcoded secrets beyond F29.

---

## Recommended fix order

1. **F1, F2** — money-path fail-opens. Small, surgical, high consequence. Do first.
2. **F4** — reconcile the pillar-ordering inversion in the live-probe path against `rubric.yaml` before the customer-agent walker scores anything real; **F3** — confirm/close the median-vs-scorer mismatch.
3. **F10** — dedupe the PR #71 text cases (or stop counting collisions as distinct coverage) before they feed scores.
4. **F5 (webhook range-check), F7, F8** — audit-path robustness before turning on production scan wiring + edge gateway.
5. **F15–F18** — supply-chain / CI hygiene (SHA-pin actions, host-scope the PAT helper, enforce a minimum live golden roster, fix the resolver hack).
6. **F13, F20, F30** — partial-coverage report crashes and the 25-vs-27 dim-count contradiction; documentation/test-command drift.

*Findings marked "Verified" were re-checked line-by-line against `7f2dc61`. Findings marked "Reported" come from the subsystem passes and are well-supported by quoted source but warrant a confirming test from the owning engineer, especially F3 (needs a nondeterministic-agent run) and F10 (needs the current fixture/registry counts).*
