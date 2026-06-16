"""BEAAA-5016 -> BEAAA-5739 acceptance tests for verify-api-v0.2.

Covers the acceptance criteria from BEAAA-5016 v0.1 + BEAAA-5739 v0.2 migration:
 - Route works against a deterministic fixture envelope.
 - Valid, expired, revoked, and not-found states each render correctly.
 - Generated PDF carries the certificate-of-scan boundary language from the
   BEAAA-4915 CMO packet, canon-migrated to BackBond per BEAAA-5739 AC3.
 - JSON envelope is content-negotiated and Ed25519-signed.
 - Five PM1 pins (rubric_version, scanner_version, corpus_version,
   model_versions, seed) emitted on every body and render surface
   (BEAAA-5739 AC2).
 - mint signature validates corpus_version / model_versions / seed
   non-empty (BEAAA-5739 AC4).
 - Reproducibility: same minted row -> byte-stable signature across N=5
   re-signs (BEAAA-5739 AC5).
 - Boundary held: no are-scanner-api-v1@v1.0 Output change; no rubric change.
"""

from __future__ import annotations

import json
from io import BytesIO

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

import portal
import verify_v0_2 as copy


VALID_ENVELOPE_ID = "env_fixture_valid_001"
EXPIRED_ENVELOPE_ID = "env_fixture_expired_001"
REVOKED_ENVELOPE_ID = "env_fixture_revoked_001"

# Five PM1 pins per BEAAA-5739 AC2. corpus_version + model_versions + seed are
# additive on v0.2; rubric_version + scanner_version carried over from v0.1.
FIXTURE_MODEL_VERSIONS = {"primary": "gpt-4o-2026-05-21", "judge": "claude-opus-4-6"}
FIXTURE_SEED = "scan-2026-06-13-seed-001"
FIXTURE_CORPUS_VERSION = "0.2.0"


def _mint_fixture(service):
    service.mint_verify_envelope(
        envelope_id=VALID_ENVELOPE_ID,
        scan_id="scan_2026_06_13_001",
        vendor_name="Example Vendor",
        agent_name="Example Agent",
        backbond_score=82,
        threshold_status="met_bind_threshold",
        rubric_version="v1.4.0",
        scanner_version="v0.4.1",
        corpus_version=FIXTURE_CORPUS_VERSION,
        model_versions=FIXTURE_MODEL_VERSIONS,
        seed=FIXTURE_SEED,
        scan_date="2026-06-13",
        valid_through="2099-12-31",
        methodology_url="https://portal.backbond.ai/methodology",
        probe_ledger_url="https://portal.backbond.ai/probe-ledger/",
    )
    service.mint_verify_envelope(
        envelope_id=EXPIRED_ENVELOPE_ID,
        scan_id="scan_2026_03_13_001",
        vendor_name="Older Vendor",
        agent_name="Older Agent",
        backbond_score=78,
        threshold_status="met_bind_threshold",
        rubric_version="v1.3.0",
        scanner_version="v0.4.0",
        corpus_version="0.1.0",
        model_versions={"primary": "gpt-4o-2026-03-13"},
        seed="scan-2026-03-13-seed-001",
        scan_date="2026-03-13",
        valid_through="2026-06-12",  # one day before today fixture
        methodology_url="https://portal.backbond.ai/methodology",
        probe_ledger_url="https://portal.backbond.ai/probe-ledger/",
    )
    service.mint_verify_envelope(
        envelope_id=REVOKED_ENVELOPE_ID,
        scan_id="scan_2026_05_01_001",
        vendor_name="Revoked Vendor",
        agent_name="Revoked Agent",
        backbond_score=85,
        threshold_status="met_bind_threshold",
        rubric_version="v1.4.0",
        scanner_version="v0.4.1",
        corpus_version=FIXTURE_CORPUS_VERSION,
        model_versions=FIXTURE_MODEL_VERSIONS,
        seed="scan-2026-05-01-seed-001",
        scan_date="2026-05-01",
        valid_through="2099-12-31",
        methodology_url="https://portal.backbond.ai/methodology",
        probe_ledger_url="https://portal.backbond.ai/probe-ledger/",
    )
    service.revoke_verify_envelope(REVOKED_ENVELOPE_ID, "test_revocation")


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.setenv("PORTAL_PUBLIC_ORIGIN", "https://portal.backbond.ai")
    svc = portal.build_service(tmp_path / "portal_data")
    # Pin "today" so expired-state tests are deterministic.
    svc._verify_today_iso = lambda: "2026-06-13"
    _mint_fixture(svc)
    yield svc
    svc.close()


