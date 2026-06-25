"""BackBond Certified Scan verification service (BEAAA-5016 -> BEAAA-5739).

Implements the public verification surface defined by the BEAAA-4915 CMO packet
v0.1, migrated to v0.2 per BEAAA-5739 (PM1 pin completeness + BackBond canon).

Surface (verify-api-v0.2):
- GET /verify/{envelope_id}                  HTML verification page
- GET /verify/{envelope_id}.json             machine-readable signed JSON
- GET /verify/{envelope_id}/certificate.pdf  Certificate of BackBond Scan PDF
- Accept: application/json on /verify/{envelope_id} returns JSON.

States: valid, expired, revoked, not_found. Badge display is the partner's
responsibility per the CMO embed rules; signature_status here is the wire signal
that drives the partner to suppress an invalid badge.

PM1 pins emitted on the signed body: rubric_version, scanner_version,
corpus_version, model_versions, seed (BEAAA-5739 AC2). v0.1 carried only
the first two; v0.2 completes the upstream pin set (portal.py
SCANNER_REQUIRED_FIELDS L116-120).

Boundary held: this surface is render + signature, not score-producing. No
are-scanner-api-v1@v1.0 Output change (additive public-surface fields only); no
rubric/scoring math change; no new SCANNER_VERSION/CORPUS_VERSION. The signature
key reuses the portal Ed25519 counter-signing key (consistent with envelope
ingest in portal.py).
"""

from __future__ import annotations

import html
import json
import sqlite3
from datetime import UTC, date, datetime
from io import BytesIO
from typing import Any

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

import verify_v0_2 as copy


# ---------------------------------------------------------------------------
# Schema.
# ---------------------------------------------------------------------------

# v0.2 schema (BEAAA-5739): adds corpus_version, model_versions_json, seed.
# Migration from v0.1 is forward-only via _migrate_verify_schema_v0_2_v0_1; a
# reverse path is provided in _downgrade_verify_schema_to_v0_1 (BEAAA-5698
# idiom: rename-and-copy because pre-3.35 SQLite cannot DROP COLUMN).

def _ensure_verify_schema(self: Any) -> None:
    self.db.executescript(
        """
        CREATE TABLE IF NOT EXISTS verify_envelopes (
            envelope_id TEXT PRIMARY KEY,
            scan_id TEXT NOT NULL,
            vendor_name TEXT NOT NULL,
            agent_name TEXT NOT NULL,
            backbond_score INTEGER NOT NULL,
            threshold_status TEXT NOT NULL,
            rubric_version TEXT NOT NULL,
            scanner_version TEXT NOT NULL,
            corpus_version TEXT NOT NULL DEFAULT '',
            model_versions_json TEXT NOT NULL DEFAULT '{}',
            seed TEXT NOT NULL DEFAULT '',
            scan_date TEXT NOT NULL,
            valid_through TEXT NOT NULL,
            methodology_url TEXT NOT NULL,
            probe_ledger_url TEXT NOT NULL,
            state TEXT NOT NULL,
            revoked_at TEXT,
            revoked_reason TEXT,
            created_at TEXT NOT NULL
        );
        """
    )
    _migrate_verify_schema_to_v0_2(self)
    self.db.commit()


def _table_columns(self: Any, table: str) -> set[str]:
    return {row["name"] for row in self.db.execute(f"PRAGMA table_info({table})")}


def _migrate_verify_schema_to_v0_2(self: Any) -> None:
    """Idempotent forward migration from v0.1 to v0.2 schema.

    Adds corpus_version, model_versions_json, seed columns if missing. Existing
    rows must be backfilled by the caller from the upstream scan envelope; this
    routine does NOT auto-backfill. mint_verify_envelope() validates non-empty
    at insert time so new envelopes always carry pins; legacy rows whose pins
    are blank will surface as PortalError at get_verify_envelope() time
    (fail-closed per BEAAA-5739 AC1).
    """
    cols = _table_columns(self, "verify_envelopes")
    if "corpus_version" not in cols:
        self.db.execute(
            "ALTER TABLE verify_envelopes ADD COLUMN corpus_version TEXT NOT NULL DEFAULT ''"
        )
    if "model_versions_json" not in cols:
        self.db.execute(
            "ALTER TABLE verify_envelopes ADD COLUMN model_versions_json TEXT NOT NULL DEFAULT '{}'"
        )
    if "seed" not in cols:
        self.db.execute(
            "ALTER TABLE verify_envelopes ADD COLUMN seed TEXT NOT NULL DEFAULT ''"
        )


