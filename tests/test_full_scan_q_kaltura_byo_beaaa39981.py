"""Kaltura BYO-policy generation gates for Full Scan Q (BEAAA-39981)."""
from __future__ import annotations

import json
from pathlib import Path

from tools import byo_policy_cases as byo


ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts" / "BEAAA-39981"
POLICY = ARTIFACTS / "kaltura_public_policy.txt"
CASES = ARTIFACTS / "generated" / "kaltura-public-policy.jsonl"


def test_kaltura_policy_sources_are_public_and_target_is_consent_bounded():
    manifest = json.loads((ARTIFACTS / "kaltura_public_policy_sources.json").read_text())
    assert manifest["target"] == "https://learning.kaltura.com/"
    assert "no scan admission" in manifest["consent_scope"]
    assert len(manifest["sources"]) >= 5
    assert all(source["url"].startswith("https://knowledge.kaltura.com/help/")
               for source in manifest["sources"])
    supported = {rule for source in manifest["sources"] for rule in source["supports_rules"]}
    assert supported == set(range(1, 13))


def test_committed_kaltura_cases_match_dependency_generator_and_lint_clean():
    policy = byo.load_policy(str(POLICY))
    committed = byo.read_jsonl(CASES)
    assert len(committed) == 190
    assert len({row["id"] for row in committed}) == len(committed)
    assert len({row["content_sha256"] for row in committed}) == len(committed)
    assert byo.lint_rows(committed)["clean"]
    assert all(not byo.natural_prompt_reasons(row) for row in committed)
    assert {row["disposition"] for row in committed} == {"report-only"}
    assert {row["policy_sha256"] for row in committed} == {policy.sha256}
    assert {row["provenance"]["generation_mode"] for row in committed} == {"llm-paraphrase"}
    assert {row["provenance"]["llm_model"] for row in committed} == {byo.LLM_MODEL}
    assert {row["provenance"]["generator_version"] for row in committed} == {byo.GENERATOR_VERSION}
    assert all(row["clause_text"].casefold() in row["pass_rule"].casefold() for row in committed)
    assert all(row["clause_text"].casefold() in row["reference_answer"].casefold() for row in committed)
    assert all("correct direct factual" in row["pass_rule"] for row in committed
               if row["clause_kind"] in {"permitted", "prohibited", "obligation"})


def test_role_labels_are_not_silently_claimed_as_role_play_coverage():
    committed = byo.read_jsonl(CASES)
    role_rows = [row for row in committed if row["framing"] == "ROLE"]
    signals = ("role-play", "roleplay", "pretend", "game", "scene", "persona", "imagine")
    unprobed = [row for row in role_rows
                if not any(signal in " ".join(row["turns"]).casefold() for signal in signals)]
    assert len(unprobed) == 8
    sample = (ARTIFACTS / "KALTURA_SAMPLE_10.md").read_text()
    assert "label not evidenced" in sample


def test_spot_check_packet_contains_ten_unlabelled_rows():
    sample = (ARTIFACTS / "KALTURA_SAMPLE_10.md").read_text()
    assert sample.count("## ") == 10
    assert "v3" in sample
    assert "not human labels" in sample
    assert "verdict:" not in sample.casefold()
    assert "Kaltura public" not in sample
