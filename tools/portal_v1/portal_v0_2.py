"""BEAAA-4937 / BEAAA-4908 — connector-portal-api-v0.2 continuous monitoring addendum.

Houses the additive schedule resource, signed-descriptor wiring, scheduled-scan
endpoint, and scan_diff renderer required by the v0.2 addendum (sha256
35d8a78af1221e131da4859025591f72a30b45f6759488708237a4f0bd6d968e).

Each helper is a free function that takes the live PortalService as `self`.
`attach(PortalService)` (called at the bottom of portal.py) binds them as
methods. This split keeps the v0.2 surface reviewable and lets parallel work
on portal.py proceed without merging giant blocks.

Boundary rules respected:
  - additive only; no v0.1 wire field reshape (envelope schema unchanged)
  - no are-scanner-api-v1@v1.0 Output shape change (diff is a sibling artifact)
  - no LLM judges in diff path (pure function of two persisted scans)
  - no new outbound network surface (schedule fetch reuses portal API base URL)
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding

# Imported from portal.py — the constants must stay pinned there per addendum §9.
from portal import (
    PortalError,
    PortalService,
    RUBRIC_VERSION,
    SCAN_DIFF_VERSION,
    SCHEDULE_CADENCE_SECONDS,
    SCHEDULE_CADENCES,
    SCHEDULE_PILLARS,
    SCHEDULE_POLICY_VERSION,
    b64url,
    canonical_json,
    now_iso,
    sha256_hex,
    unb64url,
)


# -- helpers ----------------------------------------------------------------------------


def _parse_iso(self: PortalService, value: str) -> datetime:
    when = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return when


def _format_iso(self: PortalService, when: datetime) -> str:
    return when.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _sign_schedule_descriptor(self: PortalService, body: dict[str, Any]) -> str:
    # Same signing key as redaction policy (v0.1 §5.4): portal CA RSA, PKCS1v15+SHA256.
    signature = self._ca_key().sign(canonical_json(body), padding.PKCS1v15(), hashes.SHA256())
    return b64url(signature)


def verify_schedule_descriptor(self: PortalService, descriptor: dict[str, Any]) -> bool:
    signature = descriptor.get("policy_signature")
    if not signature:
        return False
    body = {k: v for k, v in descriptor.items() if k != "policy_signature"}
    try:
        self._ca_cert().public_key().verify(
            unb64url(signature),
            canonical_json(body),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
        return True
    except Exception:
        return False


# -- schedule resource ------------------------------------------------------------------


def put_schedule(self: PortalService, dp_id: str, payload: dict[str, Any], actor: str = "admin") -> dict[str, Any]:
    dp = self.db.execute("SELECT * FROM design_partners WHERE id = ?", (dp_id,)).fetchone()
    if not dp:
        raise PortalError(404, "dp_not_found", "Design partner not found.")
    connector = self.db.execute("SELECT * FROM connectors WHERE dp_id = ?", (dp_id,)).fetchone()
    if not connector:
        raise PortalError(409, "connector_missing", "Connector must enroll before schedule is set.")
    if actor != "admin":
        # v0.2 §5.1 — admin/Founder only. Carrier Rule: cadence is operational.
        raise PortalError(403, "actor_not_admin", "Schedule changes require admin actor.")
    cadence = (payload or {}).get("cadence")
    if cadence not in SCHEDULE_CADENCES:
        raise PortalError(422, "cadence_invalid", f"cadence must be one of {sorted(SCHEDULE_CADENCES)}.")
    next_run_at_raw = (payload or {}).get("next_run_at")
    if cadence == "disabled":
        next_run_at = None
        window_start = None
    else:
        if not next_run_at_raw:
            raise PortalError(422, "next_run_at_required", "next_run_at required unless cadence=disabled.")
        try:
            when = self._parse_iso(next_run_at_raw)
        except ValueError as err:
            raise PortalError(422, "next_run_at_invalid", f"next_run_at not ISO-8601: {err}") from err
        next_run_at = self._format_iso(when)
        window_start = next_run_at
    existing = self.db.execute("SELECT * FROM schedules WHERE dp_id = ?", (dp_id,)).fetchone()
    last_run_at = existing["last_run_at"] if existing else None
    last_scan_id = existing["last_scan_id"] if existing else None
    descriptor_body = {
        "version": SCHEDULE_POLICY_VERSION,
        "dp_id": dp_id,
        "connector_id": connector["id"],
        "cadence": cadence,
        "next_run_at": next_run_at,
        "last_run_at": last_run_at,
        "last_scan_id": last_scan_id,
        "schedule_window_start": window_start,
    }
    signature = self._sign_schedule_descriptor(descriptor_body)
    self.db.execute(
        "INSERT INTO schedules (dp_id, cadence, next_run_at, last_run_at, last_scan_id, schedule_window_start, policy_signature, policy_version, updated_by, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(dp_id) DO UPDATE SET "
        "cadence=excluded.cadence, next_run_at=excluded.next_run_at, schedule_window_start=excluded.schedule_window_start, "
        "policy_signature=excluded.policy_signature, policy_version=excluded.policy_version, "
        "updated_by=excluded.updated_by, updated_at=excluded.updated_at",
        (
            dp_id,
            cadence,
            next_run_at,
            last_run_at,
            last_scan_id,
            window_start,
            signature,
            SCHEDULE_POLICY_VERSION,
            actor,
            now_iso(),
        ),
    )
    self.db.commit()
    self.audit(
        actor,
        "schedule_updated",
        "schedule",
        dp_id,
        {k: v for k, v in descriptor_body.items() if k != "policy_signature"},
    )
    return {**descriptor_body, "policy_signature": signature}


def get_schedule(self: PortalService, dp_id: str) -> dict[str, Any]:
    row = self.db.execute("SELECT * FROM schedules WHERE dp_id = ?", (dp_id,)).fetchone()
    if not row:
        raise PortalError(404, "schedule_not_found", "No schedule set for this DP.")
    connector = self.db.execute("SELECT * FROM connectors WHERE dp_id = ?", (dp_id,)).fetchone()
    if not connector:
        raise PortalError(409, "connector_missing", "Connector missing for DP with persisted schedule.")
    return {
        "version": row["policy_version"],
        "dp_id": dp_id,
        "connector_id": connector["id"],
        "cadence": row["cadence"],
        "next_run_at": row["next_run_at"],
        "last_run_at": row["last_run_at"],
        "last_scan_id": row["last_scan_id"],
        "schedule_window_start": row["schedule_window_start"],
        "policy_signature": row["policy_signature"],
    }


def connector_schedule(self: PortalService, connector_id: str) -> dict[str, Any]:
    connector = self.db.execute("SELECT * FROM connectors WHERE id = ?", (connector_id,)).fetchone()
    if not connector:
        raise PortalError(404, "connector_not_found", "Connector not found.")
    try:
        descriptor = self.get_schedule(connector["dp_id"])
    except PortalError as err:
        if err.code == "schedule_not_found":
            body = {
                "version": SCHEDULE_POLICY_VERSION,
                "dp_id": connector["dp_id"],
                "connector_id": connector_id,
                "cadence": "disabled",
                "next_run_at": None,
                "last_run_at": None,
                "last_scan_id": None,
                "schedule_window_start": None,
            }
            return {**body, "policy_signature": self._sign_schedule_descriptor(body)}
        raise
    return descriptor


def create_scheduled_scan(self: PortalService, connector_id: str, schedule_window_start: str) -> dict[str, Any]:
    connector = self.db.execute("SELECT * FROM connectors WHERE id = ?", (connector_id,)).fetchone()
    if not connector:
        raise PortalError(404, "connector_not_found", "Connector not found.")
    schedule = self.db.execute("SELECT * FROM schedules WHERE dp_id = ?", (connector["dp_id"],)).fetchone()
    if not schedule:
        raise PortalError(409, "schedule_not_set", "No schedule set for this DP.")
    if schedule["cadence"] == "disabled":
        raise PortalError(409, "schedule_disabled", "Schedule cadence is disabled.")
    if not schedule_window_start:
        raise PortalError(422, "schedule_window_start_required", "schedule_window_start is required.")
    if schedule_window_start != schedule["schedule_window_start"]:
        raise PortalError(409, "schedule_window_mismatch", "schedule_window_start does not match current portal schedule.")
    existing = self.db.execute(
        "SELECT * FROM scheduled_scan_keys WHERE connector_id = ? AND schedule_window_start = ?",
        (connector_id, schedule_window_start),
    ).fetchone()
    if existing:
        scan_row = self.db.execute("SELECT * FROM scans WHERE id = ?", (existing["scan_id"],)).fetchone()
        return {
            "scan_id": existing["scan_id"],
            "status": scan_row["status"] if scan_row else "unknown",
            "idempotent": True,
            "schedule_window_start": schedule_window_start,
        }
    scan = self.trigger_scan(connector["dp_id"], actor="schedule")
    self.db.execute(
        "INSERT INTO scheduled_scan_keys (connector_id, schedule_window_start, scan_id, created_at) VALUES (?, ?, ?, ?)",
        (connector_id, schedule_window_start, scan["scan_id"], now_iso()),
    )
    self.db.commit()
    self.audit(
        "schedule",
        "scheduled_scan_started",
        "scan",
        scan["scan_id"],
        {
            "connector_id": connector_id,
            "schedule_window_start": schedule_window_start,
            "dp_id": connector["dp_id"],
        },
    )
    return {
        "scan_id": scan["scan_id"],
        "status": scan["status"],
        "idempotent": False,
        "schedule_window_start": schedule_window_start,
    }


# -- scan_diff renderer -----------------------------------------------------------------


def _maybe_render_scan_diff(self: PortalService, candidate_scan_id: str, dp_id: str) -> dict[str, Any] | None:
    existing = self.db.execute(
        "SELECT * FROM scan_diffs WHERE candidate_scan_id = ?", (candidate_scan_id,)
    ).fetchone()
    if existing:
        return None
    baseline_row = self.db.execute(
        "SELECT * FROM scans WHERE dp_id = ? AND status = 'complete' AND id != ? "
        "ORDER BY completed_at DESC LIMIT 1",
        (dp_id, candidate_scan_id),
    ).fetchone()
    if not baseline_row:
        self.audit(
            "portal",
            "scan_diff_skipped",
            "scan_diff",
            candidate_scan_id,
            {"reason": "no_baseline", "dp_id": dp_id},
        )
        return None
    candidate_row = self.db.execute("SELECT * FROM scans WHERE id = ?", (candidate_scan_id,)).fetchone()
    if not candidate_row or not candidate_row["score_json"] or not baseline_row["score_json"]:
        return None
    baseline_score = json.loads(baseline_row["score_json"])
    candidate_score = json.loads(candidate_row["score_json"])
    artifact = self._compute_scan_diff(dp_id, baseline_row, baseline_score, candidate_row, candidate_score)
    portal_signature = b64url(self._counter_key().sign(canonical_json(artifact)))
    signed_artifact = {**artifact, "portal_signature": portal_signature}
    self.db.execute(
        "INSERT INTO scan_diffs (candidate_scan_id, dp_id, baseline_scan_id, kind, artifact_json, portal_signature, produced_at, scan_diff_version) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            candidate_scan_id,
            dp_id,
            baseline_row["id"],
            artifact["kind"],
            json.dumps(artifact, sort_keys=True),
            portal_signature,
            artifact["produced_at"],
            SCAN_DIFF_VERSION,
        ),
    )
    self.db.commit()
    self.audit(
        "portal",
        "scan_diff_rendered",
        "scan_diff",
        candidate_scan_id,
        {
            "dp_id": dp_id,
            "baseline_scan_id": baseline_row["id"],
            "candidate_scan_id": candidate_scan_id,
            "kind": artifact["kind"],
            "are_total_delta": artifact["are_total_delta"],
            "pillar_deltas": artifact["pillar_deltas"],
        },
    )
    return signed_artifact


def _compute_scan_diff(
    self: PortalService,
    dp_id: str,
    baseline_row,
    baseline_score: dict[str, Any],
    candidate_row,
    candidate_score: dict[str, Any],
) -> dict[str, Any]:
    produced_at = now_iso()
    baseline_pins = {
        "scanner_version": baseline_score.get("scanner_version"),
        "corpus_version": baseline_score.get("corpus_version"),
        "rubric_version": RUBRIC_VERSION,
    }
    candidate_pins = {
        "scanner_version": candidate_score.get("scanner_version"),
        "corpus_version": candidate_score.get("corpus_version"),
        "rubric_version": RUBRIC_VERSION,
    }
    incomparable_reason = None
    for field in ("scanner_version", "corpus_version", "rubric_version"):
        if baseline_pins[field] != candidate_pins[field]:
            incomparable_reason = (
                f"pin_mismatch:{field}:baseline={baseline_pins[field]}:candidate={candidate_pins[field]}"
            )
            break
    if incomparable_reason:
        kind = "incomparable"
        are_total_delta: float | None = None
        pillar_deltas: dict[str, float | None] = {p: None for p in SCHEDULE_PILLARS}
        dim_deltas: list[dict[str, Any]] = []
    else:
        kind = "comparable"
        are_total_delta = float(candidate_score.get("are_total", 0)) - float(baseline_score.get("are_total", 0))
        pillar_deltas = {}
        base_sub = baseline_score.get("subscores", {}) or {}
        cand_sub = candidate_score.get("subscores", {}) or {}
        for pillar in SCHEDULE_PILLARS:
            if pillar in base_sub and pillar in cand_sub:
                pillar_deltas[pillar] = float(cand_sub[pillar]) - float(base_sub[pillar])
            else:
                pillar_deltas[pillar] = None
        dim_deltas = self._dim_deltas(baseline_score, candidate_score)
    return {
        "scan_diff_version": SCAN_DIFF_VERSION,
        "dp_id": dp_id,
        "baseline_scan_id": baseline_row["id"],
        "candidate_scan_id": candidate_row["id"],
        "produced_at": produced_at,
        "scanner_version": candidate_pins["scanner_version"],
        "corpus_version": candidate_pins["corpus_version"],
        "rubric_version": candidate_pins["rubric_version"],
        "kind": kind,
        "incomparable_reason": incomparable_reason,
        "are_total_delta": are_total_delta,
        "pillar_deltas": pillar_deltas,
        "dim_deltas": dim_deltas,
        "envelope_signature_chain_baseline": self._envelope_chain_hashes(baseline_row["id"]),
        "envelope_signature_chain_candidate": self._envelope_chain_hashes(candidate_row["id"]),
    }


def _dim_deltas(self: PortalService, baseline_score: dict[str, Any], candidate_score: dict[str, Any]) -> list[dict[str, Any]]:
    def by_rubric_point(score: dict[str, Any]) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for ref in score.get("evidence_refs", []) or []:
            rpid = ref.get("rubric_point_id")
            if rpid and rpid not in out:
                out[rpid] = ref
        return out

    base = by_rubric_point(baseline_score)
    cand = by_rubric_point(candidate_score)
    deltas: list[dict[str, Any]] = []
    for rpid in sorted(set(base) | set(cand)):
        base_ref = base.get(rpid) or {}
        cand_ref = cand.get(rpid) or {}
        base_verdict = base_ref.get("verdict")
        cand_verdict = cand_ref.get("verdict")
        if base_verdict == cand_verdict:
            continue
        deltas.append(
            {
                "rubric_point_id": rpid,
                "verdict_baseline": base_verdict,
                "verdict_candidate": cand_verdict,
                "weight": (cand_ref or base_ref).get("weight", 0.0),
                "evidence_refs": {
                    "baseline": base_ref.get("artifact_uri"),
                    "candidate": cand_ref.get("artifact_uri"),
                },
            }
        )
    return deltas


def _envelope_chain_hashes(self: PortalService, scan_id: str) -> list[str]:
    rows = self.db.execute(
        "SELECT task_id FROM envelopes WHERE scan_id = ? ORDER BY task_id",
        (scan_id,),
    ).fetchall()
    hashes_out: list[str] = []
    for row in rows:
        path = self.config.evidence_dir / scan_id / "envelopes" / f"{row['task_id']}.json"
        if path.exists():
            hashes_out.append(sha256_hex(path.read_bytes()))
    return hashes_out


def get_scan_diff(self: PortalService, candidate_scan_id: str) -> dict[str, Any]:
    row = self.db.execute(
        "SELECT * FROM scan_diffs WHERE candidate_scan_id = ?", (candidate_scan_id,)
    ).fetchone()
    if not row:
        raise PortalError(404, "scan_diff_not_found", "No scan_diff persisted for this scan.")
    artifact = json.loads(row["artifact_json"])
    return {**artifact, "portal_signature": row["portal_signature"]}


def _maybe_advance_schedule_after_scan(self: PortalService, scan_id: str, dp_id: str) -> None:
    key_row = self.db.execute(
        "SELECT * FROM scheduled_scan_keys WHERE scan_id = ?", (scan_id,)
    ).fetchone()
    if not key_row:
        return
    schedule = self.db.execute("SELECT * FROM schedules WHERE dp_id = ?", (dp_id,)).fetchone()
    if not schedule or schedule["cadence"] not in SCHEDULE_CADENCE_SECONDS:
        return
    try:
        when = self._parse_iso(schedule["next_run_at"]) if schedule["next_run_at"] else None
    except ValueError:
        when = None
    if when is None:
        return
    cadence_seconds = SCHEDULE_CADENCE_SECONDS[schedule["cadence"]]
    new_when = when + timedelta(seconds=cadence_seconds)
    new_next_run_at = self._format_iso(new_when)
    new_window_start = new_next_run_at
    connector = self.db.execute("SELECT * FROM connectors WHERE dp_id = ?", (dp_id,)).fetchone()
    descriptor_body = {
        "version": SCHEDULE_POLICY_VERSION,
        "dp_id": dp_id,
        "connector_id": connector["id"] if connector else "",
        "cadence": schedule["cadence"],
        "next_run_at": new_next_run_at,
        "last_run_at": now_iso(),
        "last_scan_id": scan_id,
        "schedule_window_start": new_window_start,
    }
    signature = self._sign_schedule_descriptor(descriptor_body)
    self.db.execute(
        "UPDATE schedules SET next_run_at = ?, last_run_at = ?, last_scan_id = ?, "
        "schedule_window_start = ?, policy_signature = ?, updated_by = 'portal', updated_at = ? "
        "WHERE dp_id = ?",
        (
            new_next_run_at,
            descriptor_body["last_run_at"],
            scan_id,
            new_window_start,
            signature,
            now_iso(),
            dp_id,
        ),
    )
    self.db.commit()
    self.audit(
        "portal",
        "schedule_advanced",
        "schedule",
        dp_id,
        {k: v for k, v in descriptor_body.items() if k != "policy_signature"},
    )


# -- attachment -------------------------------------------------------------------------


def attach(cls: type[PortalService]) -> type[PortalService]:
    """Bind all v0.2 methods onto PortalService.

    Idempotent: re-running attach() against the same class is a no-op overwrite.
    """
    for name in (
        "_parse_iso",
        "_format_iso",
        "_sign_schedule_descriptor",
        "verify_schedule_descriptor",
        "put_schedule",
        "get_schedule",
        "connector_schedule",
        "create_scheduled_scan",
        "_maybe_render_scan_diff",
        "_compute_scan_diff",
        "_dim_deltas",
        "_envelope_chain_hashes",
        "get_scan_diff",
        "_maybe_advance_schedule_after_scan",
    ):
        setattr(cls, name, globals()[name])
    return cls
