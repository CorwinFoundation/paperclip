"""BEAAA-36045: the credential watcher's supervisor is tracked, and its heartbeat is read.

Two gaps, both found by getting the premise wrong first. I filed BEAAA-36045
claiming the watcher had no restart path, having checked `crontab -l` and `pgrep`
but not `systemctl --user`. It is in fact supervised by a systemd --user unit with
`Restart=always` and linger enabled. What that mistake exposed:

1. The unit file lived only on the box. The script was version-controlled and the
   thing that runs it was not -- BEAAA-36015's asymmetry one file over. Worse,
   `manifest.sh` recorded the script's trigger as `-`, documented as "no cron
   entry", which reads as "nothing runs this". That is exactly the wrong inference
   I made. The trigger field now names the mechanism.

2. `Restart=always` restarts a process that *exits*. It cannot see a process that
   is alive but no longer receiving events -- dropped inotify watch, swapped
   inode, wedged loop. There `systemctl --user is-active` says `active` and the log
   goes quiet, indistinguishable from "nothing wrote to the credential". The
   periodic `alive` line exists to separate those and nothing read it.

What is pinned here:

- `--assert-fresh` reports 0 on a fresh log and 5 on stale/missing/unparseable.
- Any event counts as proof of life, not just `alive`.
- The tracked unit's `ExecStart` points at the manifest's own install dir and the
  tracked script name, so unit and manifest cannot silently disagree.
- The unit is in the manifest roster, so the installer and drift detector cover it.
- No roster row uses the bare `-` trigger that caused the misreading.
"""
from __future__ import annotations

import datetime
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
HS = REPO / "ops" / "paperclip" / "host-scripts"
WATCHER = HS / "codex-auth-writewatch.py"
UNIT = HS / "codex-auth-writewatch.service"
MANIFEST = HS / "manifest.sh"

spec = importlib.util.spec_from_file_location("writewatch", WATCHER)
assert spec and spec.loader
ww = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = ww
spec.loader.exec_module(ww)


def _log(tmp_path: Path, *, age_seconds: float, event: str = "alive") -> Path:
    stamp = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=age_seconds)
    p = tmp_path / "auth-writewatch.log"
    p.write_text(json.dumps({"event": event, "shape": {"api_key": None}, "ts":
                             stamp.isoformat().replace("+00:00", "Z")}) + "\n", encoding="utf-8")
    return p


def _manifest(fn: str) -> list[str]:
    out = subprocess.run(["bash", "-c", f'. "{MANIFEST}" && {fn}'],
                         capture_output=True, text=True, check=True).stdout
    return [ln for ln in out.splitlines() if ln.strip()]


def _manifest_var(name: str) -> str:
    return subprocess.run(["bash", "-c", f'. "{MANIFEST}" && printf "%s" "${{{name}}}"'],
                          capture_output=True, text=True, check=True).stdout


# --- the heartbeat reader -------------------------------------------------

def test_fresh_log_is_ok(tmp_path: Path) -> None:
    log = _log(tmp_path, age_seconds=10)
    assert ww.assert_fresh(ww.STALE_AFTER_SECONDS, log_path=str(log)) == 0


