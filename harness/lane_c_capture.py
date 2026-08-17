#!/usr/bin/env python3
"""Fail-closed production-like evidence capture for BEAAA-21723 lane-C SPECs."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import yaml

from evals.aiuc_path_b.base import surface_seed_phrasing
from harness.lane_c_adapter import (
    FIELDS,
    SPECS,
    VERDICTS,
    LaneCError,
    build_instruction,
    extract_json_object,
    grade_decision,
    read_decision,
)

ROOT = Path(__file__).resolve().parents[1]
CANDIDATE = "6e5968fdcfd83ebe789f955cf150245351a6c0d9"
TARGET_IDENTITY = "beaaa-21725-dify-ollama-qwen25-3b"
TARGET_CLASS = "approved_non_customer_production_like_bounded_agent"
EVIDENCE_ROOT = Path("/mnt/paperclipdata/runtime-evidence/lane-c") / CANDIDATE
MAPPINGS = {name: ROOT / "rubric" / "probe_mappings" / f"{name}.yaml" for name in SPECS}

# One fail-closed exception across both lane-C paths (capture and the registered
# runner), so a refusal cannot be caught on one path and missed on the other.
CaptureError = LaneCError


def _config() -> dict[str, str]:
    cfg = {k: os.environ.get(k, "") for k in (
        "SCANNER_TARGET_URL", "SCANNER_TARGET_MODEL", "SCANNER_TARGET_KEY")}
    if not all(cfg.values()):
        raise CaptureError("missing production-like target configuration")
    return cfg


_json_object = extract_json_object


def _request(family: str, case: Any, prompt: str, seed: int) -> tuple[dict[str, Any], dict[str, Any]]:
    cfg = _config()
    instruction = build_instruction(family, case, seed)
    body = json.dumps({"model": cfg["SCANNER_TARGET_MODEL"], "prompt": instruction,
                       "temperature": 0, "seed": seed}, separators=(",", ":")).encode()
    req = urllib.request.Request(cfg["SCANNER_TARGET_URL"], data=body, method="POST", headers={
        "Authorization": f"Bearer {cfg['SCANNER_TARGET_KEY']}",
        "Content-Type": "application/json", "Accept-Encoding": "identity"})
    try:
        with urllib.request.urlopen(req, timeout=360) as response:
            if response.status != 200:
                raise CaptureError(f"target HTTP {response.status}")
            payload = json.loads(response.read(512 * 1024).decode())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise CaptureError("target transport or response failure") from exc
    if not isinstance(payload, dict):
        raise CaptureError("target response envelope is not an object")
    metadata = payload.get("backbond_metadata")
    if not isinstance(metadata, dict) or metadata.get("target_identity") != TARGET_IDENTITY:
        raise CaptureError("target identity mismatch")
    raw = payload.get("response")
    decision = read_decision(family, raw)
    return decision, {"response": raw, "metadata": metadata}


def _exclusive_json(path: Path, value: dict[str, Any]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    encoded = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise CaptureError(f"refusing to overwrite evidence: {path}") from exc
    with os.fdopen(fd, "wb") as handle:
        handle.write(encoded)
    return hashlib.sha256(encoded).hexdigest()


def _existing_run(path: Path, family: str, case_id: str, seed: int) -> dict[str, Any] | None:
    if not path.exists():
        return None
    raw = path.read_bytes()
    value = json.loads(raw)
    required = {
        "candidate_sha": CANDIDATE, "family": family, "stable_case_id": case_id,
        "seed": seed, "target_identity": TARGET_IDENTITY, "target_class": TARGET_CLASS,
        "reported_not_scored": True,
    }
    if any(value.get(key) != expected for key, expected in required.items()):
        raise CaptureError(f"existing evidence binding mismatch: {path}")
    if value.get("verdict") not in VERDICTS or not str(value.get("recorded_target_response", "")).strip():
        raise CaptureError(f"existing evidence is incomplete: {path}")
    return {"seed": seed, "verdict": value["verdict"], "sha256": hashlib.sha256(raw).hexdigest(),
            "evidence_uri": str(path)}


def capture() -> dict[str, Any]:
    subprocess.run(["/opt/backbond/lane-c/BEAAA-21725/verify_runtime_pins.sh"], check=True)
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    ancestry = subprocess.run(
        ["git", "merge-base", "--is-ancestor", CANDIDATE, head], cwd=ROOT
    )
    if ancestry.returncode != 0:
        raise CaptureError(f"candidate is not based on authorized baseline: {head}")
    summaries: dict[str, dict[str, Any]] = {}
    for family, spec in SPECS.items():
        mapping = yaml.safe_load(MAPPINGS[family].read_text())
        probes = {p["id"]: p for p in mapping["probes"]}
        if set(probes) != {c.case_id for c in spec.cases}:
            raise CaptureError(f"{family}: mapping/SPEC case mismatch")
        for case in spec.cases:
            runs = []
            for seed in range(5):
                rel = Path(family) / case.case_id / f"seed-{seed}.json"
                existing = _existing_run(EVIDENCE_ROOT / rel, family, case.case_id, seed)
                if existing is not None:
                    runs.append(existing)
                    continue
                prompt = surface_seed_phrasing(case, seed)
                started = time.time_ns()
                decision, target = _request(family, case, prompt, seed)
                verdict = grade_decision(family, case, decision)
                record = {
                    "schema": "backbond-lane-c-execution-evidence@1", "ticket_ref": "BEAAA-21723",
                    "candidate_sha": CANDIDATE, "family": family, "stable_case_id": case.case_id,
                    "seed": seed, "scanner_version": "0.5.0", "corpus_version": spec.version,
                    "probe_set_version": spec.version, "rubric_point_ids": list(case.rubric_point_ids),
                    "reported_not_scored": True, "target_identity": TARGET_IDENTITY,
                    "target_class": TARGET_CLASS, "model": _config()["SCANNER_TARGET_MODEL"],
                    "concrete_input_or_action": prompt, "expected_oracle": case.oracle,
                    "recorded_target_response": target["response"], "decision": decision,
                    "verdict": verdict, "target_metadata": target["metadata"],
                    "started_unix_ns": started, "completed_unix_ns": time.time_ns(),
                }
                digest = _exclusive_json(EVIDENCE_ROOT / rel, record)
                runs.append({"seed": seed, "verdict": verdict, "sha256": digest,
                             "evidence_uri": str(EVIDENCE_ROOT / rel)})
            probes[case.case_id]["execution_evidence"] = {
                "concrete_input_or_action": "frozen SPEC prompt; seed-rotated variants 0..4",
                "expected_oracle": json.dumps(case.oracle, sort_keys=True),
                "recorded_target_response": runs[0]["evidence_uri"],
                "verdict": runs[0]["verdict"], "github_provenance": f"BEAAA-21723@{CANDIDATE}",
                "target_identity": TARGET_IDENTITY, "target_class": TARGET_CLASS,
                "run_provenance": runs,
            }
        MAPPINGS[family].write_text(yaml.safe_dump(mapping, sort_keys=False), encoding="utf-8")
        summaries[family] = {"cases": len(spec.cases), "target_runs_per_case": 5}
    return summaries


def main() -> int:
    argparse.ArgumentParser().parse_args()
    print(json.dumps(capture(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
