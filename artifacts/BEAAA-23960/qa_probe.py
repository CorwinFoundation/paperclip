#!/usr/bin/env python3
"""BEAAA-23960 QA independent probes for AC3, AC4, AC5 at SHA 99a3205.

Written against the shipped modules directly. Deliberately does NOT reuse the
producer's fixtures or helpers, so a blind spot in their harness is not
inherited by mine.
"""
import importlib.util, json, sys, copy
from pathlib import Path

ROOT = Path("/tmp/cand99a3205")
sys.path.insert(0, str(ROOT))

from determination.growth_cel import (  # noqa: E402
    GrowthCelPack, GrowthCelError, RULE_PACK_PATH, RELEASED_VERSION, EXPECTED_INTENTS,
)
from determination.growth_parity import (  # noqa: E402
    GROWTH_RULE_TO_INTENT, reduce_to_evidence, count_determinations,
)

APPLIANCE = ROOT / "ops/paperclip/numbat/backbond-appliance-0.1.0/backbond-appliance"
COMMIT = "sha256:" + "0" * 64
fails, passes = [], []


def check(name, cond, detail=""):
    (passes if cond else fails).append(name)
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"  :: {detail}" if detail else ""))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


pack = GrowthCelPack.load()

# ---------------------------------------------------------------- AC3: leak
print("\nAC3 - no synthetic content or PII reaches the production boundary")
engine = load("_qa_engine", APPLIANCE / "appliance/rules/engine.py")
pipeline = load("_qa_pipeline", APPLIANCE / "appliance/pipeline.py")
rules = engine.RuleSet.from_yaml(str(APPLIANCE / "appliance/rules/growth_agent_pack.yaml"))

# My own corpus, seeded with unmistakable canary content + fake PII.
CANARIES = ["ZZCANARYTEXTZZ", "Ada Lovelace", "ada@example.invalid", "+1-555-0142",
            "https://evil.example.invalid/promo", "@fakehandle", "4111111111111111"]
MY_EVENTS = [
    {"type": "social_post", "id": "q1", "tenant_id": "smb-qa", "agent_id": "growth",
     "ts": "2026-08-17T10:00:00Z", "body": "ZZCANARYTEXTZZ guaranteed returns, risk free",
     "channel_authorized": True},
    {"type": "outbound_email", "id": "q2", "tenant_id": "smb-qa", "agent_id": "growth",
     "ts": "2026-08-17T10:01:00Z", "body": "Hi Ada Lovelace ZZCANARYTEXTZZ",
     "recipient": "ada@example.invalid", "phone": "+1-555-0142",
     "has_unsubscribe": False, "consent": "none", "channel_authorized": True},
    {"type": "payment", "id": "q3", "tenant_id": "smb-qa", "agent_id": "growth",
     "ts": "2026-08-17T10:02:00Z", "amount": 9000, "confirmed": False,
     "card": "4111111111111111"},
    {"type": "crm_update", "id": "q4", "tenant_id": "smb-qa", "agent_id": "growth",
     "ts": "2026-08-17T10:03:00Z", "record_count": 4200, "note": "ZZCANARYTEXTZZ"},
]
working = [pipeline.normalize(dict(e)) for e in MY_EVENTS]
dets = []
for e in working:
    dets.extend(rules.determinations(e))
BASELINE_CLASS = {"growth.unauthorized_spend": "transaction", "growth.brand_safety": "message",
                  "growth.outreach_compliance": "message", "growth.crm_data_misuse": "data_access",
                  "growth.impersonation": "message", "growth.off_scope_channel": "message"}
baselines = {r: sum(1 for e in working if e["action_class"] == c) for r, c in BASELINE_CLASS.items()}

print(f"  (reference produced {len(dets)} determinations over {len(MY_EVENTS)} canary events)")
check("reference engine actually matched my canary corpus", len(dets) > 0, f"{len(dets)} determinations")

evidence = reduce_to_evidence(pack, evaluated_rule_ids=GROWTH_RULE_TO_INTENT,
                              match_counts=count_determinations(dets), baseline_counts=baselines)
outputs = [pack.evaluate(i, e, commitment_ref=COMMIT, source_state="healthy")
           for i, e in evidence.items()]
blob = json.dumps({"evidence": evidence, "outputs": outputs})
leaked = [c for c in CANARIES if c in blob]
check("no canary content/PII in production evidence+outputs", not leaked, f"leaked={leaked}")
# Every value crossing the boundary must be an int or a known-safe literal.
bad = [(i, k, v) for i, e in evidence.items() for k, v in e.items() if type(v) is not int]
check("every evidence value is a bare int", not bad, str(bad))