def test_stale_log_is_reported(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    log = _log(tmp_path, age_seconds=ww.STALE_AFTER_SECONDS + 60)
    assert ww.assert_fresh(ww.STALE_AFTER_SECONDS, log_path=str(log)) == 5
    assert "STALE" in capsys.readouterr().err


def test_missing_log_is_stale_not_a_crash(tmp_path: Path) -> None:
    assert ww.assert_fresh(ww.STALE_AFTER_SECONDS, log_path=str(tmp_path / "nope.log")) == 5


def test_unparseable_log_is_stale(tmp_path: Path) -> None:
    p = tmp_path / "junk.log"
    p.write_text("not json\nalso not json\n", encoding="utf-8")
    assert ww.assert_fresh(ww.STALE_AFTER_SECONDS, log_path=str(p)) == 5


def test_empty_log_is_stale(tmp_path: Path) -> None:
    p = tmp_path / "empty.log"
    p.write_text("", encoding="utf-8")
    assert ww.assert_fresh(ww.STALE_AFTER_SECONDS, log_path=str(p)) == 5


def test_a_write_event_also_counts_as_proof_of_life(tmp_path: Path) -> None:
    """A busy watcher emits `write`, not `alive`. Both prove the loop is running."""
    log = _log(tmp_path, age_seconds=5, event="write")
    assert ww.assert_fresh(ww.STALE_AFTER_SECONDS, log_path=str(log)) == 0


def test_newest_event_wins_over_an_older_trailing_line(tmp_path: Path) -> None:
    """Freshness must read the newest line, not the first one it can parse."""
    old = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=99999)
    fresh = datetime.datetime.now(datetime.timezone.utc)
    p = tmp_path / "mixed.log"
    p.write_text(
        json.dumps({"event": "alive", "ts": old.isoformat().replace("+00:00", "Z")}) + "\n"
        + json.dumps({"event": "alive", "ts": fresh.isoformat().replace("+00:00", "Z")}) + "\n",
        encoding="utf-8")
    assert ww.assert_fresh(ww.STALE_AFTER_SECONDS, log_path=str(p)) == 0


def test_stale_threshold_is_three_heartbeats() -> None:
    assert ww.STALE_AFTER_SECONDS == 3 * ww.HEARTBEAT_SECONDS


def test_bad_threshold_is_a_usage_error() -> None:
    run = subprocess.run([sys.executable, str(WATCHER), "--assert-fresh=soon"],
                         capture_output=True, text=True, timeout=60)
    assert run.returncode == 64, run.stderr


def test_unknown_flag_is_a_usage_error() -> None:
    run = subprocess.run([sys.executable, str(WATCHER), "--go"],
                         capture_output=True, text=True, timeout=60)
    assert run.returncode == 64, run.stderr


# --- the tracked supervisor ----------------------------------------------

def test_unit_is_tracked_in_the_repo() -> None:
    assert UNIT.is_file(), "the systemd --user unit must be version-controlled, not box-only"
    body = UNIT.read_text(encoding="utf-8")
    assert "Restart=always" in body
    assert "WantedBy=default.target" in body


def test_unit_is_in_the_manifest_roster() -> None:
    assert UNIT.name in _manifest("host_user_unit_names")


def test_unit_execstart_matches_the_manifest_install_path() -> None:
    """The pin that makes tracking meaningful: unit and manifest cannot disagree.

    If someone relocates HOST_SCRIPTS_INSTALL_DIR, or renames the script, the unit
    would keep pointing at a path that no longer receives the file and the watcher
    would die on every restart. Both facts live in this directory, so assert them
    against each other rather than trusting them separately.
    """
    install_dir = _manifest_var("HOST_SCRIPTS_INSTALL_DIR").rstrip("/")
    exec_line = next(ln for ln in UNIT.read_text(encoding="utf-8").splitlines()
                     if ln.startswith("ExecStart="))
    assert f"{install_dir}/{WATCHER.name}" in exec_line, exec_line
    assert WATCHER.name in _manifest("host_script_names")


def test_no_roster_row_uses_the_misleading_bare_dash_trigger() -> None:
    """`-` read as "nothing runs this" and caused BEAAA-36045 to be filed wrongly."""
    rows = subprocess.run(
        ["bash", "-c", f'. "{MANIFEST}" && printf "%s\\n" "${{HOST_SCRIPTS[@]}}"'],
        capture_output=True, text=True, check=True).stdout.splitlines()
    bare = [r for r in rows if r.strip().endswith("|-")]
    assert not bare, f"rows still use the bare '-' trigger: {bare}"
    assert all("|" in r for r in rows if r.strip())
