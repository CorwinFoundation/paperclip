---
artifact_type: adr
owner: scanner-engineer
persona_audience: internal
phase: 2
canonical_terms_only: true
focus_claim: "BEAAA-13331 makes the full BackBond Scanner eval-framework release contract materializable for offline N=5 calibration / CI by giving each conflicting framework its own venv, without changing any scoring, rubric, corpus, or the production runtime slice."
contracts_referenced: ["are-scanner-api-v1@v1.3.0", "scanner-implementation-supervision-v1@v1.1"]
ticket_refs: ["BEAAA-13331", "BEAAA-12895", "BEAAA-13329", "BEAAA-7347", "BEAAA-6190"]
proof_points_cited:
  - "scanners/eval_framework_venv.py"
  - "scripts/build_eval_framework_venvs.sh"
  - "requirements-eval-garak.txt / requirements-eval-pyrit.txt / requirements-eval-agentdojo.txt / requirements-eval-inspect.txt"
  - "tests/scanners/test_eval_framework_venv.py"
  - "docs/ops/eval-framework-venvs.md"
classification: "rule-execution; wires the soft-import runners to a per-framework venv resolver; no scoring math, rubric, judge, corpus, probe-mapping, or contract output change"
scanner_version: "0.5.0"
corpus_version: "fixture-pinned per golden expected_score.json"
probe-set-version: "golden_agents_v1"
rubric_point_id: "release-wide/all active BackBond Scanner rubric points"
n5_calibration_delta: "PASS; total spread 0 and max pillar spread 0.0000 across live goldens B1/G1/G2/M1/M2/M3; byte-identical to expected"
upstream_eval_version: "garak 0.10.0, pyrit 0.6.0, agentdojo 0.1.35, inspect-ai 0.3.220 — all unchanged"
contract_output_shape_unchanged: true
non_score_moving: true
cto_cosign_ref: "PENDING — gated to CTO on PR per BEAAA-13331 acceptance"
version: 1.0
prior_version: null
self_audit_passed: true
escalations_noted:
  - "Classification is rule-execution under scanner-implementation-supervision-v1@v1.1 (wiring / offline tooling; runners already soft-import + guard on _AVAILABLE). Per the ticket the PR is still gated to CTO for review."
  - "This ADR closes the 'separate upstream-eval environment split' watch item ADR-0036 explicitly deferred."
---

# ADR-0056 — BEAAA-13331 Offline eval-framework per-venv strategy

## BLUF

Decision: materialize each conflicting offline eval framework in its **own
venv** (`garak-venv`, `pyrit-venv`, `agentdojo-venv`, `inspect-venv`), built by
`scripts/build_eval_framework_venvs.sh` from per-framework requirements slices,
and resolve framework availability + pinned version from the matching venv via
`scanners/eval_framework_venv.py` when the in-process import is unavailable.

Reason: `requirements.txt` — the full eval-framework release contract — can
never be installed in one interpreter. `garak==0.10.0` requires
`datasets<2.17`; `pyrit==0.6.0` requires `datasets>=3.0.0`. This is the exact
"separate upstream-eval environment split" ADR-0036 (BEAAA-7347) deferred.

This is non-blocking follow-up work to the production runtime hardening
(BEAAA-12895 / BEAAA-13329). It is **not** on the cutover critical path and
changes nothing in the production runtime slice.

## Context

ADR-0036 scoped the deterministic `scanner-regression` gate to a minimal,
non-conflicting slice (`requirements-scanner-regression.txt`) so the release
gate could reach its tests. Its "Watch" note left the real problem open:

> the full runtime environment still cannot install garak and PyRIT together
> under strict pip resolution. That should be handled as a separate
> upstream-eval environment split.

Offline calibration / CI still needs every framework available so the
garak / pyrit / agentdojo / inspect probe families can run. The runners already
soft-import their framework and guard on an `_AVAILABLE` flag
(`scanners/{pyrit,agentdojo,inspect}_runner.py`), and garak already runs
out-of-process via promptfoo/subprocess. So the fix is wiring, not a behavior
change.

## Decision

1. **Per-framework venvs.** `scripts/build_eval_framework_venvs.sh` builds one
   isolated venv per framework at `$ARE_EVAL_VENVS_DIR/<framework>-venv`
   (default root `/opt/are-scanner/eval-venvs`), each from its own slice
   (`requirements-eval-{garak,pyrit,agentdojo,inspect}.txt`). The pins in those
   slices are byte-identical to `requirements.txt`; the script verifies each
   import reports the pinned version and fails loudly on drift.

