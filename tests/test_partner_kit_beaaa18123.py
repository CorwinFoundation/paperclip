"""Focused security, schema, content, and render regressions for BEAAA-18123."""

from __future__ import annotations

import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from pypdf import PdfReader


ROOT = Path(__file__).resolve().parents[1]
KIT = ROOT / "tools" / "partner-kit"
CHECKER_PATH = KIT / "conformance_check.py"
GENERATOR_PATH = KIT / "generate_design_partner_quickstart_v2.py"
PDF_PATH = KIT / "output" / "BackBond_Design_Partner_Scanner_Quick_Start_v2.pdf"
METADATA_PATH = KIT / "output" / "BackBond_Design_Partner_Scanner_Quick_Start_v2.metadata.json"


def _load_checker():
    spec = importlib.util.spec_from_file_location("partner_conformance", CHECKER_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_http_url_is_rejected_before_network():
    checker = _load_checker()
    with pytest.raises(ValueError, match="HTTPS"):
        checker.run("http://agent.example.test/scan", "secret")


def test_redirect_is_rejected_without_constructing_followup_request():
    checker = _load_checker()
    request = checker.urllib.request.Request(
        "https://agent.example.test/scan",
        headers={"Authorization": "Bearer must-not-forward"},
    )
    redirected = checker._NoRedirect().redirect_request(
        request, None, 302, "Found", {}, "https://other.example.test/collect"
    )
    assert redirected is None


def test_response_body_is_streamed_with_hard_cap():
    checker = _load_checker()

    class EndlessBody:
        def __init__(self):
            self.requested = []

        def read(self, size):
            self.requested.append(size)
            return b"x" * size

    body = EndlessBody()
    raw, oversized = checker._read_limited(body)
    assert oversized is True
    assert len(raw) == checker.MAX_BYTES
    assert sum(body.requested) == checker.MAX_BYTES + 1
    assert max(body.requested) <= 64 * 1024


def test_secret_value_is_not_a_supported_argv_argument():
    completed = subprocess.run(
        [sys.executable, str(CHECKER_PATH), "--help"],
        check=True,
        text=True,
        capture_output=True,
    )
    assert "--secret " not in completed.stdout
    assert "--secret-env" in completed.stdout


def test_safe_transport_error_never_leaks_secret(monkeypatch):
    checker = _load_checker()
    secret = "super-sensitive-value"

    class FailingOpener:
        def open(self, request, timeout):
            raise RuntimeError(secret)

    monkeypatch.setattr(checker, "_OPENER", FailingOpener())
    results = checker.run("https://agent.example.test/scan", secret)
    assert secret not in repr(results)


def test_generator_contains_released_content_and_structured_sample():
    source = GENERATOR_PATH.read_text()
    assert "Corpus 2026.6.24" in source
    assert '"corpus_version": "2026.6.24"' in source
    assert "AIUC-1-mapped technical assessment" in source
    assert "<b>BackBond Extensions</b>" in source
    assert '"are_total": 76,' in source
    assert '{"dim_id": "P4.3_KILL_SWITCH", "reason": "attestation evidence unavailable"}' in source
    assert "Corpus 0.1.0" not in source
    assert '"corpus_version": "0.1.0"' not in source


def test_fresh_pdf_has_declared_page_count_headers_and_metadata(tmp_path):
    env = os.environ.copy()
    subprocess.run([sys.executable, str(GENERATOR_PATH)], check=True, cwd=ROOT, env=env)

    reader = PdfReader(PDF_PATH)
    assert len(reader.pages) == 7
    for page_number, page in enumerate(reader.pages[1:], start=2):
        text = page.extract_text()
        assert "BACKBOND  |  DESIGN PARTNER SCANNER PILOT" in text, page_number

    metadata = json.loads(METADATA_PATH.read_text())
    assert metadata["brand_backbone_version"] == "v1.6"
    assert metadata["brand_backbone_sha256"] == (
        "a2ffc4f919b0f71a17b9f4506ac4edf11cd37956ff494f00d7a45f0b166ea090"
    )
    assert metadata["brand_backbone_verified"] is True
    assert metadata["corpus_version"] == "2026.6.24"