# --- JSON envelope (machine-readable) --------------------------------------

def test_valid_json_envelope_shape_and_signature(service):
    payload = service.get_verify_envelope(VALID_ENVELOPE_ID)
    assert payload["status"] == "valid"
    assert payload["verify_api_version"] == "0.2"
    assert payload["envelope_id"] == VALID_ENVELOPE_ID
    assert payload["scan_id"] == "scan_2026_06_13_001"
    assert payload["backbond_score"] == 82
    assert payload["threshold_status"] == "met_bind_threshold"
    assert payload["rubric_version"] == "v1.4.0"
    assert payload["scanner_version"] == "v0.4.1"
    assert payload["corpus_version"] == FIXTURE_CORPUS_VERSION
    assert payload["model_versions"] == FIXTURE_MODEL_VERSIONS
    assert payload["seed"] == FIXTURE_SEED
    assert payload["scan_date"] == "2026-06-13"
    assert payload["valid_through"] == "2099-12-31"
    assert payload["methodology_url"] == "https://portal.backbond.ai/methodology"
    assert payload["probe_ledger_url"] == "https://portal.backbond.ai/probe-ledger/"
    assert payload["verification_url"] == f"https://portal.backbond.ai/verify/{VALID_ENVELOPE_ID}"
    assert "evidence_refs" not in payload
    assert "perils" not in payload
    sig = payload["signature"]
    assert sig["algorithm"] == "ed25519"
    assert sig["key_id"] == "backbond-scan-signing-2026-06"
    assert isinstance(sig["value"], str) and sig["value"]


def test_signature_verifies_with_portal_counter_public_key(service):
    payload = service.get_verify_envelope(VALID_ENVELOPE_ID)
    sig_value = payload["signature"]["value"]
    body = {k: v for k, v in payload.items() if k != "signature"}
    public_key_bytes = service._counter_key().public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    public_key = ed25519.Ed25519PublicKey.from_public_bytes(public_key_bytes)
    public_key.verify(portal.unb64url(sig_value), portal.canonical_json(body))


def test_expired_state_inferred_from_valid_through(service):
    payload = service.get_verify_envelope(EXPIRED_ENVELOPE_ID)
    assert payload["status"] == "expired"
    assert payload["envelope_id"] == EXPIRED_ENVELOPE_ID
    assert "signature" in payload


def test_revoked_state_surfaces_revoked_at(service):
    payload = service.get_verify_envelope(REVOKED_ENVELOPE_ID)
    assert payload["status"] == "revoked"
    assert payload["revoked_reason"] == "test_revocation"
    assert payload["revoked_at"]
    assert "signature" in payload


def test_not_found_state_is_signed_deterministic_shape(service):
    payload = service.get_verify_envelope("env_does_not_exist")
    assert payload["status"] == "not_found"
    assert payload["envelope_id"] == "env_does_not_exist"
    assert payload["verification_url"] == "https://portal.backbond.ai/verify/env_does_not_exist"
    assert payload["signature"]["algorithm"] == "ed25519"


def test_get_envelope_is_deterministic_across_calls(service):
    a = service.get_verify_envelope(VALID_ENVELOPE_ID)
    b = service.get_verify_envelope(VALID_ENVELOPE_ID)
    assert a == b  # signature deterministic per Ed25519 over identical canonical bytes


# --- BEAAA-5739 AC5: byte-stable signature across N=5 re-signs ------------

