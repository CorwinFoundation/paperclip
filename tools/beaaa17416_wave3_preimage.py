from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from evals.aiuc_path_b import system_prompt_extraction as spe


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git(args: list[str]) -> str:
    return subprocess.check_output(["git", *args], text=True).strip()


def _archive_sha256(commit: str, out_path: Path | None) -> str:
    data = subprocess.check_output(["git", "archive", "--format=tar", commit])
    if out_path is not None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def build_preimage(*, commit: str, nonce: str, archive_path: Path | None = None) -> dict[str, Any]:
    commit_sha = _git(["rev-parse", commit])
    parent_sha = _git(["rev-parse", f"{commit_sha}^"])
    tree_hash = _git(["rev-parse", f"{commit_sha}^{{tree}}"])
    archive_sha256 = _archive_sha256(commit_sha, archive_path)

    auth = spe.Wave3Authorization(
        target_id="aiuc-wave3-openrouter-reference-target",
        candidate_commit_sha=commit_sha,
        direct_parent_sha=parent_sha,
        tree_hash=tree_hash,
        archive_sha256=archive_sha256,
        corpus_sha256=_sha256_file(Path("rubric/probe_mappings/system_prompt_extraction.yaml")),
        config_sha256=_sha256_file(Path("rubric/aiuc_path_b/family_registry.yaml")),
        replay_nonce=nonce,
    )
    preimage = spe._authorization_preimage(auth)
    return {
        "artifact_type": "wave3_unsigned_authorization_preimage",
        "ticket_ref": "BEAAA-17416",
        "qa_gate": "BEAAA-17355",
        "supersedes_rejected_sha": "c57d6f609473d21cd62e6990724a0af463470288",
        "contract": "are-scanner-api-v1@v1.3.0",
        "scanner_version": "0.5.0",
        "corpus_version": "system_prompt_extraction@0.1.0",
        "probe_set_version": "system_prompt_extraction@0.1.0",
        "rubric_point_id": ["P3.3", "ROBUSTNESS.SECRETS.SYSTEM_PROMPT_EXTRACTION"],
        "signing_status": "unsigned_founder_signature_required_before_call_1",
        "live_call_status": "not_started",
        "authorization_preimage": preimage,
        "canonical_authorization_preimage": spe._canonical(preimage),
        "canonical_authorization_preimage_sha256": hashlib.sha256(
            spe._canonical(preimage).encode("utf-8")
        ).hexdigest(),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--commit", default="HEAD")
    parser.add_argument("--nonce", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--archive-out", type=Path)
    args = parser.parse_args()

    payload = build_preimage(commit=args.commit, nonce=args.nonce, archive_path=args.archive_out)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(args.out)


if __name__ == "__main__":
    main()
