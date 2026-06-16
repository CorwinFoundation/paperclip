"""BackBond governance questionnaire v0.1 service (BEAAA-4919).

Implements the service-side of the customer-fillable portal form, the printable
PDF, and the scanner-ready evidence bundle for the 30-question questionnaire
authored under BEAAA-4902. Attaches to portal.PortalService at module import.

Boundary (carried forward from BEAAA-4902): no scoring-math change, no
are-scanner-api-v1 Output shape change, no new rubric points. The output here
is consumed by the scanner via `governance_attestations` Input only.
"""

from __future__ import annotations

import base64
import html
import json
import sqlite3
import uuid
from io import BytesIO
from pathlib import Path
from typing import Any

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

import questionnaire_v0_1 as questionnaire_schema


# ---------------------------------------------------------------------------
# helpers (kept local; portal.py is the canonical home for sha256/canonical_json)
# ---------------------------------------------------------------------------

def _coerce_answer_value(self: Any, q: dict[str, Any], submitted_q: dict[str, Any]) -> dict[str, Any]:
    from portal import PortalError  # local import to avoid circular at module load
    cleaned: dict[str, Any] = {}
    for field in q["inputs"]:
        key = field["key"]
        ftype = field["type"]
        raw = submitted_q.get(key) if isinstance(submitted_q, dict) else None
        if raw is None and field.get("required") and ftype not in {"release_records", "escalation_chain", "int_map_by_selection"}:
            raise PortalError(422, "questionnaire_value_required", f"Question {q['number']} field {key} is required.")
        if ftype == "boolean":
            if isinstance(raw, str):
                cleaned[key] = raw.lower() in {"1", "true", "yes", "on"}
            else:
                cleaned[key] = bool(raw) if raw is not None else None
        elif ftype == "integer":
            if raw in (None, ""):
                cleaned[key] = None
            else:
                try:
                    cleaned[key] = int(raw)
                except (TypeError, ValueError) as err:
                    raise PortalError(422, "questionnaire_integer_invalid", f"Question {q['number']} {key} must be integer.") from err
                if "min" in field and cleaned[key] is not None and cleaned[key] < field["min"]:
                    raise PortalError(422, "questionnaire_integer_below_min", f"Question {q['number']} {key} below minimum.")
                if "max" in field and cleaned[key] is not None and cleaned[key] > field["max"]:
                    raise PortalError(422, "questionnaire_integer_above_max", f"Question {q['number']} {key} above maximum.")
        elif ftype == "enum":
            if raw not in field["options"]:
                raise PortalError(422, "questionnaire_enum_invalid", f"Question {q['number']} {key} must be one of {field['options']}.")
            cleaned[key] = raw
        elif ftype == "checklist":
            if isinstance(raw, str):
                items = [v.strip() for v in raw.split(",") if v.strip()]
            elif isinstance(raw, list):
                items = [str(v).strip() for v in raw if str(v).strip()]
            else:
                items = []
            for item in items:
                if item not in field["options"]:
                    raise PortalError(422, "questionnaire_checklist_invalid", f"Question {q['number']} {key} option {item!r} not allowed.")
            cleaned[key] = items
        elif ftype == "text":
            cleaned[key] = (str(raw).strip() if raw is not None else "")[: q["char_cap"]]
        elif ftype == "longtext":
            cleaned[key] = (str(raw).strip() if raw is not None else "")[: q["char_cap"]]
        elif ftype == "date":
            cleaned[key] = str(raw).strip() if raw else None
        elif ftype == "date_or_never":
            v = str(raw).strip() if raw else ""
            cleaned[key] = "never" if v.lower() == "never" else (v or None)
        elif ftype == "ledger_ref":
            cleaned[key] = str(raw).strip() if raw else None
        elif ftype == "int_map_by_selection":
            if isinstance(raw, dict):
                out: dict[str, int] = {}
                for k, v in raw.items():
                    try:
                        out[str(k)] = int(v)
                    except (TypeError, ValueError) as err:
                        raise PortalError(422, "questionnaire_int_map_invalid", f"Question {q['number']} {key} value for {k} not integer.") from err
                cleaned[key] = out
            else:
                cleaned[key] = {}
        elif ftype == "release_records":
            items = raw if isinstance(raw, list) else []
            bounded = items[: field.get("max_rows", 3)]
            cleaned[key] = [
                {
                    "release_id": str(r.get("release_id", ""))[: q["char_cap"]],
                    "deployed_at": str(r.get("deployed_at", "")),
                    "model_id": str(r.get("model_id", ""))[: q["char_cap"]],
                    "prompt_sha256": str(r.get("prompt_sha256", "")).lower(),
                }
                for r in bounded
                if isinstance(r, dict)
            ]
        elif ftype == "escalation_chain":
            items = raw if isinstance(raw, list) else []
            bounded = items[: field.get("max_rows", 10)]
            rows: list[dict[str, Any]] = []
            for r in bounded:
                if not isinstance(r, dict):
                    continue
                role = str(r.get("role", ""))[: q["char_cap"]]
                try:
                    sla = int(r.get("sla_minutes")) if r.get("sla_minutes") not in (None, "") else None
                except (TypeError, ValueError) as err:
                    raise PortalError(422, "questionnaire_escalation_invalid", f"Question {q['number']} sla_minutes must be integer.") from err
                if role:
                    rows.append({"role": role, "sla_minutes": sla})
            if field.get("required") and not rows:
                raise PortalError(422, "questionnaire_escalation_required", f"Question {q['number']} requires at least one escalation row.")
            cleaned[key] = rows
        else:
            cleaned[key] = raw
    return cleaned


