"""BEAAA-23752: prove the applicability rerouting is score-neutral.

A = decisions signed with the BASELINE's own policy_version (the semantics the
    harness had before this change)
B = harness.trusted_replay validation (this change)

If bmi and pillars are identical on all eight fixtures, the reroute moves no
score; the only difference is which policy version is stamped.

Run: ARE_ALLOW_DEV_SIGNING_KEY=true PYTHONPATH=. python3 artifacts/BEAAA-23752/applicability_equivalence.py
"""
import json, sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from api.bundle import _signing_key
from harness.golden_set._trusted_applicability import load_trusted_applicability
from rubric.scoring import Rubric, ValidatedApplicability, score

REPO = Path(__file__).resolve().parents[2]
B = REPO / "artifacts" / "beaaa-387"
rubric = Rubric.load(REPO / "rubric" / "rubric.yaml")
manifest = json.loads((B / "manifest.json").read_text())

diffs = []
for fid, fi in sorted(manifest["fixtures"].items()):
    if not fi.get("baseline_persisted"):
        continue
    base = json.loads((B / fid / "baseline.json").read_text())
    eo = json.loads((B / fid / "eval_outputs.json").read_text())
    raw = json.loads((B / fid / "applicability.json").read_text()).get("applicability_decisions") or {}
    pv = base.get("applicability_policy_version")
    common = dict(snapshot_hash=base["snapshot_hash"], forensic_bundle_uri=base["forensic_bundle_uri"],
                  scored_at=datetime.fromisoformat(base["scored_at"]), scanner_version=None,
                  corpus_version=base.get("corpus_version"), seed=base.get("seed"),
                  model_versions=base.get("model_versions"))
    a = score(rubric, eo, applicability_policy_version=pv,
              applicability_decisions=ValidatedApplicability._signed_from_policy_validation(raw, pv, _signing_key()),
              **common)
    b = score(rubric, eo, applicability_policy_version=None,
              applicability_decisions=load_trusted_applicability(B, fid), **common)
    same = a["bmi"] == b["bmi"] and a["pillars"] == b["pillars"]
    fields = sorted(k for k in set(a) | set(b)
                    if json.dumps(a.get(k), sort_keys=True, default=str) != json.dumps(b.get(k), sort_keys=True, default=str))
    print(f"{fid}: bmi {a['bmi']} vs {b['bmi']} | scores identical={same} | differing fields={fields}")
    if not same:
        diffs.append(fid)

print("\nRESULT:", "score-neutral on all fixtures" if not diffs else f"SCORE MOVED: {diffs}")
sys.exit(1 if diffs else 0)
