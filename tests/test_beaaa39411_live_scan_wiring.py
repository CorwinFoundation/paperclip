from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / "backbond/operations/site-quickfix/release-2026-10-08-scan-lifecycle"
SCAN = RELEASE / "site/scan"
CONTROLLER = ROOT / "backbond/scan-intake/web"


def test_live_page_loads_controller_in_dependency_order_and_mounts_real_controls():
    html = (SCAN / "index.html").read_text()
    scripts = [
        '/scan/scan-lifecycle.js',
        '/scan/scan-progress.js',
        '/scan/scan-page.js',
        '/scan/scan.js',
    ]
    assert [html.index(src) for src in scripts] == sorted(html.index(src) for src in scripts)
    assert 'id="submit" type="button"' in html
    assert 'data-backbond-scan-start' in html
    assert 'data-backbond-scan-progress' in html
    assert '/scan/scan-progress.css' in html


def test_adapter_has_one_start_and_poll_owner_and_preserves_live_behaviors():
    source = (SCAN / "scan.js").read_text()
    assert source.count("request('/scans'") == 1
    assert source.count("request('/scan-status'") == 1
    assert "setTimeout(poll" not in source
    assert "BackBondScanPage.mount" in source
    assert "status_token" in source and "scan_id:accepted.status_token" in source
    assert "state||value.status" in source
    for behavior in (
        "connection_proof:proof",
        "$('auth-value').value=''",
        "$('system-prompt').value=''",
        "$('endpoint').value=''",
        "request('/scan-cancel'",
        "field.checkValidity()",
        "showError",
    ):
        assert behavior in source


def test_published_controller_assets_match_candidate_sources():
    for name in ("scan-lifecycle.js", "scan-progress.js", "scan-page.js", "scan-progress.css"):
        assert (SCAN / name).read_bytes() == (CONTROLLER / name).read_bytes()


def test_readme_names_exact_site_dir_and_deploy_command():
    readme = (RELEASE / "README.md").read_text()
    assert "SITE_DIR=backbond/operations/site-quickfix/release-2026-10-08-scan-lifecycle/site" in readme
    assert 'netlify deploy --dir "$SITE_DIR" --prod' in readme
