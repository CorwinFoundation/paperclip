#!/usr/bin/env python3
"""Surgically add the optional voice-pack override to an installed intake."""
from __future__ import annotations

import argparse
import ast
import os
import re
import stat
from pathlib import Path

PACKS_BLOCK = '''DEFAULT_STARTER_PACK_ID = "{default}"
STARTER_PACK_IDS = frozenset({{
    "starter-100@1", "starter-100@2", "starter-100@3", "starter-100@4",
    "starter-voice-50@1",
}})
'''


def _write(target: Path, backup: Path, updated: str, source: str) -> None:
    info = target.lstat()
    if not stat.S_ISREG(info.st_mode) or target.is_symlink():
        raise RuntimeError(f"{target}: target must be a regular non-symlink file")
    compile(updated, str(target), "exec")
    backup.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(backup, flags, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(source.encode())
        handle.flush()
        os.fsync(handle.fileno())
    temporary = target.with_name(f".{target.name}.beaaa-39663-{os.getpid()}")
    temporary.write_text(updated)
    temporary.chmod(stat.S_IMODE(info.st_mode))
    os.replace(temporary, target)


def _default(source: str) -> str:
    values = re.findall(r'["\'](starter-100@(?:3|4))["\']', source)
    if not values or len(set(values)) != 1:
        raise RuntimeError("expected one consistent installed starter-100@3 or @4 default")
    return values[0]


def patch_app(source: str, installed_default: str) -> tuple[str, bool, str]:
    if "starter-voice-50@1" in source and "pack_id: str | None" in source:
        default = re.search(r'DEFAULT_STARTER_PACK_ID = ["\']([^"\']+)', source)
        if not default:
            raise RuntimeError("patched app is missing DEFAULT_STARTER_PACK_ID")
        return source, False, default.group(1)
    default = installed_default
    anchors = {
        "public partner": 'PUBLIC_PARTNER = "public"\n',
        "last connection field": "    vapi_assistant_id: str | None = None\n",
        "validator": "    def consent_and_transport(self):\n",
        "reserve argument": "            improvement_consent=body.consent_improvement)\n",
    }
    for label, anchor in anchors.items():
        if source.count(anchor) != 1:
            raise RuntimeError(f"app {label} anchor mismatch")
    updated = source.replace(
        anchors["public partner"], anchors["public partner"] + PACKS_BLOCK.format(default=default), 1)
    updated = updated.replace(
        anchors["last connection field"],
        anchors["last connection field"] + "    pack_id: str | None = Field(default=None, max_length=64)\n", 1)
    updated = updated.replace(
        anchors["validator"], anchors["validator"] +
        '        if self.pack_id is not None and self.pack_id not in STARTER_PACK_IDS:\n'
        '            raise ValueError("pack_id must be a registered Starter pack")\n', 1)
    updated = updated.replace(
        anchors["reserve argument"],
        "            improvement_consent=body.consent_improvement, pack_version=body.pack_id)\n", 1)
    ast.parse(updated)
    return updated, True, default


def patch_service(source: str, default: str) -> tuple[str, bool]:
    if "pack_version: str | None = None" in source and 'data.get("pack_id") or' in source:
        if default not in source:
            raise RuntimeError("patched service no longer contains preserved default")
        return source, False
    reserve = f'                terms_version: str | None = None, improvement_consent: bool = False) -> dict:\n'
    stored = f'               "{default}", created))\n'
    payload = f'            "pack_id": "{default}", "include_result_rows": True,\n'
    for label, anchor in (("reserve signature", reserve), ("stored default", stored), ("payload default", payload)):
        if source.count(anchor) != 1:
            raise RuntimeError(f"qa_service {label} anchor mismatch")
    updated = source.replace(
        reserve, reserve.replace(") -> dict:", ", pack_version: str | None = None) -> dict:"), 1)
    updated = updated.replace(stored, f'               pack_version or "{default}", created))\n', 1)
    updated = updated.replace(
        payload, f'            "pack_id": data.get("pack_id") or "{default}", "include_result_rows": True,\n', 1)
    ast.parse(updated)
    return updated, True


def patch_pair(app: Path, service: Path, backup_dir: Path) -> bool:
    app_source, service_source = app.read_text(), service.read_text()
    default = (_default(service_source) if "starter-voice-50@1" not in app_source
               else re.search(r'DEFAULT_STARTER_PACK_ID = ["\']([^"\']+)', app_source).group(1))
    app_updated, app_changed, default = patch_app(app_source, default)
    service_updated, service_changed = patch_service(service_source, default)
    if app_changed != service_changed:
        raise RuntimeError("installed pair is partially patched; refusing")
    if not app_changed:
        return False
    compile(app_updated, str(app), "exec")
    compile(service_updated, str(service), "exec")
    _write(app, backup_dir / "app.py", app_updated, app_source)
    try:
        _write(service, backup_dir / "qa_service.py", service_updated, service_source)
    except BaseException:
        app.write_text(app_source)
        raise
    return True


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--server-dir", type=Path, required=True)
    parser.add_argument("--backup-dir", type=Path, required=True)
    args = parser.parse_args()
    changed = patch_pair(args.server_dir / "app.py", args.server_dir / "qa_service.py", args.backup_dir)
    print(f"changed={str(changed).lower()} server_dir={args.server_dir}")


if __name__ == "__main__":
    main()
