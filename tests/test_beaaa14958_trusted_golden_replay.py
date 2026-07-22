"""BEAAA-14958: frozen golden replay trust is explicit and fixture-scoped."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
SOURCE = REPO / "artifacts" / "beaaa-387" / "G1"
MANIFEST = REPO / "artifacts" / "beaaa-387" / "trusted-replay-manifest-v1.json"


def _inputs(tmp_path: Path) -> Path:
    target = tmp_path / "G1"
    target.mkdir()
    shutil.copy(SOURCE / "eval_outputs.json", target / "eval_results.json")
    shutil.copy(SOURCE / "applicability.json", target / "applicability.json")
    shutil.copy(SOURCE / "baseline.json", target / "baseline.json")
    (target / "model_versions.json").write_text(
        json.dumps(json.loads((SOURCE / "baseline.json").read_text()).get("model_versions") or {})
    )
    (target / "scanner_invocation_context.json").write_text(json.dumps({
        "purpose": "legacy_replay",
        "applicability_mode": "legacy_replay",
        "non_binding_compatibility": True,
    }))
    return target


def _run(input_dir: Path, fixture: str = "G1") -> subprocess.CompletedProcess[str]:
    return subprocess.run([
        sys.executable, str(REPO / "bin" / "are-scan"), "--agent", "G1",
        "--seed", "0", "--corpus", "0.2.0", "--input-dir", str(input_dir),
        "--trusted-replay-manifest", str(MANIFEST),
        "--trusted-replay-fixture", fixture,
    ], cwd=REPO, text=True, capture_output=True, check=False)


def test_exact_frozen_fixture_is_accepted(tmp_path):
    completed = _run(_inputs(tmp_path))
    assert completed.returncode == 0, completed.stderr


def test_mismatched_fixture_identity_fails_closed(tmp_path):
    completed = _run(_inputs(tmp_path), fixture="G2")
    assert completed.returncode == 2
    assert "fixture identity" in completed.stderr


def test_tampered_applicability_fails_closed(tmp_path):
    input_dir = _inputs(tmp_path)
    payload = json.loads((input_dir / "applicability.json").read_text())
    payload["applicability_decisions"]["GOVERNANCE.PII.HANDLING_ATTESTATION"] = {
        "fires": False, "na_justification": "forged waiver", "na_justification_key": None,
    }
    (input_dir / "applicability.json").write_text(json.dumps(payload))
    completed = _run(input_dir)
    assert completed.returncode == 2
    assert "digest mismatch" in completed.stderr


def test_unpinned_manifest_fails_closed(tmp_path):
    input_dir = _inputs(tmp_path)
    forged = tmp_path / "manifest.json"
    forged.write_text(MANIFEST.read_text().replace('"version": "1.0.0"', '"version": "9.9.9"'))
    completed = subprocess.run([
        sys.executable, str(REPO / "bin" / "are-scan"), "--agent", "G1",
        "--seed", "0", "--corpus", "0.2.0", "--input-dir", str(input_dir),
        "--trusted-replay-manifest", str(forged), "--trusted-replay-fixture", "G1",
    ], cwd=REPO, text=True, capture_output=True, check=False)
    assert completed.returncode == 2
    assert "manifest digest" in completed.stderr


def test_manifest_and_fixture_options_are_all_or_nothing(tmp_path):
    input_dir = _inputs(tmp_path)
    completed = subprocess.run([
        sys.executable, str(REPO / "bin" / "are-scan"), "--agent", "G1",
        "--seed", "0", "--corpus", "0.2.0", "--input-dir", str(input_dir),
        "--trusted-replay-fixture", "G1",
    ], cwd=REPO, text=True, capture_output=True, check=False)
    assert completed.returncode == 2
    assert "must be supplied together" in completed.stderr
