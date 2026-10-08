import json
import subprocess
from pathlib import Path


MODULE = Path(__file__).resolve().parents[1] / "backbond/scan-intake/web/scan-progress.js"


def js(expression):
    script = f"const p=require({json.dumps(str(MODULE))});console.log(JSON.stringify({expression}))"
    return json.loads(subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True).stdout)


def test_known_total_is_clamped_visually_without_changing_raw_counts():
    value = js("p.model({state:'running',stage_label:'Evidence checks',processed_count:12,accepted_pack_total:10,usable_count:8,unavailable_count:2,timing:{elapsed_seconds:61}})")
    assert value["processed"] == 12
    assert value["total"] == 10
    assert value["percent"] == 100
    assert value["heading"] == "Scan in progress"
    assert value["terminal"] is False


def test_unknown_total_has_no_determinate_percentage_and_qualified_estimate():
    value = js("p.model({state:'running',processed_count:3,accepted_pack_total:null,usable_count:2,unavailable_count:0,timing:{elapsed_seconds:65,estimated_remaining_seconds:{low:30,high:90}}})")
    assert value["percent"] is None
    assert value["elapsed"] == "01:05"
    assert value["estimate"] == "00:30–01:30 remaining"


def test_all_states_have_truthful_copy_and_active_states_never_offer_start():
    states = js("['queued','running','reconnecting','unconfirmed','finished','incomplete','error'].map(state=>p.model({state,timing:{}}))")
    assert len(states) == 7
    assert all(item["action"] != "Start scan" for item in states)
    assert states[4]["guidance"].startswith("All accepted packs were processed. Review")


def test_announcement_separates_processing_from_assessment_status():
    text = js("p.announcement(p.model({state:'finished',processed_count:10,accepted_pack_total:10,timing:{}}))")
    assert "Review results for assessment status" in text
    assert "passed" not in text.lower()


def test_announcements_are_coalesced_and_elapsed_ticks_are_silent():
    value = js("""(()=>{let now=0,live={textContent:''};let a=new p.Announcer(live,()=>now);let raw={state:'running',stage_label:'Checks',processed_count:1,timing:{elapsed_seconds:1}};let first=a.update(raw);raw.timing.elapsed_seconds=2;let tick=a.update(raw);raw.processed_count=2;now=1000;let early=a.update(raw);now=6000;let later=a.update(raw);return {first,tick,early,later,text:live.textContent}})()""")
    assert value == {"first": True, "tick": False, "early": False, "later": True, "text": "Scan progress: 2 accepted packs processed. 0 usable, 0 unavailable. Stage: Checks."}


def test_renderer_declares_determinate_progress_only_for_known_positive_total():
    source = MODULE.read_text()
    assert 'setAttribute("role","progressbar")' in source
    assert 'if(value.percent!==null)' in source
    assert 'aria-valuenow' in source and 'aria-valuemax' in source
