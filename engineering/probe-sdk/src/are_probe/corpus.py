"""Corpus loader. Reads pinned corpus pack files and verifies SHA before use.

R1 corpus packs ship inside the SDK at engineering/probe-sdk/corpus/. SHA
pins and upstream source commit pins live in lockfile.toml; this loader treats
missing pins or SHA mismatch as fatal.
"""
from __future__ import annotations

import hashlib
import json
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


class CorpusError(ValueError):
    pass


@dataclass(frozen=True)
class CorpusProbe:
    probe_id: str
    family: str
    rubric_point_id: str
    prompt: str
    expected_disposition: str
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class CorpusPack:
    pack_id: str
    path: Path
    sha256: str
    upstream_source_repo: str
    upstream_source_path: str
    upstream_source_commit_sha: str
    corpus_version: str
    probe_set_version: str
    rubric_point_ids: tuple[str, ...]
    probes: tuple[CorpusProbe, ...]


_COMMIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def _sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def assert_commit_sha(value: str, *, field: str = "upstream_source_commit_sha") -> None:
    if not isinstance(value, str) or not _COMMIT_SHA_RE.fullmatch(value):
        raise CorpusError(f"{field} must be a 40-character lowercase hex commit SHA")


def load_lockfile(lockfile_path: Path) -> dict:
    return tomllib.loads(lockfile_path.read_text())


def load_pack(sdk_root: Path, pack_id: str, lockfile: dict) -> CorpusPack:
    # Lockfile uses TOML keys without dots, so `refusal_robustness_v0.1`
    # is keyed as `refusal_robustness_v0_1`.
    toml_key = pack_id.replace(".", "_")
    entry = (lockfile.get("corpus", {}).get("packs", {}) or {}).get(toml_key)
    if not entry:
        raise CorpusError(f"corpus pack {pack_id!r} not declared in lockfile")
    source = entry.get("upstream_source") or {}
    for key in ("repo", "path", "commit_sha"):
        if not source.get(key):
            raise CorpusError(f"corpus pack {pack_id!r} missing upstream_source.{key}")
    assert_commit_sha(source["commit_sha"], field=f"{pack_id}.upstream_source.commit_sha")

    rel_path = entry["path"]
    abs_path = (sdk_root / rel_path).resolve()
    if not abs_path.is_file():
        raise CorpusError(f"corpus file missing: {abs_path}")
    actual = _sha256_file(abs_path)
    expected = entry["sha256"]
    if actual != expected:
        raise CorpusError(
            f"corpus SHA mismatch for {pack_id!r}: "
            f"expected {expected}, got {actual} "
            f"(file {abs_path})"
        )
    probes = []
    with abs_path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            probes.append(CorpusProbe(
                probe_id=row["probe_id"],
                family=row["family"],
                rubric_point_id=row["rubric_point_id"],
                prompt=row["prompt"],
                expected_disposition=row["expected_disposition"],
                metadata=dict(row.get("metadata") or {}),
            ))
    return CorpusPack(
        pack_id=pack_id,
        path=abs_path,
        sha256=actual,
        upstream_source_repo=source["repo"],
        upstream_source_path=source["path"],
        upstream_source_commit_sha=source["commit_sha"],
        corpus_version=entry["corpus_version"],
        probe_set_version=entry["probe_set_version"],
        rubric_point_ids=tuple(entry["rubric_point_ids"]),
        probes=tuple(probes),
    )
