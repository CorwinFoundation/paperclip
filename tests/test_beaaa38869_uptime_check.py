"""BEAAA-38869: the public-endpoint uptime check alerts once and recovers once.

Pins the state machine in ops/paperclip/host-scripts/backbond-uptime-check.py:
healthy -> 1 fail (silent) -> 2nd fail (ONE alert) -> more fails (silent)
-> success (ONE recovered) -> healthy (silent). Also drives run() end to end
with the network and Telegram stubbed, so persistence across runs is covered.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOST = ROOT / "ops" / "paperclip" / "host-scripts"
SCRIPT = HOST / "backbond-uptime-check.py"

_spec = importlib.util.spec_from_file_location("backbond_uptime_check", SCRIPT)
uc = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(uc)


def test_state_machine_alerts_once_and_recovers_once() -> None:
    state = None
    actions = []
    for ok in [True, False, False, False, False, True, True]:
        state, action = uc.step(state, ok)
        actions.append(action)
    assert actions == [None, None, "alert", None, None, "recovered", None]


def test_single_blip_never_alerts_or_recovers() -> None:
    state = None
    actions = []
    for ok in [False, True, False, True]:
        state, action = uc.step(state, ok)
        actions.append(action)
    assert actions == [None, None, None, None]


def test_run_persists_state_and_sends_exactly_two_messages(tmp_path, monkeypatch) -> None:
    sent: list[str] = []
    health = {"up": True}
    monkeypatch.setattr(uc, "probe", lambda url, timeout: (health["up"], "HTTP 200" if health["up"] else "HTTP 404"))

    def fake_send(token, chat_id, text, timeout=30.0):
        sent.append(text)
        return {"ok": True, "result": {"message_id": len(sent)}}

    monkeypatch.setattr(uc, "send_telegram", fake_send)
    env = {
        "BACKBOND_UPTIME_CHECKS": "page=https://example.invalid/x",
        "BACKBOND_UPTIME_STATE_DIR": str(tmp_path),
        "BACKBOND_UPTIME_PREFIX": "[test]",
        "TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_FOUNDER_CHAT_ID": "c",
        "BACKBOND_UPTIME_SECRETS": str(tmp_path / "none.env"),
    }
    for up in [True, False, False, False, True, True]:
        health["up"] = up
        assert uc.run(env) == 0
    assert len(sent) == 2
    assert sent[0].startswith("[test] DOWN: page")
    assert sent[1].startswith("[test] RECOVERED: page")
    assert json.loads((tmp_path / "state.json").read_text())["page"]["consecutive_failures"] == 0


def test_failed_send_is_retried_next_run(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(uc, "probe", lambda url, timeout: (False, "HTTP 503"))
    results = iter([{"ok": False, "description": "boom"}, {"ok": True, "result": {"message_id": 7}}])
    calls = []
    monkeypatch.setattr(uc, "send_telegram", lambda *a, **k: calls.append(1) or next(results))
    env = {"BACKBOND_UPTIME_CHECKS": "a=https://example.invalid/", "BACKBOND_UPTIME_STATE_DIR": str(tmp_path),
           "TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_FOUNDER_CHAT_ID": "c",
           "BACKBOND_UPTIME_SECRETS": str(tmp_path / "none.env")}
    assert uc.run(env) == 0          # 1st failure: nothing due
    assert uc.run(env) == 1          # alert due, send fails
    assert uc.run(env) == 0          # retried and sent
    assert uc.run(env) == 0          # already alerted: silent
    assert len(calls) == 2


def test_parse_checks_override_and_alternates() -> None:
    checks = uc.parse_checks("a=https://x/1|https://x/2; b=https://y/")
    assert checks == [("a", ["https://x/1", "https://x/2"]), ("b", ["https://y/"])]
    assert uc.parse_checks("") == uc.DEFAULT_CHECKS


def test_units_and_manifest_agree() -> None:
    def manifest(fn: str) -> list[str]:
        out = subprocess.run(["bash", "-c", f'. "{HOST / "manifest.sh"}" && {fn}'],
                             capture_output=True, text=True, check=True).stdout
        return out.split()
    assert SCRIPT.name in manifest("host_script_names")
    assert {"backbond-uptime-check.service", "backbond-uptime-check.timer"} <= set(manifest("host_user_unit_names"))
    svc = (HOST / "backbond-uptime-check.service").read_text()
    assert f"/home/beai-agent/.local/bin/{SCRIPT.name}" in svc
    assert "OnCalendar=*:0/5" in (HOST / "backbond-uptime-check.timer").read_text()
