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
from evals.amazon_quick_assessment import load_profile
from tools.quick_assessment import render as render_assessment
from tools.quick_concurrency_spike import spike
from tools.quick_source_checks import prepare as prepare_source_checks
from harness.amazon_quick_accounting import checked_budget
from harness.amazon_quick_recovery import recovery_plan
from harness.amazon_quick_sources import collect
from evals.quick_source_evidence import attack_exposure
from harness.amazon_quick_batch import QuickBatchRunner


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

# The expanded profiles and runtime imports must also survive the allowlisted
# image closure. This is schema/import qualification, never live evidence.
for version, calls in (("1.2", 790), ("1.3", 795)):
    for variant in ("hr_finance", "healthcare"):
        profile = load_profile(variant, version)
        if profile["case_count"] != 600 or profile["initial_target_calls"] != calls:
            raise RuntimeError("expanded profile call accounting failed")
        report = render_assessment({"assessment_subject": "configuration_copy", "status": "incomplete", "results": []})
        if "CONFIGURATION COPY" not in report or "BackBond Score: N/A" not in report:
            raise RuntimeError("expanded report scope failed")
print(json.dumps({"status": "expanded_profile_imports_verified_only", "profiles": 4,
                  "target_calls": 0, "judge_calls": 0}))
source_plan=prepare_source_checks(load_profile('hr_finance','1.3'),'a'*40)
if source_plan['case_count']!=100 or source_plan['initial_target_calls']!=140:
    raise RuntimeError('positive_source_selection_closure_failed')
