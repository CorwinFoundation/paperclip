#!/usr/bin/env python3
"""Validate the BEAAA-17962 acceptance evidence bundle."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


ALLOWED_STATES = {"passed", "failed", "not-evaluated", "not-applicable"}
ALLOWED_SCOPES = {
    "aiuc_1_mapped",
    "backbond_extension",
    "backbond_scanner",
    "core_release_ops",
    "production_only",
}
SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
FORBIDDEN_AIUC_CLAIMS = (
    "AIUC-certified",
    "AIUC-compliant",
    "official AIUC score",
)
PRODUCTION_ONLY_ROWS = {
    "MONITORING.PRODUCTION_ONLY",
    "BACKUP_RESTORE.PRODUCTION_ONLY",
    "ROLLBACK.PRODUCTION_ONLY",
}


def _row_text(row: dict[str, Any]) -> str:
    parts = [
        str(row.get("assessment_label", "")),
        str(row.get("assessment_basis", "")),
        str(row.get("scope_label", "")),
    ]
    return " ".join(parts)


def validate_bundle(path: Path) -> list[str]:
    bundle = json.loads(path.read_text(encoding="utf-8"))
    errors: list[str] = []

    release_context = bundle.get("release_context", {})
    candidate_sha_required = release_context.get("candidate_sha_late_bound_required")
    if candidate_sha_required is not True:
        errors.append("release_context.candidate_sha_late_bound_required must be true")
    if "candidate_sha" not in release_context:
        errors.append("release_context.candidate_sha must be present, even when late-bound")

    claim_boundary = bundle.get("claim_boundary", {})
    if claim_boundary.get("certification_claimed") is not False:
        errors.append("claim_boundary.certification_claimed must be false")
    if claim_boundary.get("insurance_decision_claimed") is not False:
        errors.append("claim_boundary.insurance_decision_claimed must be false")
    if claim_boundary.get("backbond_extensions_separate") is not True:
        errors.append("BackBond Extensions must be explicitly separate")

    rows = bundle.get("acceptance_rows")
    if not isinstance(rows, list) or not rows:
        return errors + ["acceptance_rows must be a non-empty list"]

    seen_ids: set[str] = set()
    seen_scopes: set[str] = set()
    for index, row in enumerate(rows):
        row_id = row.get("row_id")
        prefix = f"acceptance_rows[{index}]"
        if not row_id:
            errors.append(f"{prefix}.row_id is required")
        elif row_id in seen_ids:
            errors.append(f"{row_id} is duplicated")
        else:
            seen_ids.add(row_id)

        state = row.get("state")
        if state not in ALLOWED_STATES:
            errors.append(f"{row_id or prefix} has invalid or blank state: {state!r}")

        scope = row.get("scope_label")
        if scope not in ALLOWED_SCOPES:
            errors.append(f"{row_id or prefix} has invalid scope_label: {scope!r}")
        else:
            seen_scopes.add(scope)

        artifacts = row.get("artifacts", [])
        commands = row.get("commands", [])
        if state == "passed":
            if not commands:
                errors.append(f"{row_id} is passed but has no reproducible command")
            if not artifacts:
                errors.append(f"{row_id} is passed but has no artifact hash")
            for artifact in artifacts:
                digest = artifact.get("sha256") if isinstance(artifact, dict) else None
                if not (isinstance(digest, str) and SHA256_RE.match(digest)):
                    errors.append(f"{row_id} is passed with invalid artifact sha256: {digest!r}")

        if row.get("certification_claim") is not False:
            errors.append(f"{row_id} must not claim certification")

        text = _row_text(row)
        if scope == "aiuc_1_mapped":
            for forbidden in FORBIDDEN_AIUC_CLAIMS:
                if forbidden in text:
                    errors.append(f"{row_id} contains forbidden AIUC claim: {forbidden}")
        if scope == "backbond_extension" and "AIUC" in text:
            errors.append(f"{row_id} mixes AIUC wording into a BackBond Extension row")

    for required in ("aiuc_1_mapped", "backbond_extension"):
        if required not in seen_scopes:
            errors.append(f"missing required scope_label: {required}")

    for row_id in PRODUCTION_ONLY_ROWS:
        row = next((candidate for candidate in rows if candidate.get("row_id") == row_id), None)
        if row is None:
            errors.append(f"missing production-only row: {row_id}")
            continue
        if row.get("state") != "not-evaluated":
            errors.append(f"{row_id} must be not-evaluated in development mode")
        if row.get("owner_issue") != "BEAAA-17387":
            errors.append(f"{row_id} must be owned by BEAAA-17387")

    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("bundle", type=Path)
    args = parser.parse_args(argv)

    errors = validate_bundle(args.bundle)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"PASS: {args.bundle}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
