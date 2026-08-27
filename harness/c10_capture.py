#!/usr/bin/env python3
"""Fail-closed input and target preflight for BEAAA-25646 C10 capture."""
from __future__ import annotations

import datetime as dt
import argparse
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ROSTER = ROOT / "artifacts" / "BEAAA-25646" / "c10-roster-v1.0.json"
LEDGER = ROOT / "artifacts" / "BEAAA-25646" / "c10-mapping-ledger-v1.0.jsonl"
ROSTER_SHA256 = "e0046d6698aaa2ae44418a8d2bdf91098e87d19f31500a2d86500adde7402976"
LEDGER_SHA256 = "c0be24afea3ed424be9d9bddad96815dcf17ff3e58ef5f5f238839ff9af35184"
TARGET_IDENTITY = "beaaa-21725-dify-ollama-qwen25-3b"
TARGET_SOURCE_REVISION = "sha256:50ab2378567a62b811a2967759dd91f254864c3495cbe50576bd8a85bc6edd56"
TARGET_MODEL_IMMUTABLE_ID = "357c53fb659c"
TARGET_RUNTIME = "ollama/ollama:0.6.8"
TARGET_METADATA = Path(
    "/home/beai-agent/.paperclip/secrets/lane-c/consumers/BEAAA-25646.metadata.json"
)
REQUIRED_TARGET_NAMES = ("SCANNER_TARGET_URL", "SCANNER_TARGET_MODEL", "SCANNER_TARGET_KEY")
EVIDENCE_DIR = ROOT / "artifacts" / "BEAAA-25646" / "execution_evidence"
REQUIRED_EVIDENCE_FIELDS = (
    "stable_case_id", "recorded_target_response", "verdict", "verdict_provenance",
    "runner_identity", "target_identity", "target_source_revision", "executed_at",
    "artifact_ref",
)
ARTIFACT_REF_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
REPORTED_NOT_SCORED_VERDICT = "OBSERVED_REPORTED_NOT_SCORED"
RUNNER_IDENTITY = "eval_framework_capture"
VERDICT_PROVENANCE = "candidate-worker:v1"


class CaptureError(RuntimeError):
    """A governed binding failed; no target request may be issued."""


def validate_execution_evidence(
    evidence: dict[str, object], *, stable_case_id: str, runner_identity: str
) -> None:
    """Reject any evidence that cannot prove the exact governed target run."""
    missing = [name for name in REQUIRED_EVIDENCE_FIELDS if not str(evidence.get(name) or "").strip()]
    if missing:
        raise CaptureError(f"malformed execution evidence: missing {missing}")
    if evidence["stable_case_id"] != stable_case_id:
        raise CaptureError("execution evidence stable case identity mismatch")
    target_metadata = evidence.get("target_metadata")
    if not isinstance(target_metadata, dict):
        raise CaptureError("target response lacks structured provenance metadata")
    authoritative = _authoritative_target_provenance()
    expected_target_metadata = {
        "target_identity": TARGET_IDENTITY,
        "target_source_revision": authoritative["target_source_revision"],
        "target_model_immutable_id": TARGET_MODEL_IMMUTABLE_ID,
        "target_runtime": TARGET_RUNTIME,
    }
    mismatched_fields = sorted(
        key
        for key, value in expected_target_metadata.items()
        if target_metadata.get(key) != value
    )
    if mismatched_fields:
        raise CaptureError(
            "target structured provenance mismatch: fields="
            + ",".join(mismatched_fields)
        )
    response_id = target_metadata.get("response_id")
    response_model = target_metadata.get("response_model")
    target_model = target_metadata.get("target_model")
    if not isinstance(response_id, str) or not response_id.strip():
        raise CaptureError("target response lacks immutable response identity")
    if not isinstance(response_model, str) or response_model != target_model:
        raise CaptureError("target response model provenance mismatch")
    if evidence["runner_identity"] != runner_identity:
        raise CaptureError("runner identity mismatch")
    if evidence["target_identity"] != TARGET_IDENTITY:
        raise CaptureError("target identity mismatch")
    if evidence["target_source_revision"] != TARGET_SOURCE_REVISION:
        raise CaptureError("target immutable source revision mismatch")
    if evidence["verdict"] != REPORTED_NOT_SCORED_VERDICT:
        raise CaptureError("verdict violates reported-not-scored boundary")
    if not ARTIFACT_REF_RE.fullmatch(str(evidence["artifact_ref"])):
        raise CaptureError("malformed reproducible artifact reference")
    try:
        stamp = dt.datetime.fromisoformat(str(evidence["executed_at"]).replace("Z", "+00:00"))
    except ValueError as exc:
        raise CaptureError("malformed execution evidence timestamp") from exc
    if stamp.tzinfo is None:
        raise CaptureError("malformed execution evidence timestamp")


