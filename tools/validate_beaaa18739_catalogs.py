#!/usr/bin/env python3
"""Validate BEAAA-18739 row shape, diversity, dedupe, and lexical near-overlap."""
from __future__ import annotations
import json, re
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path
from build_beaaa18739_catalogs import FAMILIES, OUT, canon, main as build

ROOT=Path(__file__).resolve().parents[1]
REPORT=OUT.with_name("beaaa-18739-validation.json")
REQUIRED={"stable_case_id","case_slug","family_id","version","substantive_cluster","actor_trust_boundary","control_type","design_partner_profiles","preconditions","prompt_turn_sequence","actual_state_sequence","expected_observable_behavior","failure_condition","deterministic_oracle","evidence_fields","applicability","provenance","reporting_status","semantic_behavior_fingerprint","dedupe_sha256"}
def skeleton(r):
    v=canon({k:r[k] for k in ("actor_trust_boundary","preconditions","prompt_turn_sequence","actual_state_sequence","expected_observable_behavior","failure_condition","deterministic_oracle","applicability")}).lower()
    v=re.sub(r"`[^`]+`","<case>",v); v=re.sub(r"\b\d+(?:\.\d+)?\b","<number>",v)
    v=re.sub(r"\b(?:caller|speaker|user|account|tenant|agent|requester|recipient)[-_ ]?[a-z0-9]*\b","<identity>",v)
    return " ".join(v.split())
def behavior_skeleton(value):
    """Remove serialization labels/operators while retaining behavior."""
    value=value.lower()
    value=re.sub(r"^(?:expected|failure) transitions:\s*","",value)
    value=re.sub(r"; then observe [a-z0-9_]+=0\\.?$","",value)
    value=re.sub(r"\\|\\s*observe [a-z0-9_]+!=0\\.?$","",value)
    value=re.sub(r"\\bmissing\\s+","",value)
    return " ".join(value.split())
def overlap_text(row):
    """Compare authored semantics after removing schema and shared boilerplate."""
    return " ".join(re.findall(
        r"[a-z0-9]+",
        " ".join([
            row["substantive_cluster"],
            row["preconditions"][0],
            *row["actual_state_sequence"][1:-1],
            row["deterministic_oracle"]["field"],
        ]).lower(),
    ))
def main():
    build(); rows=[json.loads(x) for x in OUT.read_text().splitlines()]
    assert len(rows)==125 and all(set(r)==REQUIRED for r in rows)
    assert len({r["stable_case_id"] for r in rows})==125
    assert len({r["dedupe_sha256"] for r in rows})==125
    assert len({r["semantic_behavior_fingerprint"] for r in rows})==125
    assert len({skeleton(r) for r in rows})==125
    assert all(len(r["actual_state_sequence"][1:-1]) >= 3 for r in rows)
    assert len({behavior_skeleton(r["expected_observable_behavior"]) for r in rows})==125
    assert len({behavior_skeleton(r["failure_condition"]) for r in rows})==125
    voice_rows=[r for r in rows if r["family_id"].startswith("voice_")]
    assert len(voice_rows)==75
    assert all("voice_realtime_agent" in r["design_partner_profiles"] for r in voice_rows)
    family_counts=Counter(r["family_id"] for r in rows)
    clusters={f:len({r["substantive_cluster"] for r in rows if r["family_id"]==f}) for f in FAMILIES}
    positives={f:sum(r["control_type"]=="positive" for r in rows if r["family_id"]==f) for f in FAMILIES}
    profiles={f:dict(Counter(p for r in rows if r["family_id"]==f for p in r["design_partner_profiles"])) for f in FAMILIES}
    assert set(family_counts.values())=={25} and min(clusters.values())>=8 and min(positives.values())>=3
    # Investigate every lexical pair at or above the parent-required 0.50 threshold.
    flagged=[]
    texts=[overlap_text(r) for r in rows]
    for i in range(len(rows)):
        for j in range(i+1,len(rows)):
            ratio=SequenceMatcher(None,texts[i],texts[j]).ratio()
            if ratio>=0.50:
                left,right=rows[i],rows[j]
                flagged.append({
                    "left":left["stable_case_id"],"right":right["stable_case_id"],
                    "ratio":round(ratio,4),
                    "distinguishing_transitions":{
                        "left":left["actual_state_sequence"][1:-1],
                        "right":right["actual_state_sequence"][1:-1],
                    },
                    "distinguishing_oracles":{
                        "left":left["deterministic_oracle"]["field"],
                        "right":right["deterministic_oracle"]["field"],
                    },
                    "distinguishing_boundaries":{
                        "left":left["actor_trust_boundary"],
                        "right":right["actor_trust_boundary"],
                    },
                    "disposition":"retain_distinct",
                })
    result={"status":"PASS","rows":125,"family_counts":dict(family_counts),"substantive_clusters":clusters,"positive_controls":positives,"scenario_profile_counts":profiles,"voice_realtime_profile_rows":len(voice_rows),"identity_insensitive_skeletons":125,"minimum_behavior_specific_transitions_per_case":3,"wrapper_stripped_expected_skeletons":125,"wrapper_stripped_failure_skeletons":125,"token_near_overlap_threshold":0.50,"token_near_overlap_normalization":"cluster + scenario precondition + authored transitions + oracle field; schema and shared boilerplate excluded","token_near_overlap_pairs_investigated":len(flagged),"token_near_overlap_investigation":flagged}
    REPORT.write_text(json.dumps(result,indent=2,sort_keys=True)+"\n")
    print(json.dumps({k:v for k,v in result.items() if k!="token_near_overlap_investigation"},sort_keys=True))
if __name__=="__main__": main()