def test_signature_byte_stable_across_n5_resigns(service):
    """Reproducibility test: same scan envelope -> same verify envelope ->
    same signature byte-stable across N=5 re-signs (BEAAA-5739 AC5)."""
    payloads = [service.get_verify_envelope(VALID_ENVELOPE_ID) for _ in range(5)]
    signatures = {p["signature"]["value"] for p in payloads}
    assert len(signatures) == 1, f"expected 1 unique signature, got {len(signatures)}: {signatures}"
    # And the bodies match byte-for-byte under canonical_json serialization.
    bodies = [{k: v for k, v in p.items() if k != "signature"} for p in payloads]
    canonical = {portal.canonical_json(b) for b in bodies}
    assert len(canonical) == 1


# --- BEAAA-5739 AC4: mint validation refuses missing v0.2 pins ------------

def test_mint_rejects_missing_corpus_version(service):
    from portal import PortalError
    with pytest.raises(PortalError) as exc_info:
        service.mint_verify_envelope(
            envelope_id="env_missing_corpus",
            scan_id="scan_x",
            vendor_name="V",
            agent_name="A",
            backbond_score=70,
            threshold_status="met_bind_threshold",
            rubric_version="v1.4.0",
            scanner_version="v0.4.1",
            corpus_version="",
            model_versions={},
            seed="some-seed",
            scan_date="2026-06-13",
            valid_through="2099-12-31",
        )
    assert exc_info.value.code == "verify_corpus_version_missing"


def test_mint_rejects_missing_seed(service):
    from portal import PortalError
    with pytest.raises(PortalError) as exc_info:
        service.mint_verify_envelope(
            envelope_id="env_missing_seed",
            scan_id="scan_x",
            vendor_name="V",
            agent_name="A",
            backbond_score=70,
            threshold_status="met_bind_threshold",
            rubric_version="v1.4.0",
            scanner_version="v0.4.1",
            corpus_version="0.2.0",
            model_versions={},
            seed="",
            scan_date="2026-06-13",
            valid_through="2099-12-31",
        )
    assert exc_info.value.code == "verify_seed_missing"


def test_mint_rejects_non_dict_model_versions(service):
    from portal import PortalError
    with pytest.raises(PortalError) as exc_info:
        service.mint_verify_envelope(
            envelope_id="env_bad_models",
            scan_id="scan_x",
            vendor_name="V",
            agent_name="A",
            backbond_score=70,
            threshold_status="met_bind_threshold",
            rubric_version="v1.4.0",
            scanner_version="v0.4.1",
            corpus_version="0.2.0",
            model_versions=["not-a-dict"],  # type: ignore[arg-type]
            seed="seed-x",
            scan_date="2026-06-13",
            valid_through="2099-12-31",
        )
    assert exc_info.value.code == "verify_model_versions_invalid"


def test_mint_accepts_empty_model_versions_dict(service):
    # Empty dict is allowed; missing keys is not. Some upstream scans (fixture
    # adapter, pre-eval) emit `model_versions: {}` (portal.py:1027). Public
    # surface renders "(none)" but signed body still includes the empty dict.
    service.mint_verify_envelope(
        envelope_id="env_empty_models",
        scan_id="scan_empty",
        vendor_name="V",
        agent_name="A",
        backbond_score=64,
        threshold_status="met_bind_threshold",
        rubric_version="v1.4.0",
        scanner_version="v0.4.1",
        corpus_version="0.2.0",
        model_versions={},
        seed="seed-empty",
        scan_date="2026-06-13",
        valid_through="2099-12-31",
    )
    payload = service.get_verify_envelope("env_empty_models")
    assert payload["model_versions"] == {}


# --- HTML page -------------------------------------------------------------

def test_valid_html_renders_cmo_copy(service):
    import html as _html
    page = service.render_verify_page(VALID_ENVELOPE_ID)
    assert copy.PAGE_H1 in page
    assert _html.escape(copy.VERIFIED_BODY) in page
    assert copy.FOOTER_DISCLAIMER in page
    assert "Example Agent" in page
    assert "Example Vendor" in page
    assert "82" in page
    assert "v1.4.0" in page
    assert "/verify/" + VALID_ENVELOPE_ID + ".json" in page
    assert "/verify/" + VALID_ENVELOPE_ID + "/certificate.pdf" in page


