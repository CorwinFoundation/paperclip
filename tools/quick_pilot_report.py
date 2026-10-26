"""Render an existing private pilot run to Markdown; no target/judge calls."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from evals.internal_agent_knowledge import CASES, CASE_BY_ID, FIXTURE_SHA256, PROFILE_ID, PROFILE_SHA256, digest, summarize
from tools.internal_agent_pilot import SCHEMA, read_json


def cell(value):
    # Markdown text from assessment artifacts is untrusted. Exclude HTML,
    # clickable links, table breaks and newlines from report cells.
    import html
    text = " ".join(str(value).split())
    return html.escape(text).replace("\\", "\\\\").replace("|", "\\|").replace("[", "\\[").replace("]", "\\]").replace("`", "\\`").replace("*", "\\*").replace("_", "\\_")


def render(run):
    if (run.get("schema") != SCHEMA or run.get("profile_id") != PROFILE_ID
            or run.get("profile_sha256") != PROFILE_SHA256 or run.get("fixture_sha256") != FIXTURE_SHA256
            or run.get("run_sha256") != digest({k: v for k, v in run.items() if k != "run_sha256"})
            or run.get("execution_kind") not in {"scripted_fixture", "amazon_quick"}):
        raise ValueError("matching_unchanged_run_required")
    results = run.get("results", [])
    if len(results) > len(CASES) or len({row["case_id"] for row in results}) != len(results):
        raise ValueError("unique_known_results_required")
    for row in results:
        case = CASE_BY_ID[row["case_id"]]
        if (row.get("verdict") not in {"pass", "fail", "inconclusive", "could_not_run"}
                or row.get("scenario_id") != case.scenario_id or row.get("role") != case.role
                or row.get("variant") != case.variant or row.get("setup_sha256") != case.setup_sha256
                or row.get("score_contribution") is not False):
            raise ValueError("profile_bound_report_only_results_required")
    summary = {**summarize(results), "execution_kind": run["execution_kind"]}
    if "summary" in run and run["summary"] != summary:
        raise ValueError("summary_disagrees_with_results")
    fixture = run["execution_kind"] == "scripted_fixture"
    kind = "SCRIPTED FIXTURE - no customer performance evidence" if fixture else "LIVE AWS QUICK - operator-attested setup"
    lines = ["# Amazon Quick pilot assessment", "", kind, "",
             f"Profile: {PROFILE_ID} | BackBond Score: N/A", "",
             f"Assessment status: {summary['status']}. This is a scoped report-only assessment; no underwriting or coverage decision.", "",
             f"Captured target submission attempts: {run.get('target_submission_attempts', 0)}.",
             f"Evaluator: {cell(run.get('evaluator', {}).get('kind', 'not recorded'))}.",
             f"Source run SHA-256: `{run['run_sha256']}`.", "",
             "## Results", "", "| Outcome | Cases |", "| --- | ---: |"]
    lines += [f"| {outcome} | {summary['counts'].get(outcome, 0)} |"
              for outcome in ("pass", "fail", "inconclusive", "could_not_run")]
    lines += ["", f"Matched-control utility: {summary['authorized_utility']['passed']}/{summary['authorized_utility']['expected']} passed.",
              "", "| Case | Identity | Outcome | Evidence / next action |", "| --- | --- | --- | --- |"]
    by_id = {r["case_id"]: r for r in results}
    for case in CASES:
        row = by_id.get(case.case_id, {"verdict": "could_not_run", "reason": "not_graded_or_not_attempted"})
        lines.append(f"| {case.case_id} | {case.role} | {cell(row['verdict'])} | {cell(row['reason'])} |")
    lines += ["", "## Interpretation and limits", "",
              "Failures identify observed behavior under the recorded setup. Missing evidence remains inconclusive or could-not-run; refusing all requests cannot pass the utility controls.", "",
              "The source setup, ACL/RLS matrix and retrieval-exposure receipts are operator attestations. Hashes detect edits but are not AWS signatures. Citation metadata does not establish native tool execution or business-action outcomes.", "",
              "The captured artifact contains private answers. This report intentionally excludes transcripts. Review the private run and source evidence before sharing a finding or prescribing a change.", "",
              "Remediation and retest: review every failed case with the customer, identify the source/permission/agent configuration change, and authorize only the affected cases plus matched controls in a new bounded run. Preserve the old run and ledger; never replay an uncertain submission.", "",
              "Out of scope: Identity Center sessions requiring trusted identity propagation, native actions/Automate, revocation lifecycle, persistent memory, Desktop and Office extensions.", ""]
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        report = render(read_json(args.run, limit=8 * 1024 * 1024))
        with args.output.open("x", encoding="utf-8") as stream:
            stream.write(report)
        print(json.dumps({"status": "report_rendered", "target_calls": 0, "judge_calls": 0, "backbond_score": "N/A"}))
        return 0
    except (OSError, ValueError, KeyError, TypeError):
        print(json.dumps({"status": "incomplete", "error": "unchanged_run_and_new_report_path_required"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
