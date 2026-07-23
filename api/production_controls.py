"""Production-control proof helpers used by shipped Scanner runtime paths.

These helpers keep BEAAA-16992/BEAAA-17051 evidence generation bound to
importable runtime code instead of script-local assertions.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable


BLOCKING_SECURITY_SEVERITIES = {"critical", "high"}
REQUIRED_HOOK_FIELDS = {
    "event_type",
    "design_partner_ref",
    "scan_id",
    "evidence_ref",
    "payload",
}
REQUIRED_HOOK_TYPES = {
    "result_record",
    "customer_feedback_hook",
    "eventual_outcome_hook",
}


def sha256_bytes(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def security_findings_result(findings: Iterable[dict[str, Any]]) -> dict[str, Any]:
    normalized = []
    counts: dict[str, int] = {}
    for finding in findings:
        severity = str(finding.get("severity", "")).lower() or "unknown"
        item = dict(finding)
        item["severity"] = severity
        normalized.append(item)
        counts[severity] = counts.get(severity, 0) + 1
    blocking = [
        item for item in normalized if item.get("severity") in BLOCKING_SECURITY_SEVERITIES
    ]
    return {
        "pass": not blocking,
        "blocking_severities": sorted(BLOCKING_SECURITY_SEVERITIES),
        "findings_by_severity": counts,
        "findings": normalized,
        "blocking_findings": blocking,
    }


def load_security_findings(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        findings = payload
    elif isinstance(payload, dict):
        findings = payload.get("findings", [])
    else:
        raise ValueError("security_findings_source_must_be_object_or_array")
    if not isinstance(findings, list):
        raise ValueError("security_findings_must_be_array")
    return [dict(item) for item in findings]


def repo_security_findings(repo: Path) -> list[dict[str, Any]]:
    """Run the shipped no-egress security source for production-control proof.

    The current source is intentionally narrow: it checks the deploy path that
    can mutate production and fails closed on patterns that would break the
    box-egress wall or leak secrets into image state.
    """

    findings: list[dict[str, Any]] = []
    box_deploy = repo / "scripts" / "deploy" / "box_deploy.sh"
    text = box_deploy.read_text(encoding="utf-8")
    executable_text = "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )
    if "docker pull" in executable_text or "podman pull" in executable_text:
        findings.append(
            {
                "id": "box-egress-pull-command",
                "severity": "critical",
                "source": str(box_deploy.relative_to(repo)),
                "detail": "box deploy path must not originate image pulls",
            }
        )
    if "/var/lib/are-scanner" not in text:
        findings.append(
            {
                "id": "missing-mounted-state-dir",
                "severity": "high",
                "source": str(box_deploy.relative_to(repo)),
                "detail": "Scanner state must be host-mounted at /var/lib/are-scanner",
            }
        )
    if "--env-file" not in text:
        findings.append(
            {
                "id": "missing-secrets-env-file",
                "severity": "high",
                "source": str(box_deploy.relative_to(repo)),
                "detail": "Scanner secrets must stay outside the image",
            }
        )
    return findings


class ProductionControlLedger:
    def __init__(self, store_dir: Path):
        self.store_dir = store_dir

    def append(self, event: dict[str, Any]) -> dict[str, Any]:
        missing = sorted(REQUIRED_HOOK_FIELDS - set(event))
        if missing:
            raise ValueError(f"hook_event_missing_fields:{','.join(missing)}")
        event_type = str(event["event_type"])
        path = self.store_dir / f"{event_type}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        record = dict(event)
        record["record_sha256"] = sha256_bytes(
            json.dumps(record, sort_keys=True, separators=(",", ":")).encode("utf-8")
        )
        if path.exists():
            for existing in self.read(event_type):
                if existing["record_sha256"] == record["record_sha256"]:
                    return {"path": path, "record": record}
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, sort_keys=True) + "\n")
        return {"path": path, "record": record}

    def read(self, event_type: str) -> list[dict[str, Any]]:
        path = self.store_dir / f"{event_type}.jsonl"
        records: list[dict[str, Any]] = []
        if not path.exists():
            return records
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            record = json.loads(line)
            missing = sorted((REQUIRED_HOOK_FIELDS | {"record_sha256"}) - set(record))
            if missing:
                raise ValueError(f"{path}:{line_no}:hook_event_missing_fields:{','.join(missing)}")
            if record["event_type"] != event_type:
                raise ValueError(f"{path}:{line_no}:hook_event_type_mismatch")
            expected_sha = record.pop("record_sha256")
            actual_sha = sha256_bytes(
                json.dumps(record, sort_keys=True, separators=(",", ":")).encode("utf-8")
            )
            record["record_sha256"] = expected_sha
            if actual_sha != expected_sha:
                raise ValueError(f"{path}:{line_no}:hook_event_hash_mismatch")
            records.append(record)
        return records

    def validate_required(self) -> dict[str, Any]:
        event_counts: dict[str, int] = {}
        evidence_refs = []
        errors = []
        records = []
        line_counts = {}
        for event_type in sorted(REQUIRED_HOOK_TYPES):
            path = self.store_dir / f"{event_type}.jsonl"
            try:
                event_records = self.read(event_type)
            except ValueError as exc:
                errors.append(str(exc))
                event_records = []
            line_counts[event_type] = (
                len(path.read_text(encoding="utf-8").splitlines()) if path.exists() else 0
            )
            if event_records:
                event_counts[event_type] = len(event_records)
                records.extend(event_records)
                evidence_refs.extend(record["evidence_ref"] for record in event_records)
        return {
            "durable_store": "jsonl retained hook ledgers",
            "event_counts": event_counts,
            "evidence_refs": evidence_refs,
            "ledger_errors": errors,
            "retained_written_records": records,
            "retained_ledger_line_counts": line_counts,
            "retained_ledgers": {
                event_type: str(self.store_dir / f"{event_type}.jsonl")
                for event_type in sorted(REQUIRED_HOOK_TYPES)
                if (self.store_dir / f"{event_type}.jsonl").exists()
            },
            "pass": event_counts == {event_type: 1 for event_type in REQUIRED_HOOK_TYPES}
            and all(evidence_refs)
            and not errors,
        }


def retained_evidence_path(state_dir: Path) -> Path:
    return state_dir / "retained-evidence" / "production-like-target-output.json"


def validate_retained_evidence_mount(
    *,
    state_dir: Path,
    container_mount_args: Iterable[str],
    hash_before: str | None,
    hash_after: str | None,
) -> dict[str, Any]:
    expected_mount = f"{state_dir}:/var/lib/are-scanner"
    retained_path = retained_evidence_path(state_dir)
    mount_present = expected_mount in set(container_mount_args)
    historical_evidence_preserved = bool(
        mount_present
        and retained_path.exists()
        and hash_before is not None
        and hash_after is not None
        and hash_before == hash_after
    )
    return {
        "state_dir": str(state_dir),
        "retained_evidence_path": str(retained_path),
        "expected_mount": expected_mount,
        "mount_present": mount_present,
        "historical_evidence_preserved": historical_evidence_preserved,
    }
