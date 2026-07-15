import json
import subprocess
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]


def test_beaaa14142_contract_anchor_verifier_passes() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "scripts/beaaa14142_verify_contract_anchor.py",
            "verify",
        ],
        cwd=REPO,
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    assert payload["status"] == "pass"
    assert payload["counts"]["attempted"] == 270
    assert payload["counts"]["terminal"] == 270
    assert payload["counts"]["evidenced"] == 270
    assert payload["counts"]["accepted"] == 270
    assert payload["counts"]["excluded"] == 0
