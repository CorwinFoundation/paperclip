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
        'Operational tool observations concern the synthetic scanner workspace. Unobserved tests may be '
        'outside the selected pack or unsupported by the configured interface. Licensed standard text, '
        'organizational documents and assessor review are still required.</p>' + "".join(sections) + '</section>'
    )
