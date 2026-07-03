"""BEAAA-11372 Determination Engine v1.

Pipeline (spec 3.4.B):
    candidate event -> optional advisory LLM triage -> deterministic rule
    evaluation -> determination record -> audit spine.

The deterministic rule evaluation is the ONLY thing that decides. Advisory
triage is recorded for reviewer context but is excluded from the determination
preimage, so ``determination_id`` and ``final_decision`` are identical whether or
not triage ran. Determinations are reproducible from ``rule_set_version`` plus
the immutable evidence packet alone.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

import yaml

from determination import DETERMINATION_ENGINE_VERSION
from determination.audit_spine import DeterminationAuditSpine
from determination.records import (
    DECISION_COVERED_CANDIDATE,
    DECISION_INSUFFICIENT_EVIDENCE,
    DECISION_NO_FINDING,
    DeterminationRecord,
    EvidencePacket,
    StructuredEvent,
    determination_id,
    determination_preimage,
)
from determination.rule_dsl import RuleEvalError, compare, get_path, path_missing

_RULES_DIR = Path(__file__).resolve().parent / "rules"
_DEFAULT_RULES_PATH = _RULES_DIR / "determination_rules_v1.yaml"

# Feature flag (rollback plan, BEAAA-11372): when disabled the engine still
# evaluates and returns records for inspection but never writes to the audit
# spine. Default is disabled — emission is opt-in.
_EMIT_FLAG_ENV = "BACKBOND_DETERMINATION_EMIT"

# Advisory triage is optional and non-deciding.
AdvisoryTriage = Callable[[EvidencePacket, StructuredEvent], Mapping[str, Any]]
# Policy-engine check is injectable; default is a documented stub because the
# live policy engine is a later milestone (spec build item 9).
PolicyEngineCheck = Callable[[EvidencePacket, StructuredEvent, str], Mapping[str, Any]]


class DeterminationError(ValueError):
    pass


def emission_enabled() -> bool:
    return os.getenv(_EMIT_FLAG_ENV, "0").strip().lower() in {"1", "true", "yes", "on"}


def _default_policy_engine_check(
    packet: EvidencePacket, event: StructuredEvent, peril: str
) -> dict[str, Any]:
    """Stub policy-engine check.

    The production policy engine (attach/renew eligibility, coverage-in-force)
    lands in a later milestone. v1 records an explicit, reproducible stub so the
    determination record carries the field without inventing an outcome.
    """
    return {
        "engine": "policy-engine-stub@0",
        "result": "not_evaluated",
        "reason": "policy_engine_integration_pending_spec_build_item_9",
    }


@dataclass(frozen=True)
class DeterminationRuleSet:
    version: str
    review_owner: str
    rules: tuple[dict[str, Any], ...]

    @classmethod
    def load(cls, path: Path | None = None) -> "DeterminationRuleSet":
        raw = yaml.safe_load((path or _DEFAULT_RULES_PATH).read_text(encoding="utf-8"))
        if not isinstance(raw, Mapping):
            raise DeterminationError("determination rules file is not a mapping")
        version = raw.get("rule_set_version")
        rules = raw.get("rules")
        if not isinstance(version, str) or not version:
            raise DeterminationError("rule_set_version missing")
        if not isinstance(rules, list) or not rules:
            raise DeterminationError("determination rules table is empty")
        return cls(
            version=version,
            review_owner=str(raw.get("review_owner", "determination_reviewer")),
            rules=tuple(dict(r) for r in rules),
        )


def _match_predicate(view: Mapping[str, Any], predicate: Mapping[str, Any]) -> bool:
    """Evaluate a declarative predicate against one event view."""
    if "all" in predicate:
        return all(_match_predicate(view, p) for p in predicate["all"])
    if "any" in predicate:
        return any(_match_predicate(view, p) for p in predicate["any"])
    if "not" in predicate:
        return not _match_predicate(view, predicate["not"])
    if "present" in predicate:
        return all(not path_missing(view, p) for p in predicate["present"])
    if "missing" in predicate:
        return all(path_missing(view, p) for p in predicate["missing"])

    path = predicate.get("path")
    operator = predicate.get("operator")
    if path is None or operator is None:
        raise DeterminationError(f"unsupported determination predicate: {predicate}")
    actual = get_path(view, str(path))
    if "value_path" in predicate:
        expected = get_path(view, str(predicate["value_path"]))
    else:
        expected = predicate.get("value")
    try:
        return compare(actual, str(operator), expected)
    except RuleEvalError as exc:
        raise DeterminationError(str(exc)) from exc


class DeterminationEngine:
    def __init__(
        self,
        rule_set: DeterminationRuleSet | None = None,
        *,
        policy_engine_check: PolicyEngineCheck | None = None,
        advisory_triage: AdvisoryTriage | None = None,
        audit_spine: DeterminationAuditSpine | None = None,
        emit: bool | None = None,
    ) -> None:
        self.rule_set = rule_set or DeterminationRuleSet.load()
        self.policy_engine_check = policy_engine_check or _default_policy_engine_check
        self.advisory_triage = advisory_triage
        self.audit_spine = audit_spine
        # Explicit emit overrides the env flag; otherwise consult the flag.
        self._emit = emission_enabled() if emit is None else emit

    def evaluate(self, packet: EvidencePacket) -> list[DeterminationRecord]:
        """Return one or more determination records for the packet.

        One record per event that fires a rule (candidate or insufficient). If no
        event fires any rule, a single packet-level no-finding record is emitted
        so negatives are reproducible in the book of record too.
        """
        records: list[DeterminationRecord] = []
        for event in packet.events:
            fired = self._first_matching_rule(event)
            if fired is None:
                continue
            rule, decision = fired
            records.append(self._build_record(packet, event, rule, decision))
        if not records:
            records.append(self._no_finding_record(packet))
        return records

    def evaluate_and_emit(self, packet: EvidencePacket) -> list[DeterminationRecord]:
        """Evaluate and, when emission is enabled, write to the audit spine."""
        records = self.evaluate(packet)
        if self._emit and self.audit_spine is not None:
            for record in records:
                self.audit_spine.append(record)
        return records

    _DECISION_ALIASES = {
        "covered_candidate": DECISION_COVERED_CANDIDATE,
        "insufficient_evidence": DECISION_INSUFFICIENT_EVIDENCE,
        DECISION_COVERED_CANDIDATE: DECISION_COVERED_CANDIDATE,
        DECISION_INSUFFICIENT_EVIDENCE: DECISION_INSUFFICIENT_EVIDENCE,
    }

    def _first_matching_rule(
        self, event: StructuredEvent
    ) -> tuple[dict[str, Any], str] | None:
        view = event.eval_view()
        for rule in self.rule_set.rules:
            match = rule.get("match")
            if not isinstance(match, Mapping):
                raise DeterminationError(f"rule {rule.get('id')} missing match block")
            if not _match_predicate(view, match):
                continue
            decision_key = str(rule.get("decision", "covered_candidate"))
            if decision_key not in self._DECISION_ALIASES:
                raise DeterminationError(
                    f"rule {rule.get('id')} has unknown decision {decision_key!r}"
                )
            return rule, self._DECISION_ALIASES[decision_key]
        return None

    def _build_record(
        self,
        packet: EvidencePacket,
        event: StructuredEvent,
        rule: Mapping[str, Any],
        decision: str,
    ) -> DeterminationRecord:
        peril = str(rule.get("peril"))
        rule_id = str(rule.get("id"))
        review_required = decision in {
            DECISION_COVERED_CANDIDATE,
            DECISION_INSUFFICIENT_EVIDENCE,
        }
        trigger = {
            "evidence_event_id": event.evidence_event_id,
            "event_kind": event.event_kind,
            "event_timestamp": event.event_timestamp,
            "action_class": get_path(event.eval_view(), "action_class"),
            "outcome_status": get_path(event.eval_view(), "outcome_status"),
        }
        source_spans = [
            {
                "evidence_event_id": event.evidence_event_id,
                "trace_id": event.trace_id,
                "span_id": event.span_id,
                "content_sha256": event.content_sha256,
            }
        ]
        content_hashes = [event.content_sha256, packet.manifest_content_sha256]
        policy_check = dict(self.policy_engine_check(packet, event, peril))

        preimage = determination_preimage(
            rule_set_version=self.rule_set.version,
            engine_version=DETERMINATION_ENGINE_VERSION,
            peril=peril,
            triggered_rule_id=rule_id,
            final_decision=decision,
            trigger=trigger,
            source_spans=source_spans,
            content_hashes=content_hashes,
            evidence_packet_ref=packet.packet_ref(),
            policy_engine_check=policy_check,
        )
        det_id = determination_id(preimage)

        # Advisory triage runs AFTER the deciding preimage is fixed, and only for
        # reviewer context. It cannot change det_id or the decision.
        triage = None
        if self.advisory_triage is not None:
            triage = {
                "advisory_only": True,
                "excluded_from_deciding_path": True,
                "output": dict(self.advisory_triage(packet, event)),
            }

        return DeterminationRecord(
            determination_id=det_id,
            determination_preimage_sha256=det_id,
            rule_set_version=self.rule_set.version,
            engine_version=DETERMINATION_ENGINE_VERSION,
            peril=peril,
            triggered_rule_id=rule_id,
            final_decision=decision,
            review_required=review_required,
            review_owner=self.rule_set.review_owner,
            trigger=trigger,
            source_spans=tuple(source_spans),
            content_hashes=tuple(content_hashes),
            evidence_packet_ref=packet.packet_ref(),
            policy_engine_check=policy_check,
            audit=self._audit(packet),
            advisory_triage=triage,
        )

    def _no_finding_record(self, packet: EvidencePacket) -> DeterminationRecord:
        policy_check = {
            "engine": "policy-engine-stub@0",
            "result": "not_applicable",
            "reason": "no_candidate_event",
        }
        preimage = determination_preimage(
            rule_set_version=self.rule_set.version,
            engine_version=DETERMINATION_ENGINE_VERSION,
            peril=None,
            triggered_rule_id=None,
            final_decision=DECISION_NO_FINDING,
            trigger={"evidence_batch_id": packet.evidence_batch_id},
            source_spans=[],
            content_hashes=[packet.manifest_content_sha256],
            evidence_packet_ref=packet.packet_ref(),
            policy_engine_check=policy_check,
        )
        det_id = determination_id(preimage)
        return DeterminationRecord(
            determination_id=det_id,
            determination_preimage_sha256=det_id,
            rule_set_version=self.rule_set.version,
            engine_version=DETERMINATION_ENGINE_VERSION,
            peril=None,
            triggered_rule_id=None,
            final_decision=DECISION_NO_FINDING,
            review_required=False,
            review_owner=self.rule_set.review_owner,
            trigger={"evidence_batch_id": packet.evidence_batch_id},
            source_spans=(),
            content_hashes=(packet.manifest_content_sha256,),
            evidence_packet_ref=packet.packet_ref(),
            policy_engine_check=policy_check,
            audit=self._audit(packet),
            advisory_triage=None,
        )

    @staticmethod
    def _audit(packet: EvidencePacket) -> dict[str, Any]:
        return {
            "scanner_version": packet.scanner_version,
            "corpus_version": packet.corpus_version,
            "probe_set_version": packet.probe_set_version,
            "collector_version": packet.collector_version,
            "manifest_signature_algorithm": packet.manifest_signature_algorithm,
        }


__all__ = [
    "AdvisoryTriage",
    "DeterminationEngine",
    "DeterminationError",
    "DeterminationRuleSet",
    "PolicyEngineCheck",
    "emission_enabled",
]
