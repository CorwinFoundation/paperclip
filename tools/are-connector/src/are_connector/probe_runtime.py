from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Any, Callable, Literal

import requests

from .canonical import canonical_json
from .http import EgressAllowlist, call_agent
from .redaction import redact_transcript_value
from .response_limits import OversizedResponseError
from .runtime import extract_response_text


ProbeStatus = Literal["pass", "fail", "error"]
VerdictRuleKind = Literal["regex", "equality", "judge"]


@dataclass(frozen=True)
class VerdictRule:
    kind: VerdictRuleKind
    expected: str | None = None
    pattern: str | None = None
    flags: int = 0
    judge_id: str | None = None


@dataclass(frozen=True)
class ProbeSpec:
    probe_id: str
    payload: dict[str, Any]
    verdict_rule: VerdictRule
    probe_set_version: str
    timeout_seconds: int = 60
    evidence_uri_prefix: str = "are-connector://probe-runtime"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProbeVerdict:
    probe_id: str
    status: ProbeStatus
    transcript_sha: str
    raw_response_capped_bytes: int
    scanner_version: str
    corpus_version: str
    model_versions: dict[str, Any]
    seed: int
    probe_set_version: str
    evidence_ref: str
    reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "probe_id": self.probe_id,
            "status": self.status,
            "transcript_sha": self.transcript_sha,
            "raw_response_capped_bytes": self.raw_response_capped_bytes,
            "scanner_version": self.scanner_version,
            "corpus_version": self.corpus_version,
            "model_versions": self.model_versions,
            "seed": self.seed,
            "probe_set_version": self.probe_set_version,
            "evidence_ref": self.evidence_ref,
            "reason": self.reason,
        }


JudgeCallout = Callable[[ProbeSpec, dict[str, Any], str], bool]


class ProbeRuntime:
    def __init__(
        self,
        agent_endpoint: str,
        scanner_version: str,
        corpus_version: str,
        model_versions: dict[str, Any],
        seed: int,
        probe_set_version: str,
        portal_base_url: str = "https://portal.local",
        judge_callout: JudgeCallout | None = None,
    ) -> None:
        self.agent_endpoint = agent_endpoint
        self.scanner_version = scanner_version
        self.corpus_version = corpus_version
        self.model_versions = model_versions
        self.seed = seed
        self.probe_set_version = probe_set_version
        self.allowlist = EgressAllowlist(portal_base_url, agent_endpoint)
        self.judge_callout = judge_callout

    def run(self, probe: ProbeSpec) -> ProbeVerdict:
        try:
            response = call_agent(
                self.agent_endpoint,
                probe.payload,
                self.allowlist,
                timeout=probe.timeout_seconds,
            )
            response_text = extract_response_text(response)
            status, reason = self._apply_rule(probe, response, response_text)
            transcript_sha = build_transcript_sha(probe.payload, response)
            raw_response_capped_bytes = len(canonical_json(response))
            return self._verdict(probe, status, transcript_sha, raw_response_capped_bytes, reason)
        except Exception as exc:
            reason = classify_probe_error(exc)
            transcript_sha = build_transcript_sha(probe.payload, {"error": reason})
            return self._verdict(probe, "error", transcript_sha, 0, reason)

    def _apply_rule(self, probe: ProbeSpec, response: dict[str, Any], response_text: str) -> tuple[ProbeStatus, str | None]:
        rule = probe.verdict_rule
        if rule.kind == "regex":
            if rule.pattern is None:
                return "error", "error_invalid_rule"
            return ("pass", None) if re.search(rule.pattern, response_text, rule.flags) else ("fail", "regex_no_match")
        if rule.kind == "equality":
            return ("pass", None) if response_text == rule.expected else ("fail", "equality_mismatch")
        if rule.kind == "judge":
            if self.judge_callout is None or not rule.judge_id:
                return "error", "error_judge_unpinnable"
            return ("pass", None) if self.judge_callout(probe, response, response_text) else ("fail", "judge_rejected")
        return "error", "error_invalid_rule"

    def _verdict(
        self,
        probe: ProbeSpec,
        status: ProbeStatus,
        transcript_sha: str,
        raw_response_capped_bytes: int,
        reason: str | None,
    ) -> ProbeVerdict:
        return ProbeVerdict(
            probe_id=probe.probe_id,
            status=status,
            transcript_sha=transcript_sha,
            raw_response_capped_bytes=raw_response_capped_bytes,
            scanner_version=self.scanner_version,
            corpus_version=self.corpus_version,
            model_versions=self.model_versions,
            seed=self.seed,
            probe_set_version=probe.probe_set_version or self.probe_set_version,
            evidence_ref=f"{probe.evidence_uri_prefix}/{probe.probe_set_version}/{probe.probe_id}/{transcript_sha}",
            reason=reason,
        )


def build_transcript_sha(request_payload: dict[str, Any], response_payload: dict[str, Any]) -> str:
    transcript = {
        "request": redact_transcript_value(request_payload),
        "response": redact_transcript_value(response_payload),
    }
    try:
        canonical = canonical_json(transcript)
    except Exception as exc:
        raise ValueError("error_redaction_failed") from exc
    return sha256(canonical).hexdigest()


def classify_probe_error(exc: Exception) -> str:
    if isinstance(exc, OversizedResponseError):
        return "error_oversized"
    if isinstance(exc, ValueError) and str(exc) == "error_redaction_failed":
        return "error_redaction_failed"
    if isinstance(exc, requests.Timeout):
        return "error_network"
    if isinstance(exc, requests.RequestException):
        return "error_network"
    name = exc.__class__.__name__.lower()
    if "connection" in name or "network" in name or "timeout" in name:
        return "error_network"
    return "error_network"


def load_probe_set(name: str, probe_set_dir: str | Path) -> list[ProbeSpec]:
    path = Path(probe_set_dir) / f"{name}.json"
    raw = json.loads(path.read_text())
    probe_set_version = raw["probe_set_version"]
    probes: list[ProbeSpec] = []
    for item in raw["probes"]:
        rule = item["verdict_rule"]
        probes.append(
            ProbeSpec(
                probe_id=item["probe_id"],
                payload=item["payload"],
                verdict_rule=VerdictRule(**rule),
                probe_set_version=item.get("probe_set_version", probe_set_version),
                timeout_seconds=int(item.get("timeout_seconds", raw.get("timeout_seconds", 60))),
                metadata=dict(item.get("metadata", {})),
            )
        )
    return probes
