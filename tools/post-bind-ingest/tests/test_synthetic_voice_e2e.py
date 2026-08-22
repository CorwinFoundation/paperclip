from pathlib import Path
import json, subprocess, sys

def test_synthetic_voice_e2e(tmp_path: Path) -> None:
    script=Path(__file__).resolve().parents[1]/"scripts/run_synthetic_voice_e2e.py"
    proc=subprocess.run([sys.executable,str(script),"--output-dir",str(tmp_path)],capture_output=True,text=True,check=True)
    report=json.loads(proc.stdout)
    assert report["status"]=="PASS"
    assert all(report["assertions"].values())
    assert report["counts"]["normalized_events"] == 3
    assert report["counts"]["log_tracker_rows"] == 3
    assert report["counts"]["no_content_nodes_scanned"] > 0
    assert report["counts"]["no_content_violations"] == 0
    assert report["continuity"]["continuity_status"] == "healthy"
