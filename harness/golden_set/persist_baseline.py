"""Persist a per-fixture v0.2.3 baseline snapshot under artifacts/beaaa-387/.

A "baseline" here is the locked score_response for a golden-set fixture under
a tagged scanner_version + rubric_version + corpus_version + seed. The L2
reproducibility gate (`harness/golden_set/repro_gate.py`), the §2 calibration
delta computation, and the §5 discrimination metric all consume this artifact
as their reference point.

Two lock sources are supported:

  deterministic_replay  — fixture has eval_outputs.json + expected_score.json
                          already locked under v0.2.3. The persisted baseline
                          is a byte-identical replay of expected_score; this
                          path requires no API calls. Applies to B1, M1 today.

  live_path_a / live_path_b — fixture's eval_outputs come from a fresh probe
                          run against the live model/deployment, scored
                          through rubric/scoring under v0.2.3. Applies to
                          G1-G3, B2, B3, M2, M3 — owned by BEAAA-387b/c.

A manifest at `artifacts/beaaa-387/manifest.json` indexes all 9 fixtures with
their lock state, scanner_version, rubric_version, are_total, and expected
range. The CTO Friday status reads from this manifest for §2/§3/§5.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rubric.scoring import Rubric, score

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "tests" / "fixtures" / "golden_agents"
BASELINES = REPO / "artifacts" / "beaaa-387"
RUBRIC_PATH = REPO / "rubric" / "rubric.yaml"

# Matches tests/test_golden_agents.py — the scored_at + snapshot_hash used
# when the locked expected_score files were produced. Required for
# byte-identical replay of the deterministic_replay path.
DETERMINISTIC_SCORED_AT = datetime(2026, 5, 9, 0, 0, 0, tzinfo=timezone.utc)
DETERMINISTIC_SNAPSHOT_HASH = "sha256:" + "a" * 64


def _load_json(p: Path) -> dict[str, Any]:
    return json.loads(p.read_text(encoding="utf-8"))


def _write_json(p: Path, payload: dict[str, Any]) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def persist_from_locked_fixture(fixture_id: str) -> dict[str, Any]:
    """Deterministic-replay baseline persistence.

    Re-scores the fixture's locked eval_outputs.json under v0.2.3 and asserts
    byte-identical match against expected_score.json. Writes the score
    response and a sidecar metadata file under artifacts/beaaa-387/{fid}/.
    """
    fdir = FIXTURES / fixture_id
    agent = _load_json(fdir / "agent.json")
    eo = _load_json(fdir / "eval_outputs.json")
    expected = _load_json(fdir / "expected_score.json")

    rubric = Rubric.load(RUBRIC_PATH)
    actual = score(
        rubric, eo,
        snapshot_hash=DETERMINISTIC_SNAPSHOT_HASH,
        forensic_bundle_uri=f"https://golden.local/{fixture_id}",
        scored_at=DETERMINISTIC_SCORED_AT,
    )
    if actual != expected:
        raise RuntimeError(
            f"{fixture_id}: deterministic replay diverged from locked expected_score "
            "— L1 regression; do not persist"
        )

    out_dir = BASELINES / fixture_id
    _write_json(out_dir / "baseline.json", actual)
    _write_json(out_dir / "eval_outputs.json", eo)

    sidecar = {
        "fixture_id": fixture_id,
        "lock_source": "deterministic_replay",
        "locked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "scanner_version": actual.get("scanner_version"),
        "rubric_version": actual.get("rubric_version"),
        "corpus_version": actual.get("corpus_version", ""),
        "seed": actual.get("seed"),
        "are_total": actual["bmi"],
        "band_display": actual["band"]["display"],
        "agent_metadata": agent,
        "expected_score_path": str((fdir / "expected_score.json").relative_to(REPO)),
        "eval_outputs_path": str((fdir / "eval_outputs.json").relative_to(REPO)),
    }
    _write_json(out_dir / "baseline_metadata.json", sidecar)
    return sidecar


def persist_from_live_path_a(
    fixture_id: str,
    *,
    eval_outputs: dict[str, Any],
    model_versions: dict[str, str],
    corpus_version: str,
    seed: int,
    system_prompt_sha256: str | None = None,
    expected_bmi_range: tuple[int, int] | list[int] | None = None,
    sdk_versions: dict[str, str] | None = None,
    seed_accepted: bool = True,
    source_artifact: str | None = None,
    internal_only: bool = False,
    anchor_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Live-Path-A baseline persistence.

    Scores `eval_outputs` produced from a fresh live-API probe run (4 measured
    dims + 17 prior-derived) under v0.2.3, asserts the ARE total falls inside
    `expected_bmi_range` (else raises — calibration breach), and writes the
    full set of fixture-promotion artifacts:

      - artifacts/beaaa-387/{fid}/baseline.json
      - artifacts/beaaa-387/{fid}/baseline_metadata.json
      - artifacts/beaaa-387/{fid}/eval_outputs.json    (copy for traceability)
      - tests/fixtures/golden_agents/{fid}/eval_outputs.json
      - tests/fixtures/golden_agents/{fid}/expected_score.json

    Caller is responsible for the agent.json flip (status -> live) — that
    sequencing is intentional so a failed run does not half-promote the
    fixture.
    """
    fdir = FIXTURES / fixture_id
    agent = _load_json(fdir / "agent.json")

    rubric = Rubric.load(RUBRIC_PATH)
    actual = score(
        rubric, eval_outputs,
        snapshot_hash=DETERMINISTIC_SNAPSHOT_HASH,
        forensic_bundle_uri=f"https://golden.local/{fixture_id}",
        scored_at=DETERMINISTIC_SCORED_AT,
        scanner_version=None,  # fall through to scanners.SCANNER_VERSION (0.2.3)
        corpus_version=corpus_version,
        seed=seed,
        model_versions=model_versions,
    )

    if expected_bmi_range is not None:
        lo, hi = int(expected_bmi_range[0]), int(expected_bmi_range[1])
        if not (lo <= actual["bmi"] <= hi):
            raise RuntimeError(
                f"{fixture_id}: calibration breach — ARE total {actual['bmi']} "
                f"outside expected_bmi_range [{lo}, {hi}]. Supreme Law: pause + "
                "co-sign with Underwriter before persisting."
            )

    out_dir = BASELINES / fixture_id
    _write_json(out_dir / "baseline.json", actual)
    _write_json(out_dir / "eval_outputs.json", eval_outputs)

    # File eval_outputs.json + expected_score.json into the fixture dir so the
    # L1 deterministic gate (tests.test_golden_agents) picks up the promotion.
    _write_json(fdir / "eval_outputs.json", eval_outputs)
    _write_json(fdir / "expected_score.json", actual)

    sidecar = {
        "fixture_id": fixture_id,
        "lock_source": "live_path_a",
        "locked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "scanner_version": actual.get("scanner_version"),
        "rubric_version": actual.get("rubric_version"),
        "corpus_version": actual.get("corpus_version", corpus_version),
        "seed": actual.get("seed"),
        "seed_accepted_by_vendor": seed_accepted,
        "model_versions": dict(model_versions),
        "sdk_versions": dict(sdk_versions or {}),
        "are_total": actual["bmi"],
        "band_display": actual["band"]["display"],
        "expected_bmi_range": list(expected_bmi_range) if expected_bmi_range else None,
        "agent_metadata": agent,
        "source_artifact": source_artifact,
    }
    if system_prompt_sha256:
        sidecar["system_prompt_sha256"] = system_prompt_sha256
    if internal_only:
        sidecar["internal_only"] = True
    if anchor_metadata:
        sidecar.update(anchor_metadata)
    _write_json(out_dir / "baseline_metadata.json", sidecar)
    return sidecar


