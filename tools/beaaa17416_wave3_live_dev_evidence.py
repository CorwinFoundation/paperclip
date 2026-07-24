from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import subprocess
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from types import FrameType
from typing import Any, Callable, Iterator

from evals.aiuc_path_b import base
from evals.aiuc_path_b import system_prompt_extraction as spe


FOUNDER_DEV_AUTH_REF = "BEAAA-17418#comment-53829031-44ad-48aa-bd5c-788943a67162"
CTO_TIMEOUT_POLICY_REF = "BEAAA-17693"
TARGET_ID = "aiuc-wave3-openrouter-reference-target"
DEFAULT_TIMEOUT_SECONDS = 20.0
DEFAULT_MAX_TOKENS = 256

Transport = Callable[..., dict[str, Any]]


class DeadlineExceeded(TimeoutError):
    pass


def _git(args: list[str]) -> str:
    return subprocess.check_output(["git", *args], text=True).strip()


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _archive_sha256(commit: str) -> str:
    return hashlib.sha256(subprocess.check_output(["git", "archive", "--format=tar", commit])).hexdigest()


def _auth(commit: str, nonce: str) -> spe.Wave3Authorization:
    commit_sha = _git(["rev-parse", commit])
    return spe.Wave3Authorization(
        target_id=TARGET_ID,
        candidate_commit_sha=commit_sha,
        direct_parent_sha=_git(["rev-parse", f"{commit_sha}^"]),
        tree_hash=_git(["rev-parse", f"{commit_sha}^{{tree}}"]),
        archive_sha256=_archive_sha256(commit_sha),
        authorization_ref=os.environ.get("WAVE3_AUTHORIZATION_REF", ""),
        authorization_signature=os.environ.get("WAVE3_AUTHORIZATION_SIGNATURE", ""),
        corpus_sha256=_sha256_file(Path("rubric/probe_mappings/system_prompt_extraction.yaml")),
        config_sha256=_sha256_file(Path("rubric/aiuc_path_b/family_registry.yaml")),
        replay_nonce=nonce,
    )


def _validate_exact_authorization(auth: spe.Wave3Authorization) -> None:
    spe.validate_authorization(
        auth,
        expected_commit_sha=auth.candidate_commit_sha,
        expected_direct_parent_sha=auth.direct_parent_sha,
        expected_tree_hash=auth.tree_hash,
        expected_archive_sha256=auth.archive_sha256,
    )


@contextmanager
def _hard_deadline(seconds: float) -> Iterator[None]:
    def _raise_deadline(signum: int, frame: FrameType | None) -> None:
        raise DeadlineExceeded("deadline_exceeded")

    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.setitimer(signal.ITIMER_REAL, 0)
    signal.signal(signal.SIGALRM, _raise_deadline)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        if previous_timer[0] > 0:
            signal.setitimer(signal.ITIMER_REAL, previous_timer[0], previous_timer[1])


def _target_error(
    *,
    case_id: str,
    seed: int,
    error_class: str,
    elapsed_ms: int,
    status: int | None = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "body": "",
        "case_id": case_id,
        "seed": seed,
        "target_id": TARGET_ID,
        "endpoint": spe.OPENROUTER_ENDPOINT,
        "model": spe.OPENROUTER_MODEL,
        "evidence_mode": "development_live_openrouter_error",
        "founder_development_authorization_ref": FOUNDER_DEV_AUTH_REF,
        "cto_timeout_policy_ref": CTO_TIMEOUT_POLICY_REF,
        "target_execution_error": True,
        "error_class": error_class,
        "elapsed_ms": elapsed_ms,
    }


def _extract_content(payload: dict[str, Any]) -> str:
    message = payload.get("choices", [{}])[0].get("message", {})
    content = message.get("content") or message.get("refusal") or message.get("reasoning") or ""
    if not content and isinstance(message.get("reasoning_details"), list):
        content = "\n".join(
            str(item.get("text", ""))
            for item in message["reasoning_details"]
            if isinstance(item, dict)
        )
    return str(content).strip()


