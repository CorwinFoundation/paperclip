"""CI guard for design-partner scanner/corpus release-pin drift (BEAAA-16789)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from scanners import CORPUS_VERSION, SCANNER_VERSION


ROOT = Path(__file__).resolve().parents[1]
SURFACES = {
    "connector": ROOT / "tools/are-connector/README.md",
    "quickstart": ROOT / "artifacts/beaaa-16356/backbond-scanner-design-partner-quickstart-v0.2.md",
    "connection_pack": ROOT / "artifacts/beaaa-16356/backbond-scanner-connection-pack-template-v0.1.md",
}


def _pins(text: str) -> tuple[str, str]:
    scanner = re.search(r"scanner_version(?:`|\s)*:\s*`?([0-9]+\.[0-9]+\.[0-9]+)", text)
    corpus = re.search(r"corpus_version(?:`|\s)*:\s*`?([0-9]+\.[0-9]+\.[0-9]+)", text)
    assert scanner, "scanner_version pin missing"
    assert corpus, "corpus_version pin missing"
    return scanner.group(1), corpus.group(1)


def _assert_pin_pair(surface: str, actual: tuple[str, str]) -> None:
    expected = (SCANNER_VERSION, CORPUS_VERSION)
    assert actual == expected, f"{surface} pins {actual}, expected Scanner constants {expected}"


def test_all_design_partner_surfaces_match_scanner_constants() -> None:
    from tools.portal_v1 import portal

    _assert_pin_pair("portal", (portal.SCANNER_VERSION, portal.CORPUS_VERSION))
    manifest = portal.PortalService.__new__(portal.PortalService).vendor_scan_capability_manifest(
        scanner_binary_path="",
        fallback_mode="fixture_fallback",
    )
    unavailable_families = {
        item["family"] for item in manifest["developed_but_unavailable_capability_families"]
    }
    assert f"scanner_source_{SCANNER_VERSION.replace('.', '_')}" not in unavailable_families
    assert all(
        item["reason_code"] != "portal_pin_holdback"
        for item in manifest["developed_but_unavailable_capability_families"]
    )
    for surface, path in SURFACES.items():
        _assert_pin_pair(surface, _pins(path.read_text(encoding="utf-8")))


def test_guard_rejects_one_surface_mismatch() -> None:
    with pytest.raises(AssertionError, match="connection_pack pins"):
        _assert_pin_pair("connection_pack", (SCANNER_VERSION, "9.9.9"))
