import importlib.util
from pathlib import Path
import sqlite3

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "activate", ROOT / "backbond/scan-intake/activate_kaltura_browser_transport.py"
)
activate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(activate)


def source_with_transport(declaration):
    source = (ROOT / "backbond/scan-intake/server/app.py").read_text()
    current = [line for line in source.splitlines()
               if line.strip().startswith("transport: str = Field(pattern=")]
    assert len(current) == 1
    return source.replace(current[0], "    " + declaration, 1)


def test_patch_is_surgical_and_idempotent():
    # Exercise legacy input independently of the current supported transports.
    legacy = source_with_transport(activate.OLD_TRANSPORT).replace(activate.VALIDATION, "")
    custom = legacy.replace("class ScanRequest", "# locally preserved recovery/mail guards\nclass ScanRequest")
    patched = activate.patch_app(custom)
    assert patched == activate.patch_app(patched)
    assert "# locally preserved recovery/mail guards" in patched
    assert 'self.endpoint_url != "https://learning.kaltura.com/"' in patched
    assert 'self.auth.type != "none"' in patched
    assert patched.replace(activate.NEW_TRANSPORT, activate.OLD_TRANSPORT).replace(activate.VALIDATION, "") == custom


@pytest.mark.parametrize("declaration", [
    'transport: str = Field(pattern="^(rest|ag_ui)$")',
    activate.OLD_TRANSPORT + "\n" + activate.OLD_TRANSPORT,
    'transport: str = Field(pattern="^(rest|unknown)$")\n' + activate.NEW_TRANSPORT_AGUI,
])
def test_patch_still_refuses_unknown_or_ambiguous_transport_declarations(declaration):
    with pytest.raises(RuntimeError, match="expected exactly one recognized transport declaration"):
        activate.patch_app(declaration + "\n" + activate.ANCHOR)


@pytest.mark.parametrize("declaration", [
    activate.OLD_TRANSPORT, activate.NEW_TRANSPORT,
    activate.OLD_TRANSPORT_AGUI, activate.NEW_TRANSPORT_AGUI,
])
@pytest.mark.parametrize("with_quick", [False, True])
def test_supported_declarations_keep_agui_and_restore_required_validation(declaration, with_quick):
    expected = activate.NEW_TRANSPORT_AGUI if "|ag_ui|" in declaration else activate.NEW_TRANSPORT
    if with_quick:
        declaration = declaration.replace(")$", "|amazon_quick)$")
        expected = expected.replace(")$", "|amazon_quick)$")
    source = source_with_transport(declaration).replace(activate.VALIDATION, "")
    patched = activate.patch_app(source)
    assert patched == source.replace(declaration, expected).replace(
        activate.VALIDATION_ANCHOR,
        activate.VALIDATION_ANCHOR + activate.VALIDATION,
    )
    assert activate.patch_app(patched) == patched


def test_current_install_is_unchanged():
    source = (ROOT / "backbond/scan-intake/server/app.py").read_text()
    assert activate.patch_app(source) == source


def test_transport_example_in_comment_is_preserved():
    example = "# Example: " + activate.OLD_TRANSPORT + "\n"
    source = example + source_with_transport(activate.OLD_TRANSPORT).replace(activate.VALIDATION, "")
    patched = activate.patch_app(source)
    assert patched.startswith(example)
    assert "    " + activate.NEW_TRANSPORT + "\n" in patched
    assert activate.patch_app(patched) == patched


def test_unrecognized_existing_kaltura_validation_refuses_patch():
    source = (ROOT / "backbond/scan-intake/server/app.py").read_text()
    source = source.replace("kaltura_genie_browser requires auth type none", "custom auth validation")
    with pytest.raises(RuntimeError, match="unrecognized Kaltura validation block"):
        activate.patch_app(source)


@pytest.mark.parametrize("kind", ["duplicate", "unknown", "missing_anchor"])
def test_ambiguous_or_unrecognized_install_refuses_patch(kind):
    source = (ROOT / "backbond/scan-intake/server/app.py").read_text()
    if kind == "duplicate":
        source += "\n" + activate.NEW_TRANSPORT_AGUI
    elif kind == "unknown":
        source = source.replace("json_map|ag_ui|vapi", "json_map|unknown|vapi")
    else:
        source = source.replace(activate.VALIDATION, "").replace(activate.VALIDATION_ANCHOR, "")
    with pytest.raises(RuntimeError):
        activate.patch_app(source)


def test_active_or_dispatch_uncertain_scan_refuses_reload(tmp_path):
    db_path = tmp_path / "intake.sqlite"
    with sqlite3.connect(db_path) as db:
        db.execute("CREATE TABLE scans (status TEXT)")
        db.execute("INSERT INTO scans VALUES ('dispatch_unknown')")
    with pytest.raises(RuntimeError, match="refusing intake reload"):
        activate.assert_idle(db_path)


def test_terminal_history_allows_reload(tmp_path):
    db_path = tmp_path / "intake.sqlite"
    with sqlite3.connect(db_path) as db:
        db.execute("CREATE TABLE scans (status TEXT)")
        db.executemany("INSERT INTO scans VALUES (?)", [("finished",), ("error",), ("cancelled",)])
    activate.assert_idle(db_path)
