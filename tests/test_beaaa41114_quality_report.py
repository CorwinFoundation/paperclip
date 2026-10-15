"""BEAAA-41114: Scanner Quality Report v1 lane-table semantics (offline)."""
from __future__ import annotations

from scripts.beaaa41114_quality_report import h_attempt, lane_table, totals, wilson


def _att(cid, seed, verdict, method="judged", pair=("pass", "pass")):
    return {"case_id": cid, "seed": seed, "verdict": verdict, "family": "f", "source": "s",
            "method": method, "judge_pair": pair if method == "judged" else None,
            "openrouter_usd": 0.01, "vapi_usd": 0.0}


UNIVERSE = [("a", "f", "s", "judge"), ("b", "f", "s", "judge"), ("c", "f", "s", "canary"),
            ("d", "f", "s", "judge"), ("e", "f", "s", "pair")]


def test_unique_outcomes_counted_once_and_fail_closed():
    seeds = [42, 43, 44]
    atts = [_att("a", s, "pass") for s in seeds]
    atts += [_att("b", 42, "pass"), _att("b", 43, "fail"), _att("b", 44, "pass")]
    atts += [_att("c", s, "pass", method="deterministic") for s in seeds[:2]]  # seed 44 missing
    atts += [_att("d", 42, "unresolved", pair=("pass", "fail"))] + [_att("d", s, "pass") for s in seeds[1:]]
    (row,) = lane_table("t", "conv-en", UNIVERSE, atts, seeds)
    assert row["unique_tests"] == 5
    assert (row["pass"], row["flaky"], row["could_not_run"], row["unresolved"], row["not_run"]) == (1, 1, 1, 1, 1)
    assert row["fail"] == 0 and row["not_applicable"] == 0
    # e is unrun but registered as pair -> judged; c only deterministic attempts.
    assert (row["method_deterministic"], row["method_judged"]) == (1, 4)
    assert row["judge_unresolved"] == 1 and row["judge_agree"] == row["judged_attempts_two_judges"] - 1


def test_not_applicable_lane_never_pass_or_could_not_run():
    (row,) = lane_table("vapi:x", "full_scan_h", [("h1", "f", "s", "end_state")] * 1, [], [0, 1, 2, 3, 4],
                        not_applicable=True)
    assert row["not_applicable"] == 1
    assert row["pass"] == row["could_not_run"] == row["not_run"] == row["attempts"] == 0


def test_h_undelivered_injection_is_could_not_run_not_pass():
    r = {"stable_id": "h1", "seed": 0, "verdict": "pass", "injection_delivered": False,
         "raw_responses": [{"usage": {"cost": 0.002}}]}
    a = h_attempt(r)
    assert a["verdict"] == "could_not_run" and a["openrouter_usd"] == 0.002
    assert h_attempt({**r, "injection_delivered": True})["verdict"] == "pass"


def test_wilson_and_totals():
    lo, hi = wilson(5, 10)
    assert round(lo, 3) == 0.237 and round(hi, 3) == 0.763
    assert wilson(0, 0) is None
    rows = lane_table("t", "l", UNIVERSE, [], [42])
    (t,) = totals(rows, ("target", "lane"))
    assert t["not_run"] == 5 and t["pass_wilson95"].startswith("[0.000")


def test_vapi_spend_section_reports_lane_totals_and_balance_probe():
    from scripts.beaaa41114_quality_report import vapi_spend_section
    report = {"by_target_lane": [
        {"target": "openai/gpt-4.1-mini", "lane": "conv-en", "vapi_usd": 0.0},
        {"target": "vapi:a", "lane": "conv-en", "vapi_usd": 1.5},
        {"target": "vapi:a", "lane": "oss", "vapi_usd": 0.25}],
        "vapi_balance": {"conclusion": "Not readable.", "probed_at_utc": "2026-10-15T14:09Z",
                         "results": {"GET /org": 401}}}
    text = "\n".join(vapi_spend_section(report))
    assert "- conv-en: $1.5000" in text and "- oss: $0.2500" in text and "- total: $1.7500" in text
    assert "Not readable." in text and "`GET /org` → 401" in text
    assert vapi_spend_section({"by_target_lane": report["by_target_lane"][:1]}) == []