def persist_from_live_path_b(
    fixture_id: str,
    *,
    eval_outputs: dict[str, Any],
    vendor: str,
    endpoint_label: str,
    prior_id: str,
    corpus_version: str,
    seed: int,
    expected_bmi_range: tuple[int, int] | list[int] | None = None,
    sdk_versions: dict[str, str] | None = None,
    source_artifact: str | None = None,
    tamper_resistance_notes: str | None = None,
) -> dict[str, Any]:
    """Live-Path-B baseline persistence — deployed customer-facing agents.

    Parallels `persist_from_live_path_a` but for Path B fixtures (M2 Botpress,
    others under BEAAA-68). Path B has no fixed model_version (deployment-
    side opaque), so the locked metadata records the endpoint label + prior_id
    + vendor instead. Tamper-resistance notes (per cto-os.md adversarial-input
    principle) are required when the deployed agent could inject scanner-
    context tokens into responses.

    Side effects mirror Path A: writes baseline.json + baseline_metadata.json
    + eval_outputs.json under artifacts/beaaa-387/{fid}/, AND files
    eval_outputs.json + expected_score.json into the fixture dir so the L1
    deterministic gate picks up the promotion. Caller flips agent.json status.
    """
    fdir = FIXTURES / fixture_id
    agent = _load_json(fdir / "agent.json")

    rubric = Rubric.load(RUBRIC_PATH)
    actual = score(
        rubric, eval_outputs,
        snapshot_hash=DETERMINISTIC_SNAPSHOT_HASH,
        forensic_bundle_uri=f"https://golden.local/{fixture_id}",
        scored_at=DETERMINISTIC_SCORED_AT,
        scanner_version=None,
        corpus_version=corpus_version,
        seed=seed,
        model_versions=None,  # Path B: model opaque behind deployment
    )

    if expected_bmi_range is not None:
        lo, hi = int(expected_bmi_range[0]), int(expected_bmi_range[1])
        if not (lo <= actual["bmi"] <= hi):
            raise RuntimeError(
                f"{fixture_id}: calibration breach — ARE total {actual['bmi']} "
                f"outside expected_bmi_range [{lo}, {hi}]. Supreme Law: pause + "
                "co-sign with Underwriter before persisting."
            )

    out_dir = BASELINES / fixture_id
    _write_json(out_dir / "baseline.json", actual)
    _write_json(out_dir / "eval_outputs.json", eval_outputs)
    _write_json(fdir / "eval_outputs.json", eval_outputs)
    _write_json(fdir / "expected_score.json", actual)

    sidecar = {
        "fixture_id": fixture_id,
        "lock_source": "live_path_b",
        "locked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "scanner_version": actual.get("scanner_version"),
        "rubric_version": actual.get("rubric_version"),
        "corpus_version": actual.get("corpus_version", corpus_version),
        "seed": actual.get("seed"),
        "vendor": vendor,
        "endpoint_label": endpoint_label,
        "prior_id": prior_id,
        "sdk_versions": dict(sdk_versions or {}),
        "are_total": actual["bmi"],
        "band_display": actual["band"]["display"],
        "expected_bmi_range": list(expected_bmi_range) if expected_bmi_range else None,
        "agent_metadata": agent,
        "source_artifact": source_artifact,
    }
    if tamper_resistance_notes:
        sidecar["tamper_resistance_notes"] = tamper_resistance_notes
    _write_json(out_dir / "baseline_metadata.json", sidecar)
    return sidecar


