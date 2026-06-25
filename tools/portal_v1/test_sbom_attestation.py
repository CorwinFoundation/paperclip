"""BEAAA-4926 acceptance tests: SBOM intake + KEV/NVD bind cross-check.

Tests the GOVERNANCE.SUPPLY_CHAIN.CVE_ATTESTATION evidence dimension authored by
BEAAA-4910 (HoUW ADR v0.1). Every test is deterministic: no live network feeds,
fixed timestamps, fixed SBOM bytes.
"""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta

import pytest

from portal import (
    CVE_ATTESTATION_RUBRIC_POINT_ID,
    CVE_ATTESTATION_TEST_ID,
    CVE_ATTESTATION_WEIGHT,
    PortalError,
    build_service,
    sha256_hex,
)
from test_portal import enroll_ready_dp, submit_all_tasks


CYCLONEDX_BODY = json.dumps(
    {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "version": 1,
        "metadata": {"timestamp": "2026-06-11T00:00:00Z", "tools": [{"name": "syft", "version": "1.18.0"}]},
        "components": [
            {"type": "library", "name": "requests", "version": "2.32.3", "purl": "pkg:pypi/requests@2.32.3"},
            {"type": "library", "name": "urllib3", "version": "2.2.2", "purl": "pkg:pypi/urllib3@2.2.2"},
            {"type": "library", "name": "certifi", "version": "2024.7.4", "purl": "pkg:pypi/certifi@2024.7.4"},
        ],
    },
    sort_keys=True,
).encode("utf-8")

SPDX_BODY = json.dumps(
    {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": "acme-agent-runtime",
        "documentNamespace": "https://example.invalid/sbom/acme-agent-runtime",
        "creationInfo": {"created": "2026-06-11T00:00:00Z", "creators": ["Tool: spdx-tools-1.0"]},
        "packages": [
            {"SPDXID": "SPDXRef-Package-requests", "name": "requests", "versionInfo": "2.32.3", "downloadLocation": "NOASSERTION"},
            {"SPDXID": "SPDXRef-Package-urllib3", "name": "urllib3", "versionInfo": "2.2.2", "downloadLocation": "NOASSERTION"},
        ],
    },
    sort_keys=True,
).encode("utf-8")


PACKAGE_LOCK_BODY = b'{"name":"acme-agent-runtime","version":"1.0.0","lockfileVersion":3,"requires":true}'


def _fresh_ts(hours_ago: float = 1.0) -> str:
    when = datetime.now(UTC) - timedelta(hours=hours_ago)
    return when.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _stale_ts(hours_ago: float = 80.0) -> str:
    return _fresh_ts(hours_ago)


def _base_attestation(
    sbom_body: bytes,
    sbom_format: str,
    *,
    cve_executed_at: str | None = None,
    kev_retrieved_at: str | None = None,
    nvd_retrieved_at: str | None = None,
    kev_hits: list[dict] | None = None,
    nvd_hits: list[dict] | None = None,
) -> dict:
    cve_executed_at = cve_executed_at or _fresh_ts(1.0)
    kev_retrieved_at = kev_retrieved_at or _fresh_ts(2.0)
    nvd_retrieved_at = nvd_retrieved_at or _fresh_ts(2.0)
    return {
        "sbom": {
            "format": sbom_format,
            "body_base64": base64.b64encode(sbom_body).decode("ascii"),
            "generated_at": "2026-06-11T00:00:00Z",
            "generator": "syft-1.18.0",
        },
        "package_lock_evidence": {
            "source": "npm_lock",
            "body_base64": base64.b64encode(PACKAGE_LOCK_BODY).decode("ascii"),
        },
        "cve_cross_check": {
            "executed_at": cve_executed_at,
            "feeds": {
                "kev": {
                    "source": "CISA KEV",
                    "feed_version_or_retrieved_at": kev_retrieved_at,
                    "hits": kev_hits or [],
                },
                "nvd": {
                    "source": "NVD",
                    "feed_version_or_retrieved_at": nvd_retrieved_at,
                    "hits": nvd_hits or [],
                },
            },
            "tool_output_uri": "file:///tmp/sca-output.json",
            "tool_output_sha256": sha256_hex(b"deterministic-tool-output"),
        },
        "attestation_statement": "Acme attests SBOM and CVE cross-check are accurate as of bind submission.",
    }


@pytest.fixture
def service(tmp_path):
    svc = build_service(tmp_path)
    yield svc
    svc.close()


def _ready_scan(service):
    signing_key, dp, enrollment = enroll_ready_dp(service)
    scan = service.trigger_scan(dp["dp_id"])
    return signing_key, dp, enrollment, scan