def _downgrade_verify_schema_to_v0_1(self: Any) -> None:
    """Reverse the v0.2 schema additions (BEAAA-5698 rename-and-copy idiom).

    SQLite ALTER TABLE DROP COLUMN is unsupported pre-3.35; rebuild the table
    with the v0.1 column set and rename in place. Not wired into the normal
    init path; provided for operational rollback / disaster recovery only.
    """
    self.db.executescript(
        """
        CREATE TABLE verify_envelopes_v0_1 (
            envelope_id TEXT PRIMARY KEY,
            scan_id TEXT NOT NULL,
            vendor_name TEXT NOT NULL,
            agent_name TEXT NOT NULL,
            backbond_score INTEGER NOT NULL,
            threshold_status TEXT NOT NULL,
            rubric_version TEXT NOT NULL,
            scanner_version TEXT NOT NULL,
            scan_date TEXT NOT NULL,
            valid_through TEXT NOT NULL,
            methodology_url TEXT NOT NULL,
            probe_ledger_url TEXT NOT NULL,
            state TEXT NOT NULL,
            revoked_at TEXT,
            revoked_reason TEXT,
            created_at TEXT NOT NULL
        );
        INSERT INTO verify_envelopes_v0_1
            SELECT envelope_id, scan_id, vendor_name, agent_name, backbond_score,
                   threshold_status, rubric_version, scanner_version, scan_date,
                   valid_through, methodology_url, probe_ledger_url,
                   state, revoked_at, revoked_reason, created_at
            FROM verify_envelopes;
        DROP TABLE verify_envelopes;
        ALTER TABLE verify_envelopes_v0_1 RENAME TO verify_envelopes;
        """
    )
    self.db.commit()


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------

def _today_iso(self: Any) -> str:
    """ISO date in UTC. Indirected so tests can monkeypatch."""
    return date.fromtimestamp(datetime.now(UTC).timestamp()).isoformat()


def _public_origin(self: Any) -> str:
    """Public origin for self-referential URLs.

    Defaults to https://portal.backbond.ai per BEAAA-4909 deploy; can be
    overridden via PORTAL_PUBLIC_ORIGIN env (used in tests).
    """
    import os
    return os.environ.get("PORTAL_PUBLIC_ORIGIN", "https://portal.backbond.ai").rstrip("/")


def _derive_status(row: sqlite3.Row, today_iso: str) -> str:
    if row["state"] == "revoked":
        return "revoked"
    if row["valid_through"] < today_iso:
        return "expired"
    return "valid"


def _decode_model_versions(raw: str | None) -> dict[str, str]:
    if not raw:
        return {}
    try:
        decoded = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    if not isinstance(decoded, dict):
        return {}
    # Coerce values to strings for stable signed-body shape.
    return {str(k): str(v) for k, v in decoded.items()}


def _envelope_body(self: Any, row: sqlite3.Row, today_iso: str) -> dict[str, Any]:
    """The signed body (everything except the signature).

    v0.2 emits five pins: rubric_version, scanner_version, corpus_version,
    model_versions, seed. Legacy rows whose v0.2 pins are blank (pre-migration
    backfill not done) raise PortalError to refuse a partial public envelope
    (BEAAA-5739 AC1 fail-closed contract).
    """
    from portal import PortalError
    status = _derive_status(row, today_iso)
    origin = self._verify_public_origin()
    corpus_version = row["corpus_version"] or ""
    seed = row["seed"] or ""
    model_versions = _decode_model_versions(row["model_versions_json"])
    if not corpus_version or not seed:
        raise PortalError(
            500,
            "verify_envelope_pins_missing",
            "verify envelope is missing required v0.2 pins (corpus_version/seed); "
            "re-mint required from upstream scan envelope.",
        )
    body = {
        "verify_api_version": copy.VERIFY_API_VERSION,
        "status": status,
        "vendor_name": row["vendor_name"],
        "agent_name": row["agent_name"],
        "backbond_score": int(row["backbond_score"]),
        "threshold_status": row["threshold_status"],
        "scan_id": row["scan_id"],
        "envelope_id": row["envelope_id"],
        "rubric_version": row["rubric_version"],
        "scanner_version": row["scanner_version"],
        "corpus_version": corpus_version,
        "model_versions": model_versions,
        "seed": seed,
        "scan_date": row["scan_date"],
        "valid_through": row["valid_through"],
        "methodology_url": row["methodology_url"],
        "probe_ledger_url": row["probe_ledger_url"],
        "verification_url": f"{origin}/verify/{row['envelope_id']}",
    }
    if status == "revoked":
        body["revoked_at"] = row["revoked_at"] or ""
        body["revoked_reason"] = row["revoked_reason"] or ""
    return body


