#!/usr/bin/env python3
"""backbond-uptime-check.py -- tell the founder when a public endpoint is down.

BEAAA-38869 item 3. Run every five minutes by backbond-uptime-check.timer
(systemd --user, no root). Each run probes every configured check once:

  * 2 consecutive failures of a check -> ONE Telegram message to the founder.
  * further failures                  -> nothing (already alerted).
  * first success after an alert      -> ONE "recovered" message.
  * a single failure then success     -> nothing (a blip, never alerted).
  * healthy                           -> nothing.

A check is healthy when ANY of its URLs answers HTTP 2xx within the timeout.
Alternates exist for the scan intake while it moves from /qa-preview/v1 to
/scan/v1 (BEAAA-38864); drop the old ones once the alias is retired.

Config (all optional, for testing or a temporary override):
  BACKBOND_UPTIME_CHECKS     "name=url[|alt-url...];name2=url"  replaces the list
  BACKBOND_UPTIME_STATE_DIR  default ~/.local/state/backbond-uptime
  BACKBOND_UPTIME_THRESHOLD  consecutive failures before alerting (default 2)
  BACKBOND_UPTIME_TIMEOUT    seconds per request (default 15)
  BACKBOND_UPTIME_PREFIX     text prepended to every message, e.g. "[test]"
  BACKBOND_UPTIME_SECRETS    env file with the Telegram credentials; falls back
                             to FOUNDER_DIGEST_SECRETS, then the same default
                             path tools/founder-digest/daily.sh sources.

Credentials: TELEGRAM_BOT_TOKEN and TELEGRAM_FOUNDER_CHAT_ID (process env wins
over the file). The token is never printed.

Exit status: 0 whether endpoints are up or down (a down endpoint is reported to
the founder, not to systemd); 1 if a due Telegram message could not be sent --
the state is then left un-alerted so the next run retries.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

DEFAULT_CHECKS: list[tuple[str, list[str]]] = [
    ("scanner release-identity", ["https://scanner.backbond.ai/scanner/v1/release-identity"]),
    ("scan intake API", [
        "https://api.backbond.ai/scan/v1/health",
        "https://api.backbond.ai/scan/v1/config",
        "https://api.backbond.ai/qa-preview/v1/config",
    ]),
    ("scan page", ["https://backbond.ai/scan/"]),
]

DEFAULT_SECRETS = (
    "/home/beai-agent/.paperclip/instances/default/projects/"
    "59f8876e-e729-4dda-98f9-1317c2b50492/8e61a25d-a945-4569-9f9c-4bce1cfef97b/"
    "_default/.secrets/telegram.env"
)
TELEGRAM_API = "https://api.telegram.org"
USER_AGENT = "backbond-uptime-check/1 (BEAAA-38869)"


# --- state machine (pure; unit-tested) ------------------------------------

def step(prev: dict | None, ok: bool, threshold: int = 2) -> tuple[dict, str | None]:
    """Advance one check's state by one probe result.

    Returns (new_state, action) where action is None, "alert" or "recovered".
    The caller persists new_state only after the action's message was sent.
    """
    prev = prev or {}
    fails = int(prev.get("consecutive_failures", 0))
    alerted = bool(prev.get("alerted", False))
    if ok:
        return {"consecutive_failures": 0, "alerted": False}, ("recovered" if alerted else None)
    fails += 1
    if not alerted and fails >= threshold:
        return {"consecutive_failures": fails, "alerted": True}, "alert"
    return {"consecutive_failures": fails, "alerted": alerted}, None


# --- config ---------------------------------------------------------------

def parse_checks(spec: str | None) -> list[tuple[str, list[str]]]:
    if not spec or not spec.strip():
        return DEFAULT_CHECKS
    checks = []
    for entry in spec.split(";"):
        entry = entry.strip()
        if not entry:
            continue
        name, sep, urls = entry.partition("=")
        if not sep or not urls.strip():
            raise ValueError(f"bad BACKBOND_UPTIME_CHECKS entry: {entry!r}")
        checks.append((name.strip(), [u.strip() for u in urls.split("|") if u.strip()]))
    return checks


def load_env_file(path: str) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export "):].strip()
        out[key] = val.strip().strip('"').strip("'")
    return out


def telegram_credentials(env: dict[str, str]) -> tuple[str | None, str | None]:
    path = env.get("BACKBOND_UPTIME_SECRETS") or env.get("FOUNDER_DIGEST_SECRETS") or DEFAULT_SECRETS
    file_env = load_env_file(path)
    token = env.get("TELEGRAM_BOT_TOKEN") or file_env.get("TELEGRAM_BOT_TOKEN")
    chat = env.get("TELEGRAM_FOUNDER_CHAT_ID") or file_env.get("TELEGRAM_FOUNDER_CHAT_ID")
    return token, chat


# --- I/O ------------------------------------------------------------------

def probe(url: str, timeout: float) -> tuple[bool, str]:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            code = resp.status
            return 200 <= code < 300, f"HTTP {code}"
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code}"
    except Exception as exc:  # DNS, TLS, timeout, refused
        return False, f"{type(exc).__name__}: {str(exc)[:160]}"


def probe_check(urls: list[str], timeout: float) -> tuple[bool, str]:
    details = []
    for url in urls:
        ok, detail = probe(url, timeout)
        if ok:
            return True, f"{url} {detail}"
        details.append(f"{url} -> {detail}")
    return False, "; ".join(details)


def send_telegram(token: str, chat_id: str, text: str, timeout: float = 30.0) -> dict:
    body = urllib.parse.urlencode({
        "chat_id": chat_id, "text": text, "disable_web_page_preview": "true",
    }).encode("utf-8")
    req = urllib.request.Request(f"{TELEGRAM_API}/bot{token}/sendMessage", data=body, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            return json.loads(exc.read().decode("utf-8"))
        except Exception:
            return {"ok": False, "description": f"HTTP {exc.code}"}
    except Exception as exc:
        # str(exc) can include the request URL, and the URL carries the token.
        return {"ok": False, "description": type(exc).__name__}


def compose(action: str, name: str, urls: list[str], detail: str, fails: int, prefix: str) -> str:
    stamp = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
    head = f"{prefix} " if prefix else ""
    if action == "alert":
        return (f"{head}DOWN: {name}\n{urls[0]}\n"
                f"Failed {fails} checks in a row (every 5 min). Last error: {detail}\n{stamp}")
    return f"{head}RECOVERED: {name} is back up.\n{urls[0]}\n{stamp}"


def run(env: dict[str, str]) -> int:
    checks = parse_checks(env.get("BACKBOND_UPTIME_CHECKS"))
    state_dir = Path(env.get("BACKBOND_UPTIME_STATE_DIR")
                     or Path(env.get("HOME", str(Path.home()))) / ".local/state/backbond-uptime")
    threshold = int(env.get("BACKBOND_UPTIME_THRESHOLD", "2"))
    timeout = float(env.get("BACKBOND_UPTIME_TIMEOUT", "15"))
    prefix = env.get("BACKBOND_UPTIME_PREFIX", "").strip()

    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / "state.json"
    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}

    rc = 0
    for name, urls in checks:
        ok, detail = probe_check(urls, timeout)
        new, action = step(state.get(name), ok, threshold)
        record = {"check": name, "ok": ok, "detail": detail,
                  "consecutive_failures": new["consecutive_failures"], "action": action}
        if action:
            token, chat = telegram_credentials(env)
            if not token or not chat:
                record["telegram"] = {"ok": False, "description": "missing TELEGRAM_BOT_TOKEN/TELEGRAM_FOUNDER_CHAT_ID"}
                resp = record["telegram"]
            else:
                text = compose(action, name, urls, detail, new["consecutive_failures"], prefix)
                resp = send_telegram(token, chat, text)
                record["telegram"] = {"ok": resp.get("ok"),
                                      "message_id": (resp.get("result") or {}).get("message_id"),
                                      "description": resp.get("description")}
            if not resp.get("ok"):
                rc = 1
                # Not sent: keep the failure count but stay un-alerted so the next run retries.
                if action == "alert":
                    new["alerted"] = False
                else:
                    new = dict(state.get(name) or {})
        new["last_checked"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        new["last_detail"] = detail
        state[name] = new
        print(json.dumps(record), flush=True)

    tmp = state_file.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, state_file)
    return rc


if __name__ == "__main__":
    sys.exit(run(dict(os.environ)))
