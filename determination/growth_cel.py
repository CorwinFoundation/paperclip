"""Pinned, content-free CEL rule pack for growth-agent perils (BEAAA-20177).

This is the production port of the reference appliance's declarative growth pack
(`ops/paperclip/numbat/backbond-appliance-0.1.0/.../growth_agent_pack.yaml`).  The
reference pack matches on content — `_raw.text`, regex term lists, per-event flags —
because it runs inside the partner environment where content is visible.  Production
never sees any of that.  The partner environment reduces its local matches to
counters, and this module decides verdicts from counters alone.

Two properties carry the port:

* **No new evaluator.**  Expressions compile against the same closed CEL subset the
  voice lane already ships (`voice_cel.CLOSED_SUBSET`).  Porting six growth perils
  adds six intents and zero expression capability.
* **Monitor-only, structurally.**  The pack declares `posture: monitor_only`, the
  loader accepts no other value, and per-rule keys are closed to
  `{intent_id, expression}` — so `enforce: true` cannot be expressed at all, in the
  pack or in a candidate promoted over it.

Rule intent is preserved one-for-one with the reference pack; see
`determination.growth_parity` for the reduction that carries that intent across the
boundary, and `tests/test_beaaa20177_growth_cel.py` for the synthetic parity proof.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from determination.voice_cel import (
    CLOSED_SUBSET,
    COMMITMENT_REF_PATTERN,
    FORBIDDEN_KEYS as VOICE_FORBIDDEN_KEYS,
    SOURCE_STATES,
    VoiceCelError,
    reject_forbidden_keys,
)


RULE_PACK_PATH = Path(__file__).with_name("rules") / "growth_perils_cel_v1.json"
RELEASED_VERSION = "growth-perils-cel@1.0.0"
POSTURE = "monitor_only"

#: Growth agents work over outreach and CRM records, so the voice lane's audio and
#: transcript vocabulary is necessary but not sufficient.  These are the content and
#: identity fields a growth peril would be tempted to carry across the boundary.
GROWTH_FORBIDDEN_KEYS = frozenset(
    {
        "ad_copy",
        "address",
        "attachment",
        "attachments",
        "body",
        "caption",
        "campaign_copy",
        "contact",
        "contacts",
        "copy",
        "creative",
        "creatives",
        "crm_record",
        "crm_records",
        "customer",
        "customers",
        "dm",
        "dms",
        "email",
        "email_address",
        "first_name",
        "flagged_terms",
        "full_name",
        "handle",
        "handles",
        "keyword",
        "keywords",
        "last_name",
        "lead",
        "leads",
        "link",
        "links",
        "matched_terms",
        "matched_text",
        "message",
        "message_body",
        "messages",
        "name",
        "note",
        "notes",
        "payload",
        "payloads",
        "post",
        "posts",
        "record",
        "records",
        "recipient",
        "recipients",
        "sender",
        "subject",
        "url",
        "urls",
    }
)

#: The full set enforced on this lane.  Wider than voice, never narrower.
FORBIDDEN_KEYS = VOICE_FORBIDDEN_KEYS | GROWTH_FORBIDDEN_KEYS

#: Determination-layer labels for the six ported perils.  These name peril intents on
#: the Passive Monitor surface; they are not entries in the canonical scoring sheet
#: (`rubric/rubric.yaml`) and carry no weight, exactly as the voice lane's VOICE.*
#: labels do not.
RUBRIC_POINTS = {
    "unauthorized_spend_without_confirmation": "GROWTH.UNAUTHORIZED_SPEND",
    "brand_safety_signal": "GROWTH.BRAND_SAFETY",
    "outreach_compliance_missing": "GROWTH.OUTREACH_COMPLIANCE",
    "crm_data_misuse": "GROWTH.CRM_DATA_MISUSE",
    "impersonation_signal": "GROWTH.IMPERSONATION",
    "off_scope_behavior": "GROWTH.OFF_SCOPE_BEHAVIOR",
}

EXPECTED_INTENTS = frozenset(RUBRIC_POINTS)


class GrowthCelError(VoiceCelError):
    """The growth rule pack or supplied metadata violated the closed boundary.

    Subclasses the voice error so a caller guarding the determination boundary
    catches both lanes with one except clause.
    """


def reject_growth_forbidden_keys(value: Any, path: str = "$") -> None:
    """Recursive content-key rejection using this lane's wider vocabulary."""
    try:
        reject_forbidden_keys(value, path, FORBIDDEN_KEYS)
    except VoiceCelError as exc:
        raise GrowthCelError(str(exc)) from exc


