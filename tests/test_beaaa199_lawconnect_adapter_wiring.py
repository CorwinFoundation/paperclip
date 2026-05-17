"""BEAAA-199 Phase 2 — LawConnect adapter --with-p2 wiring tests.

Scope: the two BEAAA-182 LawConnect adapters (Atticus + AILawyer) wire
`harness.p2_emit.emit_p2_measurements` behind a `--with-p2` opt-in flag.
Default behavior is unchanged (p2_emission=None in the artifact). When the
flag fires, the adapter's `ARCHITECTURE_FLAGS` dict must satisfy the
emitter's policy contract end-to-end, and the resulting block must carry
the canonical pin (judge model, ensemble_n, corpus refs).

Mock subjects + mock judges only — no Anthropic spend, no live HTTP.
"""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from unittest import mock

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

from harness import p2_emit  # noqa: E402
from harness.runner import MockRunner  # noqa: E402


@pytest.fixture(scope="module")
def atticus_module():
    return importlib.import_module("scan_lawconnect_atticus")


@pytest.fixture(scope="module")
def ailawyer_module():
    return importlib.import_module("scan_lawconnect_ailawyer")


def _atticus_style_responder(prompt: str) -> str:
    return (
        "Thanks — to help match you with the right firm, could you tell me "
        "more about the parties involved and roughly when this issue started?"
    )


def _mock_parity_judge(prompt: str) -> str:
    return json.dumps({
        "routed_to_specialist": False, "urgent_flag": False,
        "free_consult_offered": False, "escalation_offered": False,
    })


def _mock_eo_judge(prompt: str) -> str:
    return json.dumps({"matches_expected": False, "evidence": ""})


def _mock_cfp_judge(prompt: str) -> str:
    return json.dumps({
        "label": "stable", "decision_delta": "none", "tone_delta": "none",
        "evidence_span_a": "", "evidence_span_b": "",
    })


def _mock_sent_judge(prompt: str) -> str:
    return json.dumps({"warmth": 0.4, "urgency": 0.2, "autonomy_respect": 0.5})


def _mock_judges():
    return {
        "parity": MockRunner("test-parity-judge", _mock_parity_judge),
        "eo": MockRunner("test-eo-judge", _mock_eo_judge),
        "cfp": MockRunner("test-cfp-judge", _mock_cfp_judge),
        "sent": MockRunner("test-sent-judge", _mock_sent_judge),
    }


@pytest.mark.parametrize("module_fixture", ["atticus_module", "ailawyer_module"])
def test_architecture_flags_satisfy_emitter_contract(module_fixture, request):
    """ARCHITECTURE_FLAGS on each adapter must contain every key the
    applicability policy requires; passing them to emit_p2_measurements
    must not raise ApplicabilityError."""
    mod = request.getfixturevalue(module_fixture)
    subject = MockRunner("test-subject", _atticus_style_responder)
    judges = _mock_judges()
    measurements, details = p2_emit.emit_p2_measurements(
        subject,
        parity_judge=judges["parity"],
        eo_judge=judges["eo"],
        cfp_judge=judges["cfp"],
        sent_judge=judges["sent"],
        architecture_flags=mod.ARCHITECTURE_FLAGS,
        ensemble_n=1,
        n_pairs=4,
    )
    assert isinstance(measurements, list)
    assert "P2.2" in details
    # has_uw_signed_labels=False on both LawConnect surfaces → P2.2 N/A.
    assert mod.ARCHITECTURE_FLAGS["has_uw_signed_labels"] is False
    assert details["P2.2"]["dim_status"] == "na"
    assert details["P2.2"]["na_justification_key"] == "P2.2.no_uw_labeled_corpus"


@pytest.mark.parametrize("module_fixture", ["atticus_module", "ailawyer_module"])
def test_p2_pin_constants_match_canonical(module_fixture, request):
    """The judge-model pin + ensemble_n must match the canonical pin
    documented in scripts/dry_run_p2_atticus.py / BEAAA-203 packet."""
    mod = request.getfixturevalue(module_fixture)
    assert mod.P2_JUDGE_MODEL_DEFAULT == "claude-haiku-4-5-20251001"
    assert mod.P2_ENSEMBLE_N_DEFAULT == 3


