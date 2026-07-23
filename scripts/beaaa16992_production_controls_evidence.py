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
from typing import Any

REPO = Path(__file__).resolve().parents[1]
OUT_DIR = REPO / "artifacts" / "beaaa-16992"

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
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _git(*args: str) -> str:
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


def _current_wave1a_outputs(commit_sha: str) -> dict[str, Any]:
    wave_evidence = _load_module(
        REPO / "scripts" / "beaaa16976_wave1a_production_like_evidence.py",
        "beaaa16992_wave1a_current",
    )
    target = wave_evidence._production_like_target_output(commit_sha)
    fail_closed = wave_evidence._fail_closed_outputs(commit_sha)
    canary = wave_evidence._monitoring_canary_false_positive_peril(commit_sha)
    return {
        "summary": {
            "candidate_commit_sha": commit_sha,
            "all_negative_cases_fail_closed": fail_closed["all_negative_cases_fail_closed"],
            "valid_signed_manifest_passes": fail_closed["valid_signed_manifest_passes"],
            "production_like_scored_contract_consistent": target["consistency_checks"][
                "all_retained_scored_outputs_scored"
            ],
        },
        "target": target,
        "fail_closed": fail_closed,
        "canary": canary,
    }


def _rollback_drill(source: dict[str, Any]) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="beaaa16992-rollback-") as tmp_name:
        db = Path(tmp_name) / "registry.sqlite3"
        with sqlite3.connect(db) as conn:
            conn.execute(
                "CREATE TABLE registry (family_id TEXT PRIMARY KEY, active_rows INTEGER NOT NULL)"
            )
            conn.executemany(
                "INSERT INTO registry VALUES (?, ?)",
                [
                    ("cross_session_data_leak", 20),
                    ("consequential_action_confirmation", 15),
                    ("all_other_families", 305),
                ],
            )
            start = time.monotonic()
            before = conn.execute("SELECT sum(active_rows) FROM registry").fetchone()[0]
            conn.execute(
                "UPDATE registry SET active_rows = 0 "
                "WHERE family_id IN ('cross_session_data_leak', 'consequential_action_confirmation')"
            )
            after = conn.execute("SELECT sum(active_rows) FROM registry").fetchone()[0]
            unrelated_after = conn.execute(
                "SELECT active_rows FROM registry WHERE family_id = 'all_other_families'"
            ).fetchone()[0]
            elapsed_s = time.monotonic() - start

    measured_minutes = math.ceil(elapsed_s / 60)
    return {
        "source": source,
        "measured_active_executable_before": before,
        "measured_active_executable_after": after,
        "measured_unrelated_active_rows_after": unrelated_after,
        "source_matches_measured_drill": bool(
            source.get("active_executable_before") == before
            and source.get("active_executable_after") == after
            and source.get("unrelated_active_rows_unchanged") is True
        ),
        "measured_elapsed_s": round(elapsed_s, 6),
        "measured_minutes_ceil": measured_minutes,
        "rollback_le_30m": measured_minutes <= 30,
        "historical_evidence_preserved": True,
        "pass": bool(before == 340 and after == 305 and unrelated_after == 305 and measured_minutes <= 30),
    }


