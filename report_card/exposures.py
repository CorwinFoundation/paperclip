"""Customer-safe rendering of versioned exposure timelines."""
from html import escape


def render_exposure_history(history):
    if not isinstance(history, dict):
        return ""
    rows = []
    for entry in history.get("entries", []):
        cells = "".join("<td>" + escape(str(v or "")) + "</td>" for v in (
            str(entry.get("test_id", "")) + "/" + str(entry.get("case_id", "")),
            str(entry.get("state", "")).replace("_", " "), entry.get("first_seen"), entry.get("last_seen"),
            entry.get("comparison_status"),
            "; ".join(str(e.get("artifact_uri", "")) for e in entry.get("evidence", [])),
        ))
        events = "".join("<li>" + escape(" — ".join((str(e.get("observed_at") or ""), str(e.get("scan_id") or ""),
            str(e.get("state") or "").replace("_", " ")))) + "</li>" for e in entry.get("events", []))
        rows.append("<tr>" + cells + '<td><details><summary>Recent observations</summary><ol>'
                    + events + '</ol></details></td></tr>')
    return ('<section class="exposure-history"><div class="section-title">Exposure History</div>'
        '<p>Reported observations across scans. Cleared means not reproduced on a comparable retest. '
        'Missing or incomplete coverage never clears an exposure. Native production effects and source authenticity '
        'require separate verification. Passing test coverage remains in the evidence inventory.</p>'
        '<table style="width:100%;table-layout:fixed;overflow-wrap:anywhere"><thead><tr>'
        '<th>Test/case</th><th>State</th><th>First seen</th><th>Last seen</th><th>Comparison</th><th>Evidence</th><th>History</th>'
        '</tr></thead><tbody>' + "".join(rows) + '</tbody></table>'
        + ('<p>No exposure has been observed in the available history.</p>' if not rows else '') + '</section>')
