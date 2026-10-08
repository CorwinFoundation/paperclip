from pathlib import Path
import importlib.util


ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "scripts/ops/patch_scan_intake_progress.py"
spec = importlib.util.spec_from_file_location("progress_installer", SCRIPT)
installer = importlib.util.module_from_spec(spec)
assert spec.loader
spec.loader.exec_module(installer)

LIVE_BASELINE = '''from fastapi import HTTPException
import httpx
class App:
    def post(self, path):
        return lambda fn: fn
app = App()
class Store:
    def status(self, token): return {}
    def scan(self, scan_id): return {}
store = Store()
SCANNER_URL = "http://scanner"
def _headers(data=None): return {}
class StatusRequest:
    status_token = "x"
def _status(token: str) -> dict:
    value = store.status(token)
    if not value:
        raise HTTPException(404, "scan not found")
    return value


@app.post("/scan/v1/scan-status")
def scan_status(body: StatusRequest):
    # POST so the bearer-style token never lands in access-log URLs.
    value = _status(body.status_token)
    value.pop("scan_id")
    return value
'''


def test_exact_live_baseline_is_surgically_patched_and_idempotent():
    patched, changed = installer.patch_source(LIVE_BASELINE)
    assert changed
    assert patched.replace(installer.PROGRESS, "").replace(installer.NEW_STATUS, installer.OLD_STATUS) == LIVE_BASELINE
    assert all(field in patched for field in ("last_activity_at", "current_test_id", "current_family"))
    assert installer.patch_source(patched) == (patched, False)


def test_refuses_drifted_or_duplicate_anchor():
    for source in (LIVE_BASELINE.replace('value.pop("scan_id")', 'value.pop("scan_id", None)'), LIVE_BASELINE * 2):
        try:
            installer.patch_source(source)
        except RuntimeError as error:
            assert "refusing" in str(error)
        else:
            raise AssertionError("drifted baseline was accepted")
