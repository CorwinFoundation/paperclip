"""BEAAA-33673 Deliverable 2, step CONTROL: round 2 independent observations.

Budget, fixed before round 1 and unchanged after seeing its spread:
  4 controls per family, uniformly, across all 17 cohort families = 68 attempts.
  No reallocation toward the families that round 1 made look rescuable.
  A family that lands short STAYS short.

A control is admitted by tools/beaaa31092_content_slice._control_rejections, not
here. This script only measures and records; every admission check (same oracle,
same judge configuration, response independence roster-to-control and
control-to-control at master 29b042d8) is applied at score time.
"""
import dataclasses, hashlib, inspect, json, os, sys, time, traceback
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import beaaa31092_content_slice as S
from tools import beaaa33673_zero_build_slice as Z
from tools.content_candidate_inventory import materialize_candidate_case, candidate_judge_criteria
from tools.registered_case_executor import default_judge_runner, _judge_materialized_text_case
from harness.customer_runner import _VapiCustomerAgentRunner

ASSISTANT = "09aea361-04b3-4a62-bcb0-2f2582f11ec8"
TARGET_ID = "vapi:09aea361@api.vapi.ai"
ROUND = "beaaa-33673-r2"
PER_FAMILY = 4
COHORT = ROOT / "artifacts/beaaa-33673/cohort-150.json"
R1_LOG = Path("/tmp/bb33673/capture-log.jsonl")
OUT = Path("/tmp/bb33673/controls.json")
LOG = Path("/tmp/bb33673/controls-log.jsonl")

def spec_fields(spec):
    if dataclasses.is_dataclass(spec):
        return {f.name: getattr(spec, f.name) for f in dataclasses.fields(spec)}
    return {k: v for k, v in vars(spec).items() if not k.startswith("_")}

def ask(subject, prompt):
    params = list(inspect.signature(subject.complete).parameters)
    try:
        return subject.complete(prompt)
    except TypeError:
        return subject.complete(prompts=[prompt]) if "prompts" in params else subject.complete([prompt])

def text_of(resp):
    if isinstance(resp, str):
        return resp
    for attr in ("text", "output_text", "content", "response"):
        v = getattr(resp, attr, None) or (resp.get(attr) if isinstance(resp, dict) else None)
        if isinstance(v, str) and v.strip():
            return v
    return json.dumps(resp, default=str)

cohort = json.loads(COHORT.read_text())
rows = cohort["rows"]
# The oracle digest is derived from the PINNED INVENTORY row, not the cohort row:
# the cohort file carries selection metadata and none of ORACLE_IDENTITY_FIELDS,
# so digesting it would hand every control an oracle identity of all-None and
# fail admission on oracle_digest_mismatch for reasons unrelated to the oracle.
by_case = {r["stable_case_id"]: r for r in S._jsonl(Z.PINNED_INVENTORY_PATH)}

# Round 1 owns the roster. Controls run only once every roster attempt is spent,
# so a control can never be selected against a family whose spread is still moving.
for _ in range(600):
    spent = set()
    if R1_LOG.exists():
        for line in R1_LOG.read_text(encoding="utf-8").splitlines():
            if line.strip():
                spent.add(json.loads(line)["stable_case_id"])
    if len(spent) >= len(rows):
        break
    print(f"waiting for round 1: {len(spent)}/{len(rows)} attempts spent", flush=True)
    time.sleep(20)
else:
    sys.exit("round 1 did not finish within the wait window")

measured = {}
for line in R1_LOG.read_text(encoding="utf-8").splitlines():
    if line.strip():
        rec = json.loads(line)
        if rec.get("verdict"):
            measured[rec["stable_case_id"]] = rec

# Selection is deterministic and fixed: the first PER_FAMILY measured cases of each
# family in cohort order. Not the cases whose round-1 verdict looks most useful.
selected = []
per_family = {}
for row in rows:
    fam, case = row["family_id"], row["stable_case_id"]
    if case not in measured or per_family.get(fam, 0) >= PER_FAMILY:
        continue
    per_family[fam] = per_family.get(fam, 0) + 1
    selected.append((fam, case))
print(f"BUDGET: {PER_FAMILY}/family, {len(selected)} controls over {len(per_family)} families, round {ROUND}", flush=True)

judge_digest = Z.judge_config_digest("probe-only")
subject = _VapiCustomerAgentRunner(secret=os.environ["VAPI_API_KEY"], assistant_id=ASSISTANT)
os.environ.setdefault("ARE_FULL_SCAN_JUDGE_PROVIDER", "openrouter")
judge = default_judge_runner()

records, spent_cases = [], set()
if LOG.exists():
    for line in LOG.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rec = json.loads(line)
            spent_cases.add(rec["bound_case_id"])
            if rec.get("verdict"):
                records.append(rec)
logf = LOG.open("a", encoding="utf-8")
for i, (family, case_id) in enumerate(selected, 1):
    if case_id in spent_cases:
        continue
    try:
        spec = materialize_candidate_case(family, case_id)
        fields = spec_fields(spec)
        prompts = fields.get("prompts") or fields.get("prompt_turn_sequence") or []
        if isinstance(prompts, str):
            prompts = [prompts]
        responses = [text_of(ask(subject, p)) for p in prompts]
        res = _judge_materialized_text_case(
            judge, prompts=list(prompts), responses=responses,
            expected=fields.get("expected") or fields.get("deterministic_oracle") or {},
            judge_criteria=candidate_judge_criteria(spec),
            applicability=fields.get("applicability"),
        )
        joined = "\n\n".join(r for r in responses if r)
        inv = by_case[case_id]
        rec = {"family_id": family, "bound_case_id": case_id, "capture_round": ROUND,
               "verdict": str(res.get("verdict", "")).casefold(), "n": 1,
               "target_id": TARGET_ID, "target_calls": len(prompts), "status": "complete",
               "acquisition_basis": "target-measured",
               "evidence_ref": f"{ROUND}/{case_id}",
               "case_slug": f"{ROUND}:{case_id}",
               "prompt_sha256": hashlib.sha256("\n".join(prompts).encode()).hexdigest(),
               "response_sha256": S._response_digest(joined),
               "recorded_target_response": joined,
               "oracle_digest": S._digest({f: inv.get(f) for f in S.ORACLE_IDENTITY_FIELDS}),
               "judge_config_digest": judge_digest,
               "judge": res}
        records.append(rec)
        logf.write(json.dumps(rec, default=str) + "\n"); logf.flush()
        print(f"[{i:3d}/{len(selected)}] {family}/{case_id} -> {rec['verdict']}", flush=True)
    except Exception as exc:
        logf.write(json.dumps({"bound_case_id": case_id, "family_id": family,
                               "error": repr(exc), "tb": traceback.format_exc()}, default=str) + "\n")
        logf.flush()
        print(f"[{i:3d}/{len(selected)}] {family}/{case_id} ERROR {exc!r}", flush=True)
    time.sleep(1)

OUT.write_text(json.dumps(records, indent=1, default=str))
from collections import Counter
print("WROTE", OUT, len(records), "controls", flush=True)
print("VERDICTS:", json.dumps(Counter(r["verdict"] for r in records)), flush=True)