def update_manifest(entries: dict[str, dict[str, Any]]) -> Path:
    """Merge new baseline-state entries into artifacts/beaaa-387/manifest.json.

    Entries that aren't present in `entries` are preserved (idempotent merge);
    entries that are present overwrite their prior value. The manifest tracks
    all 9 fixtures (G1-G3, B1-B3, M1-M3) even before they are baselined, so
    §2/§3/§5 wiring can iterate over a stable set.
    """
    manifest_path = BASELINES / "manifest.json"
    if manifest_path.exists():
        manifest = _load_json(manifest_path)
    else:
        manifest = {
            "issue": "BEAAA-387",
            "scanner_version_target": "0.2.3",
            "rubric_version_target": "0.1.2",
            "fixtures": {},
        }
    manifest["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for fid, payload in entries.items():
        manifest["fixtures"][fid] = payload
    _write_json(manifest_path, manifest)
    return manifest_path


def all_fixture_ids() -> list[str]:
    return sorted(p.name for p in FIXTURES.iterdir() if p.is_dir())


def fixture_lock_state(fixture_id: str) -> dict[str, Any]:
    """Return a manifest-shaped entry describing the current lock state of a
    fixture, without persisting anything. Used to seed the manifest for
    fixtures still at pending_baseline_scan.
    """
    agent = _load_json(FIXTURES / fixture_id / "agent.json")
    expected = FIXTURES / fixture_id / "expected_score.json"
    state = {
        "fixture_id": fixture_id,
        "slot": agent.get("slot"),
        "type": agent.get("type"),
        "vendor": agent.get("vendor"),
        "expected_band": agent.get("expected_band") or agent.get("expected_band_range"),
        "expected_bmi": agent.get("expected_bmi") or agent.get("expected_bmi_range"),
        "agent_status": agent.get("status"),
        "baseline_persisted": False,
    }
    if expected.exists():
        exp = _load_json(expected)
        state["scanner_version"] = exp.get("scanner_version")
        state["rubric_version"] = exp.get("rubric_version")
        state["are_total"] = exp.get("bmi")
        state["band_display"] = (exp.get("band") or {}).get("display")
    return state