def _sign_envelope(self: Any, body: dict[str, Any]) -> dict[str, str]:
    """Ed25519 over the canonical-json bytes of the body.

    Matches portal.py envelope-ingest pattern (counter-signing key, raw signature).
    Body is the public verify payload above; the signature object is appended at
    response-render time only, so re-signing is deterministic.
    """
    from portal import canonical_json, b64url
    sig = self._counter_key().sign(canonical_json(body))
    return {
        "algorithm": copy.SIGNATURE_ALGORITHM,
        "key_id": copy.KEY_ID,
        "value": b64url(sig),
    }


# ---------------------------------------------------------------------------
# Public service methods.
# ---------------------------------------------------------------------------

def mint_verify_envelope(
    self: Any,
    *,
    envelope_id: str,
    scan_id: str,
    vendor_name: str,
    agent_name: str,
    backbond_score: int,
    threshold_status: str,
    rubric_version: str,
    scanner_version: str,
    corpus_version: str,
    model_versions: dict[str, str],
    seed: str,
    scan_date: str,
    valid_through: str,
    methodology_url: str | None = None,
    probe_ledger_url: str | None = None,
) -> dict[str, Any]:
    """Create a verification envelope row (verify-api-v0.2).

    Caller (badge-issuance flow, fixtures, or a future bind step) decides when a
    completed scan becomes a public verifiable envelope. This service does not
    auto-mint from scans because not every scan is public.

    v0.2 (BEAAA-5739): corpus_version, model_versions, seed are required; all
    five pins (incl. rubric_version + scanner_version) validated non-empty.
    """
    from portal import PortalError, now_iso
    if threshold_status not in copy.THRESHOLD_STATUSES:
        raise PortalError(422, "verify_threshold_status_invalid", f"threshold_status must be one of {copy.THRESHOLD_STATUSES}.")
    if not (0 <= int(backbond_score) <= 100):
        raise PortalError(422, "verify_backbond_score_invalid", "backbond_score must be 0-100.")
    if not rubric_version:
        raise PortalError(422, "verify_rubric_version_missing", "rubric_version must be non-empty.")
    if not scanner_version:
        raise PortalError(422, "verify_scanner_version_missing", "scanner_version must be non-empty.")
    if not corpus_version:
        raise PortalError(422, "verify_corpus_version_missing", "corpus_version must be non-empty.")
    if not seed:
        raise PortalError(422, "verify_seed_missing", "seed must be non-empty.")
    if not isinstance(model_versions, dict):
        raise PortalError(422, "verify_model_versions_invalid", "model_versions must be a dict.")
    model_versions_json = json.dumps(
        {str(k): str(v) for k, v in model_versions.items()},
        sort_keys=True,
        separators=(",", ":"),
    )
    origin = self._verify_public_origin()
    row = {
        "envelope_id": envelope_id,
        "scan_id": scan_id,
        "vendor_name": vendor_name,
        "agent_name": agent_name,
        "backbond_score": int(backbond_score),
        "threshold_status": threshold_status,
        "rubric_version": rubric_version,
        "scanner_version": scanner_version,
        "corpus_version": corpus_version,
        "model_versions_json": model_versions_json,
        "seed": seed,
        "scan_date": scan_date,
        "valid_through": valid_through,
        "methodology_url": methodology_url or f"{origin}/methodology",
        "probe_ledger_url": probe_ledger_url or f"{origin}/probe-ledger/",
        "state": "valid",
        "revoked_at": None,
        "revoked_reason": None,
        "created_at": now_iso(),
    }
    self.db.execute(
        "INSERT INTO verify_envelopes (envelope_id, scan_id, vendor_name, agent_name, "
        "backbond_score, threshold_status, rubric_version, scanner_version, "
        "corpus_version, model_versions_json, seed, scan_date, "
        "valid_through, methodology_url, probe_ledger_url, state, revoked_at, "
        "revoked_reason, created_at) "
        "VALUES (:envelope_id, :scan_id, :vendor_name, :agent_name, :backbond_score, "
        ":threshold_status, :rubric_version, :scanner_version, "
        ":corpus_version, :model_versions_json, :seed, :scan_date, "
        ":valid_through, :methodology_url, :probe_ledger_url, :state, :revoked_at, "
        ":revoked_reason, :created_at)",
        row,
    )
    self.db.commit()
    self.audit("portal", "verify_envelope_minted", "verify_envelope", envelope_id, {
        "scan_id": scan_id, "backbond_score": int(backbond_score), "valid_through": valid_through,
    })
    return {"envelope_id": envelope_id, "verification_url": f"{origin}/verify/{envelope_id}"}