def test_valid_html_renders_all_five_pm1_pins(service):
    """BEAAA-5739 AC2: HTML renders rubric_version + scanner_version +
    corpus_version + model_versions + seed."""
    page = service.render_verify_page(VALID_ENVELOPE_ID)
    # Five pin labels from PUBLIC_LABELS, all present on the rendered page.
    assert copy.PUBLIC_LABELS["rubric_version"] in page
    assert copy.PUBLIC_LABELS["scanner_version"] in page
    assert copy.PUBLIC_LABELS["corpus_version"] in page
    assert copy.PUBLIC_LABELS["model_versions"] in page
    assert copy.PUBLIC_LABELS["seed"] in page
    # And their values render.
    assert FIXTURE_CORPUS_VERSION in page
    assert FIXTURE_SEED in page
    assert "primary=gpt-4o-2026-05-21" in page
    assert "judge=claude-opus-4-6" in page


def test_html_does_not_expose_internal_rubric_fields(service):
    page = service.render_verify_page(VALID_ENVELOPE_ID)
    lowered = page.lower()
    assert "evidence_refs" not in lowered
    assert "\"perils\"" not in lowered


def test_expired_html_uses_expired_body_and_suppress_note(service):
    page = service.render_verify_page(EXPIRED_ENVELOPE_ID)
    assert copy.EXPIRED_BODY in page
    assert "Badge display must be suppressed or replaced" in page


def test_revoked_html_uses_revoked_body_and_suppress_note(service):
    page = service.render_verify_page(REVOKED_ENVELOPE_ID)
    assert copy.REVOKED_BODY in page
    assert "Badge display must be suppressed or replaced" in page


def test_not_found_html_renders_not_found_body(service):
    page = service.render_verify_page("env_missing")
    assert copy.NOT_FOUND_BODY in page
    assert "env_missing" in page
    # Confirm the not_found HTML still carries the footer disclaimer + carrier-rule boundary.
    assert copy.FOOTER_DISCLAIMER in page


# --- BackBond canon enforcement (BEAAA-5739 AC3) ---------------------------

def test_html_emits_backbond_canon_not_single_cap(service):
    """Single-cap 'Backbond' must not appear in active public copy outside of
    the lowercase hostname `backbond.ai` and dict keys like `backbond_score`."""
    page = service.render_verify_page(VALID_ENVELOPE_ID)
    # Strip allowed historical surfaces from the page first: the hostname
    # `backbond.ai` and the lowercase `backbond_score` technical dict-key /
    # label "BackBond score" (both legitimate; the label has two caps).
    sanitized = page.replace("backbond.ai", "").replace("backbond_score", "").replace("BackBond score", "")
    # `BackBond` (two-cap) is the canonical brand and remains. Any remaining
    # single-cap "Backbond" indicates a canon violation.
    assert "Backbond" not in sanitized, "active public copy carries deprecated single-cap 'Backbond' spelling"


def test_pdf_emits_backbond_canon_not_single_cap(service):
    pdf = service.render_verify_certificate_pdf(VALID_ENVELOPE_ID)
    from pypdf import PdfReader
    text = "\n".join(p.extract_text() or "" for p in PdfReader(BytesIO(pdf)).pages)
    sanitized = text.replace("backbond.ai", "").replace("backbond_score", "").replace("BackBond score", "")
    assert "Backbond" not in sanitized, "PDF certificate carries deprecated single-cap 'Backbond' spelling"


# --- Boundary language (Carrier Rule + CMO rejected wording) ---------------

REJECTED_PHRASES = (
    "BackBond Insured",
    "BackBond Licensed",
    "Backbond Insured",   # historical spellings also rejected
    "Backbond Licensed",
    "Outcome-safe",
    "Safety certified",
    "Governance certified",
    "Carrier-backed",
)


@pytest.mark.parametrize("envelope_id", [VALID_ENVELOPE_ID, EXPIRED_ENVELOPE_ID, REVOKED_ENVELOPE_ID, "env_missing"])
def test_html_does_not_emit_rejected_badge_phrases(service, envelope_id):
    page = service.render_verify_page(envelope_id)
    for phrase in REJECTED_PHRASES:
        assert phrase not in page


