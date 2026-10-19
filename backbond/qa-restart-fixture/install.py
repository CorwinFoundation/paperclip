#!/usr/bin/env python3
"""BEAAA-42388: install, inspect or remove the QA restart fixture.

Run as root by ``scanner-deploy qa-restart-fixture <action>`` from a copy
extracted out of the live image. Actions:

* ``install [--delay-seconds N]`` — idempotent. Creates the dedicated
  ``qa-38869-callback`` secret once (0600, reused on rerun), installs the
  fixture and its hardened systemd unit, publishes it through an nginx
  snippet (``nginx -t`` gated, restored on failure), and adds only the
  ``qa-38869-callback`` ref to the scanner webhook secret maps while keeping
  every other configured ref byte-for-byte. The scanner container reads the
  new ref at its next activation.
* ``status`` / ``receipts`` — counts and sanitized receipts only.
* ``remove`` — removes this fixture, its nginx snippet, its secret and its
  ref; other refs and all receipts / scan evidence are retained.

Nothing here prints a secret, token, probe body or corpus content.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import shlex
import socket
import stat
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

REF = "qa-38869-callback"
SECRET_VARS = ("ARE_WEBHOOK_SECRETS_JSON", "ARE_SCAN_WEBHOOK_SECRETS_JSON")
SERVICE = "backbond-qa-restart-fixture.service"
#: 127.0.0.1:8788-8791 already belong to other host-local services on the
#: scanner server; 8791 was the first choice and collided (BEAAA-42388).
PORT = int(os.environ.get("QA_FIXTURE_PORT") or 18791)
SERVICE_NAME = "backbond-qa-restart-fixture"
DEFAULT_DELAY_SECONDS = 5.0
MAX_DELAY_SECONDS = 25.0
SITE_ANCHOR = "include /etc/nginx/snippets/backbond-stage-b-inbound.conf;"
RECEIPT_KEYS = (
    "scan_id", "terminal_status", "first_received_at", "last_received_at",
    "delivery_count", "signature_valid", "delivery_ids",
)

HERE = Path(__file__).resolve().parent


def _path(name: str, default: str) -> Path:
    return Path(os.environ.get(name) or default)


FIXTURE_DIR = _path("QA_FIXTURE_DIR", "/opt/backbond-qa-restart-fixture")
SECRET_FILE = _path("QA_FIXTURE_SECRET_FILE", "/etc/backbond/qa-38869-callback.secret")
UNIT_FILE = _path("QA_FIXTURE_UNIT_FILE", f"/etc/systemd/system/{SERVICE}")
SNIPPET = _path("QA_FIXTURE_NGINX_SNIPPET", "/etc/nginx/snippets/backbond-qa-restart-fixture.conf")
SITE = _path("QA_FIXTURE_NGINX_SITE", "/etc/nginx/sites-available/scanner.backbond.ai")
ENV_FILE = _path("SCANNER_EDGE_ENV", "/etc/backbond/scanner-edge.env")
RECEIPTS = _path("QA_FIXTURE_RECEIPTS", "/var/lib/backbond-qa-restart-fixture/receipts.json")
SYSTEMCTL = shlex.split(os.environ.get("SCANNER_SYSTEMCTL") or "systemctl")
NGINX = shlex.split(os.environ.get("QA_FIXTURE_NGINX") or "nginx")
HEALTH_URL = os.environ.get("QA_FIXTURE_HEALTH_URL") or f"http://127.0.0.1:{PORT}/qa-restart-fixture/healthz"
HEALTH_WAIT_SECONDS = float(os.environ.get("QA_FIXTURE_HEALTH_WAIT_SECONDS") or 15)


def die(message: str) -> None:
    raise SystemExit(f"[scanner-deploy] ERROR: {message}")


def log(message: str) -> None:
    print(f"[scanner-deploy] {message}")


def run(cmd: list[str], *, check: bool = True) -> int:
    proc = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    if check and proc.returncode != 0:
        detail = (proc.stderr.strip().splitlines() or [""])[-1][:200]
        die(f"{' '.join(cmd[:3])} failed (exit {proc.returncode}): {detail}")
    return proc.returncode


def read_text(path: Path) -> str | None:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return None
    except OSError:
        die(f"refusing {path}: not a plain file")
    with os.fdopen(fd, "r") as fh:
        if not stat.S_ISREG(os.fstat(fh.fileno()).st_mode):
            die(f"refusing {path}: not a regular file")
        return fh.read()


def write_atomic(path: Path, content: str, mode: int) -> bool:
    """Write ``content`` atomically; returns False when already identical."""
    if read_text(path) == content:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        st = path.stat()
        owner = (st.st_uid, st.st_gid)
    except FileNotFoundError:
        owner = None
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w") as fh:
            os.fchmod(fh.fileno(), mode)
            if owner is not None and os.geteuid() == 0:
                os.fchown(fh.fileno(), *owner)
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise
    dfd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)
    return True


def ensure_secret() -> tuple[str, bool]:
    existing = read_text(SECRET_FILE)
    if existing is not None:
        if stat.S_IMODE(SECRET_FILE.stat().st_mode) != 0o600:
            die(f"{SECRET_FILE} must be mode 0600")
        value = existing.strip()
        if len(value) < 32:
            die(f"{SECRET_FILE} is too short; remove it to regenerate")
        return value, False
    SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
    value = secrets.token_hex(32)
    fd = os.open(SECRET_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        os.write(fd, (value + "\n").encode())
        os.fsync(fd)
    finally:
        os.close(fd)
    return value, True


def unit_text(delay: float) -> str:
    return f"""# BEAAA-42388 QA restart fixture. Managed by scanner-deploy qa-restart-fixture.
