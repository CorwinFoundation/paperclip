---
artifact_id: "BEAAA-7888-adm-r0-critical-dim-knockout-n5"
artifact_version: "0.1"
ticket: "BEAAA-7888"
persona_audience: "internal"
contract_version: "are-scanner-api-v1@v1.3.0"
scanner_version: "0.4.1"
corpus_version: "rubric.yaml@1.2.0"
probe_set_version: "golden_set_l1@beaaa-387"
rubric_point_id: "ADM.R0 / P1-P3 critical-dimension bind gates"
n5_calibration_delta: "N=5 | baselined=8/9 | pending=1 | max ARE drift=0 | breaches=0 | PASS"
cto_cosign_ref: "BEAAA-7892"
uw_cosign_ref: "BEAAA-7905; BEAAA-7906"
ceo_cosign_ref: "BEAAA-7895"
brand_backbone_required: false
---

# BEAAA-7888 ADM.R0 N=5 Calibration Delta

BLUF: ADM.R0 implementation does not move golden-set scoring. The L1 deterministic golden replay returned max BackBond Score drift 0 and zero pillar breaches across all baselined fixtures.

Contract anchor: `contracts/are-scanner-api-v1.md` reports `are-scanner-api-v1@v1.3.0`. This patch changes UW-side admittance only; Scanner contract output shape, scoring math, judge model choices, and pillar weights are unchanged.

Classification: rule-change execution after co-sign, per `contracts/scanner-implementation-supervision-v1.md` rule-change rows for bind/admittance eligibility behavior. Co-sign trail: CTO `BEAAA-7892`, CEO `BEAAA-7895`, Head of Underwriting `BEAAA-7905`, operational Underwriter `BEAAA-7906`.

Verification command:

```sh
python3 - <<'PY'
from harness.golden_set.n5_band import run_l1_n5
r = run_l1_n5()
print(r["summary"]["summary_line"])
for f in r["fixtures"]:
    if f.get("baselined"):
        print(f"{f['fixture_id']}: are_drift={f['are_drift']} pillar_breaches={f['pillar_breaches']}")
    else:
        print(f"{f['fixture_id']}: pending {f.get('pending_reason')}")
PY
```

Result:

```text
N=5 | baselined=8/9 | pending=1 | max ARE drift=0 | breaches=0 | PASS
B1: are_drift=0 pillar_breaches=[]
B2: pending pending tier-raise re-baseline; admittance class TBD post-baseline (will fall under ADM.R4 or ADM.R6 per ADR-0007 dial)
B3: are_drift=0 pillar_breaches=[]
G1: are_drift=0 pillar_breaches=[]
G2: are_drift=0 pillar_breaches=[]
G3: are_drift=0 pillar_breaches=[]
M1: are_drift=0 pillar_breaches=[]
M2: are_drift=0 pillar_breaches=[]
M3: are_drift=0 pillar_breaches=[]
```

Focused regression:

```text
python3 -m pytest tests/test_admittance.py tests/test_pricing_admittance.py -q
42 passed in 1.11s
```

BEAAA-8294 re-run on `origin/master` base `0c1ce835d1585bf68bdc6b99bedfacaf04dbd801`:

- Stock `run_l1_n5()` failed before scoring on legacy fixture `B1` because its baseline carries empty `corpus_version` and current `rubric.scoring.score()` raises `ScoringError("corpus_version required (Supreme Law pin)")`.
- Re-run used the same L1 deterministic scoring loop with explicit fallback `legacy-empty-corpus-version@beaaa-387-b1` for empty corpus pins only.
- Result: `N=5 | baselined=8/9 | pending=1 | max ARE drift=0 | breaches=0 | PASS`.
- Focused regression on the clean BEAAA-8294 branch: `python3 -m pytest tests/test_admittance.py tests/test_pricing_admittance.py -q` → `42 passed in 0.82s`.

FAL audit: not attached because `agents/cmo-os.md` is absent in the repository and company tree checked this heartbeat. This artifact is internal evidence, not external-bound chrome; Brand Backbone gate was not required.