def write_execution_evidence_exclusive(
    evidence: dict[str, object], *, stable_case_id: str, runner_identity: str
) -> Path:
    """Validate first, then create one immutable row file without overwrite."""
    validate_execution_evidence(
        evidence, stable_case_id=stable_case_id, runner_identity=runner_identity
    )
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    path = EVIDENCE_DIR / f"{stable_case_id}.json"
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise CaptureError(f"evidence already exists for {stable_case_id}; refusing overwrite") from exc
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(evidence, handle, indent=2, sort_keys=True)
        handle.write("\n")
    return path


def _sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise CaptureError(f"missing governed input: {path}") from exc


def _authoritative_target_provenance() -> dict[str, str]:
    """Load the independently provisioned, ticket-scoped target binding."""
    try:
        metadata = json.loads(TARGET_METADATA.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CaptureError("malformed authoritative target metadata") from exc
    if metadata.get("consumer_ticket") != "BEAAA-25646":
        raise CaptureError("target consumer identity mismatch")
    if metadata.get("binding_kind") != "symbolic_reference":
        raise CaptureError("target binding is not independently provisioned")
    if metadata.get("target_source_revision") != TARGET_SOURCE_REVISION:
        raise CaptureError("target immutable source revision mismatch")
    if metadata.get("values_disclosed") is not False:
        raise CaptureError("target binding metadata discloses secret values")
    return {"target_source_revision": str(metadata["target_source_revision"])}


def preflight() -> dict[str, object]:
    """Validate immutable inputs and the ticket-scoped target binding."""
    if _sha256(ROSTER) != ROSTER_SHA256 or _sha256(LEDGER) != LEDGER_SHA256:
        raise CaptureError("governed roster or mapping-ledger hash mismatch")
    try:
        roster = json.loads(ROSTER.read_text(encoding="utf-8"))
        ledger = [json.loads(line) for line in LEDGER.read_text(encoding="utf-8").splitlines()]
    except (OSError, json.JSONDecodeError) as exc:
        raise CaptureError("malformed governed input or target metadata") from exc
    roster_ids = [row.get("stable_case_id") for row in roster]
    ledger_ids = [row.get("stable_case_id") for row in ledger]
    if len(roster) != 172 or roster_ids != ledger_ids or len(set(roster_ids)) != 172:
        raise CaptureError("C10 roster/ledger identity mismatch")
    if any(row.get("reporting_status") != "reported_not_scored" or row.get("score_input") is not False for row in ledger):
        raise CaptureError("C10 mapping ledger violates reported-not-scored boundary")
    authoritative = _authoritative_target_provenance()
    missing = [name for name in REQUIRED_TARGET_NAMES if not os.environ.get(name)]
    if missing:
        raise CaptureError(f"missing production-like target configuration: {missing}")
    return {
        "ticket_ref": "BEAAA-25646",
        "rows": 172,
        "roster_sha256": ROSTER_SHA256,
        "ledger_sha256": LEDGER_SHA256,
        "target_identity": TARGET_IDENTITY,
        "target_source_revision": authoritative["target_source_revision"],
        "reporting_status": "reported_not_scored",
    }


def _request_case(row: dict[str, object]) -> tuple[str, dict[str, object]]:
    """Run one roster row through the authorized candidate-worker target."""
    body = json.dumps({
        "model": os.environ["SCANNER_TARGET_MODEL"],
        "messages": [{
            "role": "user",
            "content": "\n\n".join(str(turn) for turn in row["prompt_turn_sequence"]),
        }],
        "temperature": 0,
        "seed": 0,
    }, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(
        os.environ["SCANNER_TARGET_URL"], data=body, method="POST",
        headers={
            "Authorization": f"Bearer {os.environ['SCANNER_TARGET_KEY']}",
            "Content-Type": "application/json", "Accept-Encoding": "identity",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=360) as response:
            if response.status != 200:
                raise CaptureError(f"target HTTP {response.status}")
            payload = json.loads(response.read(512 * 1024).decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise CaptureError("target transport or response failure") from exc
    if not isinstance(payload, dict):
        raise CaptureError("target response envelope is not an object")
    metadata = payload.get("backbond_metadata")
    if not isinstance(metadata, dict):
        raise CaptureError("target response lacks verdict provenance metadata")
    authoritative = _authoritative_target_provenance()
    if metadata.get("target_identity") != TARGET_IDENTITY:
        raise CaptureError("target identity mismatch")
    if metadata.get("target_source_revision") != authoritative["target_source_revision"]:
        raise CaptureError("target immutable source revision mismatch")
    choices = payload.get("choices")
    if not isinstance(choices, list) or len(choices) != 1:
        raise CaptureError("target returned missing, malformed, or ambiguous choices")
    choice = choices[0]
    if not isinstance(choice, dict) or not isinstance(choice.get("message"), dict):
        raise CaptureError("target returned a malformed assistant message")
    content = choice["message"].get("content")
    if not isinstance(content, str) or not content.strip():
        raise CaptureError("target returned missing, malformed, or empty assistant content")
    recorded = content.strip()
    return recorded, dict(metadata)


def capture() -> dict[str, object]:
    """Capture the exact governed roster, safely resuming validated prior rows."""
    binding = preflight()
    roster = json.loads(ROSTER.read_text(encoding="utf-8"))
    completed = 0
    for row in roster:
        stable_case_id = str(row["stable_case_id"])
        existing_path = EVIDENCE_DIR / f"{stable_case_id}.json"
        if existing_path.exists():
            try:
                existing = json.loads(existing_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise CaptureError(
                    f"malformed existing evidence for {stable_case_id}; refusing resume"
                ) from exc
            validate_execution_evidence(
                existing, stable_case_id=stable_case_id, runner_identity=RUNNER_IDENTITY
            )
            completed += 1
            continue
        started = dt.datetime.now(dt.timezone.utc)
        started_ns = time.time_ns()
        recorded, target_metadata = _request_case(row)
        completed_at = dt.datetime.now(dt.timezone.utc)
        artifact_ref = "sha256:" + hashlib.sha256(recorded.encode("utf-8")).hexdigest()
        evidence = {
            "schema": "backbond-c10-execution-evidence@1",
            "ticket_ref": "BEAAA-25646",
            "stable_case_id": stable_case_id,
            "recorded_target_response": recorded,
            "verdict": REPORTED_NOT_SCORED_VERDICT,
            "verdict_provenance": VERDICT_PROVENANCE,
            "runner_identity": RUNNER_IDENTITY,
            "target_identity": TARGET_IDENTITY,
            "target_source_revision": TARGET_SOURCE_REVISION,
            "target_metadata": target_metadata,
            "executed_at": completed_at.isoformat().replace("+00:00", "Z"),
            "started_at": started.isoformat().replace("+00:00", "Z"),
            "started_unix_ns": started_ns,
            "completed_unix_ns": time.time_ns(),
            "artifact_ref": artifact_ref,
            "reporting_status": "reported_not_scored",
            "score_input": False,
            "rubric_point_id": next(
                json.loads(line)["rubric_point_id"] for line in LEDGER.read_text().splitlines()
                if json.loads(line)["stable_case_id"] == stable_case_id
            ),
        }
        write_execution_evidence_exclusive(
            evidence, stable_case_id=stable_case_id, runner_identity=RUNNER_IDENTITY
        )
        completed += 1
    return {**binding, "captured": completed, "evidence_dir": str(EVIDENCE_DIR)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", action="store_true")
    args = parser.parse_args()
    print(json.dumps(capture() if args.capture else preflight(), sort_keys=True))
