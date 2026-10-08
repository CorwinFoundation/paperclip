import subprocess
import stat
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / "backbond/operations/site-quickfix/release-2026-10-08-scan-lifecycle"
ACTIVATE = RELEASE / "activate.sh"
PATCHER = RELEASE / "patch_live_progress.py"


def test_progress_activation_sources_verify_without_mutation():
    result = subprocess.run(
        ["bash", str(ACTIVATE), "verify"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout == "activation_sources_verified\n"


def test_activation_is_split_and_preserves_operational_guards():
    source = ACTIVATE.read_text()
    assert 'case "$MODE"' in source
    assert "patch_live_progress.py" in source
    assert "systemctl restart" not in source
    assert "backend_staged_no_restart" in source
    assert 'netlify deploy --site "$NETLIFY_SITE_ID" --dir "$staging" --prod' in source
    assert 'test -n "${NETLIFY_BASE_DIR:-}"' in source
    assert 'test "$site_domain" = "backbond.ai"' in source
    assert 'cmp -s "$staging/live-$asset" "$NETLIFY_BASE_DIR/scan/$asset"' in source
    assert "cmp - \"$SITE/scan/scan-progress.js\"" in source
    assert "connection_proof:proof" in source
    assert "request('/scan-cancel'" in source
    assert "report_emailed===true" in source
    assert "sqlite" not in source.lower()
    assert "mailgun" not in source.lower()


def live_app(progress_fields: str) -> str:
    return f'''{progress_fields}
tenant_id = f"partner-{{row['partner']}}"
# kaltura_genie_browser requires the approved public URL
# kaltura_genie_browser requires auth type none
if value["status"] == "dispatch_unknown":
    pass
if value["status"] == "finished" and value.get("report_emailed"):
    pass
'''


def test_live_patcher_changes_only_exact_progress_block_and_replays(tmp_path):
    namespace = {}
    exec(PATCHER.read_text(), namespace)
    target = tmp_path / "app.py"
    backup = tmp_path / "original.py"
    target.write_text(live_app(namespace["OLD"]))
    original = target.read_text()
    assert namespace["patch"](target, backup) is True
    assert namespace["NEW"] in target.read_text()
    assert target.read_text().replace(namespace["NEW"], namespace["OLD"]) == original
    assert stat.S_IMODE(backup.stat().st_mode) == 0o600
    assert namespace["patch"](target, backup) is False


def test_live_patcher_fails_closed_on_guard_mismatch(tmp_path):
    namespace = {}
    exec(PATCHER.read_text(), namespace)
    target = tmp_path / "app.py"
    target.write_text(namespace["OLD"])
    try:
        namespace["patch"](target, tmp_path / "backup.py")
    except RuntimeError as error:
        assert "live guard mismatch" in str(error)
    else:
        raise AssertionError("missing live guards must fail closed")
