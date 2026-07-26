# Offline eval-framework per-venv strategy (BEAAA-13331)

**BLUF.** The full eval-framework release contract in `requirements.txt` can
never be installed into one interpreter — `garak==0.10.0` needs `datasets<2.17`
while `pyrit==0.6.0` needs `datasets>=3.0.0`. We materialize each conflicting
framework in its **own venv**, built by `scripts/build_eval_framework_venvs.sh`,
and the soft-import runners resolve availability + version from the matching
venv via `scanners/eval_framework_venv.py`. This is offline-calibration / CI
substrate only — it does **not** touch the production runtime slice
(BEAAA-13329) or any scoring / rubric / corpus.

Contract: `are-scanner-api-v1@v1.0`. Classification: rule-execution (wiring;
the runners already soft-import and guard on `_AVAILABLE`). No CTO co-sign is
required to run it, but the PR is gated to CTO per the ticket.

## Why one venv can't work

| Framework | pin | transitive constraint |
|-----------|-----|-----------------------|
| garak     | `0.10.0` | `datasets<2.17,>=2.14.6` |
| pyrit     | `0.6.0`  | `datasets>=3.0.0` |

`pip install -r requirements.txt` fails with `ResolutionImpossible` on the
`datasets` ranges (openai pins had collided earlier too). `requirements.txt`
therefore is the **release contract** — the source-of-truth pins — not a
one-shot install target.

## Layout

```
$ARE_EVAL_VENVS_DIR/                 # default /opt/are-scanner/eval-venvs
  garak-venv/bin/python              <- requirements-eval-garak.txt      (garak==0.10.0)
  pyrit-venv/bin/python              <- requirements-eval-pyrit.txt      (pyrit==0.6.0)
  agentdojo-venv/bin/python          <- requirements-eval-agentdojo.txt  (agentdojo==0.1.35)
  inspect-venv/bin/python            <- requirements-eval-inspect.txt    (inspect-ai==0.3.220)
```

Each per-framework `requirements-eval-*.txt` pins exactly the same version as
`requirements.txt`. Bumping any of them is a **rule-change** (CTO co-sign +
fresh N=5 calibration delta) per
`contracts/scanner-implementation-supervision-v1.md`.

## Build recipe

```bash
# build all four (idempotent; re-run to rebuild)
scripts/build_eval_framework_venvs.sh

# build a subset
scripts/build_eval_framework_venvs.sh garak pyrit

# custom root (e.g. a CI cache dir)
ARE_EVAL_VENVS_DIR=/tmp/eval-venvs scripts/build_eval_framework_venvs.sh

# verify every lock is pinned, hashed, duplicate-free, and isolated
python scripts/verify_python_locks.py
```

The script creates each venv, installs its hash-locked slice with
`pip --require-hashes`, verifies the import reports the pinned version, and
records install plus `pip check` transcripts under
`$ARE_EVAL_VENVS_DIR/transcripts` (fails loudly on drift or conflicts).
Confirm a built box with:

```bash
ARE_EVAL_VENVS_DIR=/opt/are-scanner/eval-venvs python -m scanners.eval_framework_venv
#   agentdojo  OK       0.1.35
#   garak      OK       0.10.0
#   inspect    OK       0.3.220
#   pyrit      OK       0.6.0
```

Exit 0 = all present at the expected pins; exit 1 = something missing/drifted.

## How the runners consume it

`scanners/{pyrit,agentdojo,inspect}_runner.py` keep their in-process `import`
as the **primary** resolution. Only when that import fails do they fall back to
`scanners.eval_framework_venv.resolve_framework(<framework>)`:

- **No venv configured / present** → `(False, "not-installed")`. Byte-identical
  to the historical `except ImportError` branch, so existing runner unit tests
  and the N=5 golden calibration are unchanged.
- **Venv present** → subprocess-probe `<venv>/bin/python -c "import <mod>; print(mod.__version__)"`
  → `(True, "<version>")`, stamped on the emitted measurement row exactly where
  the in-process version would have gone.

garak already runs out-of-process via promptfoo/subprocess, so it needs no
runner change — its venv exists for uniform materialization + version-stamping.

### Runner-specific note: inspect

The phase-1 `inspect_runner` path is an **in-process** data-model round-trip
that needs the local `inspect_ai.dataset.Sample` / `scorer.Score` symbols. To
actually *run* that path in-tree you still `pip install inspect-ai` into the
harness venv. The inspect venv covers version materialization and the future
phase-2 native `inspect_ai.eval()` subprocess driver
(`scanners.eval_framework_venv.run_in_venv`). If `_INSPECT_AVAILABLE` resolves
True via the venv but the local symbols are absent, `_require_inspect()` raises
a clear error rather than crashing on `Sample(None)`.

## Phase-2 hook (not wired here)

`scanners.eval_framework_venv.run_in_venv(framework, args)` runs
`<venv-python> <args...>` and returns the `CompletedProcess`. This is the
supported boundary for a future native driver (e.g. a real PyRIT orchestrator /
`inspect_ai.eval()` executed inside the isolated venv, returning JSON on
stdout). Phase-1 runners do not use it — they only need `resolve_framework`.

## Non-goals / invariants

- No change to the production runtime slice (BEAAA-13329).
- No scoring, rubric, judge, corpus, probe-mapping, or contract-output change.
- Wrap, don't fork: no upstream internals are copied; the subprocess boundary
  is the integration surface (same as `scanners/promptfoo_runner.py`).