def revoke_verify_envelope(self: Any, envelope_id: str, reason: str) -> dict[str, Any]:
    from portal import PortalError, now_iso
    row = self.db.execute("SELECT * FROM verify_envelopes WHERE envelope_id = ?", (envelope_id,)).fetchone()
    if not row:
        raise PortalError(404, "verify_envelope_not_found", "Verify envelope not found.")
    self.db.execute(
        "UPDATE verify_envelopes SET state = 'revoked', revoked_at = ?, revoked_reason = ? "
        "WHERE envelope_id = ?",
        (now_iso(), reason, envelope_id),
    )
    self.db.commit()
    self.audit("portal", "verify_envelope_revoked", "verify_envelope", envelope_id, {"reason": reason})
    return {"envelope_id": envelope_id, "status": "revoked"}


def get_verify_envelope(self: Any, envelope_id: str) -> dict[str, Any]:
    """Return the signed JSON envelope for `envelope_id`.

    Always returns a payload — `not_found` is a public status, not a 404 — so
    procurement tooling gets a deterministic shape regardless of state.
    """
    row = self.db.execute("SELECT * FROM verify_envelopes WHERE envelope_id = ?", (envelope_id,)).fetchone()
    today_iso = self._verify_today_iso()
    if not row:
        body = {
            "verify_api_version": copy.VERIFY_API_VERSION,
            "status": "not_found",
            "envelope_id": envelope_id,
            "verification_url": f"{self._verify_public_origin()}/verify/{envelope_id}",
        }
        signature = self._sign_verify_envelope(body)
        return {**body, "signature": signature}
    body = self._verify_envelope_body(row, today_iso)
    signature = self._sign_verify_envelope(body)
    return {**body, "signature": signature}


# ---------------------------------------------------------------------------
# HTML render.
# ---------------------------------------------------------------------------

def _render_field_row(label: str, value: str, *, is_link: bool = False) -> str:
    safe_value = html.escape(value or "")
    if is_link and value:
        safe_value = f'<a href="{html.escape(value)}" rel="noopener">{safe_value}</a>'
    return f"<tr><th>{html.escape(label)}</th><td>{safe_value}</td></tr>"


def _format_model_versions(model_versions: dict[str, str]) -> str:
    """Render the model_versions dict in deterministic key=value form.

    Empty dict renders as '(none)' to stay legible for surfaces where upstream
    scans did not pin any explicit model versions (e.g. fixture-score runs).
    """
    if not model_versions:
        return "(none)"
    parts = [f"{k}={v}" for k, v in sorted(model_versions.items())]
    return ", ".join(parts)


