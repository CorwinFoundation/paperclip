#!/usr/bin/env python3
"""Fail-fast validation for the pinned Garak probe registry."""
from __future__ import annotations

import argparse
import importlib
import os
import sys
from pathlib import Path

import yaml


def validate(path: Path) -> list[str]:
    # CI and production keep Garak in the isolated eval venv. Make the check
    # use that pinned interpreter's site-packages even when invoked by the
    # scanner's runtime/test interpreter.
    venv = Path(os.environ.get("ARE_EVAL_VENVS_DIR", "/opt/are-scanner/eval-venvs")) / "garak-venv"
    site_packages = next(venv.glob("lib/python*/site-packages"), None)
    if site_packages and str(site_packages) not in sys.path:
        sys.path.insert(0, str(site_packages))
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    retired = {
        str(row["probe_id"]): str(row.get("reason") or "")
        for row in doc.get("retired_probes", [])
    }
    errors: list[str] = []
    for row in doc.get("probes", []):
        probe_id = str(row.get("probe_id") or "")
        if not probe_id.startswith("garak."):
            continue
        parts = probe_id.removeprefix("garak.").split(".")
        if len(parts) != 2:
            errors.append(f"{probe_id}: expected garak.<module>.<class>")
            continue
        module_name, class_name = parts
        try:
            module = importlib.import_module(f"garak.probes.{module_name}")
            getattr(module, class_name)
        except (ImportError, AttributeError) as exc:
            if probe_id not in retired or not retired[probe_id]:
                errors.append(f"{probe_id}: unresolved ({exc})")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", nargs="?", type=Path, default=Path("rubric/probe_mappings/garak_beaaa152.yaml"))
    args = parser.parse_args()
    errors = validate(args.path)
    if errors:
        for error in errors:
            print(error)
        return 1
    print(f"garak registry OK: {args.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