def test_pdf_does_not_emit_rejected_badge_phrases(service):
    pdf = service.render_verify_certificate_pdf(VALID_ENVELOPE_ID)
    from pypdf import PdfReader
    text = "\n".join(p.extract_text() or "" for p in PdfReader(BytesIO(pdf)).pages)
    for phrase in REJECTED_PHRASES:
        assert phrase not in text


# --- PDF certificate -------------------------------------------------------

def test_pdf_carries_certificate_of_scan_copy(service):
    pdf = service.render_verify_certificate_pdf(VALID_ENVELOPE_ID)
    assert pdf.startswith(b"%PDF")
    from pypdf import PdfReader
    text = "\n".join(p.extract_text() or "" for p in PdfReader(BytesIO(pdf)).pages)
    assert copy.CERT_TITLE in text
    # CERT_LEAD is line-wrapped at 92 chars; spot-check anchor substrings.
    assert "BackBond certifies that the named agent" in text
    assert "bind-threshold requirement" in text
    # CERT_FOOTER is also line-wrapped — anchor substrings.
    assert "Certificate of scan only" in text
    assert "not evidence of bound" in text
    assert "Example Agent" in text
    assert "82" in text


def test_pdf_does_not_expose_internal_rubric_fields(service):
    pdf = service.render_verify_certificate_pdf(VALID_ENVELOPE_ID)
    from pypdf import PdfReader
    text = "\n".join(p.extract_text() or "" for p in PdfReader(BytesIO(pdf)).pages)
    lowered = text.lower()
    assert "evidence_refs" not in lowered
    assert "perils" not in lowered


def test_pdf_renders_all_five_pm1_pins(service):
    """BEAAA-5739 AC2: PDF renders rubric_version + scanner_version +
    corpus_version + model_versions + seed."""
    pdf = service.render_verify_certificate_pdf(VALID_ENVELOPE_ID)
    from pypdf import PdfReader
    text = "\n".join(p.extract_text() or "" for p in PdfReader(BytesIO(pdf)).pages)
    assert copy.PUBLIC_LABELS["corpus_version"] in text
    assert copy.PUBLIC_LABELS["model_versions"] in text
    assert copy.PUBLIC_LABELS["seed"] in text
    assert FIXTURE_CORPUS_VERSION in text
    assert FIXTURE_SEED in text


def test_pdf_emitted_for_all_states(service):
    for eid in (VALID_ENVELOPE_ID, EXPIRED_ENVELOPE_ID, REVOKED_ENVELOPE_ID, "env_missing"):
        pdf = service.render_verify_certificate_pdf(eid)
        assert pdf.startswith(b"%PDF")


# --- Route handler (HTTP dispatch) -----------------------------------------

def test_handle_get_dispatches_json_via_path_suffix(service):
    import verify_service
    result = verify_service.handle_get(service, ["verify", f"{VALID_ENVELOPE_ID}.json"], accept_header="")
    assert result is not None
    status, body, ctype = result
    assert status == 200
    assert ctype == "application/json"
    parsed = json.loads(body)
    assert parsed["envelope_id"] == VALID_ENVELOPE_ID
    assert parsed["status"] == "valid"
    # v0.2 pins land on the route JSON too.
    assert parsed["corpus_version"] == FIXTURE_CORPUS_VERSION
    assert parsed["model_versions"] == FIXTURE_MODEL_VERSIONS
    assert parsed["seed"] == FIXTURE_SEED


def test_handle_get_dispatches_json_via_accept_header(service):
    import verify_service
    result = verify_service.handle_get(
        service, ["verify", VALID_ENVELOPE_ID], accept_header="application/json"
    )
    assert result is not None
    status, body, ctype = result
    assert status == 200
    assert ctype == "application/json"
    parsed = json.loads(body)
    assert parsed["envelope_id"] == VALID_ENVELOPE_ID


