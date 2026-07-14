import json
import subprocess
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]


def test_beaaa14134_retell_reissue_verifier_passes() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "scripts/beaaa14134_reissue_retell_cases.py",
            "verify",
        ],
        cwd=REPO,
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    assert payload["status"] == "pass"
    assert payload["counts"]["attempted"] == 1090
    assert payload["counts"]["terminal"] == 1090
    assert payload["counts"]["evidenced"] == 1090
    assert payload["counts"]["accepted"] == 820
    assert payload["counts"]["excluded"] == 270
