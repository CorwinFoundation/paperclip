from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from harness.release_gate import ReleaseGateError, build_release_manifest, validate_release


REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "check_v1_release_gate.py"


def _packet(tmp_path: Path, *, artifact_uri: str = "evidence.json", judges=None) -> Path:
    (tmp_path / "evidence.json").write_text('{"ok": true}\n', encoding="utf-8")
    payload = {
        "scanner_version": "1.0.0",
        "corpus_version": "2026.06.29",
        "rubric_version": "1.2.0",
        "seed": 0,
        "model_versions": {
            "primary": "openai/gpt-4.1-2025-04-14",
            "judges": judges if judges is not None else ["anthropic/claude-3-5-haiku-20241022"],
            "detectors": {"llm-guard": "0.3.16"},
        },
        "evidence_refs": [
            {
                "rubric_point_id": "ROB.PI.DIRECT",
                "test_id": "direct-prompt-injection-smoke",
                "artifact_uri": artifact_uri,
                "verdict": "pass",
                "weight": 1.0,
            }
        ],
    }
    path = tmp_path / "packet.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _manifest(tmp_path: Path, packet: Path, *, evidence_uri_index=None) -> Path:
    manifest = build_release_manifest([packet])
    manifest["reproducibility_runs"] = [
        {
            "agent_id": "ref-known-good-stable-v0",
            "seed": seed,
            "are_total": 90 + (seed % 2),
            "subscores": {
                "GOVERNANCE": 23,
                "ROBUSTNESS": 22 + (seed % 2),
                "BIAS_FAIRNESS": 23,
                "OPS": 22,
            },
        }
        for seed in range(5)
    ]
    manifest["evidence_uri_index"] = evidence_uri_index or []
    egress_artifact = tmp_path / "egress-negative-control.json"
    egress_artifact.write_text(json.dumps({
        "blocked": True,
        "block_type": "EgressNotAllowed",
        "corpus_version": "2026.06.29",
        "execution_environment": "pytest",
        "network_success": False,
        "scanner_version": "1.0.0",
        "seed": 0,
        "target_url": "http://example.com/",
        "timestamp": "2026-06-30T00:00:00+00:00",
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    manifest["egress_negative_control_artifact"] = str(egress_artifact.relative_to(REPO)) if egress_artifact.is_relative_to(REPO) else str(egress_artifact)
    path = tmp_path / "release-manifest.json"
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def test_release_gate_passes_local_evidence_and_manifest(tmp_path: Path):
    packet = _packet(tmp_path)
    manifest = _manifest(tmp_path, packet)

    report = validate_release([packet], manifest_path=manifest, repo_root=REPO)

    assert report["status"] == "pass"
    assert report["evidence_ref_count"] == 1
    assert report["used_pins"]["detector_versions"] == {"llm-guard": "0.3.16"}


def test_release_gate_fails_missing_evidence_refs(tmp_path: Path):
    packet = _packet(tmp_path)
    data = json.loads(packet.read_text(encoding="utf-8"))
    data["evidence_refs"] = []
    packet.write_text(json.dumps(data), encoding="utf-8")
    manifest = _manifest(tmp_path, packet)

    with pytest.raises(ReleaseGateError) as exc:
        validate_release([packet], manifest_path=manifest, repo_root=REPO)

    assert any(f.code == "MISSING_EVIDENCE_REFS" for f in exc.value.findings)


def test_release_gate_fails_empty_artifact_uri(tmp_path: Path):
    packet = _packet(tmp_path, artifact_uri="")
    manifest = _manifest(tmp_path, packet)

    with pytest.raises(ReleaseGateError) as exc:
        validate_release([packet], manifest_path=manifest, repo_root=REPO)

    assert any(f.code == "MISSING_EVIDENCE_FIELD" and "artifact_uri" in f.detail for f in exc.value.findings)


def test_release_gate_fails_unreachable_local_artifact(tmp_path: Path):
    packet = _packet(tmp_path, artifact_uri="missing.json")
    manifest = _manifest(tmp_path, packet)

    with pytest.raises(ReleaseGateError) as exc:
        validate_release([packet], manifest_path=manifest, repo_root=REPO)

    assert any(f.code == "UNREACHABLE_ARTIFACT_URI" for f in exc.value.findings)


def test_release_gate_requires_manifest_index_for_custom_scheme(tmp_path: Path):
    uri = "are-probe://candidate/ROB.PI.DIRECT/sha256:" + ("a" * 64)
    packet = _packet(tmp_path, artifact_uri=uri)
    manifest = _manifest(tmp_path, packet)

    with pytest.raises(ReleaseGateError) as exc:
        validate_release([packet], manifest_path=manifest, repo_root=REPO)

    assert any(f.code == "UNREACHABLE_ARTIFACT_URI" for f in exc.value.findings)

    indexed_manifest = _manifest(tmp_path, packet, evidence_uri_index=[{"artifact_uri": uri}])
    assert validate_release([packet], manifest_path=indexed_manifest, repo_root=REPO)["status"] == "pass"


def test_release_gate_fails_unpinned_judge(tmp_path: Path):
    packet = _packet(tmp_path, judges=["anthropic/claude-3-5-sonnet-latest"])
    manifest = _manifest(tmp_path, packet)

    with pytest.raises(ReleaseGateError) as exc:
        validate_release([packet], manifest_path=manifest, repo_root=REPO)

    assert any(f.code == "UNPINNED_JUDGE_MODEL" for f in exc.value.findings)


def test_release_gate_fails_manifest_missing_used_seed(tmp_path: Path):
    packet = _packet(tmp_path)
    manifest = build_release_manifest([packet])
    manifest["seeds"] = []
    manifest["reproducibility_runs"] = []
    manifest["egress_negative_control_artifact"] = "missing-egress-negative-control.json"
    manifest_path = tmp_path / "release-manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ReleaseGateError) as exc:
        validate_release([packet], manifest_path=manifest_path, repo_root=REPO)

    assert any(f.code == "MANIFEST_MISSING_PIN" and "seeds" in f.detail for f in exc.value.findings)


def test_release_gate_requires_egress_negative_control_artifact(tmp_path: Path):
    packet = _packet(tmp_path)
    manifest = build_release_manifest([packet])
    manifest["reproducibility_runs"] = []
    manifest_path = tmp_path / "release-manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ReleaseGateError) as exc:
        validate_release([packet], manifest_path=manifest_path, repo_root=REPO)

    assert any(f.code == "MISSING_EGRESS_NEGATIVE_CONTROL" for f in exc.value.findings)


def test_release_gate_fails_network_success_negative_control(tmp_path: Path):
    packet = _packet(tmp_path)
    manifest = build_release_manifest([packet])
    manifest["reproducibility_runs"] = []
    artifact = tmp_path / "egress-negative-control.json"
    artifact.write_text(json.dumps({
        "blocked": False,
        "corpus_version": "2026.06.29",
        "execution_environment": "pytest",
        "network_success": True,
        "scanner_version": "1.0.0",
        "seed": 0,
        "status_code": 200,
        "target_url": "http://example.com/",
        "timestamp": "2026-06-30T00:00:00+00:00",
    }), encoding="utf-8")
    manifest["egress_negative_control_artifact"] = str(artifact)
    manifest_path = tmp_path / "release-manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ReleaseGateError) as exc:
        validate_release([packet], manifest_path=manifest_path, repo_root=REPO)

    assert any(f.code == "EGRESS_NEGATIVE_CONTROL_FAILED" for f in exc.value.findings)


def test_release_gate_fails_without_n5_runs(tmp_path: Path):
    packet = _packet(tmp_path)
    manifest = _manifest(tmp_path, packet)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data.pop("reproducibility_runs")
    manifest.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(ReleaseGateError) as exc:
        validate_release([packet], manifest_path=manifest, repo_root=REPO)

    assert any(f.code == "MISSING_N5_REPRODUCIBILITY_RUNS" for f in exc.value.findings)


def test_release_gate_fails_n5_total_or_subscore_spread_over_one(tmp_path: Path):
    packet = _packet(tmp_path)
    manifest = _manifest(tmp_path, packet)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["reproducibility_runs"][4]["are_total"] = 93
    data["reproducibility_runs"][4]["subscores"]["OPS"] = 25
    manifest.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(ReleaseGateError) as exc:
        validate_release([packet], manifest_path=manifest, repo_root=REPO)

    breaches = {f.detail.split()[0] for f in exc.value.findings if f.code == "N5_REPRODUCIBILITY_BREACH"}
    assert breaches == {"are_total", "OPS"}


def test_release_gate_cli_exits_nonzero_on_fail(tmp_path: Path):
    packet = _packet(tmp_path, artifact_uri="missing.json")
    manifest = _manifest(tmp_path, packet)

    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--manifest", str(manifest), str(packet)],
        cwd=REPO,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert proc.returncode == 1
    assert "UNREACHABLE_ARTIFACT_URI" in proc.stdout


def test_release_gate_cli_discovers_sibling_manifest_for_remote_evidence(tmp_path: Path):
    uri = "are-probe://candidate/ROB.PI.DIRECT/sha256:" + ("a" * 64)
    packet = _packet(tmp_path, artifact_uri=uri)
    _manifest(tmp_path, packet, evidence_uri_index=[{"artifact_uri": uri}]).rename(
        tmp_path / "release_manifest.json"
    )

    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--repo-root", str(REPO), str(packet)],
        cwd=REPO,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert proc.returncode == 0
    assert json.loads(proc.stdout)["status"] == "pass"
