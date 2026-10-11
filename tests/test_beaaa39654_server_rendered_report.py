"""BEAAA-39654: the Kaltura report link's first (non-JS) response is server-rendered."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

RELEASE = (Path(__file__).resolve().parents[1] / "backbond" / "operations" / "site-quickfix"
           / "release-2026-10-11-management-report")
TOKEN = "99c83595be4fd9087cc85ba8790c37fc"
KALTURA_PAGES = [RELEASE / "scan" / "r" / f"{TOKEN}.html", RELEASE / "scan" / "r" / TOKEN / "index.html"]
EVIL = "<script>alert(1)</script>"
INLINE_SCRIPT = re.compile(r"<script(?![^>]*\bsrc=)[^>]*>", re.I)


def _voice_tests() -> list[dict]:
    roster = [("VDN", 10, "in_domain_normal"), ("VPF", 5, "product_fabrication"), ("VASR", 5, "asr_shaped_input"),
              ("VML", 15, "multilingual_guardrails"), ("VSS", 5, "screen_share_readback"),
              ("VSP", 5, "summary_poisoning"), ("VGD", 5, "goals_data_collection"),
              ("VHD", 5, "human_disclosure"), ("VAA", 5, "abuse_toward_agent")]
    tests = [{"id": f"{p}-{i:02d}", "family": f, "verdict": "pass"} for p, n, f in roster for i in range(1, n + 1)]
    for t in tests[:16]:
        t["verdict"] = "fail"
    return tests


def _fixture(**overrides) -> dict:
    original = [{"id": "PI-29" if i == 0 else f"T-{i}", "family": "prompt_injection",
                 "verdict": "fail" if i < 10 else "pass", "prompt": EVIL, "reply": EVIL} for i in range(100)]
    scenarios = [f"{p}{i:02d}" for p, n in (("S", 6), ("I", 6), ("U", 4), ("P", 2), ("F", 2)) for i in range(1, n + 1)]
    targeted = [{"id": f"KFR-{s}-R{r}", "family": "Targeted synthetic", "verdict": "pass"}
                for s in scenarios for r in (1, 2, 3)]
    for i, t in enumerate(targeted):
        t["verdict"] = "pass" if i < 37 else "fail" if i < 48 else "inconclusive" if i < 57 else "unverified"
    data = {
        "agent_label": EVIL + ' "quoted" & co', "endpoint_host": "learning.kaltura.com",
        "pack_version": "starter-100@3", "created_at": "2026-10-08T23:02:18.542288+00:00", "partner": "kaltura",
        "tests": original, "outcome": "complete", "incomplete_reason": None, "starter_score": None,
        "counts": {"passed": 171, "failed": 37, "could_not_run": 12}, "report_kind": "starter_extensions",
        "result_sets": [
            {"pack_id": "starter-100", "pack_version": "starter-100@3", "status": "complete", "tests": original},
            {"pack_id": "starter-voice-50", "pack_version": "starter-voice-50@1", "status": "complete",
             "tests": _voice_tests()},
            {"pack_id": "codex-targeted-20", "pack_version": "codex-targeted-20@1", "status": "complete",
             "tests": targeted},
        ],
    }
    data.update(overrides)
    return data


def _prerender(tmp_path: Path, data: dict, *extra: str) -> subprocess.CompletedProcess:
    report = tmp_path / "report.json"
    report.write_text(json.dumps(data), encoding="utf-8")
    return subprocess.run([sys.executable, str(RELEASE / "prerender.py"), str(report), TOKEN,
                           "--out", str(tmp_path / "out.html"), *extra],
                          capture_output=True, text=True, check=False)


def test_prerender_escapes_and_server_renders_title_target_and_counts(tmp_path):
    result = _prerender(tmp_path, _fixture(), "--expect",
                        "scenarios=180,executions=220,passed=171,failed=37,gaps=12,inconclusive=9,"
                        "unverified=3,adjudicable=208")
    assert result.returncode == 0, result.stderr
    counts = json.loads(result.stdout)
    assert (counts["passed"], counts["failed"], counts["gaps"], counts["executions"]) == (171, 37, 12, 220)
    page = (tmp_path / "out.html").read_text(encoding="utf-8")
    assert EVIL not in page and "alert(1)</script>" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt; &quot;quoted&quot; &amp; co" in page
    assert re.search(r"<title>[^<]*Starter scan \+ extensions \| BackBond</title>", page)
    assert re.search(r'<h1 id="agent-name">[^<]*Starter scan \+ extensions</h1>', page)
    for name in ('name="description"', 'property="og:title"', 'property="og:description"'):
        assert name in page
    for value in ("learning.kaltura.com", "8 October 2026", "180 scenarios", "220 recorded executions",
                  "171 met criteria", "37 flagged", "12 could not run", "9 inconclusive", "3 unverified"):
        assert value in page
    assert "Loading your report" not in page
    assert not INLINE_SCRIPT.search(page) and " style=" not in page and "<style" not in page
    assert '<script src="/scan/report.js" defer></script>' in page
    assert '<p id="report-error" role="status" hidden></p>' in page
    assert "BackBond Score: N/A" in page and "Starter score" not in page


def test_prerender_never_counts_not_run_as_pass(tmp_path):
    data = _fixture()
    for t in data["result_sets"][0]["tests"][10:15]:
        t["verdict"] = "could_not_run"
    data["counts"] = {"passed": 166, "failed": 37, "could_not_run": 17}
    result = _prerender(tmp_path, data)
    assert result.returncode == 0, result.stderr
    counts = json.loads(result.stdout)
    assert counts["passed"] == 166 and counts["gaps"] == 17 and counts["adjudicable"] == 203
    assert counts["gap_breakdown"] == {"could not run": 5, "inconclusive": 9, "unverified": 3}
    # An API document that claims the not-run rows passed is rejected, not rendered.
    data["counts"] = {"passed": 171, "failed": 37, "could_not_run": 12}
    assert _prerender(tmp_path, data).returncode != 0
    # Expectations are enforced against the computed counts.
    assert _prerender(tmp_path, _fixture(), "--expect", "passed=172").returncode == 2


def test_prerender_fails_closed_on_mismatched_combined_inputs(tmp_path):
    bad_host = _fixture(endpoint_host="other.example")
    assert _prerender(tmp_path, bad_host).returncode != 0
    missing = _fixture()
    missing["result_sets"].pop()
    assert _prerender(tmp_path, missing).returncode != 0


def test_plain_reports_keep_the_normal_label_and_no_score(tmp_path):
    data = {"agent_label": "Plain", "endpoint_host": "agent.example", "pack_version": "starter-100@3",
            "created_at": "2026-10-08T00:00:00Z", "starter_score": 97.5, "outcome": "complete",
            "tests": [{"id": f"T-{i}", "family": "x", "verdict": "pass" if i else "could_not_run"} for i in range(100)]}
    result = _prerender(tmp_path, data)
    assert result.returncode == 0, result.stderr
    page = (tmp_path / "out.html").read_text(encoding="utf-8")
    assert "<title>Plain - Agent assessment | BackBond</title>" in page
    assert "Starter scan + extensions" not in page and "97" not in page
    assert "99 met criteria" in page and "1 could not run" in page


def test_no_customer_report_page_is_committed_for_publication():
    # Founder, 11 Oct 2026: the Kaltura report stays private; no static page may carry its title/target/counts.
    assert not any(p.exists() for p in KALTURA_PAGES)
    assert not [p for p in (RELEASE / "scan" / "r").rglob("*.html") if p.name != "index.html" or p.parent.name != "r"]


def test_template_has_no_loading_placeholder():
    template = (RELEASE / "scan" / "r" / "index.html").read_text(encoding="utf-8")
    assert "Loading your report" not in template
    assert '<p id="report-error" role="status" hidden></p>' in template


def test_report_js_matches_build_output(tmp_path):
    copy = tmp_path / "release"
    shutil.copytree(RELEASE, copy, ignore=shutil.ignore_patterns("brand", "*.html"))
    subprocess.run([sys.executable, str(copy / "build.py")], check=True, capture_output=True)
    assert (copy / "scan" / "report.js").read_bytes() == (RELEASE / "scan" / "report.js").read_bytes()


def test_report_viewer_starts_recipient_verification_on_auth_challenge():
    source = (RELEASE / "scan" / "report.js").read_text(encoding="utf-8")
    assert "credentials:'include'" in source
    assert "response.status === 401 || response.status === 403" in source
    assert "verification(match[1])" in source
    for required in ("'/verification'", "'/session'", "Email me a code", "Open report"):
        assert required in source


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_report_js_syntax_and_renderer_regressions():
    subprocess.run(["node", "--check", str(RELEASE / "scan" / "report.js")], check=True)
    result = subprocess.run(["node", str(RELEASE / "verify.js")], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert "Status element removed on success and revealed on error" in result.stdout