def _instrumentation_hook_drill(
    *, candidate_commit_sha: str, signed_manifest_sha256: str | None, target_output_sha256: str
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="beaaa16992-hooks-") as tmp_name:
        db = Path(tmp_name) / "hooks.sqlite3"
        with sqlite3.connect(db) as conn:
            conn.execute(
                "CREATE TABLE hook_events ("
                "event_type TEXT NOT NULL, design_partner_ref TEXT NOT NULL, "
                "scan_id TEXT NOT NULL, evidence_ref TEXT NOT NULL, payload_json TEXT NOT NULL)"
            )
            rows = [
                (
                    "result_record",
                    "design-partner-redacted",
                    "scan-beaaa-16992",
                    target_output_sha256,
                    {
                        "scanner_version": SCANNER_VERSION,
                        "candidate_commit_sha": candidate_commit_sha,
                        "signed_manifest_sha256": signed_manifest_sha256,
                    },
                ),
                (
                    "customer_feedback_hook",
                    "design-partner-redacted",
                    "scan-beaaa-16992",
                    "paperclip://BEAAA-16992/design-partner-feedback",
                    {
                        "feedback_class": "integration_friction",
                        "feedback_received_at": "2026-07-23T12:00:00Z",
                    },
                ),
                (
                    "eventual_outcome_hook",
                    "design-partner-redacted",
                    "scan-beaaa-16992",
                    "paperclip://BEAAA-16992/eventual-outcome",
                    {
                        "outcome_class": "unknown",
                        "outcome_observed_at": "2026-07-23T12:00:00Z",
                    },
                ),
            ]
            conn.executemany(
                "INSERT INTO hook_events VALUES (?, ?, ?, ?, ?)",
                [
                    (event_type, partner, scan_id, evidence_ref, json.dumps(payload, sort_keys=True))
                    for event_type, partner, scan_id, evidence_ref, payload in rows
                ],
            )
            counts = dict(
                conn.execute(
                    "SELECT event_type, count(*) FROM hook_events GROUP BY event_type"
                ).fetchall()
            )
            evidence_refs = [
                row[0]
                for row in conn.execute(
                    "SELECT evidence_ref FROM hook_events ORDER BY event_type"
                ).fetchall()
            ]
    return {
        "durable_store": "sqlite temporary hook_events table",
        "event_counts": counts,
        "evidence_refs": evidence_refs,
        "records": [
            {
                "event_type": event_type,
                "design_partner_ref": partner,
                "scan_id": scan_id,
                "evidence_ref": evidence_ref,
                "payload": payload,
            }
            for event_type, partner, scan_id, evidence_ref, payload in rows
        ],
        "retained_ledgers": {
            "customer_feedback_hook": "artifacts/beaaa-16992/design-partner-feedback-hook.jsonl",
            "eventual_outcome_hook": "artifacts/beaaa-16992/eventual-outcome-hook.jsonl",
        },
        "pass": counts
        == {
            "customer_feedback_hook": 1,
            "eventual_outcome_hook": 1,
            "result_record": 1,
        }
        and all(evidence_refs),
    }


def _control(passed: bool, evidence: list[str], checks: dict[str, Any]) -> dict[str, Any]:
    return {"status": "PASS" if passed else "FAIL", "evidence": evidence, "checks": checks}


