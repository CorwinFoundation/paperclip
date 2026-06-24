#!/usr/bin/env python3
"""Compute the SDK build_hash and patch it into lockfile.toml.

Build hash = sha256(canonical_json(pinned)) where `pinned` is the
deterministic dict of every version-bearing field in the lockfile plus
the per-file sha256 of every shipped corpus pack and every src/are_probe
module file. This is the single reproducibility anchor referenced by the
R1 acceptance test (same target + same build_hash + same seed
=> byte-identical are_total + pillar subscores).
"""
from __future__ import annotations

import hashlib
import json
import sys
import tomllib
from pathlib import Path

SDK_ROOT = Path(__file__).resolve().parents[1]
LOCKFILE = SDK_ROOT / "lockfile.toml"


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _module_shas() -> dict[str, str]:
    out: dict[str, str] = {}
    for p in sorted((SDK_ROOT / "src" / "are_probe").rglob("*.py")):
        rel = p.relative_to(SDK_ROOT).as_posix()
        out[rel] = _sha256(p)
    return out


def _corpus_shas() -> dict[str, str]:
    out: dict[str, str] = {}
    for p in sorted((SDK_ROOT / "corpus").glob("*.jsonl")):
        rel = p.relative_to(SDK_ROOT).as_posix()
        out[rel] = _sha256(p)
    return out


def compute() -> str:
    lock = tomllib.loads(LOCKFILE.read_text())
    deps = lock.get("deps") or {}
    scanner = lock.get("scanner_master") or {}
    signing = lock.get("signing") or {}
    corpus_packs = lock.get("corpus", {}).get("packs", {}) or {}
    corpus_sources = {
        pack_id: (entry.get("upstream_source") or {})
        for pack_id, entry in sorted(corpus_packs.items())
    }
    pinned = {
        "probe_sdk_version": lock.get("probe_sdk_version"),
        "probe_sdk_release": lock.get("probe_sdk_release"),
        "deps": deps,
        "scanner_master_expected_version": scanner.get("expected_version"),
        "scanner_master_contract_version":
            (lock.get("scanner_master", {}).get("contract") or {}).get(
                "contract_version"
            ),
        "signing_algorithm": signing.get("algorithm"),
        "signing_key_id": signing.get("key_id"),
        "module_shas": _module_shas(),
        "corpus_shas": _corpus_shas(),
        "corpus_sources": corpus_sources,
    }
    canonical = json.dumps(pinned, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def main() -> int:
    build_hash = compute()
    text = LOCKFILE.read_text()
    if "build_hash = " in text:
        new = []
        for line in text.splitlines():
            if line.startswith("build_hash = "):
                new.append(f'build_hash = "{build_hash}"')
            else:
                new.append(line)
        LOCKFILE.write_text("\n".join(new) + "\n")
    print(build_hash)
    return 0


if __name__ == "__main__":
    sys.exit(main())
