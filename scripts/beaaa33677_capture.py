"""BEAAA-33677 round 1, step CAPTURE: live measurement of the 200-row zero-build cohort.

Budget, fixed before the run and pre-registered on the issue:
  200 rows, 1 attempt each, hard cap 200 attempts, one round.
  A row that errors or lands short STAYS short and is reported held with its literal.
  No top-up, no reselection.

Roster observations only. This script emits NO control records; control
independence (master 29b042d8) is enforced by the slice builder at score time,
not worked around here.
"""
import dataclasses, hashlib, inspect, json, os, sys, time, traceback
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import beaaa31092_content_slice as S
from tools.content_candidate_inventory import materialize_candidate_case, candidate_judge_criteria
from tools.registered_case_executor import default_judge_runner, _judge_materialized_text_case
from harness.customer_runner import _VapiCustomerAgentRunner

ASSISTANT = "09aea361-04b3-4a62-bcb0-2f2582f11ec8"
TARGET_ID = "vapi:09aea361@api.vapi.ai"
ROUND = "beaaa-33677-r1"
MAX_ATTEMPTS, MAX_CONSECUTIVE_ERRORS = 200, 5
COHORT = ROOT / "artifacts/beaaa-33677/cohort-200.json"
OUT = Path("/tmp/bb33677out/capture.json")
LOG = Path("/tmp/bb33677out/capture-log.jsonl")
OUT.parent.mkdir(parents=True, exist_ok=True)

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
assert cohort["cohort_size"] == 200, cohort["cohort_size"]
cases = [(r["family_id"], r["stable_case_id"]) for r in cohort["rows"]]
print(f"BUDGET: {len(cases)} rows, 1 attempt each, cap {MAX_ATTEMPTS}, round {ROUND}", flush=True)
print(f"COHORT inventory_sha256={cohort['inventory_sha256']}", flush=True)

subject = _VapiCustomerAgentRunner(secret=os.environ["VAPI_API_KEY"], assistant_id=ASSISTANT)
os.environ.setdefault("ARE_FULL_SCAN_JUDGE_PROVIDER", "openrouter")
judge = default_judge_runner()

# Resumable: a killed run leaves completed rows in the log. Re-attempting a case
# already measured would spend target calls for nothing and double-count the round.
records, errored = [], {}
if LOG.exists():
    for line in LOG.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("verdict"):
            records.append(row)
        elif row.get("error"):
            errored[row["stable_case_id"]] = row
done = {r["stable_case_id"] for r in records}
# An errored row has spent its single attempt. Under the no-top-up rule it is not retried.
spent = done | set(errored)
if spent:
    print(f"RESUMING: {len(done)} measured + {len(errored)} errored already spent their attempt", flush=True)

attempts, consecutive_errors = len(spent), 0
logf = LOG.open("a", encoding="utf-8")
for family, case_id in cases:
    if case_id in spent:
        continue
    if attempts >= MAX_ATTEMPTS or consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
        print(f"STOPPING EARLY at {case_id}: attempts={attempts} consecutive_errors={consecutive_errors}", flush=True)
        break
    attempts += 1
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
        verdict = str(res.get("verdict", "")).casefold()
        joined = "\n\n".join(r for r in responses if r)
        rec = {"stable_case_id": case_id, "family_id": family, "capture_round": ROUND,
               "verdict": verdict, "target_id": TARGET_ID, "target_calls": len(prompts),
               "status": "complete", "acquisition_basis": "target-measured",
               "prompt_sha256": hashlib.sha256("\n".join(prompts).encode()).hexdigest(),
               "response_sha256": S._response_digest(joined),
               "recorded_target_response": joined, "judge": res}
        records.append(rec)
        logf.write(json.dumps(rec, default=str) + "\n"); logf.flush()
        print(f"[{attempts:3d}/200] {family}/{case_id} -> {verdict} ({len(joined)} chars)", flush=True)
        consecutive_errors = 0
    except Exception as exc:
        consecutive_errors += 1
        logf.write(json.dumps({"stable_case_id": case_id, "family_id": family,
                               "error": repr(exc), "tb": traceback.format_exc()},
                              default=str) + "\n"); logf.flush()
        print(f"[{attempts:3d}/200] {family}/{case_id} ERROR {exc!r}", flush=True)
    time.sleep(1)

OUT.write_text(json.dumps(records, indent=1, default=str))
from collections import Counter
print("WROTE", OUT, len(records), "records of", len(cases), "rows;", attempts, "attempts spent", flush=True)
print("VERDICTS:", json.dumps(Counter(r["verdict"] for r in records)), flush=True)
print("DISTINCT RESPONSE DIGESTS:", len({r["response_sha256"] for r in records}), "of", len(records), flush=True)