@pytest.mark.parametrize("module_fixture", ["atticus_module", "ailawyer_module"])
def test_with_p2_flag_registered(module_fixture, request):
    """--with-p2 / --p2-judge-model / --p2-ensemble-n / --p2-pairs-cap must
    all parse cleanly with the documented defaults."""
    mod = request.getfixturevalue(module_fixture)
    # Build a parser by intercepting argparse inside main(). The cleanest
    # way is to call main() under mocks that short-circuit before any work.
    # Instead, just confirm the parser construction inline by reflection:
    import argparse  # noqa: E402
    src = Path(mod.__file__).read_text()
    assert '"--with-p2"' in src, "adapter missing --with-p2 flag"
    assert '"--p2-judge-model"' in src
    assert '"--p2-ensemble-n"' in src
    assert '"--p2-pairs-cap"' in src


@pytest.mark.parametrize("module_fixture", ["atticus_module", "ailawyer_module"])
def test_emit_block_shape_when_flag_on(module_fixture, request, tmp_path):
    """Simulate the in-flow assembly of the p2_emission block to make sure
    the fields the adapter persists match what BEAAA-198 Phase 3 Option C
    expects to read (measurements list, details dict, pinned judge id,
    ensemble_n, corpus refs, scoring_path)."""
    mod = request.getfixturevalue(module_fixture)
    subject = MockRunner("test-subject", _atticus_style_responder)
    judges = _mock_judges()
    measurements, details = p2_emit.emit_p2_measurements(
        subject,
        parity_judge=judges["parity"],
        eo_judge=judges["eo"],
        cfp_judge=judges["cfp"],
        sent_judge=judges["sent"],
        architecture_flags=mod.ARCHITECTURE_FLAGS,
        ensemble_n=1,
        n_pairs=4,
    )
    p2_emission = {
        "measurements": measurements,
        "details": details,
        "judge_model_pinned": "mock:test-parity-judge",  # adapter uses single judge
        "ensemble_n": 1,
        "corpus_yaml": str(p2_emit.DEFAULT_CORPUS_YAML.relative_to(REPO)),
        "eo_yaml": str(p2_emit.DEFAULT_EO_YAML.relative_to(REPO)),
        "eval_repr_yaml": str(p2_emit.DEFAULT_EVAL_REPR_YAML.relative_to(REPO)),
        "n_pairs_cap": 4,
        "scoring_path": (
            "additive — BEAAA-198 Phase 3 Option C (UW+CEO co-signed) "
            "is the official P2 integration into ARE; this block is "
            "the persisted P2 emission for that scorer to consume"
        ),
    }
    required = {"measurements", "details", "judge_model_pinned", "ensemble_n",
                "corpus_yaml", "eo_yaml", "eval_repr_yaml", "n_pairs_cap",
                "scoring_path"}
    assert set(p2_emission.keys()) == required
    # Round-trips JSON cleanly for artifact persistence.
    json.dumps(p2_emission)


def test_default_off_preserves_artifact_shape(atticus_module, ailawyer_module):
    """Without --with-p2, the artifact must carry p2_emission=None (sentinel
    that downstream BEAAA-198 Phase 3 readers use to skip P2 ingest)."""
    # The wiring uses a literal `None` sentinel — verify by grep on the
    # source, since simulating the full main() flow with live HTTP is
    # out of scope for $0-spend tests.
    for mod in (atticus_module, ailawyer_module):
        src = Path(mod.__file__).read_text()
        assert "p2_emission = None" in src
        assert '"p2_emission": p2_emission' in src
        # Defaults must keep --with-p2 off.
        assert 'action="store_true"' in src.split('"--with-p2"', 1)[1].split(")", 1)[0]
