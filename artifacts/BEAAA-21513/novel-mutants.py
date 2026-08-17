#!/usr/bin/env python3
"""BEAAA-21513 R2 QA: mutants the producer's own mutation_check.py does NOT cover.

Their M1-M6 / W1-W5 set is sound. These probe the gaps around it: trigger
surface, the fail-closed evidence seed, shell strictness in the artifact step,
and false claims in the evidence document itself.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

WT = Path("/tmp/qa21513r2")
SUITE = "tests/test_scanner_image_readonly_verify_beaaa21512.py"
WF = ".github/workflows/scanner-image-readonly-verify.yml"
SRC = "scripts/verify_scanner_image.py"

# (name, file, old, new, why it matters)
MUTANTS = [
    ("N1 evidence falsely claims the cross-check was done", SRC,
     '"image_and_actions_artifact_cross_checked": False,',
     '"image_and_actions_artifact_cross_checked": True,',
     "D3 regression: the PASS document would assert a binding never verified"),

    ("N2 evidence schema regressed to @1", SRC,
     'SCHEMA_VERSION = "backbond-scanner-readonly-image-verification@2"',
     'SCHEMA_VERSION = "backbond-scanner-readonly-image-verification@1"',
     "consumers keying on @2 would silently read a @1-shaped document"),

    ("N3 add id-token: write to permissions", WF,
     "  actions: read\n",
     "  actions: read\n  id-token: write\n",
     "grants OIDC signing identity to a read-only verifier"),

    ("N4 add an extra read scope", WF,
     "  actions: read\n",
     "  actions: read\n  issues: read\n",
     "scope creep beyond the CTO ruling"),

    ("N5 widen the trigger to pull_request_target", WF,
     "on:\n  workflow_dispatch:\n",
     "on:\n  workflow_dispatch:\n  pull_request_target:\n",
     "auto-runs the verifier on untrusted PR context"),

    ("N6 delete the fail-closed evidence seed step", WF,
     "      - name: Initialize fail-closed evidence\n",
     "      - name: DISABLED initialize fail-closed evidence\n        if: false\n",
     "on early failure no evidence exists; upload is if-no-files-found: warn, so the run reports no FAIL document"),

    ("N7 drop set -euo pipefail from the artifact download step", WF,
     '            "${GITHUB_API_URL}/repos/${GITHUB_REPOSITORY}/actions/artifacts/${SOURCE_ARTIFACT_ID}" \\\n'
     "            --output source-artifact-metadata.json\n",
     '            "${GITHUB_API_URL}/repos/${GITHUB_REPOSITORY}/actions/artifacts/${SOURCE_ARTIFACT_ID}" \\\n'
     "            --output source-artifact-metadata.json || true\n",
     "swallows an artifact API failure; step continues on a truncated download"),

    ("N8 drop --fail-with-body from the artifact zip download", WF,
     "          curl --fail-with-body --location --silent --show-error \\\n",
     "          curl --location --silent --show-error \\\n",
     "a 403/404 body would be saved as source-artifact.zip instead of failing"),

    ("N9 relax the bundled-checksum regex to accept any digest", SRC,
     r'        match = re.fullmatch(r"([0-9a-f]{64})\s+\*?.*scanner-edge-image\.tar", checksum_text)',
     r'        match = re.fullmatch(r"([0-9a-f]*)\s*\*?.*scanner-edge-image\.tar", checksum_text)',
     "accepts a zero-length or short digest in the bundled checksum"),

    ("N10 evidence upload no longer runs on failure", WF,
     "        if: always()\n",
     "        if: success()\n",
     "the FAIL evidence document is never uploaded, so failures leave no artifact"),
]


def main() -> int:
    rows = []
    for name, rel, old, new, why in MUTANTS:
        original = (WT / rel).read_text(encoding="utf-8")
        if old not in original:
            rows.append((name, "ANCHOR-MISS", "", why))
            continue
        with tempfile.TemporaryDirectory() as td:
            box = Path(td) / "repo"
            shutil.copytree(WT, box, symlinks=True,
                            ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache"))
            (box / rel).write_text(original.replace(old, new, 1), encoding="utf-8")
            p = subprocess.run([sys.executable, "-m", "pytest", "-q", SUITE],
                               cwd=box, capture_output=True, text=True)
            tail = (p.stdout.strip().splitlines() or [""])[-1]
            rows.append((name, "KILLED" if p.returncode else "SURVIVED", tail, why))

    print("=== Novel mutants (outside the producer's own set) ===")
    surv = []
    for name, verdict, tail, why in rows:
        print(f"[{verdict:9s}] {name}")
        print(f"            {tail}")
        if verdict == "SURVIVED":
            print(f"            RISK: {why}")
            surv.append(name)
    print(f"\n{len(rows) - len(surv)}/{len(rows)} killed; {len(surv)} SURVIVED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
