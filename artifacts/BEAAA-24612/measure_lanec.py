import json, os, subprocess, sys
LANEC = ("human_escalation_flagging","customer_defined_risk_pack","mcp_tool_integrity")
def snap(flag):
    env = dict(os.environ); env.pop("BACKBOND_FOUNDER_DEV_MODE", None)
    if flag: env["BACKBOND_FOUNDER_DEV_MODE"]="on"
    env["PYTHONPATH"]="."
    code = '''
import json
from tools.active_test_registry import build_registry
rows=build_registry()
lc=[r for r in rows if r.get("test_id") in %r]
out={"lane_c_rows":len(lc),
 "scoring_active_true":sum(1 for r in lc if r.get("scoring_active") is True),
 "reporting":{},
 "total_scoring_active":sum(1 for r in rows if r.get("scoring_active") is True),
 "total_rows":len(rows)}
for r in lc:
    k=r.get("reporting_status"); out["reporting"][k]=out["reporting"].get(k,0)+1
print(json.dumps(out))
''' % (LANEC,)
    p=subprocess.run([sys.executable,"-c",code],capture_output=True,text=True,env=env,cwd=".")
    if p.returncode!=0: return {"error":p.stderr.strip().splitlines()[-1] if p.stderr else "fail"}
    return json.loads(p.stdout.strip().splitlines()[-1])
print(json.dumps({"flag_off":snap(False),"flag_on":snap(True)}, indent=2))
