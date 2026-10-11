from pathlib import Path


SOURCE = (Path(__file__).resolve().parents[1] / "backbond" / "operations" /
          "site-quickfix" / "release-2026-10-07-starter-v2" / "site" /
          "scan" / "report.js").read_text()


def test_combined_report_has_three_truthful_pack_slots_and_no_score_label():
    for value in ("starter-100@3", "starter-voice-50@1", "codex-targeted-20@1", "not_run"):
        assert value in SOURCE
    assert "Starter scan + extensions" in SOURCE
    assert "uniqueCombinedTests" in SOURCE and "seen.has(id)" in SOURCE
    assert "Scoring is deferred; no Full Scan score or BackBond Score is published." in SOURCE
    assert "derived overlay" in SOURCE


def test_combined_report_keeps_pi29_first_and_clears_loading_status():
    assert "=== 'PI-29'" in SOURCE
    assert "$('report-error').replaceChildren()" in SOURCE
    assert "['Pack','Family','Ran','Passed','Needs attention','Flaky','Could not run']" in SOURCE


def test_standalone_voice_report_keeps_the_production_roster_and_no_score_contract():
    for value in (
        "['VDN',10,'in_domain_normal']",
        "['VPF',5,'product_fabrication']",
        "['VASR',5,'asr_shaped_input']",
        "['VML',15,'multilingual_guardrails']",
        "['VSS',5,'screen_share_readback']",
        "['VSP',5,'summary_poisoning']",
        "['VGD',5,'goals_data_collection']",
        "['VHD',5,'human_disclosure']",
        "['VAA',5,'abuse_toward_agent']",
    ):
        assert value in SOURCE
    assert "ids.size !== data.tests.length" in SOURCE
    assert "data.tests.length > 60" in SOURCE
    assert "expected.get(item.id) !== item.family" in SOURCE
    assert "data.outcome !== 'incomplete' && ids.size !== 60" in SOURCE
    assert "This scan did not finish: ' + tests.length + ' of 60 tests have a result." in SOURCE
    assert "60 voice-related text/browser tests." in SOURCE
    assert "No score is published for this pack." in SOURCE
