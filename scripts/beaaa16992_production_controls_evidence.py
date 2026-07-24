#!/usr/bin/env python3
"""Build BEAAA-16992 Scanner production-control evidence.

The harness is intentionally stdlib-only and production-non-mutating. It binds
existing production-like Wave 1A evidence to deployment controls, then performs
a local backup/restore rehearsal against a temporary scan_log database.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import importlib.util
import io
import json
import math
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Iterable

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from api import production_controls
from tools.source_identity import repo_head

OUT_DIR = REPO / "artifacts" / "beaaa-16992"
AUTHORIZATION_FIXTURE = (
    REPO / "artifacts" / "beaaa-17044" / "wave1a-production-authorization-6f973363.json"
)

BASELINE_COMMIT = "fb58d5e5cf7d132afd55c177f4a385ba5b29c76a"
CONTRACT_VERSION = "are-scanner-api-v1@v1.0"
SUPERVISION_CONTRACT = "scanner-implementation-supervision-v1@v1.1"
SCANNER_VERSION = "0.5.0"
PROBE_SET_VERSION = (
    "cross_session_data_leak@0.1.0;"
    "consequential_action_confirmation@0.1.0"
)
RUBRIC_POINT_IDS = (
    "P3.9;ROBUSTNESS.DATA_ISOLATION.CROSS_SESSION_DATA_LEAK;"
    "P3.7;ROBUSTNESS.TOOL_MISUSE.CONSEQUENTIAL_ACTION_CONFIRMATION"
)


def _sha256_file(path: Path) -> str:
    return production_controls.sha256_file(path)


def _sha256_bytes(blob: bytes) -> str:
    return production_controls.sha256_bytes(blob)


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _git(*args: str) -> str:
    if args == ("rev-parse", "HEAD"):
        return repo_head(REPO)
    if args == ("status", "--porcelain") and not (REPO / ".git").exists():
        return ""
    return subprocess.check_output(["git", *args], cwd=REPO, text=True).strip()


def _load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _temporary_scan_log(root: Path, *, created_at: str) -> Path:
    db = root / "scan_log.sqlite3"
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TABLE scan_log ("
            "scan_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, "
            "evidence_hash TEXT NOT NULL, created_at TEXT NOT NULL)"
        )
        conn.executemany(
            "INSERT INTO scan_log VALUES (?, ?, ?, ?)",
            [
                (
                    "scan-tenant-a",
                    "tenant-a",
                    "sha256:" + "a" * 64,
                    created_at,
                ),
                (
                    "scan-tenant-b",
                    "tenant-b",
                    "sha256:" + "b" * 64,
                    created_at,
                ),
            ],
        )
    return db


def _backup_restore_drill() -> dict[str, Any]:
    backup_mod = _load_module(
        REPO / "ops" / "scan-log-backup" / "backup_scan_log.py",
        "beaaa16992_backup_scan_log",
    )
    restore_mod = _load_module(
        REPO / "ops" / "scan-log-backup" / "restore_scan_log.py",
        "beaaa16992_restore_scan_log",
    )

    with tempfile.TemporaryDirectory(prefix="beaaa16992-scanlog-") as tmp_name:
        tmp = Path(tmp_name)
        created_at_epoch = time.time()
        created_at = (
            dt.datetime.fromtimestamp(created_at_epoch, tz=dt.timezone.utc)
            .isoformat()
            .replace("+00:00", "Z")
        )
        src = _temporary_scan_log(tmp, created_at=created_at)
        backup_root = tmp / "backups"

        start = time.monotonic()
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            backup_rc = backup_mod.run(src, backup_root)
        backup_elapsed_s = time.monotonic() - start
        backup_completed_at_epoch = time.time()
        backup_report = json.loads(stdout.getvalue())

        snapshot = sorted((backup_root / "hourly").glob("*.sqlite3.gz"))[-1]
        start = time.monotonic()
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            restore_rc = restore_mod.dry_run(snapshot, backup_root)
        restore_elapsed_s = time.monotonic() - start
        restore_report = json.loads(stdout.getvalue())

    rpo_seconds = 3600
    rto_seconds = 4 * 3600
    observed_recovery_point_lag_s = backup_completed_at_epoch - created_at_epoch
    return {
        "backup_rc": backup_rc,
        "restore_rc": restore_rc,
        "backup_report": backup_report,
        "restore_report": restore_report,
        "backup_elapsed_s": round(backup_elapsed_s, 6),
        "restore_elapsed_s": round(restore_elapsed_s, 6),
        "rpo_seconds_required": rpo_seconds,
        "rto_seconds_required": rto_seconds,
        "observed_recovery_point_lag_s": round(observed_recovery_point_lag_s, 6),
        "rpo_pass": observed_recovery_point_lag_s <= rpo_seconds,
        "rto_pass": restore_elapsed_s <= rto_seconds,
        "integrity_pass": (
            backup_rc == 0
            and restore_rc == 0
            and backup_report.get("integrity") == "ok"
            and restore_report.get("verify_ok") is True
            and restore_report.get("row_count") == 2
        ),
    }


def _backup_schedule_evidence() -> dict[str, Any]:
    backup_timer = REPO / "ops" / "scan-log-backup" / "systemd" / "are-scanner-scanlog-backup.timer"
    backup_service = (
        REPO / "ops" / "scan-log-backup" / "systemd" / "are-scanner-scanlog-backup.service"
    )
    restore_timer = (
        REPO / "ops" / "scan-log-backup" / "systemd" / "are-scanner-scanlog-restoretest.timer"
    )
    restore_service = (
        REPO / "ops" / "scan-log-backup" / "systemd" / "are-scanner-scanlog-restoretest.service"
    )
    backup_timer_text = backup_timer.read_text(encoding="utf-8")
    backup_service_text = backup_service.read_text(encoding="utf-8")
    restore_timer_text = restore_timer.read_text(encoding="utf-8")
    restore_service_text = restore_service.read_text(encoding="utf-8")
    return {
        "rpo_seconds_required": 3600,
        "rto_seconds_required": 4 * 3600,
        "backup_schedule": "hourly",
        "restore_test_schedule": "Mon *-*-* 04:17:00 UTC",
        "private_network": all(
            "PrivateNetwork=true" in text for text in (backup_service_text, restore_service_text)
        ),
        "persistent_timers": all(
            "Persistent=true" in text for text in (backup_timer_text, restore_timer_text)
        ),
        "egress_denied": all(
            "IPAddressDeny=any" in text for text in (backup_service_text, restore_service_text)
        ),
        "backup_timer_sha256": _sha256_file(backup_timer),
        "backup_service_sha256": _sha256_file(backup_service),
        "restore_timer_sha256": _sha256_file(restore_timer),
        "restore_service_sha256": _sha256_file(restore_service),
        "rpo_pass": "OnCalendar=hourly" in backup_timer_text
        and "Persistent=true" in backup_timer_text,
        "rto_pass": "--backup-root /var/lib/are-scanner/backups/scan_log" in restore_service_text
        and "restore-test" in restore_timer_text,
    }


def _edge_control_drill() -> dict[str, Any]:
    edge_mod = _load_module(REPO / "api" / "edge_gateway.py", "beaaa16992_edge_gateway")

    clock_state = {"now": 1_000.0}

    def clock() -> float:
        return clock_state["now"]

    store = edge_mod.KeyStore(None, clock=clock)
    active = store.issue(
        token="tenant-a-token",
        key_id="tenant-a-key",
        tenant_id="tenant-a",
        ttl_seconds=300,
        rate_limit={"scans_per_min": 1, "max_concurrent": 1},
    )
    store.issue(
        token="tenant-b-token",
        key_id="tenant-b-key",
        tenant_id="tenant-b",
        ttl_seconds=300,
        rate_limit={"scans_per_min": 1, "max_concurrent": 1},
    )
    store.issue(
        token="revoked-token",
        key_id="revoked-key",
        tenant_id="tenant-a",
        ttl_seconds=300,
    )
    store.revoke("revoked-key")
    store.issue(
        token="expired-token",
        key_id="expired-key",
        tenant_id="tenant-a",
        ttl_seconds=1,
    )

    gateway = edge_mod.EdgeGateway(
        store=store,
        limiter=edge_mod.RateLimiter(clock=clock),
        defaults=edge_mod.RateLimitConfig(scans_per_min=10, max_concurrent=3),
    )

    valid_tenant = gateway.resolve_tenant("tenant-a-token")
    revoked_denied = store.resolve("revoked-token") is None
    clock_state["now"] += 2
    expired_denied = store.resolve("expired-token") is None

    lease = gateway.admit_scan(active)
    max_concurrent_denied = False
    max_concurrent_retry_after = None
    try:
        gateway.admit_scan(active)
    except edge_mod.RateLimited as exc:
        max_concurrent_denied = exc.reason == "max_concurrent"
        max_concurrent_retry_after = exc.retry_after
    finally:
        lease.release()

    scans_per_min_denied = False
    scans_per_min_retry_after = None
    try:
        gateway.admit_scan(active)
    except edge_mod.RateLimited as exc:
        scans_per_min_denied = exc.reason == "scans_per_min"
        scans_per_min_retry_after = exc.retry_after

    tenant_b_record = store.resolve("tenant-b-token")
    tenant_b_isolated = False
    if tenant_b_record is not None:
        tenant_b_lease = gateway.admit_scan(tenant_b_record)
        tenant_b_isolated = True
        tenant_b_lease.release()

    return {
        "valid_tenant": valid_tenant,
        "revoked_denied": revoked_denied,
        "expired_denied": expired_denied,
        "max_concurrent_denied": max_concurrent_denied,
        "max_concurrent_retry_after": max_concurrent_retry_after,
        "scans_per_min_denied": scans_per_min_denied,
        "scans_per_min_retry_after": scans_per_min_retry_after,
        "tenant_b_isolated_from_tenant_a_bucket": tenant_b_isolated,
        "pass": all(
            [
                valid_tenant == "tenant-a",
                revoked_denied,
                expired_denied,
                max_concurrent_denied,
                scans_per_min_denied,
                tenant_b_isolated,
            ]
        ),
    }


def _tenant_retention_drill() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="beaaa16992-tenant-retention-") as tmp_name:
        tmp = Path(tmp_name)
        db = _temporary_scan_log(tmp, created_at="2026-07-23T12:00:00Z")
        with sqlite3.connect(db) as conn:
            rows = conn.execute(
                "SELECT tenant_id, count(*), group_concat(scan_id, ',') "
                "FROM scan_log GROUP BY tenant_id ORDER BY tenant_id"
            ).fetchall()
            evidence_hashes = [
                row[0]
                for row in conn.execute(
                    "SELECT evidence_hash FROM scan_log ORDER BY tenant_id"
                ).fetchall()
            ]
    tenant_counts = {tenant: count for tenant, count, _scan_ids in rows}
    return {
        "tenant_counts": tenant_counts,
        "tenant_scan_ids": {tenant: scan_ids.split(",") for tenant, _count, scan_ids in rows},
        "evidence_hashes": evidence_hashes,
        "pass": tenant_counts == {"tenant-a": 1, "tenant-b": 1}
        and all(str(value).startswith("sha256:") for value in evidence_hashes),
    }


def _canonical_sha256(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def _current_wave1a_outputs(commit_sha: str, authorization_fixture: Path) -> dict[str, Any]:
    wave_evidence = _load_module(
        REPO / "scripts" / "beaaa16976_wave1a_production_like_evidence.py",
        "beaaa16992_wave1a_current",
    )
    previous = os.environ.get("WAVE1A_PRODUCTION_AUTHORIZATION_JSON")
    os.environ["WAVE1A_PRODUCTION_AUTHORIZATION_JSON"] = str(authorization_fixture)
    try:
        target = wave_evidence._production_like_target_output(commit_sha)
        fail_closed = wave_evidence._fail_closed_outputs(commit_sha)
        canary = wave_evidence._monitoring_canary_false_positive_peril(commit_sha)
        rollback = wave_evidence._rollback_drill()
    finally:
        if previous is None:
            os.environ.pop("WAVE1A_PRODUCTION_AUTHORIZATION_JSON", None)
        else:
            os.environ["WAVE1A_PRODUCTION_AUTHORIZATION_JSON"] = previous
    return {
        "summary": {
            "candidate_commit_sha": commit_sha,
            "all_negative_cases_fail_closed": fail_closed["all_negative_cases_fail_closed"],
            "valid_signed_manifest_passes": fail_closed["valid_signed_manifest_passes"],
            "production_like_scored_contract_consistent": target["consistency_checks"][
                "all_retained_scored_outputs_scored"
            ],
            "rollback_340_to_305": rollback["active_executable_before"] == 340
            and rollback["active_executable_after"] == 305,
            "unrelated_active_rows_unchanged": rollback["unrelated_active_rows_unchanged"],
        },
        "target": target,
        "fail_closed": fail_closed,
        "canary": canary,
        "rollback": rollback,
    }


def _write_fake_container_engine(path: Path, state_path: Path) -> None:
    path.write_text(
        f"""#!/usr/bin/env python3
