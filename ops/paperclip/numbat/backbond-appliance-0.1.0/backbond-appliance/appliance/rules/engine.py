"""Tiny, SAFE structured rule engine.

Production target is CEL (per the directive); this reference uses a restricted,
non-eval condition DSL so there is zero arbitrary-code risk in a component that
runs inside a partner's environment. Rules are declarative: field / op / value.

Rules evaluate over the WORKING event (including `_content`), because the
determination component runs locally in the partner env where content is visible.
Only the resulting determination (rule id + severity + evidence hash) is emitted.
"""
from __future__ import annotations
import re, yaml
from typing import Dict, Any, List


def _get(event: Dict[str, Any], path: str):
    cur: Any = event
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def _as_text(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, (dict, list)):
        import json
        return json.dumps(v)
    return str(v)


def _op(op: str, actual: Any, expected: Any) -> bool:
    if op == "exists":
        return actual is not None
    if op == "eq":
        return actual == expected
    if op == "ne":
        return actual != expected
    if op == "in":
        return actual in expected if isinstance(expected, (list, tuple, set)) else False
    if op == "contains":
        return _as_text(expected).lower() in _as_text(actual).lower()
    if op == "regex":
        return re.search(str(expected), _as_text(actual), re.IGNORECASE) is not None
    if op == "gt":
        try:
            return float(actual) > float(expected)
        except (TypeError, ValueError):
            return False
    if op == "lt":
        try:
            return float(actual) < float(expected)
        except (TypeError, ValueError):
            return False
    raise ValueError(f"unknown op '{op}'")


class Rule:
    def __init__(self, spec: Dict[str, Any]):
        self.id = spec["id"]
        self.severity = spec.get("severity", "medium")
        self.title = spec.get("title", self.id)
        self.tags = spec.get("tags", [])
        self.applies_to = spec.get("action_class")      # optional filter
        self.match = spec.get("match", "all")           # all | any
        self.conditions = spec.get("conditions", [])

    def evaluate(self, event: Dict[str, Any]) -> bool:
        if self.applies_to and event.get("action_class") != self.applies_to:
            return False
        results = []
        for c in self.conditions:
            actual = _get(event, c["field"])
            results.append(_op(c["op"], actual, c.get("value")))
        if not results:
            return False
        return all(results) if self.match == "all" else any(results)


class RuleSet:
    def __init__(self, rules: List[Rule]):
        self.rules = rules

    @classmethod
    def from_yaml(cls, path: str) -> "RuleSet":
        with open(path) as fh:
            doc = yaml.safe_load(fh)
        return cls([Rule(r) for r in doc.get("rules", [])])

    def determinations(self, event: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Return determinations for a WORKING event. NO content in the output."""
        out = []
        for r in self.rules:
            if r.evaluate(event):
                out.append({
                    "rule_id": r.id,
                    "severity": r.severity,
                    "title": r.title,
                    "tags": r.tags,
                    "confidence": "high",
                    "tenant_id": event.get("tenant_id"),
                    "agent_id": event.get("agent_id"),
                    "event_id": event.get("event_id"),
                    "ts": event.get("ts"),
                    "evidence_hash": event.get("evidence_hash"),
                })
        return out


class Allowlist:
    """Suppresses findings that match a benign-by-design context. An entry names a
    rule_id and conditions (evaluated on the WORKING event, locally) that mark a hit
    as expected. Scope entries tightly — an allowlist that is too broad hides real
    risk. Suppressions are counted, never silently discarded."""

    def __init__(self, entries: List[Dict[str, Any]]):
        self.by_rule: Dict[str, List[Rule]] = {}
        for e in entries:
            r = Rule({"id": e["rule_id"], "match": e.get("match", "all"),
                      "conditions": e.get("conditions", [])})
            self.by_rule.setdefault(e["rule_id"], []).append(r)

    @classmethod
    def from_yaml(cls, path: str) -> "Allowlist":
        with open(path) as fh:
            doc = yaml.safe_load(fh) or {}
        return cls(doc.get("allowlist", []))

    def suppressed(self, finding: Dict[str, Any], event: Dict[str, Any]) -> bool:
        for r in self.by_rule.get(finding["rule_id"], []):
            if r.evaluate(event):
                return True
        return False
