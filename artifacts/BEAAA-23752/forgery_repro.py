"""BEAAA-23752 / BEAAA-18082: prove the golden-set applicability path fails closed.

The three golden-set entrypoints now authenticate their applicability sidecars
through harness.trusted_replay. This exercises the forgery paths that motivated
the BEAAA-18082 boundary, through the entrypoints' own loader.

Run: ARE_ALLOW_DEV_SIGNING_KEY=true PYTHONPATH=. python3 artifacts/BEAAA-23752/forgery_repro.py
"""
import json, shutil, sys, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from harness.golden_set._trusted_applicability import load_trusted_applicability
from harness.trusted_replay import TrustedReplayError

SRC = Path(__file__).resolve().parents[1] / "beaaa-387"
tmp = Path(tempfile.mkdtemp())
shutil.copy(SRC / "trusted-replay-manifest-v1.json", tmp / "trusted-replay-manifest-v1.json")
for fid in ("B1", "G1"):
    (tmp / fid).mkdir()
    shutil.copy(SRC / fid / "applicability.json", tmp / fid / "applicability.json")

failures = []


def expect_reject(label, fixture):
    try:
        load_trusted_applicability(tmp, fixture)
    except TrustedReplayError as exc:
        print(f"PASS  {label}: {exc}")
    else:
        print(f"FAIL  {label}: ACCEPTED")
        failures.append(label)


print(f"PASS  untampered sidecar accepted: {type(load_trusted_applicability(tmp, 'B1')).__name__}")

p = tmp / "B1" / "applicability.json"
payload = json.loads(p.read_text())
payload["applicability_decisions"]["GOVERNANCE.PII.HANDLING_ATTESTATION"] = {
    "fires": False, "na_justification": "forged waiver"}
p.write_text(json.dumps(payload))
expect_reject("forged N/A waiver rejected", "B1")

shutil.copy(SRC / "G1" / "applicability.json", p)
expect_reject("cross-fixture sidecar rejected", "B1")

m = json.loads((tmp / "trusted-replay-manifest-v1.json").read_text())
m["fixtures"]["B1"]["applicability_sha256"] = "0" * 64
(tmp / "trusted-replay-manifest-v1.json").write_text(json.dumps(m))
expect_reject("tampered manifest rejected", "G1")

print("\nRESULT:", "all forgery paths fail closed" if not failures else f"BOUNDARY BROKEN: {failures}")
sys.exit(1 if failures else 0)
