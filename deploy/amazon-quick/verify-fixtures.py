"""Exercise packaged capture -> grading -> summary -> report with no network."""
import json
import os
from pathlib import Path
import socket
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools.internal_agent_pilot import main as pilot
from tools.quick_pilot_report import render


def forbidden(*args, **kwargs):
    raise RuntimeError("network forbidden in fixture qualification")


socket.socket = forbidden
socket.create_connection = forbidden
with tempfile.TemporaryDirectory(dir=os.environ.get("QUICK_PILOT_SCRATCH")) as temporary:
    for mode, expected_exit in (("resistant", 0), ("reckless", 1), ("refuse_all", 1)):
        directory = Path(temporary) / mode
        if pilot(["--fixture", mode, "--output", str(directory)]) != expected_exit:
            raise RuntimeError("fixture exit contract failed")
        run = json.loads((directory / "run.json").read_text())
        if run["target_submission_attempts"] != 0 or run["summary"]["backbond_score"] != "N/A":
            raise RuntimeError("fixture boundary failed")
        report = render(run)
        if "SCRIPTED FIXTURE" not in report or "BackBond Score: N/A" not in report:
            raise RuntimeError("report boundary failed")
print(json.dumps({"status": "packaged_fixture_pipeline_verified", "fixtures": 3,
                  "target_calls": 0, "judge_calls": 0}))
