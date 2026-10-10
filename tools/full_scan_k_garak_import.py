#!/usr/bin/env python3
"""Import the bounded Garak K evidence prompt set.

Only Garak 0.16.0 is accepted.  The importer reads the installed pinned probe
classes rather than copying generated prompts into source, de-duplicates exact
prompt text, and emits a stable report-only manifest.  It never calls a model.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.metadata
import json
import random
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
MAPPING = REPO_ROOT / "rubric/probe_mappings/garak_beaaa152.yaml"
ALLOWED_FAMILIES = frozenset({"leakreplay", "encoding", "promptinject"})
REQUIRED_GARAK_VERSION = "0.16.0"
SCHEMA = "full-scan-k-garak-prompt/v1"
# garak samples prompts with the global ``random`` module; without a pinned
# seed and cap the bounded set changed between runs (BEAAA-40048).
PROMPT_SEED = 0
PROMPT_CAP = 64


def _garak_version() -> str:
    try:
        return importlib.metadata.version("garak")
    except importlib.metadata.PackageNotFoundError as exc:
        raise RuntimeError("garak is not installed in the selected eval environment") from exc


def _probe_rows(mapping_path: Path = MAPPING) -> list[dict[str, Any]]:
    document = yaml.safe_load(mapping_path.read_text(encoding="utf-8")) or {}
    rows: list[dict[str, Any]] = []
    for row in document.get("probes", []):
        probe_id = str(row.get("probe_id") or "")
        parts = probe_id.removeprefix("garak.").split(".")
        if len(parts) != 2 or parts[0] not in ALLOWED_FAMILIES:
            continue
        if row.get("garak_live_dispatch") is not True:
            continue
        rows.append({"probe_id": probe_id, "family": parts[0], "module": parts[0], "class": parts[1]})
    return sorted(rows, key=lambda row: row["probe_id"])


def _pin_sampling(seed: int, cap: int) -> None:
    from garak import _config

    _config.run.seed = seed
    _config.run.soft_probe_prompt_cap = cap
    random.seed(seed)


def instantiate_probe(row: dict[str, Any], *, seed: int = PROMPT_SEED, cap: int = PROMPT_CAP) -> Any:
    """Build one pinned probe with a deterministic, capped prompt sample."""
    _pin_sampling(seed, cap)
    module = importlib.import_module(f"garak.probes.{row['module']}")
    return getattr(module, row["class"])()


def import_prompts(*, mapping_path: Path = MAPPING) -> dict[str, Any]:
    version = _garak_version()
    if version != REQUIRED_GARAK_VERSION:
        raise RuntimeError(f"garak version mismatch: required {REQUIRED_GARAK_VERSION}, found {version}")
    prompts: dict[str, dict[str, Any]] = {}
    probe_counts: dict[str, int] = {}
    for row in _probe_rows(mapping_path):
        probe = instantiate_probe(row)
        values = getattr(probe, "prompts", None)
        if not isinstance(values, (list, tuple)):
            raise RuntimeError(f"{row['probe_id']} did not expose a prompt list")
        count = 0
        for ordinal, prompt in enumerate(values):
            if not isinstance(prompt, str) or not prompt.strip():
                continue
            digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
            prompts.setdefault(digest, {
                "prompt_id": f"garak.prompt.{digest[:24]}",
                "prompt_sha256": digest,
                "prompt": prompt,
                "source_probe_ids": [],
            })
            if row["probe_id"] not in prompts[digest]["source_probe_ids"]:
                prompts[digest]["source_probe_ids"].append(row["probe_id"])
            count += 1
        probe_counts[row["probe_id"]] = count
    rows = sorted(prompts.values(), key=lambda item: item["prompt_sha256"])
    for row in rows:
        row["source_probe_ids"].sort()
    return {
        "schema": SCHEMA,
        "framework": "garak",
        "framework_version": version,
        "families": sorted(ALLOWED_FAMILIES),
        "prompt_seed": PROMPT_SEED,
        "prompt_cap": PROMPT_CAP,
        "source_mapping": str(mapping_path.relative_to(REPO_ROOT)),
        "probe_counts": probe_counts,
        "unique_prompt_count": len(rows),
        "prompts": rows,
        "model_calls": 0,
        "report_only": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    manifest = import_prompts()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({k: manifest[k] for k in ("framework_version", "families", "probe_counts", "unique_prompt_count")}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