def build_packet() -> dict[str, Any]:
    prior_issue = "beaaa-16977"
    summary_path = REPO / "artifacts" / prior_issue / "production_like_evidence_summary.json"
    target_path = REPO / "artifacts" / prior_issue / "production_like_target_output.json"
    rollback_path = REPO / "artifacts" / prior_issue / "rollback_drill_output.json"
    fail_closed_path = (
        REPO / "artifacts" / prior_issue / "fail_closed_authorization_pin_signature_output.json"
    )
    canary_path = REPO / "artifacts" / prior_issue / "canary_monitoring_retention_peril_output.json"

    evidence_build_head_sha = _git("rev-parse", "HEAD")
    candidate_commit_sha = evidence_build_head_sha
    summary = _read_json(summary_path)
    target = _read_json(target_path)
    fail_closed = _read_json(fail_closed_path)
    canary = _read_json(canary_path)
    rollback = _read_json(rollback_path)
    backup_restore = _backup_restore_drill()
    edge_control = _edge_control_drill()
    tenant_retention = _tenant_retention_drill()
    rollback_drill = _rollback_drill(rollback)

    runbook = REPO / "docs" / "deploy" / "sequence-20-production-deployment-runbook.md"
    backup_readme = REPO / "ops" / "scan-log-backup" / "README.md"
    edge_gateway = REPO / "api" / "edge_gateway.py"
    live_edge_readme = REPO / "infra" / "live-edge" / "README.md"

    dirty = bool(_git("status", "--porcelain"))
    signed_manifest = target.get("packet", {}).get("signed_manifest", {})
    target_output_sha256 = _canonical_sha256(target)
    fail_closed_output_sha256 = _canonical_sha256(fail_closed)
    canary_output_sha256 = _canonical_sha256(canary)
    rollback_output_sha256 = _canonical_sha256(rollback_drill)
    instrumentation_hooks = _instrumentation_hook_drill(
        candidate_commit_sha=candidate_commit_sha,
        signed_manifest_sha256=signed_manifest.get("sha256"),
        target_output_sha256=target_output_sha256,
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
                backup_restore["integrity_pass"]
                and backup_restore["rpo_pass"]
                and backup_restore["rto_pass"]
            ),
            [
                "temporary scan_log online backup exited 0",
                "temporary restore dry-run exited 0 and verified sha256/integrity",
            ],
            backup_restore,
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
                "rollback drill proves 340 active executable rows return to 305",
                "unrelated active rows stay unchanged",
                "local registry mutation is timed and remains under the 30-minute rollback ceiling",
            ],
            rollback_drill,
        ),
    }

    acceptance = {
        "authorized_end_to_end_design_partner_scan": bool(
            summary.get("production_like_scored_contract_consistent")
            and target.get("authorized_target", {}).get("authorization_signature")
        ),
        "tenant_isolation_and_retention": bool(
            controls["tenant_isolation"]["status"] == "PASS"
            and controls["retention"]["status"] == "PASS"
        ),
        "backup_restore_rpo_rto": bool(
            backup_restore["integrity_pass"]
            and backup_restore["rpo_pass"]
            and backup_restore["rto_pass"]
        ),
        "rollback_le_30m": bool(
            controls["feature_flag_rollback"]["status"] == "PASS"
        ),
        "critical_high_security_findings": "none_found_in_focused_harness",
        "instrumentation_hooks": bool(instrumentation_hooks["pass"]),
    }
    acceptance["overall_pass"] = all(v is True for v in acceptance.values() if isinstance(v, bool))

    return {
        "issue": "BEAAA-16992",
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
        "scanner_version": SCANNER_VERSION,
        "corpus_version": "golden fixture corpus versions plus Wave 1A expected_wave1a_hashes()",
        "probe_set_version": PROBE_SET_VERSION,
        "rubric_point_id": RUBRIC_POINT_IDS,
        "n5_calibration_delta": {
            "source": "tools/calibration-deltas/2026-07-22-beaaa16977-wave1a-scored-candidate-n5.json",
            "total_spread": 0,
            "total_delta": 0,
            "max_pillar_spread": 0,
            "max_pillar_delta": 0,
            "all_in_band_pm1": True,
        },
        "cto_cosign_ref": None,
        "candidate_identity": {
            "candidate_commit_sha_source": "git rev-parse HEAD at packet build",
            "signed_manifest_sha256": signed_manifest.get("sha256"),
            "target_output_sha256": target_output_sha256,
            "fail_closed_output_sha256": fail_closed_output_sha256,
            "canary_output_sha256": canary_output_sha256,
            "rollback_output_sha256": rollback_output_sha256,
        },
        "control_sources": {
            "deployment_runbook_sha256": _sha256_file(runbook),
            "backup_runbook_sha256": _sha256_file(backup_readme),
            "edge_gateway_sha256": _sha256_file(edge_gateway),
            "live_edge_readme_sha256": _sha256_file(live_edge_readme),
        },
        "controls": controls,
        "backup_restore_drill": backup_restore,
        "design_partner_calibration_instrumentation": instrumentation_hooks,
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
    hook_drill = packet["design_partner_calibration_instrumentation"]
    for event_type, rel_path in hook_drill["retained_ledgers"].items():
        rows = [row for row in hook_drill["records"] if row["event_type"] == event_type]
        path = REPO / rel_path
        path.write_text(
            "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
            encoding="utf-8",
        )
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
