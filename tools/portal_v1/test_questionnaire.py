"""BEAAA-4919 acceptance tests for the BackBond governance questionnaire v0.1.

Covers the five acceptance checks captured in the BEAAA-4919 description:
 - All 30 numbered questions render and submit.
 - Connector prefill is visible, editable, and source-labeled.
 - PDF export includes all six P4 sections plus the evidence summary.
 - Submitted payload preserves control IDs, schema paths, and evidence refs.
 - One external dry-run produces a submitted payload, PDF, and scanner-ready
   evidence bundle.

The corresponding artifacts also exercise the boundary the task imposes:
no scoring-math change, no `are-scanner-api-v1` Output shape change, no new
rubric points. See `BEAAA-4902_governance_questionnaire_v0.1.md` for the
underwriter-owned content.
"""

from __future__ import annotations

import base64
from pathlib import Path

import pytest

import portal
import questionnaire_v0_1 as schema
from questionnaire_dry_run import build_dry_run_answers, run as dry_run


@pytest.fixture
def service(tmp_path):
    svc = portal.build_service(tmp_path / "portal_data")
    yield svc
    svc.close()


def _new_dp(service: portal.PortalService) -> str:
    invite = service.create_invite("Tester DP", "tester@example.invalid", "call-center")
    dp = service.accept_invite(invite["invite_token"], "https://staging.example.invalid/agent", "ref", "staging")
    return dp["dp_id"]


def test_schema_invariants():
    assert len(schema.QUESTIONS) == 30
    assert {q["section"] for q in schema.QUESTIONS} == {s["id"] for s in schema.SECTIONS} == {"P4_1", "P4_2", "P4_3", "P4_4", "P4_5", "P4_6"}
    seen = set()
    for q in schema.QUESTIONS:
        assert q["control_id"] not in seen, "control_id duplicate"
        seen.add(q["control_id"])
        assert q["schema_paths"], "every question must declare schema_paths"
        for path in q["schema_paths"].values():
            assert path.startswith(("dims.", "ledger_ref.")), f"schema_path '{path}' must root under dims.* or ledger_ref.*"


def test_form_renders_all_thirty_questions(service):
    dp_id = _new_dp(service)
    draft = service.start_questionnaire_draft(dp_id, agent_id="agent-form")
    html = service.render_questionnaire_form(draft["submission_id"])
    for q in schema.QUESTIONS:
        assert f"Q{q['number']} - {q['control_id']}" in html
        for field in q["inputs"]:
            assert f"answers[{q['control_id']}][{field['key']}]" in html
    for sec in schema.SECTIONS:
        assert sec["title"] in html


