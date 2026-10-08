#!/usr/bin/env python3
"""Rebuild the pinned JailbreakBench conversation-case source bundle."""
from __future__ import annotations

import csv
import hashlib
import json
import re
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "third_party" / "oss-imports" / "jailbreakbench"
COMMIT = "886acc352a31533ffbcf4ef22c744658688086fc"
BASE = f"https://huggingface.co/datasets/JailbreakBench/JBB-Behaviors/resolve/{COMMIT}"
FILES = {
    "LICENSE": ("LICENSE", "7e1ae8f3676f39ff6e62af1743385c176a9476b1157f35bb9d05b4e6c65b7d94"),
    "harmful.csv": ("data/harmful-behaviors.csv", "4a8ec6832056b631eb092dccc60d37a61c3d441268268888b3d006288afeffa1"),
    "benign.csv": ("data/benign-behaviors.csv", "3cda234d21a991fa309bbfea4b6d9dae31ccdf8e9d452424b6a983e4fdc33468"),
}
PROHIBITED = re.compile(
    r"(?:minor|child|underage).{0,40}(?:sex|sexual|porn)|"
    r"(?:sex|sexual|porn).{0,40}(?:minor|child|underage)", re.I,
)


def fetch(path: str, expected: str) -> bytes:
    with urllib.request.urlopen(f"{BASE}/{path}", timeout=30) as response:
        data = response.read()
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected:
        raise RuntimeError(f"sha256 mismatch for {path}: {actual}")
    return data


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    blobs = {name: fetch(path, digest) for name, (path, digest) in FILES.items()}
    (OUT / "LICENSE").write_bytes(blobs["LICENSE"])
    kept: list[dict[str, str]] = []
    dropped: dict[str, int] = {}
    for split in ("harmful", "benign"):
        rows = list(csv.DictReader(blobs[f"{split}.csv"].decode("utf-8").splitlines()))
        rejected = [row for row in rows if PROHIBITED.search(row["Goal"])]
        dropped[f"{split}:prohibited_minor_sexual_content"] = len(rejected)
        for row in rows:
            if row not in rejected:
                kept.append({"split": split, **row})
    columns = ["split", "Index", "Goal", "Target", "Behavior", "Category", "Source"]
    with (OUT / "cases.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(kept)
    provenance = {
        "dataset": "JailbreakBench/JBB-Behaviors",
        "repository": "https://huggingface.co/datasets/JailbreakBench/JBB-Behaviors",
        "commit": COMMIT,
        "retrieved_at": "2026-10-08",
        "licence": "MIT",
        "source_rows": 200,
        "rows_imported": len(kept),
        "rows_dropped": dropped,
        "source_sha256": {name: digest for name, (_, digest) in FILES.items()},
    }
    (OUT / "PROVENANCE.json").write_text(
        json.dumps(provenance, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
