from __future__ import annotations

import argparse, csv, hashlib, io, json, subprocess
from pathlib import Path

REQUIRED = {"stable_id","canonical_name","plain_language_name","category","unit_type","surface","control_plane","report_location","owner","source_paths","source_versions","evidence_refs","manifest_refs","count","build_state","runtime_state","evidence_grade","scoring_use","report_visibility","external_claim_state","limitations","public_wording","date"}
ALLOWED_UNITS={"dimension","family","probe","case"}
ALLOWED_EVIDENCE={"none","fixture","proxy","target-bound","customer-measured"}

def canonical(value): return json.dumps(value, sort_keys=True, separators=(",",":"), ensure_ascii=False).encode()
def digest(value): return "sha256:"+hashlib.sha256(canonical(value)).hexdigest()
def eligible(manifest):
    counts=[manifest.get(k) for k in ("expected","attempted","terminal","evidenced")]
    signature_ref=manifest.get("signature_ref")
    return (
        all(type(v) is int and v >= 0 for v in counts)
        and len(set(counts)) == 1
        and isinstance(signature_ref,str)
        and bool(signature_ref.strip())
    )
def validate(source):
    ids=set()
    for row in source["records"]:
        missing=REQUIRED-set(row)
        if missing: raise ValueError(f"{row.get('stable_id')}: missing {sorted(missing)}")
        if row["stable_id"] in ids: raise ValueError("duplicate stable_id")
        ids.add(row["stable_id"])
        if row["unit_type"] not in ALLOWED_UNITS: raise ValueError("invalid unit_type")
        if row["evidence_grade"] not in ALLOWED_EVIDENCE: raise ValueError("invalid evidence_grade")
        if row["external_claim_state"] == "production_active":
            refs=[m for m in source["production_claim_manifests"] if m.get("stable_id") in row["manifest_refs"]]
            if len(refs)!=1 or not eligible(refs[0]): raise ValueError("production_active claim lacks eligible signed manifest")
    return ids
def render(source, out):
    validate(source); receipt_id=digest(source); rows=source["records"]
    common={"register_receipt_id":receipt_id,"register_version":source["register_version"],"as_of_date":source["as_of_date"]}
    dictionary={**common,"records":[r for r in rows if r["unit_type"]=="dimension"]}
    marketing={**common,"claims":[{"stable_id":r["stable_id"],"state":r["external_claim_state"],"unit_type":r["unit_type"],"count":r["count"],"evidence_grade":r["evidence_grade"],"wording":r["public_wording"]} for r in rows if r["report_visibility"]=="external"]}
    summary=["# Scanner Capability Register", "",f"Receipt: `{receipt_id}`",f"As of: {source['as_of_date']}","","All counts are typed units. External publication remains fail-closed unless a claim is generated as `production_active` from an eligible signed manifest.","", "| Record | Count | Unit | Evidence | Claim state | Limitation |","|---|---:|---|---|---|---|"]
    summary += [f"| {r['canonical_name']} | {r['count']} | {r['unit_type']} | {r['evidence_grade']} | {r['external_claim_state']} | {r['limitations']} |" for r in rows]
    backlog=[r for r in rows if r["runtime_state"]!="active" or r["external_claim_state"] in {"held","rejected"}]
    out.mkdir(parents=True,exist_ok=True)
    files={"dictionary_records.json":json.dumps(dictionary,indent=2,sort_keys=True)+"\n","marketing_claims.json":json.dumps(marketing,indent=2,sort_keys=True)+"\n","executive_summary.md":"\n".join(summary)+"\n"}
    buf=io.StringIO(); writer=csv.DictWriter(buf,fieldnames=["register_receipt_id","stable_id","count","unit_type","build_state","runtime_state","external_claim_state","limitations"]); writer.writeheader()
    for r in backlog: writer.writerow({"register_receipt_id":receipt_id,**{k:r[k] for k in writer.fieldnames if k!="register_receipt_id"}})
    files["inactive_backlog.csv"]=buf.getvalue()
    for name,text in files.items(): (out/name).write_text(text,encoding="utf-8")
    hashes={name:"sha256:"+hashlib.sha256(text.encode()).hexdigest() for name,text in files.items()}
    receipt={**common,"schema_version":source["schema_version"],"source_commit":source["source_commit"],"source_sha256":digest(source),"view_hashes":hashes,"reconciliation":[{"stable_id":r["stable_id"],"count":r["count"],"unit_type":r["unit_type"],"evidence_boundary":r["evidence_grade"],"external_claim_state":r["external_claim_state"],"limitation":r["limitations"]} for r in rows],"production_active_rule":"expected = attempted = terminal = evidenced and signature_ref is present"}
    (out/"register_receipt.json").write_text(json.dumps(receipt,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    return receipt
def main():
    p=argparse.ArgumentParser(); p.add_argument("--source",type=Path,default=Path("config/scanner_capability_register.v1.json")); p.add_argument("--out",type=Path,default=Path("generated/scanner-capability-register")); a=p.parse_args(); print(json.dumps(render(json.loads(a.source.read_text()),a.out),sort_keys=True))
if __name__=="__main__": main()