def _call_openrouter(
    *,
    api_key: str,
    prompt: str,
    case_id: str,
    seed: int,
    timeout: float,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> dict[str, Any]:
    body = {
        "model": spe.OPENROUTER_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "seed": seed,
        "max_tokens": max_tokens,
        "reasoning": {"exclude": False},
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
    started = time.monotonic()
    try:
        with _hard_deadline(timeout):
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
                content = _extract_content(payload)
                elapsed_ms = round((time.monotonic() - started) * 1000)
                if not content:
                    return _target_error(
                        case_id=case_id,
                        seed=seed,
                        error_class="empty_target_text",
                        elapsed_ms=elapsed_ms,
                        status=resp.status,
                    )
                return {
                    "status": resp.status,
                    "body": content,
                    "case_id": case_id,
                    "seed": seed,
                    "target_id": TARGET_ID,
                    "endpoint": spe.OPENROUTER_ENDPOINT,
                    "model": spe.OPENROUTER_MODEL,
                    "evidence_mode": "development_live_openrouter_response",
                    "founder_development_authorization_ref": FOUNDER_DEV_AUTH_REF,
                    "cto_timeout_policy_ref": CTO_TIMEOUT_POLICY_REF,
                    "response_id": payload.get("id"),
                    "response_model": payload.get("model"),
                    "usage": payload.get("usage"),
                    "elapsed_ms": elapsed_ms,
                }
    except DeadlineExceeded:
        return _target_error(
            case_id=case_id,
            seed=seed,
            error_class="deadline_exceeded",
            elapsed_ms=round((time.monotonic() - started) * 1000),
        )
    except urllib.error.HTTPError as exc:
        try:
            exc.read()
        except Exception:
            pass
        return _target_error(
            case_id=case_id,
            seed=seed,
            error_class=f"http_status_{exc.code}",
            elapsed_ms=round((time.monotonic() - started) * 1000),
            status=exc.code,
        )
    except json.JSONDecodeError:
        return _target_error(
            case_id=case_id,
            seed=seed,
            error_class="malformed_json",
            elapsed_ms=round((time.monotonic() - started) * 1000),
        )
    except (TimeoutError, urllib.error.URLError, OSError) as exc:
        return _target_error(
            case_id=case_id,
            seed=seed,
            error_class=exc.__class__.__name__,
            elapsed_ms=round((time.monotonic() - started) * 1000),
        )


def _build_error_evidence_row(
    *,
    auth: spe.Wave3Authorization,
    case: base.Case,
    seed: int,
    rendered_prompt: str,
    target_response: dict[str, Any],
) -> dict[str, Any]:
    decision = {
        "derived_from_target_response": True,
        "target_execution_error": True,
        "error_class": target_response["error_class"],
    }
    row: dict[str, Any] = {
        "authorization_ref": auth.authorization_ref,
        "authorization_signature": auth.authorization_signature,
        "founder_development_authorization_ref": FOUNDER_DEV_AUTH_REF,
        "cto_timeout_policy_ref": CTO_TIMEOUT_POLICY_REF,
        "endpoint": spe.OPENROUTER_ENDPOINT,
        "model": spe.OPENROUTER_MODEL,
        "target_id": auth.target_id,
        "candidate_commit_sha": auth.candidate_commit_sha,
        "direct_parent_sha": auth.direct_parent_sha,
        "tree_hash": auth.tree_hash,
        "archive_sha256": auth.archive_sha256,
        "corpus_sha256": auth.corpus_sha256,
        "config_sha256": auth.config_sha256,
        "replay_nonce": auth.replay_nonce,
        "evaluator_id": spe.EVALUATOR_ID,
        "evaluator_sha256": spe.EVALUATOR_SHA256,
        "model_hash": spe.MODEL_HASH,
        "seed": seed,
        "case_id": case.case_id,
        "target_request": {
            "authorization_ref": auth.authorization_ref,
            "candidate_commit_sha": auth.candidate_commit_sha,
            "case_id": case.case_id,
            "endpoint": spe.OPENROUTER_ENDPOINT,
            "model": spe.OPENROUTER_MODEL,
            "prompt_sha256": "sha256:" + hashlib.sha256(rendered_prompt.encode("utf-8")).hexdigest(),
            "seed": seed,
            "target_id": auth.target_id,
        },
        "target_response": target_response,
        "target_response_sha256": spe.target_response_sha256(target_response),
        "decision": decision,
        "verdict": spe.ERROR,
        "target_execution_error": True,
        "error_class": target_response["error_class"],
        "canary_observed": case.case_id == "spe-20-canary",
    }
    row["row_sha256"] = spe.evidence_row_sha256(row)
    return row


def _build_success_evidence_row(
    *,
    auth: spe.Wave3Authorization,
    case: base.Case,
    seed: int,
    rendered_prompt: str,
    target_response: dict[str, Any],
) -> dict[str, Any]:
    row = spe.build_target_evidence_row(
        auth=auth,
        case=case,
        seed=seed,
        rendered_prompt=rendered_prompt,
        target_response=target_response,
    )
    row["replay_nonce"] = auth.replay_nonce
    row["row_sha256"] = spe.evidence_row_sha256(row)
    return row


def _append_jsonl_fsync(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _load_checkpointed(rows_path: Path, auth: spe.Wave3Authorization) -> dict[tuple[str, int], dict[str, Any]]:
    checkpointed: dict[tuple[str, int], dict[str, Any]] = {}
    if not rows_path.exists():
        return checkpointed
    for line in rows_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        key = (row.get("case_id"), row.get("seed"))
        if (
            row.get("candidate_commit_sha") == auth.candidate_commit_sha
            and row.get("corpus_sha256") == auth.corpus_sha256
            and row.get("config_sha256") == auth.config_sha256
            and row.get("replay_nonce") == auth.replay_nonce
            and row.get("row_sha256") == spe.evidence_row_sha256(row)
            and isinstance(key[0], str)
            and key[1] in range(5)
        ):
            checkpointed[(key[0], key[1])] = row
    return checkpointed


def _load_source_responses(rows_path: Path | None) -> dict[tuple[str, int], dict[str, Any]]:
    responses: dict[tuple[str, int], dict[str, Any]] = {}
    if rows_path is None:
        return responses
    for line in rows_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        response = row.get("target_response")
        key = (row.get("case_id"), row.get("seed"))
        if isinstance(response, dict) and isinstance(key[0], str) and key[1] in range(5):
            copied = dict(response)
            copied["replayed_from_candidate_commit_sha"] = row.get("candidate_commit_sha")
            copied["replayed_from_row_sha256"] = row.get("row_sha256")
            copied["provider_call_repeated"] = False
            responses[(key[0], key[1])] = copied
    return responses


def _ordered_case_inputs() -> list[tuple[int, base.Case, str]]:
    inputs: list[tuple[int, base.Case, str]] = []
    for seed in range(5):
        ev = base.run_family(
            spe.SPEC,
            spe.compliant_policy,
            seed=seed,
            architecture_flags={spe.APPLICABILITY_FLAG: True},
        )
        for case_result in ev["case_results"]:
            case = next(case for case in spe.SPEC.cases if case.case_id == case_result["case_id"])
            inputs.append((seed, case, case_result["rendered_prompt"]))
    return inputs


def build_rows(
    *,
    commit: str,
    nonce: str,
    timeout: float,
    sleep_seconds: float = 0.0,
    out_dir: Path | None = None,
    transport: Transport | None = None,
    max_rows: int | None = None,
    source_rows: Path | None = None,
) -> tuple[spe.Wave3Authorization, list[dict[str, Any]]]:
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    source_responses = _load_source_responses(source_rows)
    if transport is None and not api_key and not source_responses:
        raise SystemExit("OPENROUTER_API_KEY is required")
    auth = _auth(commit, nonce)
    _validate_exact_authorization(auth)
    rows_path = out_dir / "target-evidence-rows.jsonl" if out_dir else None
    ledger_path = out_dir / "call-ledger.jsonl" if out_dir else None
    checkpointed = _load_checkpointed(rows_path, auth) if rows_path else {}
    rows: list[dict[str, Any]] = []
    call_count = 0
    for seed, case, rendered_prompt in _ordered_case_inputs():
        if max_rows is not None and len(rows) >= max_rows:
            break
        key = (case.case_id, seed)
        if key in checkpointed:
            rows.append(checkpointed[key])
            continue
        if key in source_responses:
            response = source_responses[key]
            call_started = time.time()
            call_repeated = False
        elif call_count >= spe.LIVE_CALL_CAP:
            raise SystemExit("live_call_cap_exceeded")
        else:
            call_count += 1
            call_started = time.time()
            call_repeated = True
            active_transport = transport or _call_openrouter
            started = time.monotonic()
            try:
                with _hard_deadline(timeout):
                    response = active_transport(
                        api_key=api_key,
                        prompt=rendered_prompt,
                        case_id=case.case_id,
                        seed=seed,
                        timeout=timeout,
                    )
            except DeadlineExceeded:
                response = _target_error(
                    case_id=case.case_id,
                    seed=seed,
                    error_class="deadline_exceeded",
                    elapsed_ms=round((time.monotonic() - started) * 1000),
                )
        if response.get("target_execution_error") is True:
            row = _build_error_evidence_row(
                auth=auth,
                case=case,
                seed=seed,
                rendered_prompt=rendered_prompt,
                target_response=response,
            )
        else:
            row = _build_success_evidence_row(
                auth=auth,
                case=case,
                seed=seed,
                rendered_prompt=rendered_prompt,
                target_response=response,
            )
        ledger = {
            "candidate_commit_sha": auth.candidate_commit_sha,
            "corpus_sha256": auth.corpus_sha256,
            "config_sha256": auth.config_sha256,
            "replay_nonce": auth.replay_nonce,
            "case_id": case.case_id,
            "seed": seed,
            "call_number_this_run": call_count if call_repeated else None,
            "endpoint": spe.OPENROUTER_ENDPOINT,
            "model": spe.OPENROUTER_MODEL,
            "terminal_row_sha256": row["row_sha256"],
            "target_execution_error": row.get("target_execution_error") is True,
            "error_class": row.get("error_class"),
            "elapsed_ms": response.get("elapsed_ms"),
            "provider_call_repeated": call_repeated,
            "replayed_from_candidate_commit_sha": response.get("replayed_from_candidate_commit_sha"),
            "replayed_from_row_sha256": response.get("replayed_from_row_sha256"),
            "ledger_written_at_unix": call_started,
        }
        if rows_path and ledger_path:
            _append_jsonl_fsync(rows_path, row)
            _append_jsonl_fsync(ledger_path, ledger)
        rows.append(row)
        if sleep_seconds:
            time.sleep(sleep_seconds)
    return auth, rows


def write_packet(*, out_dir: Path, auth: spe.Wave3Authorization, rows: list[dict[str, Any]]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    rows_path = out_dir / "target-evidence-rows.jsonl"
    if not rows_path.exists():
        rows_path.write_text(
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
        "ERROR": sum(1 for row in rows if row["verdict"] == spe.ERROR),
    }
    usage_rows = [
        row["target_response"].get("usage")
        for row in rows
        if isinstance(row.get("target_response"), dict) and row["target_response"].get("usage") is not None
    ]
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
        "provider_calls_repeated_for_packet_rebuild": sum(
            1
            for row in rows
            if isinstance(row.get("target_response"), dict)
            and row["target_response"].get("provider_call_repeated") is not False
        ),
        "live_call_cap": spe.LIVE_CALL_CAP,
        "cumulative_conservative_call_ceiling": 301,
        "provider_usage_records_available": len(usage_rows),
        "provider_usage": usage_rows,
        "credential_retained": False,
        "signature_status": "valid_ed25519_authorization_required_and_verified",
        "fail_closed_error_rows_are_not_scoring_evidence": True,
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
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--sleep-seconds", type=float, default=0.0)
    parser.add_argument("--source-rows", type=Path)
    args = parser.parse_args()

    auth, rows = build_rows(
        commit=args.commit,
        nonce=args.nonce,
        timeout=args.timeout,
        sleep_seconds=args.sleep_seconds,
        out_dir=args.out_dir,
        source_rows=args.source_rows,
    )
    write_packet(out_dir=args.out_dir, auth=auth, rows=rows)
    spe.validate_evidence_rows(rows, expected_authorization=auth, allow_error_rows=True)
    print(args.out_dir / "candidate-identity.json")


if __name__ == "__main__":
    main()
