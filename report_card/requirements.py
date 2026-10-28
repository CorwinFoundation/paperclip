"""Customer-safe HTML for the standard requirement evidence crosswalk."""
from html import escape

from standard_requirement_report import build_standard_requirement_report


def render_requirements(score):
    report = score.get("requirements_evidence") or build_standard_requirement_report(score)
    sections = []
    names = {"eu-ai-act": "EU AI Act", "iso-iec-42001": "ISO/IEC 42001"}
    for framework in report["frameworks"]:
        rows = []
        for control in framework["controls"]:
            observations = control.get("scan_observations", [])
            tests = "; ".join(
                f'{o["test_id"]}/{o["case_id"]}: {o["reported_verdict"]}'
                + (f' ({o["artifact_uri"]})' if o.get("artifact_uri") else "") for o in observations
            ) or "No scan observations"
            gaps = "; ".join(control.get("unobserved_test_ids", control["candidate_test_ids"])) or "None in this draft mapping"
            case_gaps = control.get("catalog_case_gaps", [])
            legacy = control.get("unresolved_mapping_concepts", [])
            if legacy:
                gaps += "; unresolved legacy mapping concepts: " + ", ".join(legacy)
            if case_gaps:
                gaps += "; unobserved catalog cases: " + ", ".join(f'{g["test_id"]}/{g["case_id"]}' for g in case_gaps)
            documents = "; ".join(control["required_document_ids"]) or "Assessor review required"
            rows.append(
                "<tr>" + "".join(f"<td>{escape(str(v))}</td>" for v in (
                    control["control_id"], tests, gaps, documents, control["requirement_conclusion"],
                )) + "</tr>"
            )
        name = names.get(framework["framework_id"], framework["framework_id"])
        observed_count = sum(bool(c.get("scan_observations")) for c in framework["controls"])
        sections.append(
            f'<details><summary>{escape(name)} — {escape(framework["mapping_version"])}; '
            f'{observed_count} of {len(framework["controls"])} controls with scan observations</summary>'
            '<table style="width:100%; table-layout:fixed; overflow-wrap:anywhere"><thead><tr><th>Requirement</th><th>Reported scan observations</th>'
            '<th>Unobserved test families</th><th>Documents requiring review</th>'
            '<th>Conclusion</th></tr></thead><tbody>' + "".join(rows) + '</tbody></table></details>'
        )
    return (
        '<section class="requirements-evidence"><div class="section-title">Requirement Evidence Mapping</div>'
        '<p>Engineering draft. Reported scan verdicts are metadata; evidence bytes and requirement '
        'applicability have not been verified by this crosswalk. Certification and conformity are not assessed. '
        'Operational tool observations concern the synthetic scanner workspace. Record checks reconcile supplied '
        'material; source authenticity, completeness and substantive adequacy require review. Unobserved tests may be '
        'outside the selected pack or unsupported by the configured interface. Licensed standard text, '
        'organizational documents and assessor review are still required.</p>' + _inventory(report) + "".join(sections) + '</section>'
    )


def _inventory(report):
    inventory = report.get("evidence_inventory")
    if not isinstance(inventory, dict):
        return ""
    rows = []
    for item in inventory.get("artifacts", []):
        metrics = item.get("diagnostic_measurements", {})
        rows.append("<tr>" + "".join(f"<td>{escape(str(v))}</td>" for v in (
            f'{item["test_id"]}/{item["case_id"]}', item.get("case_version", "Version not reported"),
            item["reported_verdict"], item.get("method", "See the versioned source test and artifact"),
            item.get("observation_scope", "See the versioned method and source evidence"),
            "; ".join(f"{k}: {v}" for k, v in sorted(metrics.items())),
            item.get("artifact_uri", "Artifact reference missing"), item.get("sha256", "Digest not reported"),
        )) + "</tr>")
    documents = inventory.get("document_review_register", [])
    return ('<details><summary>Evidence inventory — ' + str(len(rows)) + ' evidence observations; '
        + str(len(documents)) + ' document types needing applicability/review</summary>'
        '<p>Fingerprints and measurements below are reported metadata. This report has not verified original bytes. '
        'An approved scope and the operator evidence-pack export are required for assessor review.</p>'
        '<table style="width:100%; table-layout:fixed; overflow-wrap:anywhere"><thead><tr>'
        '<th>Case</th><th>Version</th><th>Outcome</th><th>Method</th><th>Observation scope</th><th>Diagnostic measurements</th>'
        '<th>Artifact</th><th>Reported SHA-256</th></tr></thead><tbody>' + "".join(rows) + '</tbody></table></details>')