[Unit]
Description=BackBond QA restart test target and signed callback receiver (BEAAA-42388)
After=network.target

[Service]
Type=simple
DynamicUser=yes
StateDirectory=backbond-qa-restart-fixture
StateDirectoryMode=0700
UMask=0077
LoadCredential=callback-secret:{SECRET_FILE}
Environment=FIXTURE_HOST=127.0.0.1
Environment=FIXTURE_PORT={PORT}
Environment=FIXTURE_DELAY_SECONDS={delay:g}
ExecStart=/usr/bin/python3 {FIXTURE_DIR}/fixture.py
Restart=on-failure
RestartSec=2
MemoryMax=128M
TasksMax=64
NoNewPrivileges=true
PrivateTmp=true
PrivateDevices=true
ProtectSystem=strict
ProtectHome=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
CapabilityBoundingSet=
LockPersonality=true

[Install]
WantedBy=multi-user.target
"""


SNIPPET_TEXT = f"""# BEAAA-42388 QA restart fixture. Managed by scanner-deploy qa-restart-fixture.
location ^~ /qa-restart-fixture/v1/ {{
    limit_except POST {{ deny all; }}
    client_max_body_size 1m;
    proxy_pass http://127.0.0.1:{PORT};
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_read_timeout 40s;
    proxy_connect_timeout 5s;
    access_log off;
}}
"""


def include_line() -> str:
    return f"    include {SNIPPET};"


def site_with_include(text: str) -> str:
    if include_line().strip() in (line.strip() for line in text.splitlines()):
        return text
    lines = text.splitlines(keepends=True)
    anchors = [i for i, line in enumerate(lines) if line.strip() == SITE_ANCHOR]
    if len(anchors) != 1:
        die(f"nginx site lacks exactly one anchor '{SITE_ANCHOR}'; refusing to guess")
    lines.insert(anchors[0] + 1, include_line() + "\n")
    return "".join(lines)


def site_without_include(text: str) -> str:
    target = include_line().strip()
    return "".join(line for line in text.splitlines(keepends=True) if line.strip() != target)


def apply_nginx(site_text: str, snippet_text: str | None) -> bool:
    """Apply site + snippet, gated on ``nginx -t``; restore both on failure."""
    old_site, old_snippet = read_text(SITE), read_text(SNIPPET)
    if old_site is None:
        die(f"nginx site {SITE} is missing")
    if old_site == site_text and old_snippet == snippet_text:
        return False
    if snippet_text is not None:
        write_atomic(SNIPPET, snippet_text, 0o644)
    write_atomic(SITE, site_text, 0o644)
    if run([*NGINX, "-t"], check=False) != 0:
        write_atomic(SITE, old_site, 0o644)
        if old_snippet is None:
            SNIPPET.unlink(missing_ok=True)
        else:
            write_atomic(SNIPPET, old_snippet, 0o644)
        die("nginx -t rejected the fixture config; previous config restored")
    if snippet_text is None:
        SNIPPET.unlink(missing_ok=True)
    run([*SYSTEMCTL, "reload", "nginx"])
    return True


def update_env_refs(secret: str | None) -> dict[str, str]:
    """Set (or with ``None`` drop) only ``REF`` in each webhook secret map."""
    text = read_text(ENV_FILE)
    if text is None:
        die(f"scanner env file {ENV_FILE} is missing")
    lines = text.splitlines(keepends=True)
    outcome: dict[str, str] = {}
    for var in SECRET_VARS:
        idx = [i for i, line in enumerate(lines) if line.startswith(f"{var}=")]
        if len(idx) > 1:
            die(f"duplicate {var} assignment in {ENV_FILE}")
        if idx:
            raw = lines[idx[0]].rstrip("\n")[len(var) + 1:]
            try:
                refs = json.loads(raw) if raw else {}
            except ValueError:
                die(f"{var} is not valid JSON; refusing to rewrite it")
            if not isinstance(refs, dict):
                die(f"{var} is not a JSON object; refusing to rewrite it")
        else:
            refs = {}
        others = len([k for k in refs if k != REF])
        if secret is None:
            if REF not in refs:
                outcome[var] = f"absent preserved_refs={others}"
                continue
            refs.pop(REF)
            state = "removed"
        elif refs.get(REF) == secret:
            outcome[var] = f"unchanged preserved_refs={others}"
            continue
        else:
            state = "updated" if REF in refs else "added"
            refs[REF] = secret
        line = f"{var}={json.dumps(refs, separators=(',', ':'))}\n"
        if idx and not refs:
            del lines[idx[0]]
        elif idx:
            lines[idx[0]] = line
        else:
            if lines and not lines[-1].endswith("\n"):
                lines[-1] += "\n"
            lines.append(line)
        outcome[var] = f"{state} preserved_refs={others}"
    write_atomic(ENV_FILE, "".join(lines), 0o600)
    return outcome


def env_ref_state() -> dict[str, bool]:
    text = read_text(ENV_FILE) or ""
    state = {}
    for var in SECRET_VARS:
        refs: object = {}
        for line in text.splitlines():
            if line.startswith(f"{var}="):
                try:
                    refs = json.loads(line[len(var) + 1:])
                except ValueError:
                    refs = {}
        state[var] = isinstance(refs, dict) and REF in refs
    return state


def sanitized_receipts() -> dict[str, object]:
    try:
        data = json.loads(RECEIPTS.read_text())
    except (OSError, ValueError):
        data = {}
    raw = data.get("receipts") if isinstance(data, dict) else None
    receipts = {}
    for scan_id, receipt in (raw or {}).items():
        if isinstance(receipt, dict):
            receipts[str(scan_id)] = {k: receipt[k] for k in RECEIPT_KEYS if k in receipt}
    invalid = data.get("invalid_signature_deliveries", 0) if isinstance(data, dict) else 0
    return {
        "schema_version": "backbond-qa-restart-callback-receipts@1",
        "secret_ref": REF,
        "invalid_signature_deliveries": invalid if isinstance(invalid, int) else 0,
        "receipts": receipts,
    }


def health() -> str:
    """``ok`` only when *this* fixture answers; another listener is ``foreign``."""
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=5) as response:
            if response.status != 200:
                return f"http_{response.status}"
            try:
                doc = json.loads(response.read(4096))
            except ValueError:
                doc = None
            ok = isinstance(doc, dict) and doc.get("service") == SERVICE_NAME
            return "ok" if ok else "foreign"
    except Exception as exc:  # noqa: BLE001 - status only
        return f"down:{type(exc).__name__}"


def service_active() -> bool:
    return run([*SYSTEMCTL, "is-active", "--quiet", SERVICE], check=False) == 0


def port_in_use() -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", PORT))
    except OSError:
        return True
    finally:
        sock.close()
    return False


def wait_healthy() -> str:
    deadline = time.monotonic() + HEALTH_WAIT_SECONDS
    state = health()
    while state != "ok" and time.monotonic() < deadline:
        time.sleep(0.5)
        state = health()
    return state


def install(delay: float) -> None:
    if not 0 <= delay <= MAX_DELAY_SECONDS:
        die(f"--delay-seconds must be within [0, {MAX_DELAY_SECONDS:g}]")
    # Never publish another service: refuse when a foreign listener holds the port.
    if not service_active() and port_in_use():
        die(f"127.0.0.1:{PORT} is already used by another listener; nothing installed")
    secret, created = ensure_secret()
    log(f"secret_ref {REF}: {'created' if created else 'reused'} ({SECRET_FILE}, 0600)")
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    code_changed = write_atomic(FIXTURE_DIR / "fixture.py", (HERE / "fixture.py").read_text(), 0o644)
    unit_changed = write_atomic(UNIT_FILE, unit_text(delay), 0o644)
    if unit_changed:
        run([*SYSTEMCTL, "daemon-reload"])
    run([*SYSTEMCTL, "enable", SERVICE])
    run([*SYSTEMCTL, "restart" if (code_changed or unit_changed) else "start", SERVICE])
    log(f"fixture unit {SERVICE}: {'restarted' if code_changed or unit_changed else 'unchanged'} delay_seconds={delay:g}")
    state = wait_healthy()
    if state != "ok":
        run([*SYSTEMCTL, "disable", "--now", SERVICE], check=False)
        die(f"fixture health={state} on 127.0.0.1:{PORT}; unit stopped, nginx and webhook refs left unchanged")
    log(f"fixture health=ok port={PORT}")
    site = read_text(SITE)
    if site is None:
        die(f"nginx site {SITE} is missing")
    nginx_changed = apply_nginx(site_with_include(site), SNIPPET_TEXT)
    log(f"nginx /qa-restart-fixture/v1/: {'reloaded' if nginx_changed else 'unchanged'}")
    for var, state in update_env_refs(secret).items():
        log(f"{var}: {REF} {state}")
    log("scanner container reads the ref at its next activation; other refs untouched")


def remove() -> None:
    run([*SYSTEMCTL, "disable", "--now", SERVICE], check=False)
    unit_removed = UNIT_FILE.exists()
    UNIT_FILE.unlink(missing_ok=True)
    if unit_removed:
        run([*SYSTEMCTL, "daemon-reload"])
    site = read_text(SITE)
    if site is not None:
        apply_nginx(site_without_include(site), None)
    for var, state in update_env_refs(None).items():
        log(f"{var}: {REF} {state}")
    (FIXTURE_DIR / "fixture.py").unlink(missing_ok=True)
    try:
        FIXTURE_DIR.rmdir()
    except OSError:
        pass
    SECRET_FILE.unlink(missing_ok=True)
    log(f"removed {SERVICE}, nginx snippet, {REF} ref and secret; receipts retained at {RECEIPTS}")


def status() -> None:
    active = service_active()
    print(f"service={'active' if active else 'inactive'}")
    print(f"health={health()}")
    print(f"secret_file={'present' if SECRET_FILE.exists() else 'absent'}")
    site = read_text(SITE) or ""
    print(f"nginx_include={'present' if include_line().strip() in site else 'absent'}")
    for var, present in env_ref_state().items():
        print(f"env_{var}_{REF}={'present' if present else 'absent'}")
    doc = sanitized_receipts()
    print(f"receipts={len(doc['receipts'])} invalid_signature_deliveries={doc['invalid_signature_deliveries']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scanner-deploy qa-restart-fixture")
    sub = parser.add_subparsers(dest="action", required=True)
    inst = sub.add_parser("install")
    inst.add_argument("--delay-seconds", type=float, default=DEFAULT_DELAY_SECONDS)
    sub.add_parser("status")
    sub.add_parser("receipts")
    sub.add_parser("remove")
    args = parser.parse_args(argv)
    if args.action == "install":
        install(args.delay_seconds)
    elif args.action == "remove":
        remove()
    elif args.action == "status":
        status()
    else:
        print(json.dumps(sanitized_receipts(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