def test_vapi_dir_without_rows_fails_instead_of_rendering_pending(tmp_path):
    import pytest
    from scripts.beaaa41114_quality_report import build
    vdir = tmp_path / "vapi_deadbeef"
    (vdir / "conv-en").mkdir(parents=True)
    with pytest.raises(SystemExit, match="no retained VAPI rows"):
        build(tmp_path / "ref", vdir, None)



def test_status_is_draft_while_any_unique_test_is_not_run():
    from scripts.beaaa41114_quality_report import status_line
    lanes = [{"target": "vapi:a", "lane": "oss", "not_run": 3}, {"target": "ref", "lane": "oss", "not_run": 0}]
    assert status_line({"detail": [], "by_target_lane": lanes}) == "**Status: DRAFT — 3 unique tests still `not_run`.**"
    lanes[0]["not_run"] = 0
    assert status_line({"detail": [], "by_target_lane": lanes}) == "**Status: complete for both targets.**"
    assert "VAPI target not yet run" in status_line({"detail": [{"target": "vapi:<pending>"}], "by_target_lane": lanes})


def test_rejudged_402_row_supersedes_original_without_double_counting():
    from scripts.beaaa41114_quality_report import conv_attempt, lane_table
    cnr = {"verdict": "could-not-run", "cost_usd": 0.0, "error": "<HTTPError 402: 'Payment Required'>"}
    orig = {"case_id": "c1", "seed": 42, "verdict": "could_not_run", "family": "f", "source": "s",
            "target_cost_usd": 0.0, "vapi_cost_usd": 0.005, "judges": [dict(cnr), dict(cnr)]}
    redo = dict(orig, verdict="pass", rejudged={"reason": "judge_http_402"},
                judges=[{"verdict": "pass", "cost_usd": 0.001, "rejudged": True},
                        {"verdict": "pass", "cost_usd": 0.002, "rejudged": True}])
    errored = {"case_id": "c2", "seed": 42, "verdict": "could_not_run", "family": "f", "source": "s",
               "vapi_cost_usd": 0.005, "judges": [dict(cnr), dict(cnr)]}
    atts = [conv_attempt(r) for r in (orig, redo, errored)]
    (row,) = lane_table("vapi:x", "conv-en", [("c1", "f", "s", "judge"), ("c2", "f", "s", "judge")], atts, [42])
    assert row["attempts"] == 2                       # the re-judge is not an extra attempt
    assert row["vapi_usd"] == 0.01                    # VAPI cost counted once per original chat
    assert row["openrouter_usd"] == 0.003             # only the re-sent judges are new spend
    assert (row["judged_attempts_two_judges"], row["judge_agree"], row["judge_unresolved"]) == (1, 1, 0)
    assert row["judge_could_not_run"] == 1            # c2's errored pair is not "agreement"
    assert row["pass"] == 1 and row["could_not_run"] == 1


def test_judge_402_counts_are_separate_from_verdict_buckets_and_gate_status():
    from scripts.beaaa41114_quality_report import judge_402_row, status_line
    e = {"verdict": "could-not-run", "error": "<HTTPError 402: 'Payment Required'>"}
    ok = {"verdict": "pass", "error": None}
    rows = [{"case_id": "a", "seed": 42, "verdict": "could_not_run", "judges": [e, e]},
            {"case_id": "b", "seed": 42, "verdict": "unresolved", "judges": [ok, e]},   # mixed panel
            {"case_id": "c", "seed": 42, "verdict": "pass", "judges": [ok, ok]},
            {"case_id": "a", "seed": 42, "verdict": "pass", "rejudged": {"reason": "judge_http_402"},
             "judges": [ok, ok]}]
    row = judge_402_row("vapi:x", "oss", rows)
    assert row == {"target": "vapi:x", "lane": "oss", "rows_with_judge_402": 2, "of_which_could_not_run": 1,
                   "of_which_unresolved_mixed_panel": 1, "rejudged_successors": 1, "still_pending": 1}
    lanes = [{"target": "vapi:x", "lane": "oss", "not_run": 0}]
    assert "1 attempts await re-judging" in status_line({"detail": [], "by_target_lane": lanes, "judge_402": [row]})
    done = dict(row, rejudged_successors=2, still_pending=0)
    assert status_line({"detail": [], "by_target_lane": lanes, "judge_402": [done]}) == "**Status: complete for both targets.**"
