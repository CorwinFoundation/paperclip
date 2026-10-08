import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "backbond/scan-intake/web/scan-lifecycle.js"


def run_js(body: str):
    script = f"const lifecycle=require({json.dumps(str(MODULE))});\n" + body
    result = subprocess.run(["node", "-e", script], check=True, text=True, capture_output=True)
    return json.loads(result.stdout)


def test_connection_success_does_not_suppress_start_and_duplicate_submit_is_inert():
    value = run_js("""
      let model=lifecycle.reduce(lifecycle.initial(), {type:'connection_passed'});
      const afterConnection=lifecycle.view(model);
      model=lifecycle.reduce(model, {type:'submit'});
      const duplicate=lifecycle.reduce(model, {type:'submit'});
      console.log(JSON.stringify({afterConnection, model, duplicate, view:lifecycle.view(model)}));
    """)
    assert value["afterConnection"] == {"action": "start", "label": "Start scan", "disabled": False, "showProgress": False}
    assert value["model"] == value["duplicate"]
    assert value["view"]["disabled"] is True


def test_accepted_and_ambiguous_starts_never_offer_start():
    value = run_js("""
      const accepted=lifecycle.reduce(lifecycle.initial(), {type:'accepted',status:'queued',statusToken:'minimum-capability'});
      const running=lifecycle.reduce(accepted, {type:'status',status:'running'});
      const unknown=lifecycle.reduce(lifecycle.initial(), {type:'ambiguous_start'});
      console.log(JSON.stringify([accepted,running,unknown].map(x=>lifecycle.view(x))));
    """)
    assert [item["action"] for item in value] == ["progress", "progress", "progress"]
    assert [item["label"] for item in value] == ["View scan progress", "View scan progress", "Start outcome unknown"]
    assert value[2]["disabled"] is True


def test_status_requires_acceptance_and_terminal_and_order_are_monotonic():
    value = run_js("""
      const idleStatus=lifecycle.reduce(lifecycle.initial(), {type:'status',status:'running',sequence:1});
      let model=lifecycle.reduce(lifecycle.initial(), {type:'accepted',status:'running',statusToken:'token',sequence:2});
      const older=lifecycle.reduce(model,{type:'status',status:'queued',sequence:1});
      model=lifecycle.reduce(model,{type:'status',status:'finished',sequence:3});
      const late=lifecycle.reduce(model,{type:'status',status:'running',sequence:4});
      console.log(JSON.stringify({idleStatus,older,finished:model,late}));
    """)
    assert value["idleStatus"]["state"] == "idle"
    assert value["older"]["state"] == "running"
    assert value["finished"]["state"] == "finished"
    assert value["late"]["state"] == "finished"


def test_unknown_outcome_only_reenables_start_after_not_accepted_reconciliation():
    value = run_js("""
      let model=lifecycle.reduce(lifecycle.initial(), {type:'ambiguous_start'});
      const unknown=lifecycle.view(model);
      model=lifecycle.reduce(model,{type:'not_accepted'});
      console.log(JSON.stringify({unknown,reconciled:lifecycle.view(model)}));
    """)
    assert value["unknown"] == {"action": "progress", "label": "Start outcome unknown", "disabled": True, "showProgress": True}
    assert value["reconciled"]["label"] == "Start scan"


def test_refresh_reconnect_and_terminal_actions_restore_truthfully():
    value = run_js("""
      const values={};
      const storage={value:null,setItem(_k,v){this.value=v},getItem(){return this.value},removeItem(){this.value=null}};
      let model=lifecycle.reduce(lifecycle.initial(), {type:'accepted',status:'running',statusToken:'minimum-capability'});
      lifecycle.save(storage,'backbond.active-scan',model);
      values.restored=lifecycle.restore(storage,'backbond.active-scan');
      values.reconnecting=lifecycle.reduce(values.restored,{type:'poll_failed'});
      values.resumed=lifecycle.reduce(values.reconnecting,{type:'status',status:'running'});
      values.finished=lifecycle.reduce(values.resumed,{type:'status',status:'finished'});
      values.incomplete=lifecycle.reduce(values.resumed,{type:'status',status:'incomplete'});
      values.error=lifecycle.reduce(values.resumed,{type:'status',status:'error'});
      values.views=Object.fromEntries(['restored','reconnecting','finished','incomplete','error'].map(k=>[k,lifecycle.view(values[k])]));
      console.log(JSON.stringify(values));
    """)
    assert value["restored"]["statusToken"] == "minimum-capability"
    assert value["reconnecting"]["state"] == "reconnecting"
    assert value["resumed"]["state"] == "running"
    assert value["views"]["restored"]["action"] == "progress"
    for state in ("finished", "incomplete"):
        assert value["views"][state]["label"] == "New scan"


def test_error_reopens_form_and_requires_fresh_connection_proof_without_changing_other_states():
    value = run_js("""
      let running=lifecycle.reduce(lifecycle.initial(),{type:'connection_passed'});
      running=lifecycle.reduce(running,{type:'accepted',status:'running',statusToken:'scan-1'});
      const completed=lifecycle.reduce(running,{type:'status',status:'finished'});
      const error=lifecycle.reduce(running,{type:'status',status:'error'});
      console.log(JSON.stringify({running:lifecycle.view(running),completed:lifecycle.view(completed),error,errorView:lifecycle.view(error)}));
    """)
    assert value["running"]["action"] == "progress"
    assert value["completed"]["label"] == "New scan"
    assert value["error"]["connectionPassed"] is False
    assert value["errorView"] == {"action": "retry", "label": "Test connection to retry", "disabled": True, "showProgress": True, "reopenForm": True}