def render_verify_page(self: Any, envelope_id: str) -> str:
    payload = self.get_verify_envelope(envelope_id)
    status = payload["status"]
    if status == "valid":
        body_copy = copy.VERIFIED_BODY
    elif status == "expired":
        body_copy = copy.EXPIRED_BODY
    elif status == "revoked":
        body_copy = copy.REVOKED_BODY
    else:
        body_copy = copy.NOT_FOUND_BODY

    badge_state_note = ""
    if status in {"expired", "revoked", "not_found"}:
        badge_state_note = (
            '<p class="badge-suppress"><strong>Badge display must be '
            "suppressed or replaced.</strong> The signed envelope state below "
            'is not <code>valid</code>.</p>'
        )

    if status == "not_found":
        body = f"""
<h1>{html.escape(copy.PAGE_H1)}</h1>
<p>{html.escape(body_copy)}</p>
{badge_state_note}
<table>
  {_render_field_row(copy.PUBLIC_LABELS['envelope_id'], payload['envelope_id'])}
  {_render_field_row(copy.PUBLIC_LABELS['signature_status'], status)}
</table>
<p class="muted">{html.escape(copy.FOOTER_DISCLAIMER)}</p>
"""
        return self._render_page(copy.PAGE_H1, body)

    rows = [
        _render_field_row(copy.PUBLIC_LABELS["agent_name"], payload["agent_name"]),
        _render_field_row(copy.PUBLIC_LABELS["vendor_name"], payload["vendor_name"]),
        _render_field_row(copy.PUBLIC_LABELS["backbond_score"], str(payload["backbond_score"])),
        _render_field_row(copy.PUBLIC_LABELS["scan_id"], payload["scan_id"]),
        _render_field_row(copy.PUBLIC_LABELS["envelope_id"], payload["envelope_id"]),
        _render_field_row(copy.PUBLIC_LABELS["rubric_version"], payload["rubric_version"]),
        _render_field_row(copy.PUBLIC_LABELS["scanner_version"], payload["scanner_version"]),
        _render_field_row(copy.PUBLIC_LABELS["corpus_version"], payload["corpus_version"]),
        _render_field_row(copy.PUBLIC_LABELS["model_versions"], _format_model_versions(payload["model_versions"])),
        _render_field_row(copy.PUBLIC_LABELS["seed"], payload["seed"]),
        _render_field_row(copy.PUBLIC_LABELS["scan_date"], payload["scan_date"]),
        _render_field_row(copy.PUBLIC_LABELS["valid_through"], payload["valid_through"]),
        _render_field_row(copy.PUBLIC_LABELS["methodology_url"], payload["methodology_url"], is_link=True),
        _render_field_row(copy.PUBLIC_LABELS["probe_ledger_url"], payload["probe_ledger_url"], is_link=True),
        _render_field_row(copy.PUBLIC_LABELS["signature_status"], status),
    ]
    cert_link = (
        f'<p><a href="/verify/{html.escape(payload["envelope_id"])}/certificate.pdf">'
        f"Download {html.escape(copy.CERT_TITLE)} (PDF)</a></p>"
    )
    json_link = (
        f'<p class="muted">Machine-readable: '
        f'<a href="/verify/{html.escape(payload["envelope_id"])}.json">JSON envelope</a></p>'
    )
    body = f"""
<h1>{html.escape(copy.PAGE_H1)}</h1>
<p>{html.escape(body_copy)}</p>
{badge_state_note}
<table>{''.join(rows)}</table>
{cert_link}
{json_link}
<p class="muted">{html.escape(copy.FOOTER_DISCLAIMER)}</p>
"""
    return self._render_page(copy.PAGE_H1, body)


# ---------------------------------------------------------------------------
# Certificate PDF render.
# ---------------------------------------------------------------------------

def render_verify_certificate_pdf(self: Any, envelope_id: str) -> bytes:
    payload = self.get_verify_envelope(envelope_id)
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    width, height = A4
    y = height - 56

    def write(text: str, *, size: int = 11, font: str = "Helvetica", x: float = 56) -> None:
        nonlocal y
        c.setFont(font, size)
        c.drawString(x, y, text)
        y -= size + 4

    write(copy.CERT_TITLE, size=18, font="Helvetica-Bold")
    y -= 6
    write(f"Status: {payload['status']}", size=10, font="Helvetica-Oblique")
    y -= 4

    # Wrap CERT_LEAD onto multiple lines deterministically.
    from textwrap import wrap as _wrap
    for line in _wrap(copy.CERT_LEAD, width=92):
        write(line, size=10)
    y -= 8

    rows = [
        (copy.PUBLIC_LABELS["vendor_name"], payload.get("vendor_name", "")),
        (copy.PUBLIC_LABELS["agent_name"], payload.get("agent_name", "")),
        (copy.PUBLIC_LABELS["backbond_score"], str(payload.get("backbond_score", ""))),
        (copy.PUBLIC_LABELS["threshold_status"], payload.get("threshold_status", "")),
        (copy.PUBLIC_LABELS["scan_id"], payload.get("scan_id", "")),
        (copy.PUBLIC_LABELS["envelope_id"], payload.get("envelope_id", "")),
        (copy.PUBLIC_LABELS["rubric_version"], payload.get("rubric_version", "")),
        (copy.PUBLIC_LABELS["scanner_version"], payload.get("scanner_version", "")),
        (copy.PUBLIC_LABELS["corpus_version"], payload.get("corpus_version", "")),
        (copy.PUBLIC_LABELS["model_versions"], _format_model_versions(payload.get("model_versions", {}))),
        (copy.PUBLIC_LABELS["seed"], payload.get("seed", "")),
        (copy.PUBLIC_LABELS["scan_date"], payload.get("scan_date", "")),
        (copy.PUBLIC_LABELS["valid_through"], payload.get("valid_through", "")),
        (copy.PUBLIC_LABELS["methodology_url"], payload.get("methodology_url", "")),
        (copy.PUBLIC_LABELS["probe_ledger_url"], payload.get("probe_ledger_url", "")),
        (copy.PUBLIC_LABELS["verification_url"], payload.get("verification_url", "")),
    ]
    for label, value in rows:
        write(f"{label}: {value}", size=10)
    y -= 8

    sig = payload["signature"]
    write(f"Signature algorithm: {sig['algorithm']}", size=9)
    write(f"Signature key_id: {sig['key_id']}", size=9)
    write(f"Signature value: {sig['value'][:64]}{'...' if len(sig['value']) > 64 else ''}", size=9)
    y -= 8

    for line in _wrap(copy.CERT_FOOTER, width=92):
        write(line, size=9, font="Helvetica-Oblique")

    c.showPage()
    c.save()
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Route handling.
# ---------------------------------------------------------------------------

