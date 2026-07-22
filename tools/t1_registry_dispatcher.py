"""Registry-driven T1 execution against a real HTTP agent adapter.

BEAAA-16614 rule-execution scope.  This module consumes the frozen canonical
registry rows directly; it does not alter scoring, rubric weights, or the API
envelope.  Every selected case produces exactly one terminal attempt row.
"""
from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping


SCANNER_VERSION = "0.5.0"
CORPUS_VERSION = "scanner-test-registry-baseline-2026-07-13"
MANIFEST_SCHEMA = "backbond-t1-dispatch-manifest/v0.1"
EVIDENCE_SCHEMA = "backbond-t1-http-attempt/v0.1"
TERMINAL_STATUSES = {"complete", "incomplete", "failed"}


@dataclass(frozen=True)
class T1Attempt:
    case_id: str
    rubric_point_id: str
    status: str
    evidence_ref: str | None
    incomplete_reason: str | None


def select_t1_rows(rows: Iterable[Mapping[str, Any]]) -> tuple[dict[str, Any], ...]:
    """Select unique active/applicable T1 cases in stable case-id order."""
    selected: dict[str, dict[str, Any]] = {}
    for source in rows:
        row = dict(source)
        active = row.get("disposition") == "production_active" or row.get("status") == "active"
        executable = row.get("countable_as_active_executable") is True or row.get(
            "production_runner_executable"
        ) is True
        tier = str(row.get("source_bucket") or row.get("tier") or "")
        applicable = row.get("applicable", row.get("is_applicable", True)) is not False
        case_id = str(row.get("case_id") or "")
        if active and executable and tier.startswith("T1") and applicable and case_id:
            if case_id in selected:
                raise ValueError(f"duplicate selected T1 case_id: {case_id}")
            selected[case_id] = row
    return tuple(selected[key] for key in sorted(selected))


class HttpAgentAdapter:
    """Minimal real adapter for the canonical single-turn T1 payload shape."""

    def __init__(self, endpoint: str, *, bearer_token: str, timeout_seconds: float = 30.0):
        self.endpoint = endpoint
        self.bearer_token = bearer_token
        self.timeout_seconds = timeout_seconds

    def complete(self, prompt: str) -> str:
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps({"prompt": prompt}, sort_keys=True).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.bearer_token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
        text = payload.get("response") or payload.get("output") or payload.get("text")
        if not isinstance(text, str):
            raise ValueError("agent response missing response/output/text string")
        return text


def dispatch_t1(
    rows: Iterable[Mapping[str, Any]],
    *,
    adapter: HttpAgentAdapter | None,
    evidence_dir: Path,
) -> dict[str, Any]:
    """Execute all selected cases once and persist exact reconciliation proof."""
    selected = select_t1_rows(rows)
    evidence_dir.mkdir(parents=True, exist_ok=True)
    attempts: list[T1Attempt] = []
    for row in selected:
        case_id = str(row["case_id"])
        rubric_point_id = str(row.get("rubric_point_id") or row.get("risk_or_rubric_id") or "")
        prompt = row.get("prompt_or_action")
        status = "complete"
        reason: str | None = None
        response_text: str | None = None
        if adapter is None:
            status, reason = "incomplete", "target_adapter_unavailable"
        elif not isinstance(prompt, str) or not prompt.strip():
            status, reason = "incomplete", "registry_payload_unavailable"
        else:
            try:
                response_text = adapter.complete(prompt)
            except (OSError, ValueError, json.JSONDecodeError, urllib.error.URLError) as exc:
                status, reason = "failed", f"target_adapter_error:{type(exc).__name__}"

        evidence = {
            "schema": EVIDENCE_SCHEMA,
            "case_id": case_id,
            "rubric_point_id": rubric_point_id,
            "scanner_version": SCANNER_VERSION,
            "corpus_version": CORPUS_VERSION,
            "model_version": "controlled-http-agent-fixture/no-llm-judge",
            "prompt_or_action": prompt,
            "target_response": response_text,
            "status": status,
            "incomplete_reason": reason,
        }
        path = evidence_dir / f"{_safe(case_id)}.json"
        path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        attempts.append(T1Attempt(case_id, rubric_point_id, status, str(path), reason))

    expected = [str(row["case_id"]) for row in selected]
    attempted = [attempt.case_id for attempt in attempts]
    terminal = [attempt.case_id for attempt in attempts if attempt.status in TERMINAL_STATUSES]
    evidenced = [attempt.case_id for attempt in attempts if attempt.evidence_ref]
    exact = expected == attempted == terminal == evidenced and len(expected) == len(set(expected))
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "scanner_version": SCANNER_VERSION,
        "corpus_version": CORPUS_VERSION,
        "probe_set_version": CORPUS_VERSION,
        "rubric_point_id": "ALL_APPLICABLE_T1",
        "model_versions": ["controlled-http-agent-fixture/no-llm-judge"],
        "expected_case_ids": expected,
        "attempted_case_ids": attempted,
        "terminal_case_ids": terminal,
        "evidenced_case_ids": evidenced,
        "attempts": [asdict(attempt) for attempt in attempts],
        "exact_reconciliation": exact,
        "complete_evidenced_family_count": 1 if attempts and all(a.status == "complete" for a in attempts) else 0,
    }
    manifest["external_gate_open"] = manifest["complete_evidenced_family_count"] >= 1 and exact
    encoded = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    manifest["manifest_sha256"] = hashlib.sha256(encoded).hexdigest()
    (evidence_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def _safe(value: str) -> str:
    return "".join(char if char.isalnum() or char in "._-" else "_" for char in value)