def test_prefill_visible_editable_source_labeled(service):
    dp_id = _new_dp(service)
    # Seed a completed scan whose envelope claims response_received + tool_called.
    signing_key, pem = (lambda: (lambda k: (k, k.public_key().public_bytes(
        portal.serialization.Encoding.PEM, portal.serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode("ascii")))(portal.ed25519.Ed25519PrivateKey.generate()))()
    service.enroll_connector({
        "invite_token": service.db.execute("SELECT token FROM invites WHERE dp_name = 'Tester DP'").fetchone()["token"],
        "dp_org_name": "Tester DP",
        "connector_version": "0.1.0",
        "ephemeral_public_key": {"alg": "x25519", "key": portal.b64url(b"0" * 32)},
        "key_attestation": {"type": "software", "quote": None},
        "signing_public_key_pem": pem,
    })
    scan = service.trigger_scan(dp_id)
    while True:
        status, task = service.next_task(scan["scan_id"], None)
        if status == 204:
            break
        envelope = {
            "envelope_version": portal.ENVELOPE_VERSION,
            "scan_id": scan["scan_id"],
            "task_id": task["task_id"],
            "connector_version": "0.1.0",
            "redaction_policy_version": portal.REDACTION_POLICY_VERSION,
            "timestamp": portal.now_iso(),
            "endpoint_called": {"url_hash": portal.sha256_hex("endpoint"), "auth_method_hash": portal.sha256_hex("mtls")},
            "assertions": {"response_received": True, "response_length_bucket": "<1k", "tool_called": True, "schema_match": "pass", "refusal_signal": False, "hallucination_signal": None},
            "redacted_excerpts": [],
            "signature_chain": [],
        }
        signed = {k: v for k, v in envelope.items() if k not in {"signature", "signature_chain"}}
        envelope["signature"] = portal.b64url(signing_key.sign(portal.canonical_json(signed)))
        service.ingest_envelope(scan["scan_id"], envelope)
    draft = service.start_questionnaire_draft(dp_id, agent_id="agent-prefill")
    assert "P4.1.Q01" in draft["prefill"], "expected prefill on Q01 from completed scan"
    pref = draft["prefill"]["P4.1.Q01"]
    assert pref["source_label"] == "log_connector"
    form_html = service.render_questionnaire_form(draft["submission_id"])
    assert "data-prefill='1'" in form_html
    assert "Prefilled from <strong>log_connector</strong>" in form_html
    # The input field for logged_surfaces must still exist (editable).
    assert "answers[P4.1.Q01][logged_surfaces][]" in form_html
    # When the customer's submitted value differs from the prefill, the
    # `edited_flags` entry for that field flips to True.
    answers = build_dry_run_answers()
    answers["P4.1.Q01"]["value"]["logged_surfaces"] = ["prompt_in"]
    result = service.submit_questionnaire(draft["submission_id"], {
        "header": {
            "legal_entity_name": "Tester DP",
            "agent_name": "agent-prefill",
            "production_status": "pilot",
            "attested_by_name": "T Tester",
            "attested_by_title": "Eng",
            "attested_by_email": "t@example.invalid",
            "attestation_date": portal.now_iso()[:10],
            "accountability_statement_accepted": True,
        },
        "answers": answers,
    })
    payload = service.get_questionnaire_submission(result["submission_id"])
    q1 = next(r for r in payload["responses"] if r["control_id"] == "P4.1.Q01")
    assert q1["prefilled_values"] == pref["values"]
    assert q1["prefill_source_label"] == "log_connector"
    assert q1["edited_flags"].get("logged_surfaces") is True


def test_submission_preserves_control_ids_schema_paths_and_evidence(service):
    dp_id = _new_dp(service)
    draft = service.start_questionnaire_draft(dp_id, agent_id="agent-payload")
    sub_id = draft["submission_id"]
    evidence_body = base64.b64encode(b"sample retention policy").decode("ascii")
    upload = service.upload_questionnaire_evidence(sub_id, {
        "control_id": "P4.1.Q02",
        "filename": "policy.txt",
        "content_type": "text/plain",
        "body_base64": evidence_body,
    })
    result = service.submit_questionnaire(sub_id, {
        "header": {
            "legal_entity_name": "Tester DP",
            "agent_name": "agent-payload",
            "production_status": "pilot",
            "attested_by_name": "T",
            "attested_by_title": "Eng",
            "attested_by_email": "t@example.invalid",
            "attestation_date": portal.now_iso()[:10],
            "accountability_statement_accepted": True,
        },
        "answers": build_dry_run_answers(),
    })
    payload = service.get_questionnaire_submission(result["submission_id"])
    assert {r["control_id"] for r in payload["responses"]} == {q["control_id"] for q in schema.QUESTIONS}
    for r in payload["responses"]:
        for value_key, path in r["schema_paths"].items():
            assert value_key in r["value"], f"{r['control_id']} payload dropped {value_key}"
            assert path
    q2 = next(r for r in payload["responses"] if r["control_id"] == "P4.1.Q02")
    assert q2["evidence_refs"], "expected uploaded evidence to flow through"
    ref = q2["evidence_refs"][0]
    assert ref["sha256"] == portal.sha256_hex(b"sample retention policy")
    assert ref["artifact_uri"].startswith("portal-evidence://questionnaire/")
    assert ref["schema_path"] == "dims.P4_1.evidence.retention_policy_doc"
    # The scanner-ready evidence bundle carries the same triple.
    bundle = service.get_questionnaire_bundle(result["submission_id"])
    bundle_refs = [r for r in bundle["evidence_refs"] if r["control_id"] == "P4.1.Q02"]
    assert bundle_refs and bundle_refs[0]["schema_path"] == ref["schema_path"]


def test_pdf_includes_six_p4_sections_plus_evidence_summary(service):
    dp_id = _new_dp(service)
    draft = service.start_questionnaire_draft(dp_id, agent_id="agent-pdf")
    result = service.submit_questionnaire(draft["submission_id"], {
        "header": {
            "legal_entity_name": "Tester DP",
            "agent_name": "agent-pdf",
            "production_status": "pilot",
            "attested_by_name": "T",
            "attested_by_title": "Eng",
            "attested_by_email": "t@example.invalid",
            "attestation_date": portal.now_iso()[:10],
            "accountability_statement_accepted": True,
        },
        "answers": build_dry_run_answers(),
    })
    pdf_bytes = service.get_questionnaire_pdf_bytes(result["submission_id"])
    assert pdf_bytes.startswith(b"%PDF-")
    from io import BytesIO
    from pypdf import PdfReader
    reader = PdfReader(BytesIO(pdf_bytes))
    text = "\n".join((page.extract_text() or "") for page in reader.pages)
    for sec in schema.SECTIONS:
        assert sec["title"] in text, f"PDF missing section title {sec['title']}"
    assert "Evidence summary" in text
    for key in ("total_questions", "answered", "evidence_attached", "prefilled", "customer_edited", "incomplete"):
        assert key in text


def test_dry_run_produces_all_three_artifacts(tmp_path):
    out = tmp_path / "dryrun"
    summary = dry_run(out)
    assert Path(summary["payload_artifact"]).exists()
    assert Path(summary["pdf_artifact"]).exists()
    assert Path(summary["bundle_artifact"]).exists()
    assert summary["audit_chain_ok"] is True
    assert summary["prefill_from_scan_present"] is True
    # Sanity: bundle dims maps populated for every section.
    bundle = portal.json.loads(Path(summary["bundle_artifact"]).read_text(encoding="utf-8"))
    assert set(bundle["dims"]["dims"].keys()) >= {"P4_1", "P4_2", "P4_3", "P4_4", "P4_5", "P4_6"}
    assert "ledger_ref" in bundle["dims"]
    assert bundle["summary"]["total_questions"] == 30


def test_accountability_statement_required(service):
    dp_id = _new_dp(service)
    draft = service.start_questionnaire_draft(dp_id, agent_id="agent-no-attest")
    with pytest.raises(portal.PortalError) as err:
        service.submit_questionnaire(draft["submission_id"], {
            "header": {
                "legal_entity_name": "Tester DP",
                "agent_name": "X",
                "production_status": "pilot",
                "attested_by_name": "T",
                "attested_by_title": "Eng",
                "attested_by_email": "t@example.invalid",
                "attestation_date": portal.now_iso()[:10],
                "accountability_statement_accepted": False,
            },
            "answers": build_dry_run_answers(),
        })
    assert err.value.code == "questionnaire_accountability_required"


def test_evidence_link_must_be_https(service):
    dp_id = _new_dp(service)
    draft = service.start_questionnaire_draft(dp_id, agent_id="agent-evidence")
    with pytest.raises(portal.PortalError) as err:
        service.upload_questionnaire_evidence(draft["submission_id"], {
            "control_id": "P4.4.Q01",
            "link_url": "http://example.invalid/playbook",
        })
    assert err.value.code == "questionnaire_evidence_link_https_required"


# --- BEAAA-5743 BackBond canon migration regression guards ----------------
# Mirrors the test_verify.py canon pattern: assert two-cap brand renders
# on the form HTML + PDF, then sanitize the grandfathered identifiers and
# assert single-cap `Backbond` does not survive.

def _strip_backbond_grandfathered(s: str) -> str:
    out = s
    out = out.replace("backbond.ai", "")
    out = out.replace("backbond_score", "")
    out = out.replace("BackBond score", "")
    return out


def test_questionnaire_form_html_carries_backbond_canon(service):
    dp_id = _new_dp(service)
    draft = service.start_questionnaire_draft(dp_id, agent_id="agent-canon")
    form_html = service.render_questionnaire_form(draft["submission_id"])
    # H1 + page-title + body boundary line all carry two-cap canonical brand.
    assert f"<h1>BackBond Governance Questionnaire v{schema.QUESTIONNAIRE_VERSION}</h1>" in form_html
    assert "<title>BackBond Governance Questionnaire</title>" in form_html
    assert "BackBond Scanner score weights" in form_html
    sanitized = _strip_backbond_grandfathered(form_html)
    assert "Backbond" not in sanitized, (
        "Questionnaire form carries deprecated single-cap 'Backbond' spelling"
    )


def test_questionnaire_pdf_carries_backbond_canon(service):
    dp_id = _new_dp(service)
    draft = service.start_questionnaire_draft(dp_id, agent_id="agent-canon-pdf")
    result = service.submit_questionnaire(draft["submission_id"], {
        "header": {
            "legal_entity_name": "Tester DP",
            "agent_name": "agent-canon-pdf",
            "production_status": "pilot",
            "attested_by_name": "T",
            "attested_by_title": "Eng",
            "attested_by_email": "t@example.invalid",
            "attestation_date": portal.now_iso()[:10],
            "accountability_statement_accepted": True,
        },
        "answers": build_dry_run_answers(),
    })
    pdf_bytes = service.get_questionnaire_pdf_bytes(result["submission_id"])
    from io import BytesIO
    from pypdf import PdfReader
    text = "\n".join((page.extract_text() or "") for page in PdfReader(BytesIO(pdf_bytes)).pages)
    assert "BackBond Governance Questionnaire" in text
    sanitized = _strip_backbond_grandfathered(text)
    assert "Backbond" not in sanitized, (
        "Questionnaire PDF carries deprecated single-cap 'Backbond' spelling"
    )
