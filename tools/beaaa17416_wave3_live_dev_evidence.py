from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import replace
from pathlib import Path
from typing import Any

from evals.aiuc_path_b import base
from evals.aiuc_path_b import system_prompt_extraction as spe


FOUNDER_DEV_AUTH_REF = "BEAAA-17418#comment-53829031-44ad-48aa-bd5c-788943a67162"
DEVELOPMENT_AUTHORIZATION = "founder_declined_signature_development_run_authorized_2026-07-24"


def _git(args: list[str]) -> str:
    return subprocess.check_output(["git", *args], text=True).strip()


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _archive_sha256(commit: str) -> str:
    return hashlib.sha256(subprocess.check_output(["git", "archive", "--format=tar", commit])).hexdigest()


def _auth(commit: str, nonce: str) -> spe.Wave3Authorization:
    commit_sha = _git(["rev-parse", commit])
    auth = spe.Wave3Authorization(
        target_id="aiuc-wave3-openrouter-reference-target",
        candidate_commit_sha=commit_sha,
        direct_parent_sha=_git(["rev-parse", f"{commit_sha}^"]),
        tree_hash=_git(["rev-parse", f"{commit_sha}^{{tree}}"]),
        archive_sha256=_archive_sha256(commit_sha),
        authorization_ref=FOUNDER_DEV_AUTH_REF,
        authorization_signature=DEVELOPMENT_AUTHORIZATION,
        corpus_sha256=_sha256_file(Path("rubric/probe_mappings/system_prompt_extraction.yaml")),
        config_sha256=_sha256_file(Path("rubric/aiuc_path_b/family_registry.yaml")),
        replay_nonce=nonce,
    )
    return auth


def _call_openrouter(*, api_key: str, prompt: str, case_id: str, seed: int, timeout: int) -> dict[str, Any]:
    body = {
        "model": spe.OPENROUTER_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "seed": seed,
        "max_tokens": 220,
    }
    req = urllib.request.Request(
        spe.OPENROUTER_ENDPOINT,
        data=json.dumps(body, sort_keys=True).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://backbond.ai",
            "X-Title": "BackBond Scanner Wave 3 development test",
        },
        method="POST",
    )
    started = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
            content = (
                payload.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
            )
            return {
                "status": resp.status,
                "body": content,
                "case_id": case_id,
                "seed": seed,
                "target_id": "aiuc-wave3-openrouter-reference-target",
                "evidence_mode": "development_live_openrouter_response",
                "founder_development_authorization_ref": FOUNDER_DEV_AUTH_REF,
                "response_id": payload.get("id"),
                "response_model": payload.get("model"),
                "usage": payload.get("usage"),
                "elapsed_ms": round((time.time() - started) * 1000),
            }
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        return {
            "status": exc.code,
            "body": error_body[:1000],
            "case_id": case_id,
            "seed": seed,
            "target_id": "aiuc-wave3-openrouter-reference-target",
            "evidence_mode": "development_live_openrouter_error",
            "founder_development_authorization_ref": FOUNDER_DEV_AUTH_REF,
            "elapsed_ms": round((time.time() - started) * 1000),
        }


def build_rows(*, commit: str, nonce: str, timeout: int, sleep_seconds: float) -> tuple[spe.Wave3Authorization, list[dict[str, Any]]]:
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise SystemExit("OPENROUTER_API_KEY is required")
    auth = _auth(commit, nonce)
    rows: list[dict[str, Any]] = []
    for seed in range(5):
        ev = base.run_family(
            spe.SPEC,
            spe.compliant_policy,
            seed=seed,
            architecture_flags={spe.APPLICABILITY_FLAG: True},
        )
        for case_result in ev["case_results"]:
            case = next(case for case in spe.SPEC.cases if case.case_id == case_result["case_id"])
            response = _call_openrouter(
                api_key=api_key,
                prompt=case_result["rendered_prompt"],
                case_id=case.case_id,
                seed=seed,
                timeout=timeout,
            )
            row = spe.build_target_evidence_row(
                auth=auth,
                case=case,
                seed=seed,
                rendered_prompt=case_result["rendered_prompt"],
                target_response=response,
            )
            row["authorization_mode"] = "development_test_no_ed25519_signature"
            row["founder_development_authorization_ref"] = FOUNDER_DEV_AUTH_REF
            row["row_sha256"] = spe.evidence_row_sha256(row)
            rows.append(row)
            if sleep_seconds:
                time.sleep(sleep_seconds)
    return auth, rows


def write_packet(*, out_dir: Path, auth: spe.Wave3Authorization, rows: list[dict[str, Any]]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "target-evidence-rows.jsonl").write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    evidence_dir = out_dir / "evidence" / "system_prompt_extraction"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    for seed in range(5):
        seed_rows = [row for row in rows if row["seed"] == seed]
        (evidence_dir / f"target-bound-seed-{seed}.json").write_text(
            json.dumps({"authorization": auth.__dict__, "case_results": seed_rows}, sort_keys=True, indent=2)
            + "\n",
            encoding="utf-8",
        )
    tally = {
        "PASS": sum(1 for row in rows if row["verdict"] == "PASS"),
        "FAIL": sum(1 for row in rows if row["verdict"] == "FAIL"),
    }
    summary = {
        "ticket_ref": "BEAAA-17416",
        "qa_gate": "BEAAA-17355",
        "supersedes_rejected_sha": "c57d6f609473d21cd62e6990724a0af463470288",
        "authorization": auth.__dict__,
        "row_count": len(rows),
        "unique_case_seed_rows": len({(row["case_id"], row["seed"]) for row in rows}),
        "canary_seeds": sorted({row["seed"] for row in rows if row["case_id"] == "spe-20-canary"}),
        "verdict_tally": tally,
        "live_call_count": len(rows),
        "live_call_cap": spe.LIVE_CALL_CAP,
        "credential_retained": False,
        "signature_status": "not_required_for_founder_authorized_development_test",
        "n5_calibration_delta": {"total": 0, "pillars": 0, "band": 0},
    }
    (out_dir / "candidate-identity.json").write_text(
        json.dumps(summary, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--commit", default="HEAD")
    parser.add_argument("--nonce", required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--sleep-seconds", type=float, default=0.0)
    args = parser.parse_args()

    auth, rows = build_rows(
        commit=args.commit,
        nonce=args.nonce,
        timeout=args.timeout,
        sleep_seconds=args.sleep_seconds,
    )
    write_packet(out_dir=args.out_dir, auth=auth, rows=rows)
    spe.validate_evidence_rows(rows, expected_authorization=auth)
    print(args.out_dir / "candidate-identity.json")


if __name__ == "__main__":
    main()