# ---- Acceptance test 1: CycloneDX JSON accepted and hashed --------------------------------------


def test_cyclonedx_json_accepted_and_hashed(service):
    _, _, _, scan = _ready_scan(service)
    payload = _base_attestation(CYCLONEDX_BODY, "cyclonedx_json")
    result = service.submit_sbom_attestation(scan["scan_id"], payload)
    assert result["received"] is True
    assert result["sbom_sha256"] == sha256_hex(CYCLONEDX_BODY)
    assert result["package_lock_sha256"] == sha256_hex(PACKAGE_LOCK_BODY)
    stored = service.get_sbom_attestation(scan["scan_id"])
    assert stored["sbom"]["format"] == "cyclonedx_json"
    assert stored["sbom"]["sha256"] == sha256_hex(CYCLONEDX_BODY)
    assert stored["sbom"]["component_count"] == 3
    audit_events = [row["event_type"] for row in service.db.execute("SELECT event_type FROM audit_log ORDER BY id")]
    assert "sbom_attestation_ingested" in audit_events
    assert service.verify_audit_chain() is True


# ---- Acceptance test 2: SPDX JSON accepted and hashed -------------------------------------------


def test_spdx_json_accepted_and_hashed(service):
    _, _, _, scan = _ready_scan(service)
    payload = _base_attestation(SPDX_BODY, "spdx_json")
    result = service.submit_sbom_attestation(scan["scan_id"], payload)
    assert result["sbom_sha256"] == sha256_hex(SPDX_BODY)
    stored = service.get_sbom_attestation(scan["scan_id"])
    assert stored["sbom"]["format"] == "spdx_json"
    assert stored["sbom"]["component_count"] == 2


# ---- Acceptance test 3: stale CVE feed (>72h) fails closed --------------------------------------


def test_stale_cve_feed_fails_closed(service):
    _, _, _, scan = _ready_scan(service)
    payload = _base_attestation(
        CYCLONEDX_BODY,
        "cyclonedx_json",
        cve_executed_at=_stale_ts(80.0),
        kev_retrieved_at=_stale_ts(80.0),
        nvd_retrieved_at=_stale_ts(80.0),
    )
    service.submit_sbom_attestation(scan["scan_id"], payload)
    decision = service.evaluate_bind(scan["scan_id"])
    assert decision["bind_status"] == "blocked"
    codes = {b["code"] for b in decision["blockers"]}
    assert "cve_cross_check_stale" in codes
    assert "kev_feed_stale" in codes
    assert "nvd_feed_stale" in codes
    assert decision["evidence_ref"]["verdict"] == "fail"
    assert decision["evidence_ref"]["weight"] == CVE_ATTESTATION_WEIGHT


# ---- Acceptance test 4: unresolved CISA KEV hit fails closed ------------------------------------


def test_unresolved_kev_hit_fails_closed(service):
    _, _, _, scan = _ready_scan(service)
    payload = _base_attestation(
        CYCLONEDX_BODY,
        "cyclonedx_json",
        kev_hits=[
            {
                "cve": "CVE-2024-0001",
                "package": "requests",
                "version": "2.32.3",
                "status": "unresolved",
                "evidence_uri": None,
            }
        ],
    )
    service.submit_sbom_attestation(scan["scan_id"], payload)
    decision = service.evaluate_bind(scan["scan_id"])
    assert decision["bind_status"] == "blocked"
    blockers = [b for b in decision["blockers"] if b["code"] == "kev_unresolved"]
    assert len(blockers) == 1
    assert blockers[0]["cve"] == "CVE-2024-0001"
    assert decision["evidence_ref"]["metadata"]["kev_unresolved_count"] == 1


# ---- Acceptance test 5: unresolved NVD critical hit fails closed --------------------------------


def test_unresolved_nvd_critical_hit_fails_closed(service):
    _, _, _, scan = _ready_scan(service)
    payload = _base_attestation(
        CYCLONEDX_BODY,
        "cyclonedx_json",
        nvd_hits=[
            {
                "cve": "CVE-2024-2222",
                "package": "urllib3",
                "version": "2.2.2",
                "cvss_v3": 9.6,
                "status": "unresolved",
                "evidence_uri": None,
            },
            {
                "cve": "CVE-2024-3333",
                "package": "urllib3",
                "version": "2.2.2",
                "cvss_v3": 8.1,
                "status": "unresolved",
                "evidence_uri": None,
            },
        ],
    )
    service.submit_sbom_attestation(scan["scan_id"], payload)
    decision = service.evaluate_bind(scan["scan_id"])
    assert decision["bind_status"] == "blocked"
    crit = [b for b in decision["blockers"] if b["code"] == "nvd_critical_unresolved"]
    assert len(crit) == 1
    assert crit[0]["cve"] == "CVE-2024-2222"
    assert decision["evidence_ref"]["metadata"]["nvd_critical_unresolved_count"] == 1
    assert decision["evidence_ref"]["metadata"]["nvd_high_open_count"] == 1


