"""Smoke test for scripts/render_card.py.

Subprocess invocation so we catch interpreter errors and exit-code regressions.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "render_card.py"
FIXTURE = REPO / "tests" / "fixtures" / "synthetic_eval_outputs.json"


class RenderCardCLITests(unittest.TestCase):
    def test_cli_writes_html_and_reports_no_bind_verdict(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "card.html"
            result = subprocess.run(
                [
                    sys.executable, str(SCRIPT), str(FIXTURE),
                    "--snapshot-hash", "sha256:cli-smoke",
                    "--forensic-bundle", "https://bundles.local/cli-smoke",
                    "--out", str(out),
                ],
                check=True,
                capture_output=True,
                text=True,
                cwd=REPO,
            )
            self.assertTrue(out.exists())
            html = out.read_text()
            self.assertIn("<!DOCTYPE html>", html)
            self.assertIn("NO BIND", html)
            stdout = result.stdout
            self.assertIn("ARE=", stdout)
            self.assertIn("verdict=NO BIND", stdout)
            self.assertIn("weak-link", stdout)

    def test_cli_scan_age_override_propagates_stale_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "card.html"
            subprocess.run(
                [
                    sys.executable, str(SCRIPT), str(FIXTURE),
                    "--snapshot-hash", "sha256:cli-stale",
                    "--forensic-bundle", "https://bundles.local/cli-stale",
                    "--out", str(out),
                    "--scan-age-days", "120",
                ],
                check=True,
                capture_output=True,
                text=True,
                cwd=REPO,
            )
            self.assertIn("STALE", out.read_text())


if __name__ == "__main__":
    unittest.main()
