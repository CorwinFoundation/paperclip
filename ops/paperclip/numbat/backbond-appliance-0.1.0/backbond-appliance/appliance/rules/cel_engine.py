"""CEL rule engine (production target per the directive).

Rules and allowlist entries carry a single `cel:` expression evaluated over the
WORKING event exposed as the variable `e` (a map, including nested `_raw`). CEL is
a safe, sandboxed expression language (Google Common Expression Language) — no
arbitrary code, appropriate for a component running in a partner environment.

Guards: expressions use has(e._raw.field) before access so a missing field is a
clean false, never an error. Regex via .matches(); numerics via int()/double().

Determination output shape is identical to the declarative engine, so the two are
interchangeable and parity-tested (tests/parity_test.py).
"""
from __future__ import annotations
import yaml
import celpy
from typing import Dict, Any, List


class _Compiled:
    def __init__(self, spec: Dict[str, Any], env: celpy.Environment):
        self.id = spec["id"]
        self.severity = spec.get("severity", "medium")
        self.title = spec.get("title", self.id)
        self.tags = spec.get("tags", [])
        self.cel = spec["cel"]
        self.program = env.program(env.compile(self.cel))

    def evaluate(self, cel_event) -> bool:
        try:
            return bool(self.program.evaluate({"e": cel_event}))
        except celpy.CELEvalError:
            return False


class CelRuleSet:
    def __init__(self, rules: List[_Compiled]):
        self.rules = rules

    @classmethod
    def from_yaml(cls, path: str) -> "CelRuleSet":
        env = celpy.Environment()
        with open(path) as fh:
            doc = yaml.safe_load(fh) or {}
        return cls([_Compiled(r, env) for r in doc.get("rules", [])])

    def determinations(self, event: Dict[str, Any]) -> List[Dict[str, Any]]:
        cel_event = celpy.json_to_cel(_json_safe(event))
        out = []
        for r in self.rules:
            if r.evaluate(cel_event):
                out.append({
                    "rule_id": r.id, "severity": r.severity, "title": r.title,
                    "tags": r.tags, "confidence": "high",
                    "tenant_id": event.get("tenant_id"), "agent_id": event.get("agent_id"),
                    "event_id": event.get("event_id"), "ts": event.get("ts"),
                    "evidence_hash": event.get("evidence_hash"),
                })
        return out


class CelAllowlist:
    def __init__(self, entries: List[Dict[str, Any]], env: celpy.Environment):
        self.by_rule: Dict[str, list] = {}
        for e in entries:
            prog = env.program(env.compile(e["cel"]))
            self.by_rule.setdefault(e["rule_id"], []).append(prog)

    @classmethod
    def from_yaml(cls, path: str) -> "CelAllowlist":
        env = celpy.Environment()
        with open(path) as fh:
            doc = yaml.safe_load(fh) or {}
        return cls(doc.get("allowlist", []), env)

    def suppressed(self, finding: Dict[str, Any], event: Dict[str, Any]) -> bool:
        progs = self.by_rule.get(finding["rule_id"], [])
        if not progs:
            return False
        cel_event = celpy.json_to_cel(_json_safe(event))
        for p in progs:
            try:
                if bool(p.evaluate({"e": cel_event})):
                    return True
            except celpy.CELEvalError:
                continue
        return False


def is_cel_pack(path: str) -> bool:
    """A pack is CEL if its filename says so or any rule/entry carries a `cel:` key."""
    if path.endswith(".cel.yaml"):
        return True
    try:
        with open(path) as fh:
            doc = yaml.safe_load(fh) or {}
    except OSError:
        return False
    items = (doc.get("rules") or []) + (doc.get("allowlist") or [])
    return any(isinstance(i, dict) and "cel" in i for i in items)


def _json_safe(event: Dict[str, Any]) -> Dict[str, Any]:
    """Drop non-JSON-native values so json_to_cel is happy (e.g. keep only the
    fields rules read; _raw/_content are plain dicts already)."""
    import json
    return json.loads(json.dumps(event, default=str))
