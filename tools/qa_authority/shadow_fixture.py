"""Create deterministic shadow evidence for the disabled rollout."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from tools.qa_authority.authority import bind, connect, evaluate, record_disposition

SHA = "a" * 40
QA_ISSUE = "11111111-1111-4111-8111-111111111111"


def run(output: Path) -> None:
    with tempfile.NamedTemporaryFile() as handle:
        db = connect(handle.name)
        bind(db, repository_id=7, pr_number=299, implementation_issue_id="fixture-implementation",
             qa_issue_id=QA_ISSUE, created_by="fixture")
        disposition_id = record_disposition(db, {
            "qa_issue_id": QA_ISSUE,
            "outcome": "APPROVED",
            "candidate_sha": SHA,
            "evidence_refs": ["fixture://evidence/1"],
            "author_identity_key": "backbond-qa",
            "author_agent_id_snapshot": "fixture-qa-agent",
            "created_by_run_id": "fixture-run",
            "paperclip_role": "qa",
            "paperclip_status": "active",
        })
        healthy = evaluate(db, repository_id=7, pr_number=299, head_sha=SHA,
                           corroboration={"valid": True, "qa_issue_id": QA_ISSUE,
                                          "qa_issue_status": "done", "disposition_id": disposition_id})
        reader_outage = evaluate(db, repository_id=7, pr_number=299, head_sha=SHA,
                                 corroboration=None)
        disagreement = evaluate(db, repository_id=7, pr_number=299, head_sha=SHA,
                                corroboration={"valid": True, "qa_issue_id": "wrong",
                                               "qa_issue_status": "done", "disposition_id": disposition_id})
    output.write_text(json.dumps({
        "check_name": "paperclip-qa-evidence-shadow",
        "required_check_state": "not_measured_by_fixture",
        "healthy": healthy.__dict__,
        "reader_outage": reader_outage.__dict__,
        "reader_disagreement": disagreement.__dict__,
    }, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    run(Path("tools/qa_authority/shadow-evidence.json"))