# A caller trying to smuggle content must fail closed, not be silently filtered.
for key in ("body", "recipient", "matched_text", "note", "full_name", "url"):
    try:
        pack.evaluate("crm_data_misuse", {"bulk_crm_access_count": 1, key: "ZZCANARYTEXTZZ"},
                      commitment_ref=COMMIT, source_state="healthy")
        check(f"smuggled '{key}' rejected", False, "ACCEPTED - boundary breach")
    except GrowthCelError:
        check(f"smuggled '{key}' rejected fail-closed", True)

# ------------------------------------------------------------- AC4: posture
print("\nAC4 - monitor-only posture, enforce:true rejected at rule and pack level")
raw = json.loads(RULE_PACK_PATH.read_text())
check("shipped pack declares monitor_only", raw["posture"] == "monitor_only", raw["posture"])
check("every emitted output carries posture=monitor_only",
      all(o["posture"] == "monitor_only" for o in outputs))
check("no output carries an enforce-ish key",
      not any(k for o in outputs for k in o if "enforc" in k.lower()))

import tempfile
def load_variant(mutate, label):
    d = copy.deepcopy(raw)
    mutate(d)
    p = Path(tempfile.mkdtemp()) / "pack.json"
    p.write_text(json.dumps(d))
    try:
        GrowthCelPack.load(p)
        return False, "LOADED"
    except GrowthCelError as e:
        return True, str(e)[:70]

for label, mut in [
    ("pack-level posture=enforce", lambda d: d.__setitem__("posture", "enforce")),
    ("pack-level posture=monitor", lambda d: d.__setitem__("posture", "monitor")),
    ("pack-level enforce:true key", lambda d: d.__setitem__("enforce", True)),
    ("rule-level enforce:true", lambda d: d["rules"][0].__setitem__("enforce", True)),
    ("rule-level posture key", lambda d: d["rules"][0].__setitem__("posture", "enforce")),
]:
    ok, why = load_variant(mut, label)
    check(f"rejected: {label}", ok, why)

# ---------------------------------------------- AC5: pin, promotion, rollback
print("\nAC5 - version pin, promotion preserves verdicts, rollback reproduces table")
ok, why = load_variant(lambda d: d.__setitem__("rule_pack_version", "growth-perils-cel@9.9.9"),
                       "unpinned version")
check("unpinned version does not load at the released pin", ok, why)

def verdict_table(p):
    return {i: p.evaluate(i, e, commitment_ref=COMMIT, source_state="healthy")["verdict"]
            for i, e in evidence.items()}

released_table = verdict_table(pack)
print(f"  released verdict table: {released_table}")

# Promotion: a candidate at a new version, loaded explicitly, adding a 7th intent.
cand = copy.deepcopy(raw)
cand["rule_pack_version"] = "growth-perils-cel@1.1.0"
cand["rules"].append({"intent_id": "new_unlabelled_peril",
                      "expression": '!has(e.x_count) || !has(e.y_count) ? "indeterminate" : '
                                    'e.x_count > 0 ? "fail" : "pass"'})
cp = Path(tempfile.mkdtemp()) / "cand.json"
cp.write_text(json.dumps(cand))
check("candidate pack does not load at the released pin",
      load_variant(lambda d: d.update(cand), "candidate at released pin")[0])
promoted = GrowthCelPack.load(cp, expected_version="growth-perils-cel@1.1.0")
promoted_table = verdict_table(promoted)
check("promotion preserves every released intent's verdict",
      promoted_table == released_table, f"{promoted_table} vs {released_table}")
check("promoted pack still monitor_only", all(
    promoted.evaluate(i, e, commitment_ref=COMMIT, source_state="healthy")["posture"] == "monitor_only"
    for i, e in evidence.items()))
try:
    promoted.evaluate("new_unlabelled_peril", {"x_count": 1, "y_count": 2},
                      commitment_ref=COMMIT, source_state="healthy")
    check("unlabelled promoted intent refuses to emit", False, "EMITTED without rubric label")
except GrowthCelError as e:
    check("unlabelled promoted intent refuses to emit", True, str(e)[:60])

rolled_back = GrowthCelPack.load()
check("rollback reproduces the pre-promotion verdict table exactly",
      verdict_table(rolled_back) == released_table)
check("rollback restores the released version pin",
      rolled_back.rule_pack_version == RELEASED_VERSION, rolled_back.rule_pack_version)

# Fail-closed on missing counters (unobserved peril must not read clean).
o = pack.evaluate("impersonation_signal", {"impersonation_signal_count": 0},
                  commitment_ref=COMMIT, source_state="healthy")
check("absent baseline yields indeterminate, not pass", o["verdict"] == "indeterminate", o["verdict"])

print(f"\nRESULT: {len(passes)} passed, {len(fails)} failed")
if fails:
    print("FAILED: " + "; ".join(fails))
sys.exit(1 if fails else 0)