def _validate_output_metadata(commitment_ref: str, source_state: str) -> None:
    """Fail closed before caller-controlled metadata reaches the output."""
    if not isinstance(commitment_ref, str) or COMMITMENT_REF_PATTERN.fullmatch(commitment_ref) is None:
        raise GrowthCelError("commitment_ref must be a canonical sha256 digest")
    if not isinstance(source_state, str) or source_state not in SOURCE_STATES:
        raise GrowthCelError("source_state is outside the continuity-state allowlist")


@dataclass(frozen=True)
class CompiledRule:
    intent_id: str
    violation_field: str
    baseline_field: str


@dataclass(frozen=True)
class GrowthCelPack:
    rule_pack_version: str
    rules: tuple[CompiledRule, ...]

    @classmethod
    def load(
        cls, path: Path = RULE_PACK_PATH, *, expected_version: str = RELEASED_VERSION
    ) -> "GrowthCelPack":
        """Load and compile a pack, failing closed on anything off the released pin.

        `expected_version` is a keyword so that promoting a candidate pack is an
        explicit, reviewable act at the call site.  A pack dropped on disk at a new
        version does not become live by being there.
        """
        raw = json.loads(path.read_text(encoding="utf-8"))
        reject_growth_forbidden_keys(raw)
        if set(raw) != {"rule_pack_version", "posture", "rules"}:
            raise GrowthCelError("rule pack has unknown top-level keys")
        version = raw["rule_pack_version"]
        if version != expected_version:
            raise GrowthCelError("rule_pack_version is not the expected pin")
        if raw["posture"] != POSTURE:
            raise GrowthCelError("rule pack posture must be monitor_only")
        compiled: list[CompiledRule] = []
        for item in raw["rules"]:
            if set(item) != {"intent_id", "expression"}:
                raise GrowthCelError("rule has unknown keys")
            match = CLOSED_SUBSET.fullmatch(item["expression"])
            if match is None:
                raise GrowthCelError(
                    f"expression is outside the closed CEL subset: {item['intent_id']}"
                )
            compiled.append(
                CompiledRule(
                    intent_id=item["intent_id"],
                    violation_field=match.group("left"),
                    baseline_field=match.group("right"),
                )
            )
        intents = {rule.intent_id for rule in compiled}
        if len(compiled) != len(intents):
            raise GrowthCelError("rule pack repeats a peril intent")
        if not EXPECTED_INTENTS <= intents:
            missing = ", ".join(sorted(EXPECTED_INTENTS - intents))
            raise GrowthCelError(f"rule pack is missing ported peril intents: {missing}")
        return cls(version, tuple(compiled))

    def evaluate(
        self,
        intent_id: str,
        evidence: Mapping[str, Any],
        *,
        commitment_ref: str,
        source_state: str,
    ) -> dict[str, Any]:
        reject_growth_forbidden_keys(evidence)
        _validate_output_metadata(commitment_ref, source_state)
        rule = next((candidate for candidate in self.rules if candidate.intent_id == intent_id), None)
        if rule is None:
            raise GrowthCelError(f"unknown intent_id: {intent_id}")
        rubric_point_id = RUBRIC_POINTS.get(intent_id)
        if rubric_point_id is None:
            # A candidate pack may carry an intent this build has no label for.  Emitting
            # an unlabelled determination would put a peril on the wire that no reviewer
            # approved, so promotion stays a two-key change: pack plus code.
            raise GrowthCelError(f"intent has no rubric point label in this build: {intent_id}")
        allowed = {rule.violation_field, rule.baseline_field}
        if set(evidence) - allowed:
            raise GrowthCelError("evidence has fields outside the rule allowlist")
        left = evidence.get(rule.violation_field)
        right = evidence.get(rule.baseline_field)
        if left is None or right is None:
            verdict = "indeterminate"
            observed_count = 0
        else:
            if type(left) is not int or type(right) is not int or left < 0 or right < 0:
                raise GrowthCelError("evidence counters must be non-negative integers")
            verdict = "fail" if left > 0 else "pass"
            observed_count = right
        output = {
            "rule_pack_version": self.rule_pack_version,
            "intent_id": intent_id,
            "rubric_point_id": rubric_point_id,
            "posture": POSTURE,
            "verdict": verdict,
            "observed_count": observed_count,
            "commitment_ref": commitment_ref,
            "health": {"source_state": source_state},
        }
        reject_growth_forbidden_keys(output)
        return output


__all__ = [
    "EXPECTED_INTENTS",
    "FORBIDDEN_KEYS",
    "GROWTH_FORBIDDEN_KEYS",
    "POSTURE",
    "RELEASED_VERSION",
    "RUBRIC_POINTS",
    "SOURCE_STATES",
    "CompiledRule",
    "GrowthCelError",
    "GrowthCelPack",
    "reject_growth_forbidden_keys",
]
