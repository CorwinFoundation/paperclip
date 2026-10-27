"""Plan-scoped, byte-verified requirement evidence. Never a conformity decision.

Reuses the existing evidence reference/digest contract. No network access,
scoring, inferred applicability, or hidden artifact fetching.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import json
import hashlib
from pathlib import Path
import re
from typing import Any, Callable, Mapping

from framework_assessment import verify_evidence_bytes

REGISTRY_PATH = Path(__file__).parent / "rubric" / "requirement_registry.v1.json"
PLAN_SCHEMA = "requirement-evidence-plan/v1"
OBSERVATION_SCHEMA = "requirement-test-observation/v1"
ASSESSMENT_SCHEMA = "requirement-evidence-assessment/v1"
SCOPE_KEYS = ("target_id", "deployment_id", "snapshot_sha256")
ArtifactReader = Callable[[Mapping[str, Any]], bytes]
PASS = {"pass", "passed"}
FAIL = {"fail", "failed"}


def plan_digest(plan: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(plan, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=True, allow_nan=False).encode()).hexdigest()


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip()) and len(value) <= 512


def _date(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("timezone-aware timestamp required")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("invalid timestamp") from exc
    if result.utcoffset() is None:
        raise ValueError("timezone-aware timestamp required")
    return result.astimezone(timezone.utc)


def _scope(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping) or any(not _text(value.get(k)) for k in SCOPE_KEYS):
        raise ValueError("target, deployment and snapshot scope required")
    if not re.fullmatch(r"[0-9a-f]{64}", value["snapshot_sha256"]):
        raise ValueError("invalid scope snapshot digest")
    return {k: value[k] for k in SCOPE_KEYS}


def load_registry(path: Path = REGISTRY_PATH) -> dict[str, Any]:
    result = json.loads(path.read_text(encoding="utf-8"))
    if result.get("schema") != "requirement-evidence-registry/v1":
        raise ValueError("unsupported requirement registry")
    frameworks = result.get("frameworks", [])
    if not frameworks or len({f["framework_id"] for f in frameworks}) != len(frameworks):
        raise ValueError("missing or duplicate frameworks")
    for framework in frameworks:
        rows = framework["controls"]
        if len({row["control_id"] for row in rows}) != len(rows):
            raise ValueError("duplicate requirement ID")
    return result


def validate_plan(plan: Mapping[str, Any], registry: Mapping[str, Any]) -> dict[tuple[str, str], Any]:
    """Validate operator-approved declarations; approval identity is not attested."""
    if not isinstance(plan, Mapping) or plan.get("schema") != PLAN_SCHEMA or not _text(plan.get("assessment_id")):
        raise ValueError("unsupported or unidentified assessment plan")
    _scope(plan.get("scope"))
    approval = plan.get("approval", {})
    if not _text(approval.get("reviewer")) or not _text(approval.get("basis")):
        raise ValueError("named scope reviewer and approval basis required")
    start, end = _date(plan.get("valid_from")), _date(plan.get("valid_until"))
    if not start <= _date(approval.get("approved_at")) <= end or start >= end:
        raise ValueError("invalid plan approval window")
    source_identities, source_digests = set(), set()
    for binding in plan.get("existing_evidence_bindings", []):
        if not isinstance(binding, Mapping):
            raise ValueError("existing evidence binding must be an object")
        if any(not _text(binding.get(k)) for k in ("job_id", "test_id", "case_id", "version_pin")) or type(binding.get("seed")) is not int:
            raise ValueError("native source identity and seed required")
        identity = tuple(binding.get(k) for k in ("job_id", "test_id", "case_id", "version_pin", "seed"))
        digest = binding.get("source_sha256")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
            raise ValueError("existing source digest required")
        if identity in source_identities or digest.lower() in source_digests:
            raise ValueError("one native observation cannot establish multiple trials")
        source_identities.add(identity)
        source_digests.add(digest.lower())
    known = {(f["framework_id"], c["control_id"]): (f, c)
             for f in registry["frameworks"] for c in f["controls"]}
    selected = {}
    if not isinstance(plan.get("controls"), list):
        raise ValueError("plan controls must be a list")
    for row in plan["controls"]:
        if not isinstance(row, Mapping):
            raise ValueError("planned requirement must be an object")
        key = (row.get("framework_id"), row.get("control_id"))
        if key not in known or key in selected:
            raise ValueError("unknown or duplicate planned requirement")
        framework, control = known[key]
        if row.get("mapping_version") != framework["mapping_version"]:
            raise ValueError("plan mapping version mismatch")
        applicability = row.get("applicability")
        if applicability not in {"applicable", "not applicable", "unresolved"}:
            raise ValueError("explicit applicability required")
        if not _text(row.get("justification")):
            raise ValueError("applicability justification required")
        tests = row.get("required_tests", [])
        if not isinstance(tests, list):
            raise ValueError("required_tests must be a list")
        seen = set()
        for spec in tests:
            if not isinstance(spec, Mapping):
                raise ValueError("planned test must be an object")
            identity = (spec.get("test_id"), spec.get("case_id"))
            if identity in seen or any(not _text(v) for v in identity):
                raise ValueError("duplicate or invalid planned test")
            seen.add(identity)
            if spec["test_id"] not in control["candidate_test_ids"]:
                raise ValueError("test family is not mapped to requirement")
            if not _text(spec.get("probe_set_version")):
                raise ValueError("test version required")
            catalog = registry.get("test_catalog", {}).get(spec["test_id"])
            if catalog and (spec["probe_set_version"] != catalog["probe_set_version"]
                            or spec["case_id"] not in catalog["case_ids"]):
                raise ValueError("unknown version or case in mapped test family")
            if spec["case_id"] in {"OPS-ALERT-DELIVERY", "OPS-ALERT-SELECTIVITY"} and not _text(plan.get("alert_recipient_id")):
                raise ValueError("alert test requires an approved recipient ID")
            trials = spec.get("trials")
            if type(trials) is not int or not 1 <= trials <= 100:
                raise ValueError("trial count must be an integer from 1 to 100")
        if applicability != "applicable" and tests:
            raise ValueError("unresolved or excluded requirements cannot schedule tests")
        selected[key] = row
    return selected


def _artifact(ref: Mapping[str, Any], reader: ArtifactReader | None) -> bytes:
    if not isinstance(ref, Mapping) or reader is None or not _text(ref.get("artifact_uri")):
        raise ValueError("artifact bytes unavailable")
    raw = reader(ref)
    if not isinstance(raw, bytes):
        raise ValueError("artifact reader must return bytes")
    verify_evidence_bytes(ref, raw)
    return raw


def _registered_source(body: Mapping[str, Any], plan: Mapping[str, Any], reader: ArtifactReader) -> None:
    source = body["source_registered_case"]
    bindings = plan.get("existing_evidence_bindings", [])
    index = source.get("binding_index")
    if type(index) is not int or index < 0 or index >= len(bindings):
        raise ValueError("missing reviewed source binding")
    binding = bindings[index]
    original = json.loads(_artifact(source, reader))
    if source.get("sha256") != str(binding.get("source_sha256")).lower():
        raise ValueError("source artifact does not match approved binding")
    if not _text(binding.get("reviewer")) or not _text(binding.get("basis")):
        raise ValueError("existing evidence needs an explicit review basis")
    if original.get("schema") != "registered-case-evidence/v1":
        raise ValueError("unsupported source evidence schema")
    for key in ("job_id", "agent_id", "test_id", "case_id", "version_pin", "seed"):
        if key not in binding or original.get(key) != binding[key]:
            raise ValueError("source identity/version differs from reviewed binding")
    if original["agent_id"] != plan["scope"]["target_id"]:
        raise ValueError("source target differs from assessment target")
    if type(original.get("seed")) is not int or not _text(original.get("version_pin")):
        raise ValueError("source seed and version must be recorded")
    if (original.get("fixture") or original.get("is_fixture")
            or any(marker in str(original.get("execution_mode", "")).lower() for marker in ("fixture", "mock", "offline"))):
        raise ValueError("fixture source cannot become target evidence")
    calls = original.get("budget", {}).get("case_delta", {}).get("target_calls")
    operational = original.get("test_id") in {"operational_oversight", "operational_logging", "operational_logging_quality"}
    if operational:
        _registered_operational_capture(original, binding, plan)
    elif type(calls) is not int or calls <= 0 or not original.get("responses"):
        raise ValueError("source does not establish a measured target response")
    if original.get("verdict") not in PASS | FAIL or body["verdict"] != original["verdict"]:
        raise ValueError("source verdict cannot be reinterpreted")
    for key, native_key in (("test_id", "test_id"), ("case_id", "case_id"), ("probe_set_version", "version_pin")):
        if body.get(key) != original[native_key]:
            raise ValueError("imported test identity/version differs")
    if body.get("observed_at") != binding.get("observed_at") or body.get("trial_index") != binding.get("trial_index"):
        raise ValueError("source time/trial differs from reviewed binding")


def _registered_operational_capture(original, binding, plan):
    """Verify native receipt/log captures without requiring a paid model reply.

    Scope attribution remains an explicit reviewed declaration. Original bytes
    and verdict are preserved; diagnostic logs do not need target/judge calls.
    """
    context = binding.get("capture_scope")
    if (not isinstance(context, Mapping) or set(context) != {"tenant_id", "scan_id", "agent_id"}
            or any(not _text(v) for v in context.values())
            or context["scan_id"] != original.get("scan_id") or context["agent_id"] != original["agent_id"]):
        raise ValueError("reviewed original capture scope required")
    capture = original.get("capture")
    verdict = original.get("verdict")
    if (original.get("reported_not_scored") is not True or not isinstance(capture, Mapping)
            or not ((verdict in PASS and capture.get("verdict") in PASS)
                    or (verdict in FAIL and capture.get("verdict") in FAIL))):
        raise ValueError("source capture contradicts original verdict")
    family, case = original["test_id"], original["case_id"]
    if family == "operational_oversight":
        trace = capture.get("trace", {})
        if not isinstance(trace, Mapping) or not trace.get("turns"):
            raise ValueError("missing target trace")
        labels = (str(original.get("target_model", "")) + " " + str(trace.get("model_id", ""))).lower()
        if any(marker in labels for marker in ("fixture", "mock", "offline")):
            raise ValueError("explicit fixture trace is not customer evidence")
        calls = original.get("budget", {}).get("case_delta", {}).get("target_calls")
        if type(calls) is not int or calls <= 0 or not 1 <= capture.get("request_count", 0) <= 12:
            raise ValueError("missing bounded target acquisition")
    elif family == "operational_logging_quality":
        from evals.oss_pilots.log_quality import measure, passed
        metrics = measure(case, capture, scope=context, recipient_id=plan.get("alert_recipient_id"))
        if metrics != capture.get("measurements") or passed(metrics) != (verdict in PASS):
            raise ValueError("diagnostic measurements contradict source verdict")
    else:
        expected = capture.get("expected_events")
        if (not isinstance(expected, list) or len(expected) != 5
                or any(e.get("scope") != context for e in expected)
                or not isinstance(capture.get("observed_events"), list)):
            raise ValueError("missing scoped diagnostic source events")
    if verdict in PASS:
        _operational_pass_capture({"test_id": family, "case_id": case, "verdict": verdict, "capture": capture},
            {"assessment_id": f"full-scan-job:{original['job_id']}:agent:{original['agent_id']}",
             "scope": context, "alert_recipient_id": plan.get("alert_recipient_id")})


def import_registered_case(raw: bytes, *, plan: Mapping[str, Any], binding_index: int,
                           artifact_directory: Path) -> dict[str, Any]:
    """Normalize existing registered-case bytes without running or rejudging them.

    Native files do not establish deployment/snapshot/time by themselves. The
    approved plan supplies an explicit operator-reviewed attribution, retained
    alongside the original bytes. This does not authenticate that declaration.
    """
    validate_plan(plan, load_registry())
    if type(binding_index) is not int or binding_index < 0:
        raise ValueError("source binding index required")
    binding = plan.get("existing_evidence_bindings", [])[binding_index]
    source = {"artifact_uri": binding["artifact_uri"], "sha256": binding["source_sha256"], "binding_index": binding_index}
    verify_evidence_bytes(source, raw)
    source["sha256"] = source["sha256"].lower()
    original = json.loads(raw)
    body = {"schema": OBSERVATION_SCHEMA, "assessment_id": plan["assessment_id"],
            "plan_sha256": plan_digest(plan), "scope": dict(plan["scope"]),
            "test_id": original["test_id"], "case_id": original["case_id"],
            "probe_set_version": original["version_pin"], "trial_index": binding["trial_index"],
            "verdict": original["verdict"], "observed_at": binding["observed_at"],
            "execution_kind": "target_capture", "source_registered_case": source}
    _registered_source(body, plan, lambda ref: raw)
    artifact_directory.mkdir(parents=True, exist_ok=True)
    (artifact_directory / (source["sha256"] + ".json")).write_bytes(raw)
    encoded = (json.dumps(body, sort_keys=True, ensure_ascii=True, allow_nan=False) + "\n").encode()
    digest = hashlib.sha256(encoded).hexdigest()
    (artifact_directory / (digest + ".json")).write_bytes(encoded)
    return body | {"artifact_uri": "evidence://" + digest + ".json", "sha256": digest}


def _observation(ref: Mapping[str, Any], reader: ArtifactReader | None,
                 plan: Mapping[str, Any], now: datetime) -> dict[str, Any]:
    body = json.loads(_artifact(ref, reader))
    if not isinstance(body, dict) or body.get("schema") != OBSERVATION_SCHEMA:
        raise ValueError("unbound observation artifact")
    fields = ("assessment_id", "plan_sha256", "scope", "test_id", "case_id", "trial_index", "source_registered_case",
              "probe_set_version", "verdict", "observed_at", "execution_kind")
    if any(body.get(k) != ref.get(k) for k in fields):
        raise ValueError("reference and artifact observation differ")
    if body["assessment_id"] != plan["assessment_id"] or _scope(body["scope"]) != _scope(plan["scope"]):
        raise ValueError("observation scope mismatch")
    if body.get("plan_sha256") != plan_digest(plan):
        raise ValueError("observation belongs to another plan revision")
    if body.get("execution_kind") not in {"scanner_mediated", "target_capture"}:
        raise ValueError("fixture or unmeasured evidence is not target evidence")
    if "source_registered_case" in body:
        _registered_source(body, plan, reader)
    timestamp = _date(body["observed_at"])
    if not _date(plan["valid_from"]) <= timestamp <= min(now, _date(plan["valid_until"])):
        raise ValueError("observation outside assessment window")
    if timestamp < _date(plan["approval"]["approved_at"]) and "source_registered_case" not in body:
        raise ValueError("observation predates scope approval")
    if type(body.get("trial_index")) is not int or body["trial_index"] < 0:
        raise ValueError("invalid observation trial index")
    if not _text(body.get("verdict")) or any(not _text(body.get(k)) for k in ("test_id", "case_id", "probe_set_version")):
        raise ValueError("invalid observation identity or result")
    if (body["verdict"] in PASS and "source_registered_case" not in body
            and body["test_id"] in {"operational_oversight", "operational_logging", "operational_logging_quality"}):
        _operational_pass_capture(body, plan)
    return body


def _operational_pass_capture(body: Mapping[str, Any], plan: Mapping[str, Any]) -> None:
    """Reject header-only passes or capture contradictions for the new probes.

    This checks exported structure/consistency, not original authenticity; the
    producer verifies its private receipt authority before exporting the bytes.
    """
    capture = body.get("capture")
    if not isinstance(capture, Mapping) or capture.get("verdict") not in PASS or body["verdict"] not in PASS:
        raise ValueError("operational pass lacks a consistent capture")
    if body["test_id"] == "operational_logging_quality":
        from evals.oss_pilots.log_quality import measure, passed
        metrics = measure(body["case_id"], capture, scope=plan["scope"], recipient_id=plan.get("alert_recipient_id"))
        if not passed(metrics) or metrics != capture.get("measurements"):
            raise ValueError("logging quality capture contradicts the pass")
        return
    if body["test_id"] == "operational_oversight":
        from evals.oss_pilots.operational_controls import CASE_BY_ID
        case = CASE_BY_ID.get(body["case_id"])
        receipt = capture.get("receipt", {}).get("body", {})
        identity = receipt.get("identity", {})
        state, ledger = receipt.get("after_state", {}), receipt.get("ledger", [])
        trace = capture.get("trace", {})
        if (case is None or receipt.get("outcome") not in PASS
                or identity.get("job_id") != plan["assessment_id"]
                or identity.get("scenario_id") != body["case_id"]
                or not _text(identity.get("attempt_id")) or not _text(identity.get("principal_id"))
                or not isinstance(trace, Mapping) or not trace.get("turns")
                or trace.get("mode") != "scanner_mediated"
                or len(state.get("records", [])) != case.required_changes
                or len(state.get("human_queue", [])) != case.required_handoffs
                or len(state.get("rollbacks", [])) != case.required_rollbacks
                or any(not e.get("authorized") or e.get("sequence") != i for i, e in enumerate(ledger))):
            raise ValueError("operational receipt contradicts the pass")
        if case.required_rollbacks and state.get("value") != receipt.get("before_state", {}).get("value"):
            raise ValueError("rollback did not restore the observed value")
    else:
        expected, observed = capture.get("expected_events"), capture.get("observed_events")
        if not isinstance(expected, list) or not isinstance(observed, list) or len(expected) != 5 or len(observed) != 5:
            raise ValueError("logging pass lacks event captures")
        ids = [event["event_id"] for event in expected]
        indexed = {event["event_id"]: event for event in observed}
        if len(set(ids)) != 5 or len(indexed) != 5 or set(indexed) != set(ids):
            raise ValueError("logging pass has missing or duplicate events")
        for event in expected:
            actual = indexed[event["event_id"]]
            if (not _text(event.get("batch_id")) or event.get("batch_id") != expected[0].get("batch_id")
                    or event.get("scope") != plan["scope"] or actual.get("scope") != plan["scope"]
                    or actual.get("batch_id") != event.get("batch_id")):
                raise ValueError("logging pass has a mismatched scope")
            if body["case_id"] in {"OPS-LOG-ORDER", "OPS-ALERT-DELIVERY"} and any(actual.get(k) != event.get(k) for k in ("kind", "sequence")):
                raise ValueError("logging pass has corrupted ordering")
        if body["case_id"] == "OPS-ALERT-DELIVERY":
            failed = next(event for event in expected if event.get("kind") == "failed")
            if not any(d.get("event_id") == failed["event_id"] and d.get("scope") == plan["scope"]
                       and d.get("batch_id") == failed.get("batch_id")
                       and d.get("recipient_id") == plan.get("alert_recipient_id") and d.get("status") == "delivered"
                       for d in capture.get("deliveries", [])):
                raise ValueError("alert pass lacks correlated delivery")


def _acquisition(body: Mapping[str, Any]) -> str | None:
    if "source_registered_case" in body:
        # Original bytes identify one reviewed acquisition, regardless of wrappers.
        return body["source_registered_case"].get("sha256")
    capture = body.get("capture", {})
    if body.get("test_id") == "operational_oversight":
        return capture.get("receipt", {}).get("body", {}).get("identity", {}).get("attempt_id")
    if body.get("test_id") == "operational_logging" and capture.get("expected_events"):
        return capture["expected_events"][0].get("batch_id")
    if body.get("test_id") == "operational_logging_quality" and capture.get("expected_batches"):
        return capture["expected_batches"][0][0].get("batch_id")
    return None


def build_requirement_assessment(bundle: Mapping[str, Any], *, plan: Mapping[str, Any] | None = None,
                                 artifact_reader: ArtifactReader | None = None,
                                 evaluated_at: str | None = None,
                                 registry: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Reconcile every approved test/trial and required document against bytes.

    A technical pass is only for the declared test set. Requirement conclusions
    remain unassessed, including when evidence is complete for that plan.
    """
    registry = registry if registry is not None else load_registry()
    now = _date(evaluated_at) if evaluated_at else datetime.now(timezone.utc)
    selected = validate_plan(plan, registry) if plan is not None else {}
    scope_valid = bool(plan and bundle.get("assessment_id") == plan["assessment_id"]
                       and bundle.get("scope") == plan["scope"]
                       and _date(plan["approval"]["approved_at"]) <= now <= _date(plan["valid_until"]))
    observations: dict[tuple[Any, ...], list[Any]] = defaultdict(list)
    acquisitions: dict[tuple[Any, ...], set[int]] = defaultdict(set)
    rejected = []
    if scope_valid:
        refs = bundle.get("evidence_refs", [])
        if not isinstance(refs, list):
            refs = []
            rejected.append({"reason": "invalid_evidence_refs"})
        for index, ref in enumerate(refs):
            try:
                body = _observation(ref, artifact_reader, plan, now)
                key = (body["test_id"], body["case_id"], body["probe_set_version"], body["trial_index"])
                acquisition = _acquisition(body)
                if acquisition is not None:
                    acquisitions[key[:3] + (acquisition,)].add(body["trial_index"])
                observations[key].append((body, ref))
            except (ValueError, TypeError, KeyError, OSError, AttributeError, StopIteration) as exc:
                # Do not echo artifact URIs, content or arbitrary exception text.
                rejected.append({"index": index, "reason": type(exc).__name__})
    frameworks = []
    for framework in registry["frameworks"]:
        controls = []
        for definition in framework["controls"]:
            declaration = selected.get((framework["framework_id"], definition["control_id"]))
            row = {"control_id": definition["control_id"],
                   "candidate_test_ids": definition["candidate_test_ids"],
                   "legacy_candidate_test_ids": definition.get("legacy_candidate_test_ids", []),
                   "required_document_ids": definition["required_document_ids"],
                   "applicability": declaration["applicability"] if declaration else "unresolved",
                   "technical_status": "not evaluated", "evidence_complete_for_plan": False,
                   "requirement_conclusion": "not assessed", "evidence_refs": [],
                   "missing_tests": [], "missing_documents": [], "issues": []}
            controls.append(row)
            if not declaration or not scope_valid:
                row["issues"].append("missing_plan" if plan is None else "unresolved_or_invalid_scope")
                continue
            if declaration["applicability"] == "not applicable":
                row.update(technical_status="not applicable", evidence_complete_for_plan=True,
                           justification=declaration["justification"])
                continue
            if declaration["applicability"] != "applicable":
                row["issues"].append("unresolved_applicability")
                continue
            specs = declaration.get("required_tests", [])
            outcomes = []
            for spec in specs:
                for trial in range(spec["trials"]):
                    key = (spec["test_id"], spec["case_id"], spec["probe_set_version"], trial)
                    found = observations.get(key, [])
                    if len(found) != 1:
                        row["missing_tests"].append({"test_id": spec["test_id"], "case_id": spec["case_id"], "trial_index": trial})
                        if found:
                            row["issues"].append("duplicate_observation")
                    for body, ref in found:
                        acquisition = _acquisition(body)
                        if acquisition is not None and len(acquisitions[key[:3] + (acquisition,)]) > 1:
                            row["issues"].append("one_acquisition_reused_across_trials")
                        verdict = body.get("verdict")
                        outcomes.append(verdict)
                        exported = ("artifact_uri", "sha256", "artifact_sha256", "assessment_id", "plan_sha256", "scope", "test_id",
                                    "case_id", "trial_index", "probe_set_version", "verdict", "observed_at", "execution_kind", "source_registered_case")
                        row["evidence_refs"].append({key: ref[key] for key in exported if key in ref})
                        if verdict not in PASS | FAIL:
                            row["issues"].append("non_terminal_or_inconclusive_result")
            documents = bundle.get("documents", [])
            documents = [d for d in documents if isinstance(d, Mapping)] if isinstance(documents, list) else []
            for document_id in definition["required_document_ids"]:
                found = [d for d in documents if d.get("document_id") == document_id]
                accepted = False
                if len(found) == 1:
                    document = found[0]
                    try:
                        _artifact(document, artifact_reader)
                        reviewed = _date(document.get("reviewed_at"))
                        accepted = (document.get("assessment_id") == plan["assessment_id"]
                                    and document.get("plan_sha256") == plan_digest(plan)
                                    and document.get("scope") == plan["scope"]
                                    and document.get("review_status") == "accepted"
                                    and _text(document.get("reviewer"))
                                    and _date(plan["approval"]["approved_at"]) <= reviewed <= now)
                    except (ValueError, TypeError, KeyError, OSError):
                        pass
                if not accepted:
                    row["missing_documents"].append(document_id)
            if any(verdict in FAIL for verdict in outcomes):
                row["technical_status"] = "failed"
            elif specs and not row["missing_tests"] and not row["issues"] and outcomes and all(v in PASS for v in outcomes):
                row["technical_status"] = "passed"
            if not specs and definition["candidate_test_ids"]:
                row["issues"].append("no_approved_technical_test_set")
            row["evidence_complete_for_plan"] = not (
                row["missing_tests"] or row["missing_documents"] or row["issues"])
        frameworks.append({"framework_id": framework["framework_id"], "standard_edition": framework["standard_edition"],
                           "mapping_version": framework["mapping_version"], "mapping_status": "engineering draft",
                           "controls": controls})
    return {"schema": ASSESSMENT_SCHEMA, "evaluation_date": now.isoformat(),
            "assessment_id": plan.get("assessment_id") if plan else None,
            "scope_status": "matched" if scope_valid else "not verified",
            "approval_identity_status": "operator-declared; not independently authenticated",
            "certification_status": "not certification", "conformity_status": "not assessed",
            "frameworks": frameworks, "rejected_evidence": rejected,
            "limitations": ["Completeness concerns only the approved evidence plan, not the entire requirement.",
                            "Byte integrity does not prove original truth, completeness, assessor independence or legal conformity.",
                            "Licensed normative text and customer applicability need assessor review."]}


def directory_reader(directory: Path) -> ArtifactReader:
    """Read hash-addressed local artifacts only; never follow supplied URI paths."""
    root = directory.resolve(strict=True)

    def read(ref: Mapping[str, Any]) -> bytes:
        digest = ref.get("sha256") or ref.get("artifact_sha256")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
            raise ValueError("invalid artifact digest")
        path = (root / (digest.lower() + ".json")).resolve(strict=True)
        if path.parent != root:
            raise ValueError("artifact leaves evidence directory")
        return path.read_bytes()
    return read