# ---- Acceptance test 6: patched/mitigated KEV/NVD finding passes with evidence URI --------------


def test_patched_or_mitigated_finding_passes_with_evidence_uri(service):
    signing_key, dp, enrollment, scan = _ready_scan(service)
    payload = _base_attestation(
        CYCLONEDX_BODY,
        "cyclonedx_json",
        kev_hits=[
            {
                "cve": "CVE-2024-0001",
                "package": "requests",
                "version": "2.32.3",
                "status": "patched",
                "evidence_uri": "https://example.invalid/patches/CVE-2024-0001",
            }
        ],
        nvd_hits=[
            {
                "cve": "CVE-2024-2222",
                "package": "urllib3",
                "version": "2.2.2",
                "cvss_v3": 9.6,
                "status": "mitigated",
                "evidence_uri": "https://example.invalid/mitigations/CVE-2024-2222",
            }
        ],
    )
    service.submit_sbom_attestation(scan["scan_id"], payload)
    decision = service.evaluate_bind(scan["scan_id"])
    assert decision["bind_status"] == "ok"
    assert decision["blockers"] == []
    assert decision["evidence_ref"]["verdict"] == "pass"
    submit_all_tasks(service, signing_key, scan, enrollment["connector_id"])
    row = service.db.execute("SELECT * FROM scans WHERE id = ?", (scan["scan_id"],)).fetchone()
    score = json.loads(row["score_json"])
    cve_refs = [r for r in score["evidence_refs"] if r["rubric_point_id"] == CVE_ATTESTATION_RUBRIC_POINT_ID]
    assert len(cve_refs) == 1
    assert cve_refs[0]["test_id"] == CVE_ATTESTATION_TEST_ID
    assert cve_refs[0]["weight"] == CVE_ATTESTATION_WEIGHT
    assert cve_refs[0]["metadata"]["sbom_sha256"] == sha256_hex(CYCLONEDX_BODY)


# ---- Additional guardrails (non-acceptance but contract-relevant) -------------------------------


def test_bind_without_attestation_is_blocked(service):
    _, _, _, scan = _ready_scan(service)
    decision = service.evaluate_bind(scan["scan_id"])
    assert decision["bind_status"] == "blocked"
    codes = {b["code"] for b in decision["blockers"]}
    assert "sbom_attestation_missing" in codes
    assert decision["evidence_ref"] is None


def test_sbom_format_unsupported_rejected(service):
    _, _, _, scan = _ready_scan(service)
    payload = _base_attestation(CYCLONEDX_BODY, "cyclonedx_json")
    payload["sbom"]["format"] = "syft_table_text"
    with pytest.raises(PortalError) as err:
        service.submit_sbom_attestation(scan["scan_id"], payload)
    assert err.value.code == "sbom_format_unsupported"


def test_kev_hit_with_status_patched_requires_evidence_uri(service):
    _, _, _, scan = _ready_scan(service)
    payload = _base_attestation(
        CYCLONEDX_BODY,
        "cyclonedx_json",
        kev_hits=[
            {
                "cve": "CVE-2024-0001",
                "package": "requests",
                "version": "2.32.3",
                "status": "patched",
                "evidence_uri": None,
            }
        ],
    )
    with pytest.raises(PortalError) as err:
        service.submit_sbom_attestation(scan["scan_id"], payload)
    assert err.value.code == "cve_hit_evidence_required"


def test_submission_is_idempotent_on_scan_id(service):
    _, _, _, scan = _ready_scan(service)
    payload = _base_attestation(CYCLONEDX_BODY, "cyclonedx_json")
    first = service.submit_sbom_attestation(scan["scan_id"], payload)
    second = service.submit_sbom_attestation(scan["scan_id"], payload)
    assert second.get("idempotent") is True
    assert second["attestation_id"] == first["attestation_id"]


def test_evidence_ref_omitted_when_no_attestation_submitted(service):
    signing_key, _, enrollment, scan = _ready_scan(service)
    submit_all_tasks(service, signing_key, scan, enrollment["connector_id"])
    row = service.db.execute("SELECT * FROM scans WHERE id = ?", (scan["scan_id"],)).fetchone()
    score = json.loads(row["score_json"])
    cve_refs = [r for r in score["evidence_refs"] if r["rubric_point_id"] == CVE_ATTESTATION_RUBRIC_POINT_ID]
    assert cve_refs == []