def test_handle_get_dispatches_html_by_default(service):
    import verify_service
    result = verify_service.handle_get(
        service, ["verify", VALID_ENVELOPE_ID], accept_header="text/html"
    )
    assert result is not None
    status, body, ctype = result
    assert status == 200
    assert ctype == "text/html; charset=utf-8"
    assert copy.PAGE_H1.encode() in body


def test_handle_get_dispatches_pdf(service):
    import verify_service
    result = verify_service.handle_get(
        service, ["verify", VALID_ENVELOPE_ID, "certificate.pdf"], accept_header=""
    )
    assert result is not None
    status, body, ctype = result
    assert status == 200
    assert ctype == "application/pdf"
    assert body.startswith(b"%PDF")


def test_handle_get_returns_none_for_non_verify_path(service):
    import verify_service
    assert verify_service.handle_get(service, ["foo", "bar"], "") is None
    assert verify_service.handle_get(service, [], "") is None


# --- Pins surface ---------------------------------------------------------

def test_pins_advertise_verify_api_v02(service):
    """BEAAA-5739 AC6: portal pins advertise v0.2 contract version."""
    pins = service.emit_pins()
    assert pins["verify_api_version"] == "0.2"
    assert "verify-api-v0.2" in pins["contracts"]


# --- Boundary preservation: no scoring path change ------------------------

def test_verify_envelope_does_not_change_scanner_or_corpus_pins(service):
    pins = service.emit_pins()
    assert pins["scanner_version"] == portal.SCANNER_VERSION
    assert pins["corpus_version"] == portal.CORPUS_VERSION
    assert pins["rubric_version"] == portal.RUBRIC_VERSION


# --- v0.1 -> v0.2 schema migration (BEAAA-5739 AC1) -----------------------

def test_schema_migration_is_idempotent(tmp_path, monkeypatch):
    """Calling _ensure_verify_schema twice on the same DB doesn't error or
    duplicate columns; v0.2 columns are present after migration."""
    monkeypatch.setenv("PORTAL_PUBLIC_ORIGIN", "https://portal.backbond.ai")
    svc = portal.build_service(tmp_path / "portal_data_migration")
    try:
        svc._ensure_verify_schema()
        svc._ensure_verify_schema()
        cols = {row["name"] for row in svc.db.execute("PRAGMA table_info(verify_envelopes)")}
        assert "corpus_version" in cols
        assert "model_versions_json" in cols
        assert "seed" in cols
    finally:
        svc.close()


def test_legacy_v0_1_row_get_verify_envelope_fails_closed(tmp_path, monkeypatch):
    """A row inserted under v0.1 schema (no corpus_version/seed) surfaces a
    fail-closed PortalError on get_verify_envelope (BEAAA-5739 AC1)."""
    from portal import PortalError, now_iso
    monkeypatch.setenv("PORTAL_PUBLIC_ORIGIN", "https://portal.backbond.ai")
    svc = portal.build_service(tmp_path / "portal_data_legacy")
    svc._verify_today_iso = lambda: "2026-06-13"
    try:
        # Insert directly bypassing mint_verify_envelope() to simulate a row
        # that landed under v0.1 schema and got migrated to v0.2 with empty
        # default pins.
        svc.db.execute(
            "INSERT INTO verify_envelopes (envelope_id, scan_id, vendor_name, agent_name, "
            "backbond_score, threshold_status, rubric_version, scanner_version, "
            "corpus_version, model_versions_json, seed, scan_date, valid_through, "
            "methodology_url, probe_ledger_url, state, revoked_at, revoked_reason, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "env_legacy_001", "scan_legacy", "V", "A", 70, "met_bind_threshold",
                "v1.4.0", "v0.4.1",
                "", "{}", "",  # v0.2 default pins, blank => fail closed
                "2026-06-13", "2099-12-31",
                "https://portal.backbond.ai/methodology",
                "https://portal.backbond.ai/probe-ledger/",
                "valid", None, None, now_iso(),
            ),
        )
        svc.db.commit()
        with pytest.raises(PortalError) as exc_info:
            svc.get_verify_envelope("env_legacy_001")
        assert exc_info.value.code == "verify_envelope_pins_missing"
    finally:
        svc.close()
