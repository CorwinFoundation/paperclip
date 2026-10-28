"""BEAAA-199 Phase 2 — harness.p2_emit reusable emitter tests.

Covers:
  - Measurement-shape contract (every emitted entry has dim_id/raw/benchmark).
  - has_uw_signed_labels gate: P2.2 N/A when False, runs when True.
  - Inconclusive dims are omitted from measurements but present in details.
  - Adapter contract preserved: full architecture_flags dict passes through.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from harness import p2_emit  # noqa: E402
from harness.runner import MockRunner  # noqa: E402

# Full architecture_flags dict matching the adapter contract — every flag
# declared in rubric/applicability_policy.yaml must be present.
BASE_FLAGS_NO_UW_LABELS = {
    "has_tool_calling": False,
    "has_rag_retrieval": False,
    "has_document_attachments": False,
    "has_inline_html_rendering": False,
    "has_system_prompt_in_response_channel": False,
    "has_credentials_in_prompt": False,
    "has_honeytoken_canary_planted": False,
    "has_multi_tenant_context": False,
    "has_uw_signed_labels": False,
}

BASE_FLAGS_WITH_UW_LABELS = dict(BASE_FLAGS_NO_UW_LABELS, has_uw_signed_labels=True)


def _atticus_style_responder(prompt: str) -> str:
    return (
        "Thanks — to help match you with the right firm, could you tell me "
        "more about the parties involved and roughly when this issue "
        "started? You can pick: a) within the last week, b) within the "
        "last month, c) more than a month ago."
    )


def _mock_parity_judge(prompt: str) -> str:
    response = prompt.split("[RESPONSE]\n", 1)[-1].strip().lower()
    return json.dumps({
        "routed_to_specialist": "specialist" in response,
        "urgent_flag": "urgent" in response or "today" in response,
        "free_consult_offered": "free consultation" in response,
        "escalation_offered": "escalate" in response,
    })


def _mock_eo_judge(prompt: str) -> str:
    response = prompt.split("[RESPONSE]\n", 1)[-1].strip().lower()
    return json.dumps({
        "matches_expected": "specialist" in response,
        "evidence": response[:120],
    })


def _mock_cfp_judge(prompt: str) -> str:
    a = prompt.split("[RESPONSE A]\n", 1)[-1].split("\n\n[RESPONSE B]", 1)[0].strip()
    b = prompt.split("[RESPONSE B]\n", 1)[-1].split("\n\nEmit JSON", 1)[0].strip()
    if a == b:
        return json.dumps({
            "label": "stable", "decision_delta": "none", "tone_delta": "none",
            "evidence_span_a": "", "evidence_span_b": "",
        })
    return json.dumps({
        "label": "drift_tone", "decision_delta": "",
        "tone_delta": "warmth differs",
        "evidence_span_a": a[:60], "evidence_span_b": b[:60],
    })


def _mock_sent_judge(prompt: str) -> str:
    return json.dumps({"warmth": 0.4, "urgency": 0.2, "autonomy_respect": 0.5})


def _build_mocks():
    return dict(
        subject=MockRunner("test-subject", _atticus_style_responder),
        parity_judge=MockRunner("test-parity-judge", _mock_parity_judge),
        eo_judge=MockRunner("test-eo-judge", _mock_eo_judge),
        cfp_judge=MockRunner("test-cfp-judge", _mock_cfp_judge),
        sent_judge=MockRunner("test-sent-judge", _mock_sent_judge),
    )


def test_emit_no_uw_labels_skips_p22():
    """When has_uw_signed_labels=False, P2.2 must be N/A (no measurement,
    details carry the policy-canonical na_justification_key)."""
    mocks = _build_mocks()
    measurements, details = p2_emit.emit_p2_measurements(
        mocks["subject"],
        parity_judge=mocks["parity_judge"],
        eo_judge=mocks["eo_judge"],
        cfp_judge=mocks["cfp_judge"],
        sent_judge=mocks["sent_judge"],
        architecture_flags=BASE_FLAGS_NO_UW_LABELS,
        ensemble_n=1,
        n_pairs=6,
    )
    dims_with_measurements = {m["dim_id"] for m in measurements}
    assert "P2.2" not in dims_with_measurements
    assert details["P2.2"]["dim_status"] == "na"
    assert details["P2.2"]["na_justification_key"] == "P2.2.no_uw_labeled_corpus"
    assert details["_applicability_decisions"]["P2.2"]["fires"] is False


def test_emit_with_uw_labels_runs_p22():
    """When has_uw_signed_labels=True, P2.2 is gated-on; emitter runs the
    EO subset and either emits a measurement or surfaces inconclusive in
    details (small-n subset is a legitimate inconclusive)."""
    mocks = _build_mocks()
    measurements, details = p2_emit.emit_p2_measurements(
        mocks["subject"],
        parity_judge=mocks["parity_judge"],
        eo_judge=mocks["eo_judge"],
        cfp_judge=mocks["cfp_judge"],
        sent_judge=mocks["sent_judge"],
        architecture_flags=BASE_FLAGS_WITH_UW_LABELS,
        ensemble_n=1,
        n_pairs=6,
    )
    assert details["_applicability_decisions"]["P2.2"]["fires"] is True
    # P2.2 details must be present (either measured or inconclusive); a "na"
    # status would indicate the gate failed to evaluate.
    assert details["P2.2"].get("dim_status") in ("ok", "inconclusive")


def test_measurement_shape_contract():
    """Every emitted measurement must have dim_id/raw/benchmark with the
    correct types for merge_measurements_with_priors to consume."""
    mocks = _build_mocks()
    measurements, _ = p2_emit.emit_p2_measurements(
        mocks["subject"],
        parity_judge=mocks["parity_judge"],
        eo_judge=mocks["eo_judge"],
        cfp_judge=mocks["cfp_judge"],
        sent_judge=mocks["sent_judge"],
        architecture_flags=BASE_FLAGS_NO_UW_LABELS,
        ensemble_n=1,
        n_pairs=6,
    )
    for m in measurements:
        assert set(m.keys()) >= {"dim_id", "raw", "benchmark"}
        assert m["dim_id"].startswith("P2.")
        assert isinstance(m["raw"], float)
        assert isinstance(m["benchmark"], str) and m["benchmark"]


def test_adapter_contract_enforced():
    """Missing or unknown architecture_flags must raise ApplicabilityError
    (delegated to harness.applicability.decide)."""
    from harness.applicability import ApplicabilityError

    mocks = _build_mocks()
    incomplete = {"has_uw_signed_labels": False}
    with pytest.raises(ApplicabilityError):
        p2_emit.emit_p2_measurements(
            mocks["subject"],
            parity_judge=mocks["parity_judge"],
            eo_judge=mocks["eo_judge"],
            cfp_judge=mocks["cfp_judge"],
            sent_judge=mocks["sent_judge"],
            architecture_flags=incomplete,
            ensemble_n=1,
            n_pairs=6,
        )


def test_emit_pipeline_integration():
    """Measurements list must be directly consumable by
    harness.pipeline.merge_measurements_with_priors without transformation."""
    from harness.pipeline import merge_measurements_with_priors

    mocks = _build_mocks()
    measurements, _ = p2_emit.emit_p2_measurements(
        mocks["subject"],
        parity_judge=mocks["parity_judge"],
        eo_judge=mocks["eo_judge"],
        cfp_judge=mocks["cfp_judge"],
        sent_judge=mocks["sent_judge"],
        architecture_flags=BASE_FLAGS_NO_UW_LABELS,
        ensemble_n=1,
        n_pairs=6,
    )
    priors = {f"P2.{i}": 2.0 for i in range(1, 7)}
    raw_scores, provenance = merge_measurements_with_priors(measurements, priors)
    for m in measurements:
        assert raw_scores[m["dim_id"]] == m["raw"]
        assert provenance[m["dim_id"]].startswith("measured:")
    # P2.6 has no measurement in Phase 2 yet → must keep the prior.
    assert raw_scores["P2.6"] == 2.0
    assert provenance["P2.6"] == "prior"


def test_timeout_keeps_other_axis_measurements(monkeypatch):
    import time

    def paired(mode, *args, **kwargs):
        if mode == "parity":
            time.sleep(2)
        return {"dim_status": "ok", "dim_raw": 77, "benchmark": mode}

    monkeypatch.setattr(p2_emit.pa, "run", paired)
    monkeypatch.setattr(p2_emit.cfp, "run", lambda *a, **k: {
        "dim_status": "ok", "dim_raw": 88, "benchmark": "cfp"})
    measurements, details = p2_emit.emit_p2_measurements(
        **_build_mocks(), architecture_flags=BASE_FLAGS_NO_UW_LABELS,
        n_pairs=2, ensemble_n=1, axis_timeouts={"P2.1": 0.05},
    )
    assert details["P2.1"]["dim_status"] == "inconclusive"
    assert details["P2.1"]["reason"] == "axis timeout exceeded"
    assert not any(m["dim_id"] == "P2.1" for m in measurements)
    assert {m["dim_id"] for m in measurements} >= {"P2.3", "P2.5"}
    assert details["P2.4"]["dim_status"] == "ok"


def test_persistent_resume_matches_uncached_output(tmp_path, monkeypatch):
    class IdentifiedMock(MockRunner):
        def p2_checkpoint_identity(self):
            return {"fixture_version": "frozen-test-v1", "role": self._name}

    monkeypatch.setattr(p2_emit.pa, 'run', lambda *a, **k: {
        'dim_status': 'ok', 'dim_raw': 77, 'benchmark': 'paired'})
    monkeypatch.setattr(p2_emit.cfp, 'run', lambda *a, **k: {
        'dim_status': 'ok', 'dim_raw': 88, 'benchmark': 'cfp'})
    mocks = _build_mocks()
    for name, runner in list(mocks.items()):
        mocks[name] = IdentifiedMock(runner._name, runner._responder)
    kwargs = dict(**mocks, architecture_flags=BASE_FLAGS_NO_UW_LABELS,
                  ensemble_n=1, n_pairs=6)
    expected = p2_emit.emit_p2_measurements(**kwargs)
    actual = p2_emit.emit_p2_measurements(**kwargs, checkpoint_directory=tmp_path)
    assert actual == expected
    assert len(list(tmp_path.glob('p2_emission_*.json'))) == 3
    # A cache miss would produce inconclusive axes, making equality fail.
    monkeypatch.setattr(p2_emit.pa, 'run', lambda *a, **k: (_ for _ in ()).throw(RuntimeError()))
    monkeypatch.setattr(p2_emit.cfp, 'run', lambda *a, **k: (_ for _ in ()).throw(RuntimeError()))
    assert p2_emit.emit_p2_measurements(**kwargs, checkpoint_directory=tmp_path) == expected


def test_partial_resume_retries_only_failed_axis(tmp_path, monkeypatch):
    """A timed out aggregate is retried; successful later axes survive."""
    import time

    class IdentifiedMock(MockRunner):
        def p2_checkpoint_identity(self):
            return {"fixture_version": "partial-resume-v1", "role": self._name}

    calls = tmp_path / 'calls'
    recovered = tmp_path / 'recovered'

    def record(name):
        with calls.open('a') as stream:
            stream.write(name + '\n')

    def paired(mode, *args, **kwargs):
        record(mode)
        if mode == 'parity' and not recovered.exists():
            time.sleep(5)
        return {'dim_status': 'ok', 'dim_raw': 77, 'benchmark': mode}

    def counterfactual(*args, **kwargs):
        record('counterfactual')
        return {'dim_status': 'ok', 'dim_raw': 88, 'benchmark': 'cfp'}

    monkeypatch.setattr(p2_emit.pa, 'run', paired)
    monkeypatch.setattr(p2_emit.cfp, 'run', counterfactual)
    mocks = {name: IdentifiedMock(runner._name, runner._responder)
             for name, runner in _build_mocks().items()}
    kwargs = dict(**mocks, architecture_flags=BASE_FLAGS_NO_UW_LABELS,
                  ensemble_n=1, n_pairs=6, axis_timeouts={'P2.1': 0.2})
    cache = tmp_path / 'checkpoints'
    first, details = p2_emit.emit_p2_measurements(**kwargs, checkpoint_directory=cache)
    assert details['P2.1']['dim_status'] == 'inconclusive'
    assert {m['dim_id'] for m in first} >= {'P2.3', 'P2.5'}
    assert not (cache / 'p2_emission_P2.1.json').exists()
    assert len(list(cache.glob('p2_emission_*.json'))) == 2
    recovered.touch()
    resumed = p2_emit.emit_p2_measurements(**kwargs, checkpoint_directory=cache)
    assert calls.read_text().splitlines() == [
        'parity', 'counterfactual', 'sentiment', 'parity']
    assert resumed[1]['P2.1']['dim_status'] == 'ok'
    assert len(list(cache.glob('p2_emission_*.json'))) == 3
    fresh = p2_emit.emit_p2_measurements(**kwargs)
    assert json.dumps(resumed, sort_keys=True) == json.dumps(fresh, sort_keys=True)


def test_checkpoint_invalidated_by_counterfactual_curve_source(tmp_path, monkeypatch):
    class IdentifiedMock(MockRunner):
        def p2_checkpoint_identity(self):
            return {"fixture_version": "curve-v1", "role": self._name}

    calls = tmp_path / "calls"

    def paired(*args, **kwargs):
        with calls.open("a") as stream:
            stream.write("paired\n")
        return {"dim_status": "ok", "dim_raw": 2, "benchmark": "paired"}

    monkeypatch.setattr(p2_emit.pa, "run", paired)
    monkeypatch.setattr(p2_emit.cfp, "run", lambda *a, **k: {
        "dim_status": "ok", "dim_raw": 2, "benchmark": "cfp"})
    mocks = {name: IdentifiedMock(runner._name, runner._responder)
             for name, runner in _build_mocks().items()}
    kwargs = dict(**mocks, architecture_flags=BASE_FLAGS_NO_UW_LABELS,
                  ensemble_n=1, n_pairs=6, checkpoint_directory=tmp_path / "cache")
    p2_emit.emit_p2_measurements(**kwargs)
    p2_emit.emit_p2_measurements(**kwargs)
    assert calls.read_text().splitlines() == ["paired", "paired"]
    from pathlib import Path
    original = Path.read_bytes

    def changed_source(path):
        data = original(path)
        return data + b"\n# changed curve" if path.name == "promptfoo_runner.py" else data

    monkeypatch.setattr(Path, "read_bytes", changed_source)
    p2_emit.emit_p2_measurements(**kwargs)
    assert calls.read_text().splitlines() == ["paired"] * 4


def test_automatic_checkpoints_resume_and_opt_out(tmp_path, monkeypatch):
    class IdentifiedMock(MockRunner):
        def p2_checkpoint_identity(self):
            return {'fixture_version': 'automatic-v1', 'role': self._name}

    # Redirect placement without changing the source fingerprint paths.
    class CacheRoot:
        def __truediv__(self, name):
            return (tmp_path if name == '.p2-checkpoints' else
                    original_root / name)

    monkeypatch.setattr(p2_emit.pa, 'run', lambda *a, **k: {
        'dim_status': 'ok', 'dim_raw': 2, 'benchmark': 'paired'})
    monkeypatch.setattr(p2_emit.cfp, 'run', lambda *a, **k: {
        'dim_status': 'ok', 'dim_raw': 2, 'benchmark': 'cfp'})
    original_root = p2_emit._REPO
    monkeypatch.setattr(p2_emit, '_REPO', CacheRoot())
    mocks = {name: IdentifiedMock(runner._name, runner._responder)
             for name, runner in _build_mocks().items()}
    kwargs = dict(**mocks, architecture_flags=BASE_FLAGS_NO_UW_LABELS,
                  ensemble_n=1, n_pairs=6)
    expected = p2_emit.emit_p2_measurements(**kwargs)
    assert len(list(tmp_path.glob('*/p2_emission_*.json'))) == 3

    def forbidden(*args, **kwargs):
        raise AssertionError('cached axis executed')

    monkeypatch.setattr(p2_emit.pa, 'run', forbidden)
    monkeypatch.setattr(p2_emit.cfp, 'run', forbidden)
    assert p2_emit.emit_p2_measurements(**kwargs) == expected
    _, fresh_details = p2_emit.emit_p2_measurements(**kwargs, automatic_checkpoints=False)
    assert fresh_details['P2.1']['dim_status'] == 'inconclusive'


def test_unsupported_adapters_remain_uncached(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    mocks = _build_mocks()
    p2_emit.emit_p2_measurements(**mocks,
        architecture_flags=BASE_FLAGS_NO_UW_LABELS, ensemble_n=1, n_pairs=6)
    assert not (tmp_path / '.p2-checkpoints').exists()


@pytest.mark.parametrize('flags', [BASE_FLAGS_NO_UW_LABELS, BASE_FLAGS_WITH_UW_LABELS])
def test_five_offline_runs_byte_match_pre_refactor_emitter(flags):
    """Frozen pre-refactor orchestration oracle; real aggregates, offline runners.

    This proves wrapping preserves deterministic outputs, not historical B2
    model responses (that persisted emission is unavailable).
    """
    import importlib.util

    source = REPO / 'tests/fixtures/p2_orchestration/pre_refactor_emitter.py'
    spec = importlib.util.spec_from_file_location('p2_pre_refactor', source)
    original = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(original)
    paths = dict(corpus_yaml=p2_emit.DEFAULT_CORPUS_YAML,
                 eo_yaml=p2_emit.DEFAULT_EO_YAML,
                 eval_repr_yaml=p2_emit.DEFAULT_EVAL_REPR_YAML,
                 policy_path=p2_emit.DEFAULT_POLICY_PATH)
    def serialize(result):
        return json.dumps(result, sort_keys=True, allow_nan=False).encode()

    expected = serialize(original.emit_p2_measurements(
        **_build_mocks(), architecture_flags=flags, n_pairs=6, ensemble_n=1, **paths))
    for _ in range(5):
        actual = p2_emit.emit_p2_measurements(
            **_build_mocks(), architecture_flags=flags, n_pairs=6, ensemble_n=1,
            automatic_checkpoints=False, **paths)
        assert serialize(actual) == expected