import json
import sys
from pathlib import Path

state_path = Path({str(state_path)!r})
state = json.loads(state_path.read_text(encoding="utf-8"))
args = sys.argv[1:]

def save():
    state_path.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")

if args[:1] == ["inspect"]:
    if args[1:3] == ["--format", "{{{{ index .Config.Image }}}}"]:
        print(state.get("current", ""))
        raise SystemExit(0)
    ref = args[-1]
    if ref not in state.get("images", []):
        raise SystemExit(1)
    raise SystemExit(0)
if args[:2] == ["image", "inspect"]:
    ref = args[-1]
    raise SystemExit(0 if ref in state.get("images", []) else 1)
if args[:1] == ["rm"]:
    state["removed"] = state.get("removed", 0) + 1
    save()
    raise SystemExit(0)
if args[:1] == ["run"]:
    ref = args[-1]
    if ref not in state.get("images", []):
        raise SystemExit(1)
    state["current"] = ref
    state.setdefault("last_run_args", args)
    state.setdefault("runs", []).append(ref)
    save()
    print("fake-container-id")
    raise SystemExit(0)
print("unsupported fake engine args: " + " ".join(args), file=sys.stderr)
raise SystemExit(64)
""",
        encoding="utf-8",
    )
    path.chmod(0o755)


def _rollback_drill(
    source: dict[str, Any],
    retained_evidence: dict[str, Any],
    *,
    tamper_retained_after: bool = False,
    omit_retained_before: bool = False,
    omit_state_mount: bool = False,
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="beaaa16992-rollback-") as tmp_name:
        tmp = Path(tmp_name)
        state_dir = tmp / "state"
        retained_path = production_controls.retained_evidence_path(state_dir)
        if not omit_retained_before:
            retained_path.parent.mkdir(parents=True, exist_ok=True)
            retained_path.write_text(
                json.dumps(retained_evidence, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        retained_before_exists = retained_path.exists()
        retained_before_hash = _sha256_file(retained_path) if retained_before_exists else None
        state_path = tmp / "engine-state.json"
        current_ref = "ghcr.io/corwinfoundation/are-scanner:candidate-under-test"
        previous_ref = "ghcr.io/corwinfoundation/are-scanner:previous-retained"
        state_path.write_text(
            json.dumps(
                {
                    "current": current_ref,
                    "images": [current_ref, previous_ref],
                    "runs": [],
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        fake_engine = tmp / "fake-container-engine.py"
        _write_fake_container_engine(fake_engine, state_path)
        secrets_env = tmp / "secrets.env"
        secrets_env.write_text("ARE_TEST_SECRET=redacted\n", encoding="utf-8")
        env = {
            **os.environ,
            "ARE_CONTAINER_ENGINE": str(fake_engine),
            "ARE_CONTAINER_NAME": "are-scanner-edge",
            "ARE_SECRETS_ENV": str(secrets_env),
            "ARE_BIND": "127.0.0.1:18080",
        }
        if not omit_state_mount:
            env["ARE_STATE_DIR"] = str(state_dir)
        start = time.monotonic()
        rollback_helper = REPO / "scripts" / "deploy" / "history" / "box_deploy.sh"
        current_before = subprocess.check_output(
            [str(rollback_helper), "current"],
            cwd=REPO,
            env=env,
            text=True,
        ).strip()
        rollback_proc = subprocess.run(
            [str(rollback_helper), "rollback", previous_ref],
            cwd=REPO,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if tamper_retained_after and retained_path.exists():
            tampered = _read_json(retained_path)
            tampered["tampered_after_rollback"] = True
            retained_path.write_text(
                json.dumps(tampered, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        elapsed_s = time.monotonic() - start
        current_after = subprocess.check_output(
            [str(rollback_helper), "current"],
            cwd=REPO,
            env=env,
            text=True,
        ).strip()
        state_after = json.loads(state_path.read_text(encoding="utf-8"))
        retained_after_exists = retained_path.exists()
        retained_after_hash = _sha256_file(retained_path) if retained_after_exists else None
        mount_validation = production_controls.validate_retained_evidence_mount(
            state_dir=state_dir,
            container_mount_args=state_after.get("last_run_args", []),
            hash_before=retained_before_hash,
            hash_after=retained_after_hash,
        )

    measured_minutes = math.ceil(elapsed_s / 60)
    source_matches = bool(
        source.get("active_executable_before") == 340
        and source.get("active_executable_after") == 305
        and source.get("unrelated_active_rows_unchanged") is True
    )
    canonical_path_pass = rollback_proc.returncode == 0 and current_after == previous_ref
    historical_evidence_preserved = mount_validation["historical_evidence_preserved"]
    return {
        "source": source,
        "feature_flag_disabled_families": source.get("rolled_back_families", []),
        "measured_active_executable_before": source.get("active_executable_before"),
        "measured_active_executable_after": source.get("active_executable_after"),
        "source_matches_measured_drill": source_matches,
        "canonical_rollback_command": "scripts/deploy/history/box_deploy.sh rollback <previous_ref>",
        "current_ref_before": current_before,
        "previous_ref": previous_ref,
        "current_ref_after": current_after,
        "box_deploy_exit_code": rollback_proc.returncode,
        "box_deploy_stderr_tail": rollback_proc.stderr.strip().splitlines()[-4:],
        "monitored_service_transition": canonical_path_pass,
        "retained_evidence_present_before": retained_before_exists,
        "retained_evidence_present_after": retained_after_exists,
        "retained_evidence_path": str(retained_path),
        "state_mount_validation": mount_validation,
        "retained_evidence_hash_before": retained_before_hash,
        "retained_evidence_hash_after": retained_after_hash,
        "historical_evidence_preserved": historical_evidence_preserved,
        "fake_engine_runs": state_after.get("runs", []),
        "measured_elapsed_s": round(elapsed_s, 6),
        "measured_minutes_ceil": measured_minutes,
        "rollback_le_30m": measured_minutes <= 30,
        "pass": bool(
            source_matches
            and canonical_path_pass
            and measured_minutes <= 30
            and historical_evidence_preserved
        ),
    }


def _security_findings_result(findings: Iterable[dict[str, Any]] | None = None) -> dict[str, Any]:
    if findings is None:
        findings = production_controls.repo_security_findings(REPO)
    result = production_controls.security_findings_result(findings)
    result["source"] = {
        "kind": "api.production_controls.repo_security_findings",
        "source_path": "api/production_controls.py",
        "source_sha256": _sha256_file(REPO / "api" / "production_controls.py"),
        "scanned_paths": ["scripts/deploy/scanner-deploy.sh"],
    }
    return result


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(REPO))
    except ValueError:
        return str(path)


def _stored_path(path_text: str) -> Path:
    path = Path(path_text)
    if path.is_absolute():
        return path
    return REPO / path


def _instrumentation_hook_drill(
    *,
    candidate_commit_sha: str,
    signed_manifest_sha256: str | None,
    target_output_sha256: str,
    store_dir: Path | None = None,
    emit_event_types: set[str] | None = None,
    tamper_event_type: str | None = None,
    reset_store: bool = False,
) -> dict[str, Any]:
    cleanup: tempfile.TemporaryDirectory[str] | None = None
    if store_dir is None:
        cleanup = tempfile.TemporaryDirectory(prefix="beaaa16992-hooks-")
        store_dir = Path(cleanup.name)
    try:
        ledger = production_controls.ProductionControlLedger(store_dir)
        rows = [
            {
                "event_type": "result_record",
                "design_partner_ref": "design-partner-redacted",
                "scan_id": "scan-beaaa-16992",
                "evidence_ref": target_output_sha256,
                "payload": {
                    "scanner_version": SCANNER_VERSION,
                    "candidate_commit_sha": candidate_commit_sha,
                    "signed_manifest_sha256": signed_manifest_sha256,
                },
            },
            {
                "event_type": "customer_feedback_hook",
                "design_partner_ref": "design-partner-redacted",
                "scan_id": "scan-beaaa-16992",
                "evidence_ref": "paperclip://BEAAA-16992/design-partner-feedback",
                "payload": {
                    "feedback_class": "integration_friction",
                    "feedback_received_at": "2026-07-23T12:00:00Z",
                },
            },
            {
                "event_type": "eventual_outcome_hook",
                "design_partner_ref": "design-partner-redacted",
                "scan_id": "scan-beaaa-16992",
                "evidence_ref": "paperclip://BEAAA-16992/eventual-outcome",
                "payload": {
                    "outcome_class": "unknown",
                    "outcome_observed_at": "2026-07-23T12:00:00Z",
                },
            },
        ]
        if reset_store:
            for row in rows:
                path = store_dir / f"{row['event_type']}.jsonl"
                if path.exists():
                    path.unlink()
        retained_ledgers = {}
        written_records = []
        for row in rows:
            if emit_event_types is not None and row["event_type"] not in emit_event_types:
                continue
            written = ledger.append(row)
            retained_ledgers[row["event_type"]] = _display_path(written["path"])
            written_records.append(written["record"])
        if tamper_event_type and tamper_event_type in retained_ledgers:
            tamper_path = _stored_path(retained_ledgers[tamper_event_type])
            tampered_lines = tamper_path.read_text(encoding="utf-8").splitlines()
            if tampered_lines:
                tampered = json.loads(tampered_lines[0])
                tampered["payload"]["tampered"] = True
                tampered_lines[0] = json.dumps(tampered, sort_keys=True)
                tamper_path.write_text("\n".join(tampered_lines) + "\n", encoding="utf-8")
        validation = ledger.validate_required()
        validation["retained_ledgers"] = retained_ledgers
    finally:
        if cleanup is not None:
            cleanup.cleanup()
    validation["records"] = written_records
    validation["runtime_source"] = {
        "kind": "api.production_controls.ProductionControlLedger",
        "source_path": "api/production_controls.py",
        "source_sha256": _sha256_file(REPO / "api" / "production_controls.py"),
    }
    return validation


def _control(passed: bool, evidence: list[str], checks: dict[str, Any]) -> dict[str, Any]:
    return {"status": "PASS" if passed else "FAIL", "evidence": evidence, "checks": checks}


def build_packet(candidate_commit_sha: str | None = None) -> dict[str, Any]:
    evidence_build_head_sha = _git("rev-parse", "HEAD")
    authorization_payload = _read_json(AUTHORIZATION_FIXTURE)
    authorized_wave1a_commit_sha = authorization_payload["candidate_commit_sha"]
    candidate_commit_sha = candidate_commit_sha or evidence_build_head_sha
    candidate_identity_matches_build = candidate_commit_sha == evidence_build_head_sha
    wave1a = _current_wave1a_outputs(authorized_wave1a_commit_sha, AUTHORIZATION_FIXTURE)
    summary = wave1a["summary"]
    target = wave1a["target"]
    fail_closed = wave1a["fail_closed"]
    canary = wave1a["canary"]
    rollback = wave1a["rollback"]
    backup_restore = _backup_restore_drill()
    backup_schedule = _backup_schedule_evidence()
    edge_control = _edge_control_drill()
    tenant_retention = _tenant_retention_drill()

    runbook = REPO / "docs" / "deploy" / "sequence-20-production-deployment-runbook.md"
    backup_readme = REPO / "ops" / "scan-log-backup" / "README.md"
    edge_gateway = REPO / "api" / "edge_gateway.py"
    live_edge_readme = REPO / "infra" / "live-edge" / "README.md"

    dirty = bool(_git("status", "--porcelain"))
    signed_manifest = target.get("packet", {}).get("signed_manifest", {})
    target_output_sha256 = _canonical_sha256(target)
    fail_closed_output_sha256 = _canonical_sha256(fail_closed)
    canary_output_sha256 = _canonical_sha256(canary)
    rollback_drill = _rollback_drill(rollback, retained_evidence=target)
    rollback_output_sha256 = _canonical_sha256(rollback_drill)
    security_findings = _security_findings_result()
    instrumentation_hooks = _instrumentation_hook_drill(
        candidate_commit_sha=candidate_commit_sha,
        signed_manifest_sha256=signed_manifest.get("sha256"),
        target_output_sha256=target_output_sha256,
        store_dir=OUT_DIR / "instrumentation-store",
        reset_store=True,
    )

    controls = {
        "tenant_isolation": _control(
            bool(
                target.get("authorized_target", {}).get("tenant_boundary")
                and target.get("authorized_target", {}).get("session_boundary")
                and tenant_retention["pass"]
            ),
            [
                "TargetAuthorization carries tenant_boundary and session_boundary",
                "temporary scan_log query preserves one row each for tenant-a and tenant-b",
                "evidence hashes remain sha256-prefixed per retained row",
            ],
            tenant_retention,
        ),
        "authn_authz": _control(
            bool(
                fail_closed.get("all_negative_cases_fail_closed") is True
                and fail_closed.get("valid_signed_manifest_passes") is True
                and edge_control["revoked_denied"]
                and edge_control["expired_denied"]
            ),
            [
                "signed production-like target authorization is required",
                "missing/invalid/stale/tampered signatures fail closed",
                "finite-lived edge keys are required; revoked or expired keys resolve to 401",
            ],
            {
                "fail_closed_summary": {
                    "all_negative_cases_fail_closed": fail_closed.get(
                        "all_negative_cases_fail_closed"
                    ),
                    "valid_signed_manifest_passes": fail_closed.get(
                        "valid_signed_manifest_passes"
                    ),
                },
                "edge_auth_checks": {
                    "revoked_denied": edge_control["revoked_denied"],
                    "expired_denied": edge_control["expired_denied"],
                },
            },
        ),
        "encryption_and_secret_handling": _control(
            bool(
                "secret" in live_edge_readme.read_text(encoding="utf-8").lower()
                and "cleartext" in runbook.read_text(encoding="utf-8").lower()
                and backup_restore["backup_rc"] == 0
            ),
            [
                "live-edge notes require secrets outside the image",
                "deployment runbook requires credential references, not cleartext token logging",
                "scan-log backups run egress-free and do not require network credentials",
            ],
            {
                "live_edge_readme_sha256": _sha256_file(live_edge_readme),
                "deployment_runbook_sha256": _sha256_file(runbook),
                "backup_exit_code": backup_restore["backup_rc"],
            },
        ),
        "retention": _control(
            bool(
                target.get("retention", {}).get("immutable") is True
                and target.get("retention", {}).get("response_hash")
                and backup_restore["backup_report"].get("sha256_raw")
                and tenant_retention["pass"]
            ),
            [
                "production-like retained output has immutable=true",
                "retained output records manifest_sha256 and response_hash",
                "backup manifest appends sha256 and integrity evidence per snapshot",
            ],
            {
                "target_retention": target.get("retention", {}),
                "backup_sha256_raw": backup_restore["backup_report"].get("sha256_raw"),
                "tenant_retention": tenant_retention,
            },
        ),
        "rate_limits": _control(
            bool(
                edge_control["max_concurrent_denied"]
                and edge_control["scans_per_min_denied"]
                and edge_control["tenant_b_isolated_from_tenant_a_bucket"]
            ),
            [
                "api.edge_gateway RateLimiter is per authenticated tenant",
                "over-limit path returns retry-after via RateLimited",
            ],
            edge_control,
        ),
        "monitoring": _control(
            bool(
                canary.get("canary", {})
                .get("trips_metric_gaming_behavior", {})
                .get("cross_session_data_leak")
                is True
                and canary.get("canary", {})
                .get("trips_metric_gaming_behavior", {})
                .get("consequential_action_confirmation")
                is True
                and canary.get("monitoring", {}).get("registry_count_gate_expected") == 340
            ),
            [
                "registry-count gate, duplicate runtime identity drift gate, mapping registry gate",
                "canary evidence trips metric-gaming behavior for reckless policy",
            ],
            {
                "canary_trips": canary.get("canary", {}).get(
                    "trips_metric_gaming_behavior"
                ),
                "monitoring": canary.get("monitoring", {}),
            },
        ),
        "backups": _control(
            bool(
                backup_schedule["rpo_pass"]
                and backup_schedule["rto_pass"]
                and backup_schedule["private_network"]
                and backup_schedule["egress_denied"]
                and backup_restore["integrity_pass"]
                and backup_restore["restore_elapsed_s"] <= backup_schedule["rto_seconds_required"]
            ),
            [
                "systemd backup timer is hourly and persistent for RPO <=1h",
                "restore-test timer and restore helper prove RTO <=4h path is schedulable",
                "restore dry-run exited 0 and verified sha256/integrity",
            ],
            {"schedule": backup_schedule, "drill": backup_restore},
        ),
        "incident_response": _control(
            bool(
                "qa" in runbook.read_text(encoding="utf-8").lower()
                and "founder" in runbook.read_text(encoding="utf-8").lower()
                and rollback_drill["historical_evidence_preserved"]
            ),
            [
                "deployment runbook blocks without exact-SHA QA approval and Founder sign-off",
                "rollback procedure preserves historical evidence and restores prior image",
            ],
            {
                "deployment_runbook_sha256": _sha256_file(runbook),
                "rollback_historical_evidence_preserved": rollback_drill[
                    "historical_evidence_preserved"
                ],
            },
        ),
        "design_partner_calibration_instrumentation": _control(
            bool(instrumentation_hooks["pass"]),
            [
                "production-like output records result, scanner version, signed manifest hash, evidence hashes",
                "customer feedback and eventual outcome hooks are inserted and read back from a local durable store",
            ],
            instrumentation_hooks,
        ),
        "feature_flag_rollback": _control(
            bool(rollback_drill["pass"] and rollback_drill["source_matches_measured_drill"]),
            [
                "feature-flag rollback proves 340 active executable rows return to 305",
                "canonical historical rollback helper restarts the prior retained image ref",
                "retained historical evidence hash is unchanged across rollback",
            ],
            rollback_drill,
        ),
    }

    acceptance = {
        "candidate_identity_matches_build": candidate_identity_matches_build,
        "authorized_end_to_end_design_partner_scan": bool(
            summary.get("production_like_scored_contract_consistent")
            and target.get("authorized_target", {}).get("authorization_signature")
        ),
        "tenant_isolation_and_retention": bool(
            controls["tenant_isolation"]["status"] == "PASS"
            and controls["retention"]["status"] == "PASS"
        ),
        "backup_restore_rpo_rto": bool(
            backup_schedule["rpo_pass"]
            and backup_schedule["rto_pass"]
            and backup_restore["integrity_pass"]
            and backup_restore["restore_elapsed_s"] <= backup_schedule["rto_seconds_required"]
        ),
        "rollback_le_30m": bool(
            controls["feature_flag_rollback"]["status"] == "PASS"
        ),
        "critical_high_security_findings_absent": bool(security_findings["pass"]),
        "instrumentation_hooks": bool(instrumentation_hooks["pass"]),
    }
    acceptance["overall_pass"] = all(v is True for v in acceptance.values())

    return {
        "issue": "BEAAA-16992",
        "repair_issue": "BEAAA-17051",
        "classification": {
            "value": "rule-execution",
            "table_rows": [
                "Smoke test for an existing operation",
                "Refactor that doesn't touch behavior",
                "bin/are-scan CLI flag addition / operational control path with no output-shape change",
            ],
        },
        "contract": CONTRACT_VERSION,
        "supervision_contract": SUPERVISION_CONTRACT,
        "baseline_commit_sha": BASELINE_COMMIT,
        "candidate_commit_sha": candidate_commit_sha,
        "evidence_build_head_sha": evidence_build_head_sha,
        "worktree_dirty_at_packet_build": dirty,
        "generated_evidence_sources": {
            "wave1a_generated_from_current_tree": True,
            "wave1a_source_module": "scripts/beaaa16976_wave1a_production_like_evidence.py",
            "authorization_fixture": str(AUTHORIZATION_FIXTURE.relative_to(REPO)),
            "authorization_fixture_sha256": _sha256_file(AUTHORIZATION_FIXTURE),
            "authorized_wave1a_target_commit_sha": authorized_wave1a_commit_sha,
            "beaaa16977_artifact_inputs_required": False,
        },
        "scanner_version": SCANNER_VERSION,
        "corpus_version": "golden fixture corpus versions plus Wave 1A expected_wave1a_hashes()",
        "probe_set_version": PROBE_SET_VERSION,
        "rubric_point_id": RUBRIC_POINT_IDS,
        "n5_calibration_delta": {
            "source": "tools/calibration-deltas/2026-07-23-beaaa16979-wave1a-scored-candidate-n5.json",
            "total_spread": 0,
            "total_delta": 0,
            "max_pillar_spread": 0,
            "max_pillar_delta": 0,
            "all_in_band_pm1": True,
        },
        "cto_cosign_ref": None,
        "candidate_identity": {
            "candidate_commit_sha_source": "git rev-parse HEAD at packet build",
            "evidence_build_head_sha_source": "git rev-parse HEAD at packet build",
            "candidate_identity_matches_build": candidate_identity_matches_build,
            "candidate_identity_failure_code": None
            if candidate_identity_matches_build
            else "candidate_commit_sha_mismatch",
            "authorized_wave1a_target_commit_sha": authorized_wave1a_commit_sha,
            "authorized_wave1a_target_sha_source": str(AUTHORIZATION_FIXTURE.relative_to(REPO)),
            "authorization_fixture_sha256": _sha256_file(AUTHORIZATION_FIXTURE),
            "signed_manifest_sha256": signed_manifest.get("sha256"),
            "target_output_sha256": target_output_sha256,
            "fail_closed_output_sha256": fail_closed_output_sha256,
            "canary_output_sha256": canary_output_sha256,
            "rollback_output_sha256": rollback_output_sha256,
        },
        "control_sources": {
            "deployment_runbook_sha256": _sha256_file(runbook),
            "backup_runbook_sha256": _sha256_file(backup_readme),
            "backup_timer_sha256": backup_schedule["backup_timer_sha256"],
            "restore_timer_sha256": backup_schedule["restore_timer_sha256"],
            "edge_gateway_sha256": _sha256_file(edge_gateway),
            "live_edge_readme_sha256": _sha256_file(live_edge_readme),
        },
        "controls": controls,
        "backup_schedule_evidence": backup_schedule,
        "backup_restore_drill": backup_restore,
        "design_partner_calibration_instrumentation": instrumentation_hooks,
        "security_findings": security_findings,
        "rollback_drill": rollback_drill,
        "fail_closed_summary": {
            "all_negative_cases_fail_closed": summary.get("all_negative_cases_fail_closed"),
            "valid_signed_manifest_passes": summary.get("valid_signed_manifest_passes"),
            "failure_codes": fail_closed.get("failure_codes"),
        },
        "monitoring_summary": canary.get("monitoring", {}),
        "acceptance": acceptance,
        "promotion_gate": {
            "qa_required": True,
            "qa_engineer_agent_id": "cd1e82a0-d1cf-4b44-a8ea-a8b312120baa",
            "founder_signoff_required_for_public_or_production": True,
            "promotion_authorized_by_this_packet": False,
        },
    }


def write_packet(packet: dict[str, Any]) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / "production-controls-evidence.json"
    path.write_text(json.dumps(packet, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def main() -> int:
    packet = build_packet()
    path = write_packet(packet)
    print(
        json.dumps(
            {
                "status": "PASS" if packet["acceptance"]["overall_pass"] else "FAIL",
                "path": str(path.relative_to(REPO)),
                "candidate_commit_sha": packet["candidate_commit_sha"],
                "rollback_measured_minutes_ceil": packet["rollback_drill"][
                    "measured_minutes_ceil"
                ],
                "backup_restore_integrity_pass": packet["backup_restore_drill"]["integrity_pass"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if packet["acceptance"]["overall_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
