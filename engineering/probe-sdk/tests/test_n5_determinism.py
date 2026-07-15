"""N=5 byte-identical scores on fixture target.

Acceptance per BEAAA-5056: spread = 0 on are_total + every pillar subscore
across 5 invocations with the same target + same seed + same R1 build.
run_timestamp is the one envelope field allowed to vary; we project it
out for byte-equality on the full envelope.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
import yaml

from are_probe.eval_results_builder import RUBRIC_V1_2_0_DIM_IDS
from fixture_target import serve_in_thread

SDK_ROOT = Path(__file__).resolve().parents[1]
SCAN_BIN = os.environ.get("ARE_SCAN_BIN", "")


def _have_scan_bin() -> bool:
    return bool(SCAN_BIN) and Path(SCAN_BIN).is_file()


def _write_dp_attestations(path: Path) -> Path:
    path.write_text(json.dumps({
        dim_id: {
            "raw": 2.5,
            "evidence_uri": f"are-probe://dp-attested/{dim_id}",
        }
        for dim_id in RUBRIC_V1_2_0_DIM_IDS
    }, sort_keys=True, indent=2))
    return path


@pytest.fixture(scope="module")
def fixture_url():
    srv, url = serve_in_thread(0)
    yield url
    srv.shutdown()


@pytest.fixture(scope="module")
def n5_envelopes(fixture_url):
    if not _have_scan_bin():
        pytest.skip(f"ARE_SCAN_BIN not available ({SCAN_BIN!r})")
    envelopes = []
    with tempfile.TemporaryDirectory(prefix="are-probe-n5-") as td:
        td = Path(td)
        # Build a per-test config inheriting from the example.
        cfg = yaml.safe_load(
            (SDK_ROOT / "config" / "are-probe.yaml.example").read_text()
        )
        cfg["target"]["url"] = fixture_url
        cfg["output"]["envelope_path"] = str(td / "envelope.json")
        cfg["output"]["freeze_timestamp"] = True
        cfg["scanner_bin_default"] = SCAN_BIN
        cfg_path = td / "cfg.yaml"
        cfg_path.write_text(yaml.safe_dump(cfg))
        dp_path = _write_dp_attestations(td / "dp_attestations.json")

        env = dict(os.environ)
        env["ARE_SCAN_BIN"] = SCAN_BIN
        env["PYTHONPATH"] = str(SDK_ROOT / "src")

        for i in range(5):
            out = td / f"env-{i}.json"
            r = subprocess.run(
                [sys.executable, "-m", "are_probe.cli",
                 "--config", str(cfg_path),
                 "--out", str(out),
                 "--agent", "are-probe-r1-fixture",
                 "--dp-attestations", str(dp_path),
                 "--freeze-timestamp"],
                env=env, capture_output=True, text=True, check=False,
            )
            assert r.returncode == 0, (
                f"are-probe failed on run {i}:\n"
                f"stdout: {r.stdout}\nstderr: {r.stderr}\n"
            )
            envelopes.append(json.loads(out.read_text()))
    return envelopes


def test_n5_are_total_spread_zero(n5_envelopes):
    totals = [e["are_total"] for e in n5_envelopes]
    assert max(totals) - min(totals) == 0, f"are_total spread != 0: {totals}"


def test_n5_pillar_subscores_spread_zero(n5_envelopes):
    pillars = ("OPS", "BIAS_FAIRNESS", "ROBUSTNESS", "GOVERNANCE")
    for pillar in pillars:
        vals = [e["subscores"][pillar] for e in n5_envelopes]
        assert max(vals) - min(vals) == 0, (
            f"subscores[{pillar}] spread != 0: {vals}"
        )


def test_bias_dims_are_measured_not_r1_placeholder(n5_envelopes):
    env = n5_envelopes[0]
    for dim_id in ("P2.1", "P2.2", "P2.3", "P2.4", "P2.5"):
        assert dim_id not in env["uncovered_dimensions"]
        refs = [
            r for r in env["evidence_refs"]
            if r["rubric_point_id"] == dim_id
        ]
        assert refs
        assert any(r["test_id"] == f"{dim_id}.measured" for r in refs)
        assert all("placeholder" not in r["artifact_uri"] for r in refs)


def test_ops_refusal_accuracy_maps_shared_refusal_corpus_to_p1_4(fixture_url):
    if not _have_scan_bin():
        pytest.skip(f"ARE_SCAN_BIN not available ({SCAN_BIN!r})")
    with tempfile.TemporaryDirectory(prefix="are-probe-ops-p14-") as td:
        td = Path(td)
        cfg = yaml.safe_load(
            (SDK_ROOT / "config" / "are-probe.yaml.example").read_text()
        )
        cfg["target"]["url"] = fixture_url
        cfg["probes"] = [{
            "family": "ops_refusal_accuracy",
            "corpus_pack": "refusal_robustness_v0.1",
        }]
        cfg["output"]["envelope_path"] = str(td / "envelope.json")
        cfg["output"]["freeze_timestamp"] = True
        cfg["scanner_bin_default"] = SCAN_BIN
        cfg_path = td / "cfg.yaml"
        cfg_path.write_text(yaml.safe_dump(cfg))
        dp_path = _write_dp_attestations(td / "dp_attestations.json")

        env = dict(os.environ)
        env["ARE_SCAN_BIN"] = SCAN_BIN
        env["PYTHONPATH"] = str(SDK_ROOT / "src")
        out = td / "env.json"
        r = subprocess.run(
            [sys.executable, "-m", "are_probe.cli",
             "--config", str(cfg_path),
             "--out", str(out),
             "--agent", "are-probe-ops-p14-fixture",
             "--dp-attestations", str(dp_path),
             "--freeze-timestamp"],
            env=env, capture_output=True, text=True, check=False,
        )
        assert r.returncode == 0, (
            f"are-probe failed:\nstdout: {r.stdout}\nstderr: {r.stderr}\n"
        )
        env_payload = json.loads(out.read_text())
    assert "P1.4" not in env_payload["uncovered_dimensions"]
    refs = [
        r for r in env_payload["evidence_refs"]
        if r["rubric_point_id"] == "P1.4"
    ]
    assert refs
    assert any(r["test_id"] == "P1.4.measured" for r in refs)
    assert all("placeholder" not in r["artifact_uri"] for r in refs)


def test_n5_envelope_byte_identical_with_frozen_timestamp(n5_envelopes):
    # With freeze_timestamp=True, the entire envelope JSON must be
    # byte-identical across all 5 runs.
    serialized = [
        json.dumps(e, sort_keys=True, separators=(",", ":"))
        for e in n5_envelopes
    ]
    assert len(set(serialized)) == 1, (
        "envelopes differ across N=5 with frozen timestamp; "
        "first diff vs second:\n"
        f"  [0]={serialized[0][:200]}...\n"
        f"  [1]={serialized[1][:200]}...\n"
    )
