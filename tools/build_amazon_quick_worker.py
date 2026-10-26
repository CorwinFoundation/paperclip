"""Create an allowlisted AWS Quick assessment build context. No credentials/state.

Uses only the report-only internal knowledge profile and its import closure.
The image has no service listener. AWS CLI v2 is supplied by the worker host.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess

FILES = (
    "requirements-runtime.txt", "release_identity.py", "release/backbond-scanner-release.json",
    "harness/__init__.py", "harness/runner.py", "harness/amazon_quick.py",
    "evals/__init__.py", "evals/internal_agent_knowledge.py", "evals/full_scan_q_kb_grounding.py",
    "evals/full_scan_m_claim_extractor.py", "scanners/__init__.py",
    "scanners/evaluator_budget.py", "scanners/starter_judge.py",
    "scanners/starter_evidence_checks.py", "scanners/starter_factual_references.py",
    "scanners/starter_pack_v2.py", "scanners/starter_pack_v3.py", "scanners/starter_voice_pack.py",
    "tools/internal_agent_pilot.py", "tools/amazon_quick_pilot.py",
    "tools/quick_pilot_operations.py", "tools/quick_pilot_report.py", "tools/quick_pilot_run.py",
    "deploy/amazon-quick/Dockerfile", "deploy/amazon-quick/run-worker.sh",
    "deploy/amazon-quick/verify-fixtures.py",
)


def build_context(root: Path, output: Path):
    output.mkdir(parents=True, exist_ok=False)
    hashes = {}
    for name in FILES:
        path = root / name
        if path.is_symlink() or not path.is_file():
            raise ValueError("regular_allowlisted_source_required")
        # Docker expects its Dockerfile in the context root.
        target_name = "Dockerfile" if name == "deploy/amazon-quick/Dockerfile" else name
        target = output / target_name
        target.parent.mkdir(parents=True, exist_ok=True)
        data = path.read_bytes()
        target.write_bytes(data)
        hashes[target_name] = hashlib.sha256(data).hexdigest()
    (output / "tools/__init__.py").write_bytes(b"")
    hashes["tools/__init__.py"] = hashlib.sha256(b"").hexdigest()
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, check=True,
                              capture_output=True, text=True).stdout.strip()
    dirty = bool(subprocess.run(["git", "status", "--porcelain"], cwd=root, check=True,
                                capture_output=True, text=True).stdout.strip())
    manifest = {"schema": "backbond-quick-worker-source/v1", "source_revision": revision,
                "working_tree_dirty": dirty, "files": hashes,
                "profile_id": "internal-agent-knowledge@1", "backbond_score": "N/A"}
    (output / "worker-source.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        manifest = build_context(Path(__file__).resolve().parents[1], args.output)
        print(json.dumps({"status": "source_context_only", "files": len(manifest["files"]),
                          "source_revision": manifest["source_revision"],
                          "working_tree_dirty": manifest["working_tree_dirty"]}))
        return 0
    except (OSError, ValueError, subprocess.SubprocessError):
        print(json.dumps({"status": "incomplete", "error": "new_output_and_reviewed_sources_required"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