def _set_schema_path(self: Any, root: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    cur = root
    for i, segment in enumerate(parts):
        last = i == len(parts) - 1
        if segment.endswith("[]"):
            key = segment[:-2]
            if last:
                if key not in cur or not isinstance(cur[key], list):
                    cur[key] = []
                if isinstance(value, list):
                    cur[key].extend(value)
                elif value is not None:
                    cur[key].append(value)
            else:
                if key not in cur or not isinstance(cur[key], list):
                    cur[key] = []
                if not cur[key] or not isinstance(cur[key][-1], dict):
                    cur[key].append({})
                cur = cur[key][-1]
        else:
            if last:
                cur[segment] = value
            else:
                if segment not in cur or not isinstance(cur[segment], dict):
                    cur[segment] = {}
                cur = cur[segment]


def _require_questionnaire_schema(self: Any) -> Any:
    from portal import PortalError
    if questionnaire_schema is None:
        raise PortalError(500, "questionnaire_schema_missing", "Governance questionnaire schema module not loaded.")
    return questionnaire_schema


def _resolve_questionnaire_prefill(self: Any, dp_id: str, agent_id: str) -> dict[str, Any]:
    prefill: dict[str, Any] = {}
    scan = self.db.execute(
        "SELECT * FROM scans WHERE dp_id = ? AND status = 'complete' ORDER BY created_at DESC LIMIT 1",
        (dp_id,),
    ).fetchone()
    if not scan:
        return prefill
    envelopes = list(self.db.execute("SELECT * FROM envelopes WHERE scan_id = ?", (scan["id"],)))
    if not envelopes:
        return prefill
    observed_surfaces: set[str] = set()
    for env_row in envelopes:
        stored_path = self.config.evidence_dir / scan["id"] / "envelopes" / f"{env_row['task_id']}.json"
        if not stored_path.exists():
            continue
        try:
            envelope = json.loads(stored_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        assertions = envelope.get("assertions", {}) if isinstance(envelope, dict) else {}
        if assertions.get("response_received"):
            observed_surfaces.update({"prompt_in", "model_response"})
        if assertions.get("tool_called"):
            observed_surfaces.update({"tool_call", "tool_response"})
    if observed_surfaces:
        prefill["P4.1.Q01"] = {
            "values": {"logged_surfaces": sorted(observed_surfaces)},
            "source_label": "log_connector",
            "source_scan_id": scan["id"],
        }
    return prefill


def start_questionnaire_draft(
    self: Any,
    dp_id: str,
    agent_id: str,
    header_seed: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from portal import PortalError, now_iso
    schema = self._require_questionnaire_schema()
    dp = self.db.execute("SELECT * FROM design_partners WHERE id = ?", (dp_id,)).fetchone()
    if not dp:
        raise PortalError(404, "dp_not_found", "Design partner not found.")
    prefill = self._resolve_questionnaire_prefill(dp_id, agent_id)
    seeded_header: dict[str, Any] = {
        "legal_entity_name": (header_seed or {}).get("legal_entity_name") or dp["dp_name"],
        "customer_id": (header_seed or {}).get("customer_id") or dp["id"],
        "agent_id": agent_id,
        "agent_name": (header_seed or {}).get("agent_name") or "",
        "production_status": (header_seed or {}).get("production_status") or "pilot",
        "attested_by_name": (header_seed or {}).get("attested_by_name") or "",
        "attested_by_title": (header_seed or {}).get("attested_by_title") or "",
        "attested_by_email": (header_seed or {}).get("attested_by_email") or dp["email"],
        "attestation_date": now_iso()[:10],
        "accountability_statement_accepted": False,
    }
    submission_id = str(uuid.uuid4())
    self.db.execute(
        "INSERT INTO questionnaire_submissions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)",
        (
            submission_id,
            dp_id,
            agent_id,
            schema.QUESTIONNAIRE_VERSION,
            "draft",
            json.dumps(seeded_header, sort_keys=True),
            json.dumps({}, sort_keys=True),
            json.dumps(prefill, sort_keys=True),
            json.dumps({}, sort_keys=True),
            now_iso(),
        ),
    )
    self.db.commit()
    self.audit("dp", "questionnaire_draft_started", "questionnaire", submission_id, {"dp_id": dp_id, "agent_id": agent_id})
    return {
        "submission_id": submission_id,
        "questionnaire_version": schema.QUESTIONNAIRE_VERSION,
        "prefill": prefill,
        "header": seeded_header,
    }


def upload_questionnaire_evidence(self: Any, submission_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    from portal import PortalError, now_iso, sha256_hex
    schema = self._require_questionnaire_schema()
    sub = self.db.execute("SELECT * FROM questionnaire_submissions WHERE id = ?", (submission_id,)).fetchone()
    if not sub:
        raise PortalError(404, "questionnaire_submission_not_found", "Submission not found.")
    if sub["status"] != "draft":
        raise PortalError(409, "questionnaire_not_draft", "Evidence can only be attached while the submission is still a draft.")
    control_id = payload.get("control_id")
    valid_controls = {q["control_id"] for q in schema.QUESTIONS}
    if control_id not in valid_controls:
        raise PortalError(422, "questionnaire_control_id_unknown", f"control_id {control_id!r} not in v0.1 questionnaire.")
    evidence_id = str(uuid.uuid4())
    sub_dir = self.config.evidence_dir / "questionnaire" / submission_id / "evidence"
    sub_dir.mkdir(parents=True, exist_ok=True)
    filename = payload.get("filename")
    content_type = payload.get("content_type")
    body_b64 = payload.get("body_base64")
    link_url = payload.get("link_url")
    if not body_b64 and not link_url:
        raise PortalError(422, "questionnaire_evidence_payload_required", "Provide either body_base64 or link_url.")
    if link_url and not str(link_url).lower().startswith("https://"):
        raise PortalError(422, "questionnaire_evidence_link_https_required", "Evidence links must be HTTPS per questionnaire v0.1 rule 4.")
    sha = None
    byte_size = None
    artifact_uri: str
    if body_b64:
        allowed_exts = {"pdf", "csv", "json", "txt", "png", "jpg", "jpeg", "md", "markdown"}
        ext = (filename or "evidence").rsplit(".", 1)[-1].lower()
        if ext not in allowed_exts:
            raise PortalError(422, "questionnaire_evidence_type_unsupported", f"Evidence extension {ext!r} not allowed; v0.1 rule 4 lists PDF/CSV/JSON/TXT/PNG/JPG/MD.")
        try:
            data = base64.b64decode(body_b64, validate=True)
        except Exception as err:  # noqa: BLE001
            raise PortalError(422, "questionnaire_evidence_body_invalid_base64", f"body_base64 invalid: {err}") from err
        if not data:
            raise PortalError(422, "questionnaire_evidence_body_empty", "body_base64 decoded to empty bytes.")
        sha = sha256_hex(data)
        byte_size = len(data)
        artifact_path = sub_dir / f"{evidence_id}.{ext}"
        artifact_path.write_bytes(data)
        artifact_uri = f"portal-evidence://questionnaire/{submission_id}/evidence/{evidence_id}.{ext}"
    else:
        artifact_uri = link_url
    self.db.execute(
        "INSERT INTO questionnaire_evidence VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            evidence_id,
            submission_id,
            control_id,
            filename,
            link_url,
            content_type,
            byte_size,
            sha,
            artifact_uri,
            now_iso(),
        ),
    )
    self.db.commit()
    self.audit(
        "dp",
        "questionnaire_evidence_uploaded",
        "questionnaire",
        submission_id,
        {"control_id": control_id, "evidence_id": evidence_id, "sha256": sha, "link_url": link_url},
    )
    return {
        "evidence_id": evidence_id,
        "control_id": control_id,
        "sha256": sha,
        "byte_size": byte_size,
        "artifact_uri": artifact_uri,
        "link_url": link_url,
    }


def submit_questionnaire(self: Any, submission_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    from portal import PortalError, canonical_json, now_iso, sha256_hex
    schema = self._require_questionnaire_schema()
    sub = self.db.execute("SELECT * FROM questionnaire_submissions WHERE id = ?", (submission_id,)).fetchone()
    if not sub:
        raise PortalError(404, "questionnaire_submission_not_found", "Submission not found.")
    if sub["status"] == "submitted":
        raise PortalError(409, "questionnaire_already_submitted", "Submission has already been submitted.")
    header = json.loads(sub["header_json"])
    header_in = payload.get("header") or {}
    for field in schema.HEADER_FIELDS:
        key = field["key"]
        if key == "accountability_statement":
            header["accountability_statement_accepted"] = bool(header_in.get("accountability_statement_accepted"))
            if field["required"] and not header["accountability_statement_accepted"]:
                raise PortalError(422, "questionnaire_accountability_required", "Accountability statement must be accepted.")
            continue
        if key in header_in and header_in[key] not in (None, ""):
            header[key] = header_in[key]
        if field["required"] and not header.get(key):
            raise PortalError(422, "questionnaire_header_field_required", f"Header field {key} is required.")
        if field.get("control") == "enum" and header.get(key) not in field.get("options", []):
            raise PortalError(422, "questionnaire_header_enum_invalid", f"Header field {key} must be in {field['options']}.")
    prefill = json.loads(sub["prefill_json"])
    answers_in = payload.get("answers") or {}
    edited_flags: dict[str, dict[str, bool]] = {}
    coerced_answers: dict[str, dict[str, Any]] = {}
    for q in schema.QUESTIONS:
        control_id = q["control_id"]
        qp = answers_in.get(control_id) or {}
        # Accept either {"value": {...}, "evidence_state": "..."} OR a flat
        # mapping of field-key -> raw-value. The first shape is what the
        # JSON API receives; the second is what the HTML form decoder
        # produces.
        value_in = qp.get("value") if isinstance(qp, dict) and isinstance(qp.get("value"), dict) else qp
        cleaned = self._coerce_answer_value(q, value_in)
        evidence_state = qp.get("evidence_state") or "incomplete"
        if evidence_state not in {"none", "incomplete", "reviewed"}:
            raise PortalError(422, "questionnaire_evidence_state_invalid", f"{control_id} evidence_state must be none|incomplete|reviewed.")
        coerced_answers[control_id] = {
            "value": cleaned,
            "evidence_state": evidence_state,
            "evidence_refs": [],
        }
        pre = prefill.get(control_id, {}).get("values") if isinstance(prefill.get(control_id), dict) else None
        if pre:
            flags: dict[str, bool] = {}
            for k, prefilled_value in pre.items():
                flags[k] = cleaned.get(k) != prefilled_value
            edited_flags[control_id] = flags
    evidence_rows = list(self.db.execute(
        "SELECT * FROM questionnaire_evidence WHERE submission_id = ?",
        (submission_id,),
    ))
    evidence_by_control: dict[str, list[dict[str, Any]]] = {}
    for row in evidence_rows:
        evidence_by_control.setdefault(row["control_id"], []).append({
            "evidence_id": row["id"],
            "filename": row["filename"],
            "link_url": row["link_url"],
            "content_type": row["content_type"],
            "byte_size": row["byte_size"],
            "sha256": row["sha256"],
            "artifact_uri": row["artifact_uri"],
            "uploaded_at": row["uploaded_at"],
        })
    for control_id, ans in coerced_answers.items():
        ans["evidence_refs"] = evidence_by_control.get(control_id, [])
        if ans["evidence_refs"] and ans["evidence_state"] == "none":
            ans["evidence_state"] = "reviewed"
    submitted_payload = self._build_submitted_payload(sub, header, coerced_answers, prefill, edited_flags)
    payload_sha = sha256_hex(canonical_json(submitted_payload))
    submitted_at = now_iso()
    self.db.execute(
        "UPDATE questionnaire_submissions SET status = 'submitted', header_json = ?, answers_json = ?, edited_flags_json = ?, submitted_at = ?, payload_sha256 = ? WHERE id = ?",
        (
            json.dumps(header, sort_keys=True),
            json.dumps(coerced_answers, sort_keys=True),
            json.dumps(edited_flags, sort_keys=True),
            submitted_at,
            payload_sha,
            submission_id,
        ),
    )
    sub_dir = self.config.evidence_dir / "questionnaire" / submission_id
    sub_dir.mkdir(parents=True, exist_ok=True)
    payload_path = sub_dir / "payload.json"
    payload_path.write_bytes(canonical_json(submitted_payload))
    pdf_bytes = self._render_questionnaire_pdf(submission_id, submitted_payload)
    pdf_path = sub_dir / "questionnaire.pdf"
    pdf_path.write_bytes(pdf_bytes)
    bundle = self._build_scanner_evidence_bundle(submission_id, submitted_payload, pdf_bytes)
    bundle_path = sub_dir / "scanner-evidence-bundle.json"
    bundle_path.write_bytes(canonical_json(bundle))
    self.db.commit()
    self.audit(
        "dp",
        "questionnaire_submitted",
        "questionnaire",
        submission_id,
        {
            "dp_id": sub["dp_id"],
            "agent_id": sub["agent_id"],
            "payload_sha256": payload_sha,
            "bundle_sha256": sha256_hex(canonical_json(bundle)),
        },
    )
    return {
        "submission_id": submission_id,
        "status": "submitted",
        "submitted_at": submitted_at,
        "payload_sha256": payload_sha,
        "bundle_sha256": sha256_hex(canonical_json(bundle)),
        "payload_artifact_uri": f"portal-evidence://questionnaire/{submission_id}/payload.json",
        "pdf_artifact_uri": f"portal-evidence://questionnaire/{submission_id}/questionnaire.pdf",
        "bundle_artifact_uri": f"portal-evidence://questionnaire/{submission_id}/scanner-evidence-bundle.json",
    }


def _build_submitted_payload(
    self: Any,
    sub: sqlite3.Row,
    header: dict[str, Any],
    answers: dict[str, dict[str, Any]],
    prefill: dict[str, Any],
    edited_flags: dict[str, dict[str, bool]],
) -> dict[str, Any]:
    from portal import PORTAL_VERSION, SCANNER_VERSION, CORPUS_VERSION, now_iso
    schema = self._require_questionnaire_schema()
    responses: list[dict[str, Any]] = []
    normalized_dims: dict[str, Any] = {}
    for q in schema.QUESTIONS:
        cid = q["control_id"]
        answer = answers[cid]
        for value_key, schema_path in q["schema_paths"].items():
            value = answer["value"].get(value_key)
            self._set_schema_path(normalized_dims, schema_path, value)
        response_entry = {
            "number": q["number"],
            "control_id": cid,
            "section": q["section"],
            "prompt": q["prompt"],
            "schema_paths": dict(q["schema_paths"]),
            "value": answer["value"],
            "evidence_state": answer["evidence_state"],
            "evidence_refs": [
                {
                    "evidence_id": ref["evidence_id"],
                    "schema_path": q["evidence_schema_path"],
                    "filename": ref.get("filename"),
                    "link_url": ref.get("link_url"),
                    "sha256": ref.get("sha256"),
                    "byte_size": ref.get("byte_size"),
                    "content_type": ref.get("content_type"),
                    "artifact_uri": ref["artifact_uri"],
                }
                for ref in answer.get("evidence_refs", [])
            ],
            "prefill_source": q["prefill_source"],
            "prefilled_values": prefill.get(cid, {}).get("values") if isinstance(prefill.get(cid), dict) else None,
            "prefill_source_label": prefill.get(cid, {}).get("source_label") if isinstance(prefill.get(cid), dict) else None,
            "edited_flags": edited_flags.get(cid, {}),
        }
        responses.append(response_entry)
    return {
        "questionnaire_version": schema.QUESTIONNAIRE_VERSION,
        "parent_ticket": schema.QUESTIONNAIRE_PARENT_TICKET,
        "impl_ticket": schema.QUESTIONNAIRE_IMPL_TICKET,
        "portal_version": PORTAL_VERSION,
        "scanner_target_version": SCANNER_VERSION,
        "corpus_target_version": CORPUS_VERSION,
        "submission_id": sub["id"],
        "dp_id": sub["dp_id"],
        "agent_id": sub["agent_id"],
        "header": header,
        "responses": responses,
        "dims": normalized_dims,
        "generated_at": now_iso(),
    }


def _build_scanner_evidence_bundle(
    self: Any,
    submission_id: str,
    submitted_payload: dict[str, Any],
    pdf_bytes: bytes,
) -> dict[str, Any]:
    from portal import PORTAL_VERSION, SCANNER_VERSION, CORPUS_VERSION, canonical_json, now_iso, sha256_hex
    responses = submitted_payload["responses"]
    counts = self._questionnaire_summary_counts(responses)
    evidence_refs = []
    for entry in responses:
        for ref in entry["evidence_refs"]:
            evidence_refs.append({
                "rubric_point_id": f"GOVERNANCE.ATTESTATION.{entry['section']}",
                "control_id": entry["control_id"],
                "schema_path": ref["schema_path"],
                "artifact_uri": ref["artifact_uri"],
                "sha256": ref["sha256"],
                "link_url": ref["link_url"],
                "uploaded_filename": ref.get("filename"),
                "verdict": "attested" if entry["evidence_state"] == "reviewed" else "claim_only",
                "evidence_state": entry["evidence_state"],
            })
    return {
        "bundle_version": "0.1",
        "submission_id": submission_id,
        "questionnaire_version": submitted_payload["questionnaire_version"],
        "parent_ticket": submitted_payload["parent_ticket"],
        "portal_version": PORTAL_VERSION,
        "scanner_target_version": SCANNER_VERSION,
        "corpus_target_version": CORPUS_VERSION,
        "header": submitted_payload["header"],
        "summary": counts,
        "dims": submitted_payload["dims"],
        "evidence_refs": evidence_refs,
        "submitted_payload_artifact_uri": f"portal-evidence://questionnaire/{submission_id}/payload.json",
        "submitted_payload_sha256": sha256_hex(canonical_json(submitted_payload)),
        "pdf_artifact_uri": f"portal-evidence://questionnaire/{submission_id}/questionnaire.pdf",
        "pdf_sha256": sha256_hex(pdf_bytes),
        "generated_at": now_iso(),
    }


def _questionnaire_summary_counts(self: Any, responses: list[dict[str, Any]]) -> dict[str, int]:
    total = len(responses)
    answered = 0
    evidence_attached = 0
    prefilled = 0
    customer_edited = 0
    incomplete = 0
    for entry in responses:
        non_empty = any(v not in (None, "", [], {}) for v in entry["value"].values())
        if non_empty:
            answered += 1
        if entry["evidence_refs"]:
            evidence_attached += 1
        if entry["prefilled_values"]:
            prefilled += 1
            if any(entry["edited_flags"].values()):
                customer_edited += 1
        if entry["evidence_state"] == "incomplete":
            incomplete += 1
    return {
        "total_questions": total,
        "answered": answered,
        "evidence_attached": evidence_attached,
        "prefilled": prefilled,
        "customer_edited": customer_edited,
        "incomplete": incomplete,
    }


def get_questionnaire_submission(self: Any, submission_id: str) -> dict[str, Any]:
    from portal import PortalError
    sub = self.db.execute("SELECT * FROM questionnaire_submissions WHERE id = ?", (submission_id,)).fetchone()
    if not sub:
        raise PortalError(404, "questionnaire_submission_not_found", "Submission not found.")
    if sub["status"] != "submitted":
        raise PortalError(409, "questionnaire_not_submitted", "Submission has not yet been submitted.")
    payload_path = self.config.evidence_dir / "questionnaire" / submission_id / "payload.json"
    if not payload_path.exists():
        raise PortalError(404, "questionnaire_payload_missing", "Submitted payload artifact not found on disk.")
    return json.loads(payload_path.read_text(encoding="utf-8"))


def get_questionnaire_bundle(self: Any, submission_id: str) -> dict[str, Any]:
    from portal import PortalError
    sub = self.db.execute("SELECT * FROM questionnaire_submissions WHERE id = ?", (submission_id,)).fetchone()
    if not sub:
        raise PortalError(404, "questionnaire_submission_not_found", "Submission not found.")
    if sub["status"] != "submitted":
        raise PortalError(409, "questionnaire_not_submitted", "Submission has not yet been submitted.")
    path = self.config.evidence_dir / "questionnaire" / submission_id / "scanner-evidence-bundle.json"
    if not path.exists():
        raise PortalError(404, "questionnaire_bundle_missing", "Scanner-evidence bundle artifact not found on disk.")
    return json.loads(path.read_text(encoding="utf-8"))


def get_questionnaire_pdf_bytes(self: Any, submission_id: str) -> bytes:
    from portal import PortalError
    sub = self.db.execute("SELECT * FROM questionnaire_submissions WHERE id = ?", (submission_id,)).fetchone()
    if not sub:
        raise PortalError(404, "questionnaire_submission_not_found", "Submission not found.")
    if sub["status"] != "submitted":
        raise PortalError(409, "questionnaire_not_submitted", "Submission has not yet been submitted.")
    path = self.config.evidence_dir / "questionnaire" / submission_id / "questionnaire.pdf"
    if not path.exists():
        raise PortalError(404, "questionnaire_pdf_missing", "PDF artifact not found on disk.")
    return path.read_bytes()


def render_questionnaire_form(self: Any, submission_id: str) -> str:
    from portal import PortalError
    schema = self._require_questionnaire_schema()
    sub = self.db.execute("SELECT * FROM questionnaire_submissions WHERE id = ?", (submission_id,)).fetchone()
    if not sub:
        raise PortalError(404, "questionnaire_submission_not_found", "Submission not found.")
    header = json.loads(sub["header_json"])
    prefill = json.loads(sub["prefill_json"])
    sections_html = []
    for sec in schema.SECTIONS:
        qs = schema.questions_by_section(sec["id"])
        rows = []
        for q in qs:
            pre = prefill.get(q["control_id"]) if isinstance(prefill, dict) else None
            pre_html = ""
            if pre and isinstance(pre, dict) and pre.get("values"):
                pre_html = (
                    f"<p class='muted' data-prefill='1'>Prefilled from <strong>{html.escape(pre.get('source_label', q['prefill_source']))}</strong>: "
                    f"<code>{html.escape(json.dumps(pre['values'], sort_keys=True))}</code> "
                    f"<em>(review and edit if needed)</em></p>"
                )
            fields_html = []
            for field in q["inputs"]:
                fkey = field["key"]
                name_attr = f"answers[{q['control_id']}][{fkey}]"
                if field["type"] == "enum":
                    opts = "".join(f"<option value='{html.escape(o)}'>{html.escape(o)}</option>" for o in field["options"])
                    fields_html.append(f"<label>{html.escape(fkey)} <select name='{html.escape(name_attr)}'><option value=''></option>{opts}</select></label>")
                elif field["type"] == "checklist":
                    boxes = "".join(
                        f"<label><input type='checkbox' name='{html.escape(name_attr)}[]' value='{html.escape(o)}'> {html.escape(o)}</label>"
                        for o in field["options"]
                    )
                    fields_html.append(f"<fieldset><legend>{html.escape(fkey)}</legend>{boxes}</fieldset>")
                elif field["type"] == "boolean":
                    fields_html.append(f"<label>{html.escape(fkey)} <select name='{html.escape(name_attr)}'><option value=''></option><option value='true'>true</option><option value='false'>false</option></select></label>")
                elif field["type"] == "integer":
                    fields_html.append(f"<label>{html.escape(fkey)} <input type='number' name='{html.escape(name_attr)}' min='{field.get('min','')}' max='{field.get('max','')}'></label>")
                elif field["type"] in {"date", "date_or_never"}:
                    fields_html.append(f"<label>{html.escape(fkey)} <input type='text' placeholder='YYYY-MM-DD or never' name='{html.escape(name_attr)}'></label>")
                elif field["type"] == "longtext":
                    fields_html.append(f"<label>{html.escape(fkey)} <textarea name='{html.escape(name_attr)}' maxlength='{q['char_cap']}'></textarea></label>")
                else:
                    fields_html.append(f"<label>{html.escape(fkey)} <input type='text' name='{html.escape(name_attr)}' maxlength='{q['char_cap']}'></label>")
            ev = ""
            if q["evidence_label"]:
                ev = f"<p class='muted'>Evidence requested: {html.escape(q['evidence_label'])} (schema path <code>{html.escape(q['evidence_schema_path'] or '')}</code>)</p>"
            rows.append(
                f"<article data-control='{html.escape(q['control_id'])}'>"
                f"<h3>Q{q['number']} - {html.escape(q['control_id'])}</h3>"
                f"<p>{html.escape(q['prompt'])}</p>"
                f"{pre_html}{''.join(fields_html)}{ev}"
                f"<label>evidence_state "
                f"<select name='answers[{q['control_id']}][evidence_state]'>"
                f"<option value='incomplete'>incomplete</option>"
                f"<option value='reviewed'>reviewed</option>"
                f"<option value='none'>none</option>"
                f"</select></label>"
                f"</article>"
            )
        sections_html.append(f"<section><h2>{html.escape(sec['title'])}</h2>{''.join(rows)}</section>")
    body = f"""
<h1>BackBond Governance Questionnaire v{schema.QUESTIONNAIRE_VERSION}</h1>
<p class='muted'>Submission ID: {submission_id} | DP: {html.escape(sub['dp_id'])} | Agent ID: {html.escape(sub['agent_id'])}</p>
<blockquote>{html.escape(schema.HEADER_INTRO)}</blockquote>
<form method='post' action='/questionnaire/{submission_id}/submit'>
<section>
<h2>Header</h2>
<label>Legal entity name <input name='header[legal_entity_name]' value='{html.escape(header.get('legal_entity_name', ''))}' required></label>
<label>Customer ID <input name='header[customer_id]' value='{html.escape(header.get('customer_id', ''))}' readonly></label>
<label>Agent ID <input name='header[agent_id]' value='{html.escape(header.get('agent_id', ''))}' readonly></label>
<label>Agent name <input name='header[agent_name]' value='{html.escape(header.get('agent_name', ''))}' required></label>
<label>Production status <select name='header[production_status]'>
{''.join(f"<option value='{html.escape(o)}'>{html.escape(o)}</option>" for o in schema.PRODUCTION_STATUSES)}
</select></label>
<label>Attested by name <input name='header[attested_by_name]' value='{html.escape(header.get('attested_by_name', ''))}' required></label>
<label>Attested by title <input name='header[attested_by_title]' value='{html.escape(header.get('attested_by_title', ''))}' required></label>
<label>Attested by email <input name='header[attested_by_email]' type='email' value='{html.escape(header.get('attested_by_email', ''))}' required></label>
<label>Attestation date <input name='header[attestation_date]' value='{html.escape(header.get('attestation_date', ''))}' required></label>
<label><input type='checkbox' name='header[accountability_statement_accepted]' value='true'> {html.escape(schema.ACCOUNTABILITY_STATEMENT)}</label>
</section>
{''.join(sections_html)}
<button type='submit'>Submit attestations</button>
</form>
<p class='muted'>Boundary: this questionnaire does not change the BackBond Scanner score weights or output shape. Missing evidence is priced at maximum risk by the scanner.</p>
"""
    return self._render_page("BackBond Governance Questionnaire", body)


def _render_questionnaire_pdf(self: Any, submission_id: str, submitted_payload: dict[str, Any]) -> bytes:
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    width, height = A4
    margin_x = 36
    y = [height - 50]
    line_gap = 12

    def newline(step: int = 1) -> None:
        y[0] -= step * line_gap
        if y[0] < 60:
            c.showPage()
            y[0] = height - 50

    def write(text: str, font: str = "Helvetica", size: int = 9, indent: int = 0) -> None:
        c.setFont(font, size)
        usable = width - margin_x * 2 - indent
        wrapped: list[str] = []
        cur = ""
        for word in (text or "").split(" "):
            candidate = (cur + " " + word).strip()
            if c.stringWidth(candidate, font, size) > usable:
                if cur:
                    wrapped.append(cur)
                cur = word
            else:
                cur = candidate
        if cur:
            wrapped.append(cur)
        for line in wrapped or [""]:
            c.drawString(margin_x + indent, y[0], line)
            newline(1)

    header = submitted_payload["header"]
    write(f"BackBond Governance Questionnaire v{submitted_payload['questionnaire_version']}", font="Helvetica-Bold", size=14)
    write(f"Submission ID: {submission_id}", size=8)
    write(f"Generated: {submitted_payload['generated_at']}", size=8)
    newline()
    write("Section 0 - Header", font="Helvetica-Bold", size=11)
    for key in ("legal_entity_name", "customer_id", "agent_id", "agent_name", "production_status", "attested_by_name", "attested_by_title", "attested_by_email", "attestation_date"):
        write(f"{key}: {header.get(key, '')}", size=9)
    write(f"Accountability statement accepted: {header.get('accountability_statement_accepted', False)}", size=9)
    newline()
    section_titles = {s["id"]: s["title"] for s in self._require_questionnaire_schema().SECTIONS}
    rendered_sections: set[str] = set()
    current_section = None
    for entry in submitted_payload["responses"]:
        if entry["section"] != current_section:
            current_section = entry["section"]
            rendered_sections.add(current_section)
            newline()
            write(section_titles.get(current_section, current_section), font="Helvetica-Bold", size=11)
        write(f"Q{entry['number']} {entry['control_id']}: {entry['prompt']}", font="Helvetica-Bold", size=9)
        for vk, vv in entry["value"].items():
            write(f"  {vk} ({entry['schema_paths'].get(vk, '?')}): {vv}", size=8)
        if entry["prefilled_values"]:
            write(f"  prefill source: {entry['prefill_source_label'] or entry['prefill_source']}", size=8)
            write(f"  prefilled values: {entry['prefilled_values']}", size=8)
            if any(entry["edited_flags"].values()):
                edits = ", ".join(k for k, v in entry["edited_flags"].items() if v)
                write(f"  customer edited fields: {edits}", size=8)
        write(f"  evidence_state: {entry['evidence_state']}", size=8)
        if entry["evidence_refs"]:
            for ref in entry["evidence_refs"]:
                label = ref.get("filename") or ref.get("link_url") or ref["artifact_uri"]
                sha = (ref.get("sha256") or "")[:16]
                write(f"  evidence: {label} sha256={sha}", size=8)
        else:
            write("  evidence: (none)", size=8)
        newline()
    for sec in self._require_questionnaire_schema().SECTIONS:
        if sec["id"] not in rendered_sections:
            write(section_titles[sec["id"]] + " (no responses captured)", font="Helvetica-Bold", size=11)
            newline()
    write("Evidence summary", font="Helvetica-Bold", size=11)
    counts = self._questionnaire_summary_counts(submitted_payload["responses"])
    for k, v in counts.items():
        write(f"  {k}: {v}", size=9)
    newline()
    write(f"Questionnaire version {submitted_payload['questionnaire_version']} | Generated {submitted_payload['generated_at']} | Parent {submitted_payload['parent_ticket']}", size=7)
    c.showPage()
    c.save()
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Routing helpers (used by portal.PortalHandler).
# ---------------------------------------------------------------------------

def handle_get(service: Any, path_parts: list[str]) -> tuple[int, bytes, str] | None:
    """Return (status, body, ctype) or None if path is not a questionnaire path."""
    # /dp/{dp_id}/questionnaire  -> start draft for an inferred agent_id and serve form
    if len(path_parts) == 3 and path_parts[0] == "dp" and path_parts[2] == "questionnaire":
        dp_id = path_parts[1]
        existing = service.db.execute(
            "SELECT * FROM questionnaire_submissions WHERE dp_id = ? AND status = 'draft' ORDER BY created_at DESC LIMIT 1",
            (dp_id,),
        ).fetchone()
        if existing:
            sub_id = existing["id"]
        else:
            agent_id = f"dp-{dp_id}-default-agent"
            draft = service.start_questionnaire_draft(dp_id, agent_id)
            sub_id = draft["submission_id"]
        body = service.render_questionnaire_form(sub_id)
        return 200, body.encode("utf-8"), "text/html; charset=utf-8"
    # /questionnaire/{submission_id}/form
    if len(path_parts) == 3 and path_parts[0] == "questionnaire" and path_parts[2] == "form":
        body = service.render_questionnaire_form(path_parts[1])
        return 200, body.encode("utf-8"), "text/html; charset=utf-8"
    # /questionnaire/{submission_id}/payload.json
    if len(path_parts) == 3 and path_parts[0] == "questionnaire" and path_parts[2] == "payload.json":
        data = service.get_questionnaire_submission(path_parts[1])
        return 200, json.dumps(data, indent=2).encode("utf-8"), "application/json"
    # /questionnaire/{submission_id}/evidence-bundle.json
    if len(path_parts) == 3 and path_parts[0] == "questionnaire" and path_parts[2] == "evidence-bundle.json":
        data = service.get_questionnaire_bundle(path_parts[1])
        return 200, json.dumps(data, indent=2).encode("utf-8"), "application/json"
    # /questionnaire/{submission_id}.pdf
    if len(path_parts) == 2 and path_parts[0] == "questionnaire" and path_parts[1].endswith(".pdf"):
        sub_id = path_parts[1][:-4]
        data = service.get_questionnaire_pdf_bytes(sub_id)
        return 200, data, "application/pdf"
    return None


def handle_post(service: Any, path_parts: list[str], body: Any) -> tuple[int, bytes, str] | None:
    # /questionnaire/{submission_id}/submit  (accepts form-encoded or JSON)
    if len(path_parts) == 3 and path_parts[0] == "questionnaire" and path_parts[2] == "submit":
        sub_id = path_parts[1]
        if isinstance(body, dict):
            payload = body
        else:
            payload = _form_to_submit_payload(body or {})
        result = service.submit_questionnaire(sub_id, payload)
        return 200, json.dumps(result, indent=2).encode("utf-8"), "application/json"
    # /questionnaire/{submission_id}/evidence  (JSON only)
    if len(path_parts) == 3 and path_parts[0] == "questionnaire" and path_parts[2] == "evidence":
        from portal import PortalError
        if not isinstance(body, dict):
            raise PortalError(415, "json_body_required", "Evidence upload requires application/json.")
        result = service.upload_questionnaire_evidence(path_parts[1], body)
        return 201, json.dumps(result, indent=2).encode("utf-8"), "application/json"
    # /dp/{dp_id}/questionnaire/draft  (start a draft for an explicit agent_id)
    if len(path_parts) == 4 and path_parts[0] == "dp" and path_parts[2] == "questionnaire" and path_parts[3] == "draft":
        from portal import PortalError
        if not isinstance(body, dict):
            raise PortalError(415, "json_body_required", "Draft creation requires application/json.")
        agent_id = body.get("agent_id") or f"dp-{path_parts[1]}-default-agent"
        result = service.start_questionnaire_draft(path_parts[1], agent_id, header_seed=body.get("header"))
        return 201, json.dumps(result, indent=2).encode("utf-8"), "application/json"
    return None


def _form_to_submit_payload(form: dict[str, list[str]]) -> dict[str, Any]:
    """Best-effort decode of `name='header[k]'` / `name='answers[CID][k]'` keys."""
    header: dict[str, Any] = {}
    answers: dict[str, dict[str, Any]] = {}
    for raw_key, values in form.items():
        if not values:
            continue
        v = values[0] if len(values) == 1 else values
        if raw_key.startswith("header[") and raw_key.endswith("]"):
            inner = raw_key[len("header["):-1]
            if inner == "accountability_statement_accepted":
                header[inner] = bool(v)
            else:
                header[inner] = v
        elif raw_key.startswith("answers[") and "][" in raw_key and raw_key.endswith("]"):
            inner = raw_key[len("answers["):-1]
            cid, sep, field = inner.partition("][")
            if not sep:
                continue
            field = field.rstrip("[]")
            answers.setdefault(cid, {})[field] = values if (raw_key.endswith("[]") or len(values) > 1) else v
    return {"header": header, "answers": answers}


# ---------------------------------------------------------------------------
# Attach to PortalService.
# ---------------------------------------------------------------------------

def attach(PortalServiceCls: type) -> None:
    """Bind questionnaire methods onto portal.PortalService."""
    fns = [
        _coerce_answer_value,
        _set_schema_path,
        _require_questionnaire_schema,
        _resolve_questionnaire_prefill,
        start_questionnaire_draft,
        upload_questionnaire_evidence,
        submit_questionnaire,
        _build_submitted_payload,
        _build_scanner_evidence_bundle,
        _questionnaire_summary_counts,
        get_questionnaire_submission,
        get_questionnaire_bundle,
        get_questionnaire_pdf_bytes,
        render_questionnaire_form,
        _render_questionnaire_pdf,
    ]
    for fn in fns:
        setattr(PortalServiceCls, fn.__name__, fn)