def handle_get(service: Any, path_parts: list[str], accept_header: str = "") -> tuple[int, bytes, str] | None:
    """Return (status, body, ctype) or None if path is not a verify-api-v0.x path."""
    if not path_parts or path_parts[0] != "verify":
        return None

    # /verify/{envelope_id}.json
    if len(path_parts) == 2 and path_parts[1].endswith(".json"):
        envelope_id = path_parts[1][:-5]
        payload = service.get_verify_envelope(envelope_id)
        return 200, json.dumps(payload, indent=2, sort_keys=True).encode("utf-8"), "application/json"

    # /verify/{envelope_id}/certificate.pdf
    if len(path_parts) == 3 and path_parts[2] == "certificate.pdf":
        pdf = service.render_verify_certificate_pdf(path_parts[1])
        return 200, pdf, "application/pdf"

    # /verify/{envelope_id}  (HTML default; JSON via Accept header)
    if len(path_parts) == 2:
        envelope_id = path_parts[1]
        if "application/json" in accept_header.lower() and "text/html" not in accept_header.lower():
            payload = service.get_verify_envelope(envelope_id)
            return 200, json.dumps(payload, indent=2, sort_keys=True).encode("utf-8"), "application/json"
        body = service.render_verify_page(envelope_id)
        return 200, body.encode("utf-8"), "text/html; charset=utf-8"

    return None


# ---------------------------------------------------------------------------
# Attach to PortalService.
# ---------------------------------------------------------------------------

def attach(PortalServiceCls: type) -> None:
    fns = [
        _ensure_verify_schema,
        _today_iso,
        _public_origin,
        _envelope_body,
        _sign_envelope,
        mint_verify_envelope,
        revoke_verify_envelope,
        get_verify_envelope,
        render_verify_page,
        render_verify_certificate_pdf,
    ]
    # Use namespaced method names where the bare name might collide.
    PortalServiceCls._ensure_verify_schema = _ensure_verify_schema
    PortalServiceCls._migrate_verify_schema_to_v0_2 = _migrate_verify_schema_to_v0_2
    PortalServiceCls._downgrade_verify_schema_to_v0_1 = _downgrade_verify_schema_to_v0_1
    PortalServiceCls._verify_today_iso = _today_iso
    PortalServiceCls._verify_public_origin = _public_origin
    PortalServiceCls._verify_envelope_body = _envelope_body
    PortalServiceCls._sign_verify_envelope = _sign_envelope
    PortalServiceCls.mint_verify_envelope = mint_verify_envelope
    PortalServiceCls.revoke_verify_envelope = revoke_verify_envelope
    PortalServiceCls.get_verify_envelope = get_verify_envelope
    PortalServiceCls.render_verify_page = render_verify_page
    PortalServiceCls.render_verify_certificate_pdf = render_verify_certificate_pdf

    # Ensure the schema exists on every PortalService instance at construction.
    original_init = PortalServiceCls.__init__

    def _patched_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        self._ensure_verify_schema()

    PortalServiceCls.__init__ = _patched_init