2. **Resolver.** `scanners/eval_framework_venv.py` exposes
   `resolve_framework(fw) -> (available, version)`. The soft-import runners keep
   their in-process `import` as the primary path and fall back to the resolver
   only when it fails:
   - no venv configured/present → `(False, "not-installed")` — byte-identical
     to the historical `except ImportError` branch;
   - venv present → subprocess-probe `<venv>/bin/python -c "import <mod>; print(mod.__version__)"`
     → `(True, "<version>")`, stamped exactly where the in-process version
     would have gone.

3. **De-footgun the contract files.** `requirements.txt` and
   `requirements-scanner-regression.txt` now state explicitly that the full
   contract is materialized across per-framework venvs, not one `pip install`.

4. **Phase-2 hook.** `run_in_venv(fw, args)` provides the subprocess boundary
   for a future native driver (real PyRIT orchestrator / `inspect_ai.eval()`
   inside the isolated venv). Phase-1 runners do not use it.

The upstream eval pins are unchanged: garak `0.10.0`, pyrit `0.6.0`, agentdojo
`0.1.35`, inspect-ai `0.3.220`. This is an environment-split decision, not a
version bump.

## Consequences

Positive: the full release contract is materializable for offline N=5
calibration / CI without asking pip to solve an impossible graph;
`requirements.txt` / `requirements-scanner-regression.txt` stop being a standing
footgun; version stamps on the emitted rows can be real for every framework
simultaneously.

Neutral: no scoring code, rubric file, judge model, corpus fixture, probe
mapping, or contract output shape changed. Score delta is zero by construction
and confirmed by the N=5 gate. Existing runner unit tests are unchanged
(they patch the module-level `_AVAILABLE` / `_VERSION` symbols, which still
exist and are still populated by the in-process import first).

Watch: for the inspect phase-1 in-process driver, `_INSPECT_AVAILABLE` can now
resolve True via a venv while the local `Sample`/`Score` symbols are absent;
`_require_inspect()` guards that case with a clear error. The phase-1 in-process
inspect path still requires `pip install inspect-ai` in the harness venv.

## Rejected alternatives

1. **Bump garak or PyRIT to a compatible `datasets` range.** Rejected — an
   upstream eval version bump is a rule-change (CTO co-sign + fresh N=5 delta)
   per `scanner-implementation-supervision-v1@v1.1`, and it would move the probe
   surface. The conflict is environmental, not a version-selection problem.

2. **pip legacy resolver / `--no-deps`.** Rejected — masks the incompatible
   `datasets` pins and produces an unsupported, non-reproducible environment.

3. **Fork the frameworks / vendor their probe code into ARE.** Rejected —
   violates the wrap-don't-fork rule; the subprocess boundary is the supported
   integration surface.

4. **Full in-process subprocess driver now (phase-2).** Deferred — the phase-1
   runners don't invoke the framework at execution time (prompts are
   pre-realized; `runner.complete()` + shared bench mappers score), so only the
   availability/version stamp needs relocating today. The `run_in_venv` hook is
   in place for when a native driver is ticketed.

## Verification

- `pip install -r requirements.txt` remains `ResolutionImpossible` (documented,
  by design) — the contract is now built via the per-venv recipe.
- New unit tests: `tests/scanners/test_eval_framework_venv.py` (unknown
  framework raises; unconfigured → `(False, "not-installed")`; present fake
  venv → `(True, version)`; broken venv degrades; `resolve_or_import`
  in-process fast path; registry pins match the release contract).
- Runner backward-compat: `tests/scanners/test_pyrit_runner.py`,
  `tests/test_inspect_runner.py`, `tests/test_pipeline_pyrit_p31_beaaa714.py`
  all pass unchanged (36 passed with the new resolver test).
- Import smoke: importing the three runners with no framework installed
  in-process exercises the resolver fallback and yields `not-installed`
  (byte-identical to before); pointing `ARE_EVAL_VENVS_DIR` at a fake pyrit
  venv flips pyrit to `(True, "0.6.0")`.
- Deterministic golden regression: `72 passed, 20 subtests passed`.
- N=5 gate (`scripts/beaaa6190_v05_rc_n5.py`): PASS; total spread 0, max pillar
  spread 0.0000 across live goldens B1/G1/G2/M1/M2/M3; byte-identical to
  expected.

## Rollback

Revert this commit. The runners return to a plain `except ImportError` →
`(False, "not-installed")` branch (functionally identical on unconfigured
hosts), and the per-venv recipe / resolver / slices are removed. No scoring or
runtime behavior changes on rollback because none changed on the way in.
