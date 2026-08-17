import json, sys
from datetime import datetime
from pathlib import Path
sys.path.insert(0,"/tmp/qa23803")
from rubric.scoring import Rubric, score
from scanners import SCANNER_VERSION
REPO=Path("/tmp/qa23803")
B=REPO/"artifacts"/"beaaa-387"
rub=Rubric.load(REPO/"rubric"/"rubric.yaml")
print("SCANNER_VERSION=",SCANNER_VERSION,"rubric.version=",rub.version)
cp=json.load(open(B.parent/"beaaa-23802"/"aligned-checkpoint-v1.json"))
bad=[]
for fid in sorted(cp["fixtures"]):
    bdir=B/fid
    bl=json.load(open(bdir/"baseline.json"))
    ev=json.load(open(bdir/"eval_outputs.json"))
    app=None
    ap=bdir/"applicability.json"
    if ap.exists(): app=json.load(open(ap)).get("applicability_decisions")
    cur=score(rub,ev,snapshot_hash=bl["snapshot_hash"],forensic_bundle_uri=bl["forensic_bundle_uri"],
              scored_at=datetime.fromisoformat(bl["scored_at"]),scanner_version=None,
              corpus_version=bl.get("corpus_version"),seed=bl.get("seed"),
              model_versions=bl.get("model_versions"),
              applicability_decisions=app,
              applicability_policy_version=bl.get("applicability_policy_version"))
    a=cp["fixtures"][fid]
    dt=int(cur["bmi"])-int(a["total"])
    pdiff={p: round(float(cur["pillars"][p]["score"])-float(a["pillars"][p]),4) for p in ("GOVERNANCE","ROBUSTNESS","BIAS_FAIRNESS","OPS")}
    flag="" if dt==0 and all(v==0 for v in pdiff.values()) else "  <<< MISMATCH"
    if flag: bad.append(fid)
    print(f"{fid}: real_bmi={cur['bmi']:>3} cp_total={a['total']:>3} dTotal={dt:>3} dPillars={pdiff}{flag}")
    # also pin check
    for pin in ("scanner_version","rubric_version","corpus_version","seed","model_versions","applicability_policy_version"):
        rv=cur.get(pin, bl.get(pin))
        if rv!=a[pin]:
            print(f"    PIN {pin}: real={rv!r} cp={a[pin]!r}  <<< PIN MISMATCH")
print("\nMISMATCHED FIXTURES:",bad or "none")
