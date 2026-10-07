"""BEAAA-38932: starter-100@3 reports show the approved omissions and upgrade blocks; @1/@2 do not."""
import json
import pathlib
import shutil
import subprocess

import pytest

DIR = pathlib.Path(__file__).resolve().parents[1] / "backbond/operations/site-quickfix/release-2026-10-07-starter-v2"
HEADINGS = ("What this Starter scan tested", "What this scan did not look at", "Review Full Scan scope")


def _render(pack):
    script = (
        "global.window={};" + (DIR / "starter-report.js").read_text() +
        ";var el={};window.renderStarterReport(" + json.dumps({
            "pack_version": pack,
            "result_rows": [{"family": "prompt_injection", "verdict": "passed", "test_id": "t1"}],
        }) + ",el);process.stdout.write(el.innerHTML);"
    )
    return subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True).stdout


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_v3_renders_blocks_below_table_and_v1_v2_do_not():
    html = _render("starter-100@3")
    assert html.index("</table>") < html.index(HEADINGS[0]) < html.index(HEADINGS[1]) < html.index(HEADINGS[2])
    for pack in ("starter-100@1", "starter-100@2"):
        assert not any(h in _render(pack) for h in HEADINGS)


def test_copy_has_no_placeholders_and_site_renderer_is_gated_on_v3():
    for name in ("starter-report.js", "site/scan/report.js"):
        src = (DIR / name).read_text()
        assert all(h in src for h in HEADINGS)
        assert "[[" not in src
        assert "Neither one counts as a pass." in src
    assert "if (data.pack_version === 'starter-100@3') starterScopeBlocks(" in (DIR / "site/scan/report.js").read_text()
