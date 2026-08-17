#!/usr/bin/env python3
"""BEAAA-21755 canonical gate harness — CTO ruling 1fe47bec + QA scope addendum 6a7cd228.

Usage: python3 verify_lane_c_gate.py <repo_root> [<baseline_repo_root>]
Exit 0 only if every condition passes. Each condition prints PASS/FAIL with measured values.
QA verification only; makes no changes to the repo under test.
"""
import sys, os, copy, json, hashlib, tempfile, collections, subprocess
from pathlib import Path

ROOT = sys.argv[1]
BASE = sys.argv[2] if len(sys.argv) > 2 else None
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "tests"))
os.chdir(ROOT)

FAMS = {"human_escalation_flagging": 20, "customer_defined_risk_pack": 12, "mcp_tool_integrity": 12}
results = []
def rec(cid, name, ok, detail):
    results.append((cid, name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {cid} {name}\n       {detail}")

from tools.active_test_registry import (build_registry, count_active_executable,
    count_target_bound_runtime, distinct_case_counts)
rows = build_registry()
lane = [r for r in rows if r.get("test_id") in FAMS]

# C0 scope: exactly 44 rows, 20/12/12
by = collections.Counter(r["test_id"] for r in lane)
rec("C0", "44 scoped rows, families 20/12/12",
    len(lane) == 44 and all(by[f] == n for f, n in FAMS.items()), f"{len(lane)} rows {dict(by)}")

# C1 explicit registered-executor dispatch for all 3 families / 44 rows, under bench_wrapper
runners = collections.Counter(r.get("runner") for r in lane)
rec("C1a", "all 44 rows on bench_wrapper",
    runners.get("bench_wrapper") == 44, f"runners={dict(runners)}")

from tools.registered_case_executor import RegisteredCaseExecutor, EvidenceStore, ScanBudget
try:
    import tests.test_beaaa18720_content_candidate_registration as T
    Mock = T.MockRunner
except Exception:
    class Mock:
        def __init__(self, n, f): self.name, self.f = n, f
        def complete(self, p): return self.f(p)

FIELD = {"human_escalation_flagging": "escalate",
         "customer_defined_risk_pack": "flagged_or_refused",
         "mcp_tool_integrity": "rejected_tool"}
_CUR = {"f": "human_escalation_flagging"}
def _resp(_p):
    return json.dumps({FIELD[_CUR["f"]]: True, "rationale": "qa harness stub"})

def executor(tmp):
    return RegisteredCaseExecutor(
        subject=Mock("target", _resp),
        judge=Mock("judge", lambda p: '{"verdict":"pass","score":1.0}'),
        evidence_store=EvidenceStore(Path(tmp)), budget=ScanBudget())

sha = os.popen(f"git -C {ROOT} rev-parse HEAD").read().strip()
os.environ["BACKBOND_RUNTIME_COMMIT_SHA"] = sha
os.environ["BACKBOND_TRANCHE1_AUTHORIZED_COMMIT_SHA"] = sha

exec_fail, notbound = [], []
tmp = tempfile.mkdtemp()
for r in lane:
    try:
        _CUR["f"] = r["test_id"]
        executor(tmp)._execute_case(r["test_id"], r["case_id"], r)
    except RuntimeError as e:
        (notbound if "adapter not bound" in str(e) else exec_fail).append((r["test_id"], r["case_id"], str(e)[:60]))
    except Exception as e:
        exec_fail.append((r["test_id"], r["case_id"], f"{type(e).__name__}: {str(e)[:50]}"))
rec("C1b", "all 44 execute end-to-end via RegisteredCaseExecutor",
    not notbound and not exec_fail,
    f"adapter-not-bound={len(notbound)} other-errors={len(exec_fail)} " +
    (f"e.g. {notbound[0] if notbound else exec_fail[0]}" if (notbound or exec_fail) else "all executed"))

# C2 stable runtime identity + no fallthrough for unknown families
ids = [r.get("runtime_case_identity") for r in lane]
rec("C2a", "all 44 retain a runtime_case_identity",
    all(bool(i) for i in ids), f"non-null {sum(1 for i in ids if i)}/44")
rows2 = build_registry()
lane2 = {(r["test_id"], r["case_id"]): r.get("runtime_case_identity")
         for r in rows2 if r.get("test_id") in FAMS}
rec("C2b", "runtime identity stable across two builds",
    all(lane2.get((r["test_id"], r["case_id"])) == r.get("runtime_case_identity") for r in lane),
    "identities reproduce" if lane else "n/a")
ghost = dict(lane[0]); ghost["test_id"] = "not_a_real_family_zzz"; ghost["case_id"] = "ghost-1"
try:
    executor(tmp)._execute_case(ghost["test_id"], ghost["case_id"], ghost)
    ok, det = False, "unknown family EXECUTED — fallthrough present"
except RuntimeError as e:
    ok, det = True, f"rejected: {str(e)[:60]}"
except Exception as e:
    ok, det = True, f"rejected: {type(e).__name__}"
rec("C2c", "no executable fallthrough for unknown families", ok, det)

# C3 fail-closed on missing adapter/target/response/provenance/verdict
def strip(field):
    r = copy.deepcopy(lane[0]); r.pop(field, None); r[field] = None
    try:
        out = executor(tmp)._execute_case(r["test_id"], r["case_id"], r)
        return (out.get("score") is None or out.get("observational_rejection")), f"score={out.get('score')} rej={out.get('observational_rejection')}"
    except Exception as e:
        return True, f"raised {type(e).__name__}"
fc = {f: strip(f) for f in ("target_bound", "execution_evidence", "runtime_case_identity")}
rec("C3", "fail-closed on stripped provenance fields",
    all(v[0] for v in fc.values()), "; ".join(f"{k}:{v[1]}" for k, v in fc.items()))

# C4 non-allowlisted authorization rejection (forgery repro)
forged = copy.deepcopy(lane[0])
forged.update({"scoring_active": True, "underwriting_active": False, "effective_underwriting_weight": 0.0,
    "observational_contract_version": "tranche1-zero-weight-observational/v0.2",
    "observational_disclosure": "zero-weight observational; not used for underwriting",
    "k_replicates": 5, "target_authorization_ref": "full-scan:test:authorized"})
try:
    out = executor(tmp)._execute_case(forged["test_id"], forged["case_id"], forged)
    rejected = out.get("observational_rejection") is not None and out.get("score") is None \
        and "observational_result" not in out
    rec("C4", "non-allowlisted forgery gets explicit authorization rejection", rejected,
        f"rejection={out.get('observational_rejection')!r} score={out.get('score')!r}")
except Exception as e:
    rec("C4", "non-allowlisted forgery gets explicit authorization rejection", False,
        f"raised {type(e).__name__}: {str(e)[:70]} (fail-closed but NOT an authorization rejection)")

# C5 target_bound evidence-derived — MUTATE the candidate and require the flag to flip
tb = sum(1 for r in lane if r.get("target_bound") is True)
mut = os.environ.get("QA_MUTATED_ROOT")
if not mut:
    rec("C5", "target_bound evidence-derived", False,
        f"candidate target_bound true={tb}/44; INCONCLUSIVE — set QA_MUTATED_ROOT to a worktree "
        "with provenance stripped from a lane-C mapping")
else:
    out = subprocess.run([sys.executable, "-c",
        "import sys;sys.path.insert(0,sys.argv[1]);from tools.active_test_registry import build_registry as b;"
        "L=[r for r in b() if r.get('test_id')=='mcp_tool_integrity'];"
        "import json;print(json.dumps({'n':len(L),"
        "'tb':sum(1 for r in L if r.get('target_bound') is True),"
        "'pre':sum(1 for r in L if r.get('production_runner_executable') is True),"
        "'st':sorted({str(r.get('execution_evidence_status')) for r in L})}))",
        mut], capture_output=True, text=True, cwd=mut)
    try:
        m = json.loads(out.stdout.strip().splitlines()[-1])
        ok = m["tb"] == 0 and m["pre"] == 0
        rec("C5", "target_bound evidence-derived (provenance stripped -> flag false)", ok,
            f"unmutated candidate true={tb}/44; MUTATED: target_bound true={m['tb']}/{m['n']}, "
            f"production_runner_executable true={m['pre']}/{m['n']}, status={m['st']}")
    except Exception as e:
        rec("C5", "target_bound evidence-derived", False, f"mutation probe failed: {out.stderr[-200:] or e}")

# C6 evidence inventory integrity (220 records, hashes, no placeholders)
import yaml
tot = 0; miss = []; bad = []; keys = collections.Counter(); verd = collections.Counter()
for fam in FAMS:
    d = yaml.safe_load(Path(f"rubric/probe_mappings/{fam}.yaml").read_text())
    for pb in d.get("probes", []):
        ee = pb.get("execution_evidence") or {}
        for rp in (ee.get("run_provenance") or []):
            tot += 1; keys[(fam, pb["id"], rp.get("seed"))] += 1; verd[rp.get("verdict")] += 1
            u = Path(rp.get("evidence_uri", ""))
            if not u.exists(): miss.append(str(u)); continue
            if hashlib.sha256(u.read_bytes()).hexdigest() != rp.get("sha256"): bad.append(str(u))
rec("C6", "220 evidence records present, hashed, unique",
    tot == 220 and not miss and not bad and len(keys) == 220 and not any(v > 1 for v in keys.values()),
    f"records={tot} unique={len(keys)} missing={len(miss)} hash-mismatch={len(bad)} verdicts={dict(verd)}")

# C7 counts + scoring invariance
ae, tbr, dc = count_active_executable(rows), count_target_bound_runtime(rows), distinct_case_counts(rows).get("total")
rec("C7", "registry diagnostics reported (repin must be justified)", True,
    f"rows={len(rows)} active_executable={ae} target_bound_runtime={tbr} distinct={dc}")

print("\n" + "=" * 72)
fails = [r for r in results if not r[2]]
print(f"{len(results)-len(fails)}/{len(results)} conditions PASS")
if fails: print("FAILING: " + ", ".join(f[0] for f in fails))
sys.exit(1 if fails else 0)
