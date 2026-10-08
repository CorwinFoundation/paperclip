import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def run_node(body: str):
    script = f"""
const lifecycle=require({json.dumps(str(ROOT / 'backbond/scan-intake/web/scan-lifecycle.js'))});
const progress={{render(){{}}}};
const button={{disabled:false,hidden:false,textContent:'',addEventListener(_n,fn){{this.click=fn}}}};
const panel={{hidden:true}};
const storage={{value:null,setItem(_k,v){{this.value=v}},getItem(){{return this.value}},removeItem(){{this.value=null}}}};
global.document={{querySelectorAll:()=>[button],querySelector:()=>panel}};
global.window={{sessionStorage:storage}};
const pagePath={json.dumps(str(ROOT / 'backbond/scan-intake/web/scan-page.js'))};
delete require.cache[require.resolve(pagePath)];
global.BackBondScanLifecycle=lifecycle; global.BackBondScanProgress=progress;
const page=require(pagePath);
{body}
"""
    result = subprocess.run(["node", "-e", script], check=True, text=True, capture_output=True)
    return json.loads(result.stdout)


def test_real_progress_state_reaches_terminal_and_new_scan_resubmits():
    value = run_node("""
let starts=0;
const mounted=page.mount({startScan:async()=>{starts++;return {state:'queued',scan_id:'scan-1'}},getProgress:async()=>({state:'finished',updated_at:'2026-10-08T00:00:00Z'}),reconcileAcceptance:async()=>null,pollMs:999999});
(async()=>{await button.click({preventDefault(){},currentTarget:button}); await mounted.poll(); const terminal={state:mounted.state().state,label:button.textContent,disabled:button.disabled}; await button.click({preventDefault(){},currentTarget:button}); console.log(JSON.stringify({terminal,starts})); mounted.stop()})()
""")
    assert value["terminal"] == {"state": "finished", "label": "New scan", "disabled": False}
    assert value["starts"] == 2


def test_submit_failure_reenables_start_and_is_not_persisted():
    value = run_node("""
let shown='';
const mounted=page.mount({startScan:async()=>{throw new Error('network down')},getProgress:async()=>({}),reconcileAcceptance:async()=>null,onError:e=>shown=e.message});
(async()=>{await button.click({preventDefault(){},currentTarget:button}); console.log(JSON.stringify({state:mounted.state(),label:button.textContent,disabled:button.disabled,saved:storage.value,shown}))})()
""")
    assert value["state"]["state"] == "idle"
    assert value["state"]["error"] == "network down"
    assert value["label"] == "Start scan"
    assert value["disabled"] is False
    assert value["saved"] is None
    assert value["shown"] == "network down"


def test_refresh_without_token_reconciles_acceptance():
    value = run_node("""
storage.value=JSON.stringify({state:'submitting',statusToken:null});
let reconciles=0;
const mounted=page.mount({startScan:async()=>({}),getProgress:async()=>({state:'running'}),reconcileAcceptance:async()=>{reconciles++;return null}});
setTimeout(()=>console.log(JSON.stringify({state:mounted.state().state,label:button.textContent,reconciles,saved:storage.value})),0)
""")
    assert value["state"] == "idle"
    assert value["label"] == "Start scan"
    assert value["reconciles"] == 1
    assert value["saved"] is None
