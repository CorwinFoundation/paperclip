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
    assert all(item["label"] == "View scan progress" for item in value)


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
    for state in ("finished", "incomplete", "error"):
        assert value["views"][state]["label"] == "New scan"
