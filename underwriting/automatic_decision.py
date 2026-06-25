"""BEAAA-8539 deterministic automatic-underwriting decision engine.

Consumes a normalized BackBond Scanner envelope and emits an underwriting-side
decision annotation. This does not alter contracts/are-scanner-api-v1.md@v1.0
output fields or scanner scoring math.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_POLICY_PATH = (
    _REPO_ROOT
    / "artifacts"
    / "beaaa-8539"
    / "BEAAA-8539_automatic_underwriting_rules_v0.1.yaml"
)

POLICY_VERSION = "automatic-underwriting-decision-policy@0.1"
RULES_TABLE_VERSION = "BEAAA-8539_automatic_underwriting_rules_v0.1"

_CLASS_RANK = {"A": 0, "B": 1, "C": 2, "D": 3}
_RESCAN_SEVERITIES = {"blocker", "referral"}


class AutomaticDecisionError(ValueError):
    pass


@dataclass(frozen=True)
class FiredRule:
    rule_id: str
    severity: str
    reason_code: str
    input_paths: tuple[str, ...]
    observed_values: dict[str, Any]
    evidence_refs: tuple[str, ...]
    remediation: str
    rescan_required: bool

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["input_paths"] = list(self.input_paths)
        data["evidence_refs"] = list(self.evidence_refs)
        return data


@dataclass(frozen=True)
class AutomaticDecision:
    policy_version: str
    rules_table_version: str
    decision_id: str
    policy_class: str
    engine_outcome: str
    backbond_score: float | int
    scanner_band: str
    auto_resolved: bool
    real_policy_bound: bool
    human_review_required: bool
    human_review_owner: str
    premium_band: dict[str, Any]
    fired_rules: tuple[FiredRule, ...]
    bind_blockers: tuple[str, ...] = field(default_factory=tuple)
    evidence_refs: tuple[str, ...] = field(default_factory=tuple)
    audit: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy_version": self.policy_version,
            "rules_table_version": self.rules_table_version,
            "decision_id": self.decision_id,
            "policy_class": self.policy_class,
            "engine_outcome": self.engine_outcome,
            "backbond_score": self.backbond_score,
            "scanner_band": self.scanner_band,
            "auto_resolved": self.auto_resolved,
            "real_policy_bound": self.real_policy_bound,
            "human_review_required": self.human_review_required,
            "human_review_owner": self.human_review_owner,
            "premium_band": dict(self.premium_band),
            "fired_rules": [rule.to_dict() for rule in self.fired_rules],
            "bind_blockers": list(self.bind_blockers),
            "evidence_refs": list(self.evidence_refs),
            "audit": dict(self.audit),
        }


def canonical_decision_bytes(decision: AutomaticDecision | Mapping[str, Any]) -> bytes:
    payload = decision.to_dict() if isinstance(decision, AutomaticDecision) else decision
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def load_policy(path: Path | None = None) -> dict[str, Any]:
    policy_path = path or _DEFAULT_POLICY_PATH
    raw = yaml.safe_load(policy_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise AutomaticDecisionError(f"policy table is not a mapping: {policy_path}")
    rules = raw.get("rules")
    if not isinstance(rules, list) or not rules:
        raise AutomaticDecisionError("policy table has no rules")
    return raw


def decide(
    envelope: Mapping[str, Any],
    *,
    policy: Mapping[str, Any] | None = None,
    policy_path: Path | None = None,
) -> AutomaticDecision:
    table = dict(policy) if policy is not None else load_policy(policy_path)
    rules = _ordered_rules(table)
    fired: list[FiredRule] = []
    result_class: str | None = None

    for rule in rules:
        match = _match_rule(envelope, rule)
        if not match.matched:
            continue
        rule_class = _result_class_for_rule(envelope, rule)
        fired_rule = _fired_rule(rule, match, envelope)
        fired.append(fired_rule)
        if result_class is None or _CLASS_RANK[rule_class] > _CLASS_RANK[result_class]:
            result_class = rule_class
        if bool(rule.get("terminal")):
            break

    if result_class is None:
        raise AutomaticDecisionError("no BEAAA-8539 underwriting rule matched envelope")

    class_config = _class_config(table, result_class)
    premium = _premium_band(table, result_class)
    score = _get_required(envelope, "are_total")
    scanner_band = _band_letter(_get_required(envelope, "band"))
    audit = {
        "scanner_version": envelope.get("scanner_version"),
        "corpus_version": envelope.get("corpus_version"),
        "model_versions": envelope.get("model_versions"),
        "seed": envelope.get("seed"),
        "run_timestamp": envelope.get("run_timestamp"),
        "evidence_complete": _evidence_complete(envelope),
    }
    decision_payload = {
        "policy_version": POLICY_VERSION,
        "rules_table_version": RULES_TABLE_VERSION,
        "policy_class": result_class,
        "engine_outcome": class_config["engine_outcome"],
        "backbond_score": score,
        "scanner_band": scanner_band,
        "fired_rule_ids": [rule.rule_id for rule in fired],
        "audit": audit,
    }
    decision_id = hashlib.sha256(canonical_decision_bytes(decision_payload)).hexdigest()
    blockers = tuple(
        rule.reason_code
        for rule in fired
        if rule.severity == "blocker" or result_class in {"B", "D"}
    )

    return AutomaticDecision(
        policy_version=POLICY_VERSION,
        rules_table_version=RULES_TABLE_VERSION,
        decision_id=decision_id,
        policy_class=result_class,
        engine_outcome=class_config["engine_outcome"],
        backbond_score=score,
        scanner_band=scanner_band,
        auto_resolved=bool(class_config["auto_resolved"]),
        real_policy_bound=bool(class_config["real_policy_bound"]),
        human_review_required=bool(class_config["human_review_required"]),
        human_review_owner=str(class_config["human_review_owner"]),
        premium_band=premium,
        fired_rules=tuple(fired),
        bind_blockers=blockers,
        evidence_refs=tuple(_envelope_evidence_uris(envelope)),
        audit=audit,
    )


@dataclass(frozen=True)
class _Match:
    matched: bool
    input_paths: tuple[str, ...] = ()
    observed_values: dict[str, Any] = field(default_factory=dict)
    evidence_refs: tuple[str, ...] = ()


def _ordered_rules(table: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    rules = table.get("rules") or []
    order = table.get("evaluation_order") or []
    order_index = {stage: i for i, stage in enumerate(order)}
    return sorted(
        rules,
        key=lambda r: (order_index.get(r.get("stage"), len(order_index)), rules.index(r)),
    )


def _class_config(table: Mapping[str, Any], policy_class: str) -> Mapping[str, Any]:
    classes = table.get("classes") or {}
    cfg = classes.get(policy_class)
    if not isinstance(cfg, Mapping):
        raise AutomaticDecisionError(f"missing class config for {policy_class}")
    return cfg


def _premium_band(table: Mapping[str, Any], policy_class: str) -> dict[str, Any]:
    mapping = ((table.get("premium_interface") or {}).get("mapping") or {})
    row = mapping.get(policy_class)
    if not isinstance(row, Mapping):
        raise AutomaticDecisionError(f"missing premium mapping for {policy_class}")
    return {
        "status": row.get("status"),
        "band_factor_stub": row.get("band_factor_stub"),
        "quote_bound": bool(row.get("quote_bound")),
        "requires_actuary_rate_module": bool(
            (table.get("premium_interface") or {}).get("production_rate_module_required")
        ),
    }


def _result_class_for_rule(envelope: Mapping[str, Any], rule: Mapping[str, Any]) -> str:
    by_score = rule.get("result_class_by_score")
    if isinstance(by_score, Mapping):
        score = float(_get_required(envelope, "are_total"))
        if score >= 70:
            return str(by_score["score_gte_70"])
        if score >= 55:
            return str(by_score["score_gte_55_lt_70"])
        return str(by_score["score_lt_55"])
    result = rule.get("result_class")
    if result not in _CLASS_RANK:
        raise AutomaticDecisionError(f"rule {rule.get('id')} has invalid result_class")
    return str(result)


def _match_rule(envelope: Mapping[str, Any], rule: Mapping[str, Any]) -> _Match:
    predicate = rule.get("predicate")
    if not isinstance(predicate, Mapping):
        raise AutomaticDecisionError(f"rule {rule.get('id')} predicate is not a mapping")
    return _match_predicate(envelope, predicate)


def _match_predicate(envelope: Mapping[str, Any], predicate: Mapping[str, Any]) -> _Match:
    if "all" in predicate:
        matches = [_match_predicate(envelope, p) for p in predicate["all"]]
        if not all(match.matched for match in matches):
            return _Match(False)
        return _merge_matches(matches)

    if "any_missing" in predicate:
        paths = tuple(str(p) for p in predicate["any_missing"])
        missing = [p for p in paths if _path_missing(envelope, p)]
        return _Match(
            bool(missing),
            paths,
            {"missing_fields": missing},
            tuple(_evidence_for_paths(envelope, paths)),
        )

    if "evidence_refs_complete" in predicate:
        actual = _evidence_complete(envelope)
        expected = bool(predicate["evidence_refs_complete"])
        return _simple_match(
            actual == expected,
            ("evidence_refs",),
            {"evidence_refs_complete": actual},
            _envelope_evidence_uris(envelope),
        )

    if "band_score_mismatch" in predicate:
        actual = _band_score_mismatch(envelope)
        expected = bool(predicate["band_score_mismatch"])
        return _simple_match(
            actual == expected,
            ("are_total", "band"),
            {"are_total": envelope.get("are_total"), "band": envelope.get("band")},
            _evidence_for_paths(envelope, ("are_total", "band")),
        )

    if "any_peril_finding" in predicate:
        wanted = predicate["any_peril_finding"]
        findings = envelope.get("peril_findings") or []
        matched = [
            f for f in findings
            if isinstance(f, Mapping)
            and all(f.get(k) == v for k, v in wanted.items())
        ]
        return _simple_match(
            bool(matched),
            ("peril_findings",),
            {"peril_findings": matched},
            _evidence_from_items(matched),
        )

    if "any_path_in_map" in predicate:
        path = str(predicate["any_path_in_map"])
        data = _get_path(envelope, path)
        if not isinstance(data, Mapping):
            data = {}
        hits = {
            f"{path}.{key}": value
            for key, value in sorted(data.items())
            if _compare(value, str(predicate["operator"]), predicate.get("value"))
        }
        return _simple_match(
            bool(hits),
            tuple(hits.keys()) or (path,),
            hits,
            _evidence_for_paths(envelope, tuple(hits.keys()) or (path,)),
        )

    path = predicate.get("path")
    operator = predicate.get("operator")
    if path is not None and operator is not None:
        path = str(path)
        actual = _get_path(envelope, path)
        matched = _compare(actual, str(operator), predicate.get("value"))
        return _simple_match(
            matched,
            (path,),
            {path: actual},
            _evidence_for_paths(envelope, (path,)),
        )

    raise AutomaticDecisionError(f"unsupported predicate: {predicate}")


def _fired_rule(rule: Mapping[str, Any], match: _Match, envelope: Mapping[str, Any]) -> FiredRule:
    evidence = match.evidence_refs or tuple(_envelope_evidence_uris(envelope))
    severity = str(rule.get("severity"))
    return FiredRule(
        rule_id=str(rule["id"]),
        severity=severity,
        reason_code=str(rule["reason_code"]),
        input_paths=match.input_paths,
        observed_values=match.observed_values,
        evidence_refs=evidence,
        remediation=str(rule.get("remediation", "")),
        rescan_required=severity in _RESCAN_SEVERITIES,
    )


def _compare(actual: Any, operator: str, expected: Any) -> bool:
    if operator == "equals":
        return actual == expected
    if operator == "in":
        return actual in expected
    if operator == "less_than":
        return _num(actual) < _num(expected)
    if operator == "greater_than_or_equal":
        return _num(actual) >= _num(expected)
    if operator == "length_greater_than":
        return _length(actual) > int(expected)
    if operator == "between_inclusive":
        lo, hi = expected
        value = _num(actual)
        return _num(lo) <= value <= _num(hi)
    if operator == "in_ranges":
        value = _num(actual)
        return any(_num(lo) <= value <= _num(hi) for lo, hi in expected)
    raise AutomaticDecisionError(f"unsupported operator: {operator}")


def _num(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise AutomaticDecisionError(f"expected numeric value, got {value!r}") from exc


def _length(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, Mapping):
        return len(value)
    if isinstance(value, (str, bytes)):
        return len(value)
    if isinstance(value, Iterable):
        return sum(1 for _ in value)
    return 0


def _get_required(envelope: Mapping[str, Any], path: str) -> Any:
    value = _get_path(envelope, path)
    if value is None:
        raise AutomaticDecisionError(f"envelope missing required field {path}")
    return value


def _get_path(data: Mapping[str, Any], path: str) -> Any:
    current: Any = data
    for part in path.split("."):
        if isinstance(current, Mapping) and part in current:
            current = current[part]
        else:
            return None
    return current


def _path_missing(data: Mapping[str, Any], path: str) -> bool:
    value = _get_path(data, path)
    return value is None or value == "" or value == []


def _band_letter(raw: Any) -> str:
    if isinstance(raw, str):
        return raw
    if isinstance(raw, Mapping) and isinstance(raw.get("letter"), str):
        return raw["letter"]
    raise AutomaticDecisionError(f"band is not a letter or letter-bearing object: {raw!r}")


def _band_score_mismatch(envelope: Mapping[str, Any]) -> bool:
    score = _num(envelope.get("are_total"))
    observed = _band_letter(envelope.get("band"))
    expected = "A" if score >= 85 else "B" if score >= 70 else "C" if score >= 55 else "D"
    return observed != expected


def _evidence_complete(envelope: Mapping[str, Any]) -> bool:
    refs = envelope.get("evidence_refs")
    if not isinstance(refs, list) or not refs:
        return False
    required = ("rubric_point_id", "test_id", "verdict", "weight")
    for ref in refs:
        if not isinstance(ref, Mapping):
            return False
        if any(ref.get(key) in (None, "") for key in required):
            return False
        if ref.get("verdict") == "n/a" and ref.get("weight") == 0.0:
            if ref.get("na_justification_key") or ref.get("na_justification"):
                continue
        if ref.get("artifact_uri") in (None, ""):
            return False
    return True


def _envelope_evidence_uris(envelope: Mapping[str, Any]) -> list[str]:
    refs = envelope.get("evidence_refs") or []
    return [
        str(ref.get("artifact_uri"))
        for ref in refs
        if isinstance(ref, Mapping) and ref.get("artifact_uri")
    ]


def _evidence_from_items(items: Iterable[Mapping[str, Any]]) -> tuple[str, ...]:
    out: list[str] = []
    for item in items:
        refs = item.get("evidence_refs") or item.get("evidence_ref") or []
        if isinstance(refs, str):
            out.append(refs)
        elif isinstance(refs, Iterable):
            out.extend(str(ref) for ref in refs if ref)
    return tuple(out)


def _evidence_for_paths(envelope: Mapping[str, Any], paths: Iterable[str]) -> tuple[str, ...]:
    refs = _envelope_evidence_uris(envelope)
    if not refs:
        return ()
    wanted = tuple(paths)
    matched = []
    for ref in envelope.get("evidence_refs") or []:
        if not isinstance(ref, Mapping):
            continue
        haystack = " ".join(str(ref.get(k, "")) for k in ("rubric_point_id", "test_id", "artifact_uri"))
        if any(path in haystack or path.split(".")[-1] in haystack for path in wanted):
            uri = ref.get("artifact_uri")
            if uri:
                matched.append(str(uri))
    return tuple(matched or refs[:3])


def _simple_match(
    matched: bool,
    paths: tuple[str, ...],
    observed: Mapping[str, Any],
    evidence_refs: Iterable[str],
) -> _Match:
    return _Match(matched, paths if matched else (), dict(observed) if matched else {}, tuple(evidence_refs) if matched else ())


def _merge_matches(matches: Iterable[_Match]) -> _Match:
    paths: list[str] = []
    observed: dict[str, Any] = {}
    evidence: list[str] = []
    for match in matches:
        paths.extend(match.input_paths)
        observed.update(match.observed_values)
        evidence.extend(match.evidence_refs)
    return _Match(True, tuple(paths), observed, tuple(dict.fromkeys(evidence)))


__all__ = [
    "AutomaticDecision",
    "AutomaticDecisionError",
    "FiredRule",
    "canonical_decision_bytes",
    "decide",
    "load_policy",
]
