"""Pinned, content-free CEL rule pack for synthetic voice metadata.

The evaluator intentionally accepts a closed CEL subset.  This keeps released
verdicts deterministic and makes the serializable boundary auditable without
adding an LLM, an audio model, or a general-purpose expression runtime.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


RULE_PACK_PATH = Path(__file__).with_name("rules") / "voice_perils_cel_v1.json"

FORBIDDEN_KEYS = frozenset(
    {
        "audio",
        "recording",
        "recordings",
        "transcript",
        "transcripts",
        "text",
        "content",
        "prompt",
        "prompts",
        "response",
        "responses",
        "spoken_pii",
        "phone",
        "phone_id",
        "phone_identifier",
        "phone_identifiers",
        "phone_number",
        "telephone",
        "telephone_identifier",
        "excerpt",
        "excerpts",
        "raw_event",
        "raw_events",
        "platform_payload",
        "platform_payloads",
    }
)

_CEL = re.compile(
    r'^!has\(e\.(?P<left>[a-z_]+)\) \|\| !has\(e\.(?P<right>[a-z_]+)\) '
    r'\? "indeterminate" : e\.(?P=left) > 0 \? "fail" : "pass"$'
)


class VoiceCelError(ValueError):
    """The rule pack or supplied metadata violated the closed boundary."""


def _normalise_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", key.casefold()).strip("_")


def reject_forbidden_keys(value: Any, path: str = "$") -> None:
    """Reject content-bearing keys recursively, including nested lists."""
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str):
                raise VoiceCelError(f"non-string key at {path}")
            if _normalise_key(key) in FORBIDDEN_KEYS:
                raise VoiceCelError(f"forbidden key at {path}.{key}")
            reject_forbidden_keys(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            reject_forbidden_keys(child, f"{path}[{index}]")


@dataclass(frozen=True)
class CompiledRule:
    intent_id: str
    violation_field: str
    baseline_field: str


@dataclass(frozen=True)
class VoiceCelPack:
    rule_pack_version: str
    rules: tuple[CompiledRule, ...]

    @classmethod
    def load(cls, path: Path = RULE_PACK_PATH) -> "VoiceCelPack":
        raw = json.loads(path.read_text(encoding="utf-8"))
        reject_forbidden_keys(raw)
        if set(raw) != {"rule_pack_version", "rules"}:
            raise VoiceCelError("rule pack has unknown top-level keys")
        version = raw["rule_pack_version"]
        if version != "voice-perils-cel@1.0.0":
            raise VoiceCelError("rule_pack_version is not the released pin")
        compiled: list[CompiledRule] = []
        for item in raw["rules"]:
            if set(item) != {"intent_id", "expression"}:
                raise VoiceCelError("rule has unknown keys")
            match = _CEL.fullmatch(item["expression"])
            if match is None:
                raise VoiceCelError(f"expression is outside the closed CEL subset: {item['intent_id']}")
            compiled.append(
                CompiledRule(
                    intent_id=item["intent_id"],
                    violation_field=match.group("left"),
                    baseline_field=match.group("right"),
                )
            )
        if len(compiled) != 4 or len({r.intent_id for r in compiled}) != 4:
            raise VoiceCelError("rule pack must contain four unique peril intents")
        return cls(version, tuple(compiled))

    def evaluate(
        self,
        intent_id: str,
        evidence: Mapping[str, Any],
        *,
        commitment_ref: str,
        source_state: str,
    ) -> dict[str, Any]:
        reject_forbidden_keys(evidence)
        rule = next((candidate for candidate in self.rules if candidate.intent_id == intent_id), None)
        if rule is None:
            raise VoiceCelError(f"unknown intent_id: {intent_id}")
        allowed = {rule.violation_field, rule.baseline_field}
        if set(evidence) - allowed:
            raise VoiceCelError("evidence has fields outside the rule allowlist")
        left = evidence.get(rule.violation_field)
        right = evidence.get(rule.baseline_field)
        if left is None or right is None:
            verdict = "indeterminate"
            observed_count = 0
        else:
            if type(left) is not int or type(right) is not int or left < 0 or right < 0:
                raise VoiceCelError("evidence counters must be non-negative integers")
            verdict = "fail" if left > 0 else "pass"
            observed_count = right
        output = {
            "rule_pack_version": self.rule_pack_version,
            "intent_id": intent_id,
            "verdict": verdict,
            "observed_count": observed_count,
            "commitment_ref": commitment_ref,
            "health": {"source_state": source_state},
        }
        reject_forbidden_keys(output)
        return output


__all__ = ["FORBIDDEN_KEYS", "VoiceCelError", "VoiceCelPack", "reject_forbidden_keys"]
