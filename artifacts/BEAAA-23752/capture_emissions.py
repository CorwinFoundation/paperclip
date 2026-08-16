"""Capture the three golden-set emissions on the master base.

argv: <outdir> <mode:reference|candidate>

reference -- master's ORIGINAL modules cannot run: score() rejects B1's None
  corpus_version and rejects the raw applicability dict. Both rejections are
  suppressed here, and ONLY those two: _TEST_MODE=True for the corpus raise,
  and raw dicts are signed with the BASELINE's own policy_version so the old
  `applicability_policy_version` stamp is preserved. Patched before importing
  the harness modules, since they bind `score` by name at import.
candidate -- the repaired modules, no suppression of any kind.

`generated_at` is wall-clock and is stripped before hashing.
"""
import hashlib, json, sys
from pathlib import Path

outdir = Path(sys.argv[1]); outdir.mkdir(parents=True, exist_ok=True)
mode = sys.argv[2]

if mode == "reference":
    import rubric.scoring as S
    from api.bundle import _signing_key
    _orig = S.score
    def _patched(rubric, eval_outputs, **kw):
        # master's corpus pin is unconditional (_TEST_MODE is declared but never
        # read there), so the reference shim maps None -> "" at the boundary.
        # Equivalent by construction: the stamp line emits "" for both.
        if kw.get("corpus_version") is None:
            kw["corpus_version"] = ""
        ad, pv = kw.get("applicability_decisions"), kw.get("applicability_policy_version")
        if isinstance(ad, dict) and ad:
            kw["applicability_decisions"] = S.ValidatedApplicability._signed_from_policy_validation(ad, pv, _signing_key())
        return _orig(rubric, eval_outputs, **kw)
    S.score = _patched

from harness.golden_set.calibration_delta import compute_delta
from harness.golden_set.n5_band import run_l1_n5
from harness.golden_set.discrimination import compute_discrimination

TODAY = "2026-08-16"
for name, fn in {"calibration_delta": lambda: compute_delta(today=TODAY),
                 "n5_band": lambda: run_l1_n5(today=TODAY),
                 "discrimination": lambda: compute_discrimination(today=TODAY)}.items():
    out = fn(); out.pop("generated_at", None)
    blob = json.dumps(out, indent=2, sort_keys=True).encode()
    (outdir / f"{name}.json").write_bytes(blob)
    print(f"{name}: sha256={hashlib.sha256(blob).hexdigest()}")
    print(f"  {out.get('summary',{}).get('summary_line')}")
