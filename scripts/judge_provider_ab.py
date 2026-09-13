#!/usr/bin/env python3
"""Replay 50 stored OpenRouter rows through one direct-Anthropic batch."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.content_candidate_inventory import (  # noqa: E402
    candidate_judge_criteria,
    materialize_candidate_case,
)
from tools.registered_case_executor import materialized_text_judge_prompt  # noqa: E402

MODEL = "claude-haiku-4-5-20251001"
STORED_MODEL = "openrouter:anthropic/claude-haiku-4.5"
MANIFEST_SHA256 = "6178b113cc8085c7646fe07adb7e18705a298b2b55a22fddb774acd86e604195"


def preflight() -> None:
    """Fail before reading fixtures or creating a paid batch."""
    configured = os.environ.get(
        "ARE_FULL_SCAN_JUDGE_MODEL", "anthropic/claude-haiku-4.5"
    )
    if not configured.startswith("anthropic/"):
        raise SystemExit("judge provider preflight failed: expected anthropic")
    if MODEL != "claude-haiku-4-5-20251001":
        raise SystemExit("judge model preflight failed")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("ANTHROPIC_API_KEY is absent")


def safe_failure_message(exc: BaseException) -> str:
    """Describe the failure without rendering exception values or secrets."""
    if isinstance(exc, AttributeError):
        name = getattr(exc, "name", None)
        if isinstance(name, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            return f"judge provider A/B failed (missing attribute: {name})"
    try:
        import anthropic
    except ImportError:
        anthropic = None
    if anthropic is not None and isinstance(exc, anthropic.APIStatusError):
        status = getattr(exc, "status_code", None)
        body = getattr(exc, "body", None)
        error = body.get("error") if isinstance(body, dict) else None
        error_type = error.get("type") if isinstance(error, dict) else None
        safe_status = status if isinstance(status, int) else "unknown"
        if isinstance(error_type, str) and re.fullmatch(
            r"[a-z][a-z0-9_]{0,63}", error_type
        ):
            return (
                "judge provider A/B failed "
                f"(HTTP {safe_status}, error.type={error_type})"
            )
        return f"judge provider A/B failed (HTTP {safe_status})"
    return f"judge provider A/B failed ({type(exc).__name__})"


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def parse_verdict(text: str) -> str:
    match = re.search(r'"verdict"\s*:\s*"(pass|partial|fail)"', text, re.I)
    return match.group(1).lower() if match else "partial"


def build_batch_requests(rows: list[dict]) -> tuple[list[dict], dict[str, str]]:
    """Build API-valid requests and an opaque custom-id lookup."""
    requests = []
    case_by_custom_id = {}
    for row_index, row in enumerate(rows):
        stable, marker, variable = row["prompt"].partition("\nResponses JSON:")
        content = [
            {"type": "text", "text": stable, "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": marker + variable},
        ]
        for vote in range(3):
            # Anthropic restricts batch custom_id values to 64 ASCII letters,
            # numbers, underscores, and hyphens. Case IDs are not API IDs and
            # can contain punctuation, so keep the provider ID opaque.
            custom_id = f"row-{row_index:03d}-vote-{vote}"
            case_by_custom_id[custom_id] = row["case_id"]
            requests.append({
                "custom_id": custom_id,
                "params": {"model": MODEL, "max_tokens": 256, "temperature": 0,
                           "messages": [{"role": "user", "content": content}]},
            })
    return requests, case_by_custom_id


def load_rows(manifest: Path) -> list[dict]:
    if hashlib.sha256(manifest.read_bytes()).hexdigest() != MANIFEST_SHA256:
        raise SystemExit("stored manifest sha256 mismatch")
    evidence_root = manifest.parent / "evidence"
    rows = []
    for path in sorted(evidence_root.rglob("*.json")):
        row = json.loads(path.read_text())
        if row.get("test_id") != "self_contradiction_pairs":
            continue
        if row.get("verdict") not in {"pass", "fail"}:
            continue
        if row.get("judge_model") != STORED_MODEL:
            raise SystemExit(f"stored model mismatch: {path}")
        if len(row.get("judge", {}).get("ensemble", [])) != 3:
            continue
        spec = materialize_candidate_case(row["test_id"], row["case_id"])
        if row.get("expected") != spec.expected:
            raise SystemExit(f"rubric/contract bytes changed: {path}")
        prompt = materialized_text_judge_prompt(
            prompts=row["payloads"], responses=row["responses"],
            expected=spec.expected, judge_criteria=candidate_judge_criteria(spec),
        )
        stable, _separator, variable = prompt.partition("\nResponses JSON:")
        rows.append({
            "case_id": row["case_id"], "stored_verdict": row["verdict"],
            "prompt": prompt, "rubric_sha256": sha(stable),
            "variable_present": bool(variable),
        })
        if len(rows) == 50:
            return rows
    raise SystemExit(f"expected 50 eligible rows, found {len(rows)}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    preflight()
    rows = load_rows(args.manifest)
    import anthropic

    client = anthropic.Anthropic()
    requests, case_by_custom_id = build_batch_requests(rows)
    batch = client.beta.messages.batches.create(requests=requests)
    while batch.processing_status == "in_progress":
        time.sleep(10)
        batch = client.beta.messages.batches.retrieve(batch.id)
    votes: dict[str, list[str]] = {row["case_id"]: [] for row in rows}
    for result in client.beta.messages.batches.results(batch.id):
        if result.result.type != "succeeded":
            raise SystemExit(f"batch failure: {result.custom_id}:{result.result.type}")
        text = "".join(b.text for b in result.result.message.content if b.type == "text")
        votes[case_by_custom_id[result.custom_id]].append(parse_verdict(text))
    details = []
    for row in rows:
        counts = Counter(votes[row["case_id"]])
        verdict = max(("fail", "partial", "pass"), key=lambda v: (counts[v], -{"fail": 0, "partial": 1, "pass": 2}[v]))
        details.append({**{k: row[k] for k in ("case_id", "stored_verdict", "rubric_sha256")},
                        "anthropic_verdict": verdict, "agree": verdict == row["stored_verdict"]})
    report = {
        "manifest_sha256": MANIFEST_SHA256, "stored_model": STORED_MODEL,
        "anthropic_model": MODEL, "rows": len(details),
        "agreements": sum(d["agree"] for d in details), "details": details,
    }
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(args.output)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except BaseException as exc:
        # Never render exception values or tracebacks: provider SDK errors can
        # contain request context, and this command runs with production secrets.
        print(safe_failure_message(exc), file=sys.stderr)
        raise SystemExit(1) from None
