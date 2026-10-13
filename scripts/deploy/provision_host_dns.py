#!/usr/bin/env python3
"""Fetch current IANA anchors off-box; install them on an egress-sealed host."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import subprocess
import tempfile
import urllib.request
import xml.etree.ElementTree as ET

IANA_URL = "https://data.iana.org/root-anchors/root-anchors.xml"
ANCHORS = Path("/etc/dnssec-trust-anchors.d/iana-root.positive")
MAX_BYTES = 128 * 1024


def render_anchors(xml: bytes, now: datetime | None = None) -> bytes:
    if len(xml) > MAX_BYTES:
        raise ValueError("IANA anchor file exceeds size limit")
    root = ET.fromstring(xml)
    if root.tag != "TrustAnchor" or root.findtext("Zone") != ".":
        raise ValueError("expected IANA root-zone TrustAnchor")
    now = now or datetime.now(timezone.utc)
    records = set()
    for key in root.findall("KeyDigest"):
        start = datetime.fromisoformat(key.attrib["validFrom"].replace("Z", "+00:00"))
        end_text = key.get("validUntil")
        end = datetime.fromisoformat(end_text.replace("Z", "+00:00")) if end_text else None
        if start.tzinfo is None or (end is not None and end.tzinfo is None):
            raise ValueError("anchor validity timestamps must include timezone")
        if start > now or (end is not None and end <= now):
            continue
        tag = int(key.findtext("KeyTag", ""))
        algorithm = int(key.findtext("Algorithm", ""))
        digest_type = int(key.findtext("DigestType", ""))
        digest = (key.findtext("Digest") or "").strip().upper()
        # DS SHA-256/SHA-384, with the currently supported IANA signing algorithms.
        lengths = {2: 64, 4: 96}
        if not 0 <= tag <= 65535 or algorithm not in {8, 13, 14, 15, 16}:
            raise ValueError("unsupported root signing key")
        if digest_type not in lengths or not re.fullmatch(
            rf"[0-9A-F]{{{lengths[digest_type]}}}", digest
        ):
            raise ValueError("invalid root DS digest")
        records.add(f". IN DS {tag} {algorithm} {digest_type} {digest}")
    if not records:
        raise ValueError("no currently valid root anchors; refusing replacement")
    return ("\n".join(sorted(records)) + "\n").encode("ascii")


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".iana-root-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.chmod(name, 0o644)
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def install_anchors(data: bytes, path: Path = ANCHORS) -> bool:
    if path.is_symlink():
        raise ValueError("refusing symlink at managed trust-anchor path")
    # systemd 255 can crash on duplicate root DS records across files. Never
    # install a second source, and never silently remove an operator's anchors.
    directories = [path.parent]
    if path == ANCHORS:
        directories += [Path("/run/dnssec-trust-anchors.d"), Path("/usr/lib/dnssec-trust-anchors.d")]
    for directory in directories:
        for other in directory.glob("*.positive"):
            if other == path or (directory != path.parent and (path.parent / other.name).exists()):
                continue
            if re.search(r"^\s*\.\s+IN\s+(DS|DNSKEY)\s", other.read_text(), re.MULTILINE):
                raise ValueError(f"root anchors already configured in {other}; migrate to {path} before install")
    previous = path.read_bytes() if path.exists() else None
    if previous == data:
        return False
    atomic_write(path, data)
    try:
        subprocess.run(["systemctl", "restart", "systemd-resolved"], check=True, timeout=30)
        subprocess.run(["resolvectl", "query", ".", "--type=DNSKEY"], check=True, timeout=30)
    except Exception:
        if previous is None:
            path.unlink()
        else:
            atomic_write(path, previous)
        subprocess.run(["systemctl", "restart", "systemd-resolved"], check=True, timeout=30)
        raise
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    fetch = commands.add_parser("fetch", help="run off-box; verified HTTPS to IANA")
    fetch.add_argument("--output", type=Path, required=True)
    install = commands.add_parser("install", help="run as root on the host; no network fetch")
    install.add_argument("--source", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "fetch":
        with urllib.request.urlopen(IANA_URL, timeout=20) as response:
            if response.geturl() != IANA_URL:
                raise ValueError("unexpected IANA URL redirect")
            xml = response.read(MAX_BYTES + 1)
        render_anchors(xml)  # Validate before replacing any local payload.
        atomic_write(args.output, xml)
        print(f"Validated current IANA root anchors: {args.output}")
    else:
        if os.geteuid() != 0:
            parser.error("install requires root")
        # Parsing completes before the managed host file or resolver is touched.
        changed = install_anchors(render_anchors(args.source.read_bytes()))
        print("Installed current IANA root anchors" if changed else "Root anchors already current")


if __name__ == "__main__":
    main()
