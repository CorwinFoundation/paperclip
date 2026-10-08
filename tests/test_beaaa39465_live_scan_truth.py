import json
import pathlib
import subprocess


ROOT = pathlib.Path(__file__).resolve().parents[1]
SITE = ROOT / "backbond/operations/site-quickfix/release-2026-10-08-scan-lifecycle/site/scan"


def node(source):
    result = subprocess.run(["node", "-e", source], check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


def test_real_scan_status_without_counts_reports_unavailable_instead_of_inventing_progress():
    progress = json.dumps(str(SITE / "scan-progress.js"))
    value = node(f"""
      const progress=require({progress});
      console.log(JSON.stringify(progress.model({{
        status:'queued', state:'queued', status_token:'token-1',
        created_at:'2026-10-08T18:00:00Z', report_emailed:false
      }})));
    """)
    assert value["processed"] is None
    assert value["total"] is None
    assert value["percent"] is None
    assert value["stage"] == "Stage unavailable"


def test_deployed_adapter_does_not_supply_fake_count_total_or_stage_defaults():
    source = (SITE / "scan.js").read_text()
    assert "accepted_pack_total:100" not in source
    assert "processed_count:Number(value.processed_count||0)" not in source
    assert "Running starter tests" not in source
    assert "reconcileAcceptance:async()=>null" not in source
    assert "mounted.reset()" in source
    assert "Your scan allowance has been restored" not in (SITE / "scan-progress.js").read_text()


def test_terminal_restore_polls_payload_and_cancel_controller_can_reset():
    source = (SITE / "scan-page.js").read_text()
    assert "lifecycle.TERMINAL.has(state.state) && state.statusToken) poll()" in source
    assert "return { poll, stop, reset" in source
