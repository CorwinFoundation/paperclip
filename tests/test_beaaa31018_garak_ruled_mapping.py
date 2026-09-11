from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def _mapping():
    return yaml.safe_load(
        (ROOT / "rubric/probe_mappings/garak_beaaa152.yaml").read_text()
    )


def test_pr1_ruled_garak_families_are_mapped_once():
    rows = _mapping()["probes"]
    by_id = {row["probe_id"]: row for row in rows}
    expected = {
        "garak.encoding.InjectAscii85", "garak.encoding.InjectAtbash",
        "garak.encoding.InjectSneakyBits", "garak.ansiescape.AnsiEscaped",
        "garak.dra.DRA", "garak.badchars.BadCharacters",
        "garak.smuggling.FunctionMasking", "garak.smuggling.HomoglyphObfuscation",
        "garak.apikey.GetKey", "garak.web_injection.MarkdownURIImageExfilExtended",
        "garak.web_injection.MarkdownURINonImageExfilExtended",
        "garak.sysprompt_extraction.SystemPromptExtraction",
    }
    assert expected <= by_id.keys()
    assert len(rows) == len(by_id)
    assert all(by_id[probe_id]["weight"] == 0.0 for probe_id in expected)
    sysprompt = by_id["garak.sysprompt_extraction.SystemPromptExtraction"]
    assert sysprompt["weight"] == 0.0
    assert sysprompt["reconciles_with"].endswith("system_prompt_extraction.yaml")


def test_modernbert_is_disagreement_only_and_never_replaces_native_detector():
    cross_check = _mapping()["refusal_cross_check"]
    assert cross_check == {
        "detector": "garak.detectors.mitigation.ModernBERTRefusal",
        "mode": "disagreement_only",
        "replaces_native_detector": False,
        "native_detector": "harness.bench.aiuc_path_b.REFUSAL_RE",
        "report_field": "refusal_detector_disagreement_rate",
    }
