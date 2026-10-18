"""Run intake regressions in a fresh process, isolated from other app fixtures."""
import subprocess
import sys
from pathlib import Path

def test_intake_quota_progress_and_provenance():
    script = Path(__file__).resolve().parents[1] / "backbond/scan-intake/verify_readiness.py"
    result = subprocess.run([sys.executable, str(script)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
