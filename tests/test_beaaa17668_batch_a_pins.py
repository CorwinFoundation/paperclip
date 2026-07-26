from __future__ import annotations

import hashlib
from pathlib import Path

from scanners.eval_framework_venv import FRAMEWORKS


ROOT = Path(__file__).resolve().parents[1]

BEHAVIORAL_FAMILIES = {
    "advbench",
    "agentdojo",
    "bbq",
    "citation_integrity_legal",
    "confidence_calibration",
    "counterfactual_stability",
    "demographic_parity",
    "donotanswer",
    "equal_opportunity",
    "factscore",
    "garak",
    "garak_beaaa152",
    "hallucination_legal",
    "halueval",
    "harmbench",
    "indirect_injection",
    "jailbreakbench",
}

CONTROL_FAMILIES = {
    "eval_coverage_breadth",
    "eval_representation",
    "mitigation_evidence",
}

PINNED_FILES = {
    "tests/probe_sets/p5_direct_advbench_v1.jsonl":
        "a0fcf4dfad27760679d0fe7811ba4c15c8827f77eef0a21dc18ae79d2ef8c0a9",
    "requirements-eval-agentdojo.txt":
        "46fac543e2fd58573e8c1acb6538fd96de97f3ee9b5b22690c3519a0a5b9ff0b",
    "tests/probe_sets/p4_citation_grounded_factscore_v1.jsonl":
        "1b0e0808bb6f9f2bb4196343c95b510418ec4b2f54d9ceb7bac0f5a8578d74ac",
    "requirements-eval-garak.txt":
        "a4a18428de2c894c3ee2d1e00daa533cbc0ff1f019019eb1654e80713cfa087a",
    "rubric/probe_mappings/garak.yaml":
        "c432a404896c13d24145cfe8964cadd52f87073668860f80edc13d848eddfed0",
    "rubric/probe_mappings/garak_beaaa152.yaml":
        "def0e15e1bdfdcfea79efd0d06bd0d2665883d992df3afff595f01599509b9a5",
    "tests/probe_sets/p4_grounded_factuality_halueval_qa_v1.jsonl":
        "fcff3a6ab4e3d9f99d2024da646c7efa6a455658a127b359aeb6038d01dad847",
    "tests/probe_sets/p4_citation_grounded_halueval_summ_v1.jsonl":
        "dff614940b01f88430095b0206a38550842ef9f79b8ff86f37423d43ffcae7a1",
    "rubric/probe_mappings/harmbench.yaml":
        "a928e8e782605453cfeabc21addb0547879a5006258aec099fab380a0466a91f",
    "tests/probe_sets/p5_direct_jailbreakbench_v1.jsonl":
        "5efb91649fbc547e041e66702839bdf95a8e0473234f81635fa54450cd4b3670",
}


def test_cto_classification_has_17_behavioral_and_3_control_families() -> None:
    assert len(BEHAVIORAL_FAMILIES) == 17
    assert len(CONTROL_FAMILIES) == 3
    assert BEHAVIORAL_FAMILIES.isdisjoint(CONTROL_FAMILIES)
    assert "equal_opportunity" in BEHAVIORAL_FAMILIES


def test_cto_approved_external_inputs_are_byte_pinned() -> None:
    for relative_path, expected in PINNED_FILES.items():
        actual = hashlib.sha256((ROOT / relative_path).read_bytes()).hexdigest()
        assert actual == expected, relative_path


def test_isolated_framework_versions_match_cto_decision() -> None:
    assert FRAMEWORKS["agentdojo"].pinned_version == "0.1.35"
    assert FRAMEWORKS["garak"].pinned_version == "0.10.0"
    assert FRAMEWORKS["pyrit"].pinned_version == "0.6.0"
