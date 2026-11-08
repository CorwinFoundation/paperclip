#!/usr/bin/env python3
"""Read-only diagnostics for the running scan-intake service.

This helper is intended to be installed root-owned on AriClaw.  It reads only
the service process environment and credential metadata.  It never prints a
token, token digest, credential path, customer data, or raw environment.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from typing import Callable, Mapping


SERVICE = "backbond-scan-intake.service"
REVISION_KEYS = (
    "BACKBOND_INTAKE_SOURCE_REVISION",
    "BACKBOND_INTAKE_REVISION",
    "SCANNER_SOURCE_REVISION",
    "IMAGE_REVISION",
    "GIT_COMMIT",
    "COMMIT_SHA",
)
# Candidate locations proposed for exact review; no runtime-controlled path grants.
APPROVED_FILES = frozenset({
    "/run/credentials/backbond-scan-intake.service/qa-scanner-tokens.json",
    "/run/credentials/backbond-scan-intake.service/default.json",
    "/var/lib/backbond-qa/partner-code-tenants.json",
})
MAX_INPUT = 1024 * 1024
SAFE_REVISION = re.compile(r"^[0-9a-f]{40}$")


def parse_process_environ(raw: bytes) -> dict[str, str]:
    """Decode /proc/*/environ without logging malformed or sensitive values."""
    result: dict[str, str] = {}
    for entry in raw.split(b"\0"):
        if b"=" not in entry:
            continue
        key, value = entry.split(b"=", 1)
        try:
            result[key.decode("utf-8")] = value.decode("utf-8")
        except UnicodeDecodeError:
            continue
    return result


def _safe_revision(env: Mapping[str, str]) -> tuple[str | None, str]:
    for key in REVISION_KEYS:
        value = env.get(key, "").strip()
        if value and SAFE_REVISION.fullmatch(value):
            return value, key
    return None, "process_environment_metadata_missing"


def _parse_expiry(value: object) -> datetime | None:
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc)
        except (ValueError, OverflowError, OSError):
            return None
    if isinstance(value, str) and value.strip():
        text = value.strip()
        try:
            parsed = datetime.fromtimestamp(float(text), tz=timezone.utc)
        except (ValueError, OverflowError, OSError):
            try:
                parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            except ValueError:
                return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


def classify_credential(value: object, *, now: datetime) -> dict[str, object]:
    """Return local metadata for one credential candidate, without acceptance claims."""
    if isinstance(value, str):
        token = value
        metadata: Mapping[str, object] = {}
    elif isinstance(value, dict):
        token = value.get("token", "")
        metadata = value
    else:
        token = ""
        metadata = {}

    configured = isinstance(token, str) and bool(token.strip())
    status = metadata.get("status") if isinstance(metadata.get("status"), str) else None
    expiry = _parse_expiry(metadata.get("expires_at"))
    if not configured:
        effective_status = "unconfigured"
    elif status in {"revoked", "inactive", "disabled"}:
        effective_status = str(status)
    elif expiry is None:
        effective_status = "configured"
    elif expiry <= now:
        effective_status = "expired"
    else:
        effective_status = "active"

    result: dict[str, object] = {
        "configured": configured,
        "authority": "local_metadata_only",
        "status": effective_status,
        "expiry_relation": (
            "unknown" if expiry is None else "expired" if expiry <= now else "not_expired"
        ),
    }
    if expiry is not None:
        result["expires_at"] = expiry.astimezone(timezone.utc).isoformat()
    return result


def _json_file(read: Callable[[Path], str | None], path: Path) -> object | None:
    text = read(path)
    if text is None:
        return None
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return None


def credential_candidates(
    env: Mapping[str, str],
    read: Callable[[Path], str | None],
    *,
    now: datetime,
) -> list[dict[str, object]]:
    """Inventory anonymous candidates; request-lane selection is not observed."""
    mapping: dict[str, object] = {}
    try:
        loaded = json.loads(env.get("QA_SCANNER_TOKENS_JSON", "{}"))
        if isinstance(loaded, dict):
            mapping.update(loaded)
    except ValueError:
        pass

    directory = env.get("CREDENTIALS_DIRECTORY")
    if directory:
        packet = _json_file(read, Path(directory) / "qa-scanner-tokens.json")
        if isinstance(packet, dict):
            mapping = packet

    dynamic = _json_file(read, Path("/var/lib/backbond-qa/partner-code-tenants.json"))
    if isinstance(dynamic, dict):
        for tenant, value in dynamic.items():
            mapping.setdefault(tenant, value)

    default: object = env.get("QA_SCANNER_TOKEN", "")
    credential_path = env.get("QA_SCANNER_CREDENTIAL_PATH")
    if credential_path:
        dedicated = _json_file(read, Path(credential_path))
        default = dedicated if dedicated is not None else {"token": ""}

    selected: list[dict[str, object]] = []
    for index, value in enumerate([default, *mapping.values()]):
        row = {"selection": "default" if index == 0 else f"tenant_slot_{index}",
               **classify_credential(value, now=now)}
        selected.append(row)
    return selected


def _read(path: Path) -> str | None:
    """Open approved files beneath root-controlled directories without symlinks.

    Walk by directory descriptors to avoid check/open races. Refuse device,
    FIFO, directory and socket targets before reading; never block on a FIFO.
    """
    if str(path) not in APPROVED_FILES:
        return None
    descriptors: list[int] = []
    try:
        directory = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        descriptors.append(directory)
        root = os.fstat(directory)
        if root.st_uid != 0 or root.st_mode & 0o022:
            return None
        for component in path.parts[1:-1]:
            directory = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                dir_fd=directory)
            descriptors.append(directory)
            info = os.fstat(directory)
            if info.st_uid != 0 or info.st_mode & 0o022:
                return None
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                     dir_fd=directory)
        descriptors.append(fd)
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0
                or info.st_mode & 0o077 or info.st_nlink != 1
                or info.st_size > MAX_INPUT):
            return None
        raw = bytearray()
        while len(raw) <= MAX_INPUT:
            chunk = os.read(fd, min(65536, MAX_INPUT + 1 - len(raw)))
            if not chunk:
                break
            raw.extend(chunk)
        return raw.decode("utf-8") if len(raw) <= MAX_INPUT else None
    except (OSError, UnicodeError, ValueError):
        return None
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _service_properties() -> dict[str, str]:
    output = subprocess.check_output(
        ["systemctl", "show", SERVICE,
         "--property=MainPID,ActiveState,SubState,FragmentPath,ExecMainStartTimestamp"],
        text=True,
        stderr=subprocess.DEVNULL,
    )
    properties: dict[str, str] = {}
    for line in output.splitlines():
        key, separator, value = line.partition("=")
        if separator:
            properties[key] = value
    return properties


def main() -> None:
    if os.geteuid() != 0:
        raise SystemExit("run as root on AriClaw")
    properties = _service_properties()
    try:
        pid = int(properties.get("MainPID", "0"))
    except ValueError:
        pid = 0
    if pid <= 1:
        raise SystemExit("intake service has no running process")

    env = parse_process_environ(Path(f"/proc/{pid}/environ").read_bytes())
    revision, revision_source = _safe_revision(env)
    now = datetime.now(timezone.utc)
    credentials = credential_candidates(env, _read, now=now)
    if _service_properties().get("MainPID") != str(pid):
        raise RuntimeError("service process changed")
    print(json.dumps({
        "service": SERVICE,
        "active_state": properties.get("ActiveState"),
        "sub_state": properties.get("SubState"),
        "source": {
            "revision": None,
            "verification": "unknown",
            "reason": "running_source_provenance_not_established",
            "unverified_environment_metadata": {
                "revision": revision, "key": revision_source,
                "verification": "unverified",
            },
            "unit_file_present": bool(properties.get("FragmentPath")),
        },
        "credential_candidate_inventory": credentials,
        "selection_scope": "default_and_anonymous_mapping_candidates",
        "request_lane_selection": "not_observed",
        "scanner_acceptance": "not_inspected",
    }, sort_keys=True))


if __name__ == "__main__":
    if len(sys.argv) != 1:
        raise SystemExit("arguments are not supported")
    try:
        main()
    except Exception:
        print(json.dumps({"error": "runtime_inspection_failed"}))
        raise SystemExit(1)
