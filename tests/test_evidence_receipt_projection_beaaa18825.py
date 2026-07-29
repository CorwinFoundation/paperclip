"""BEAAA-18825 — evidence_receipt_v1 projection (Child 3 of BEAAA-18817).

Covers the feature-gated, PII-free projection of the ratified evidence-receipt-v1
§2 schema onto the terminal webhook (``_customer_result_response``) and the CI
plugin (``ci_plugin_result``):

* AC #1 — a valid receipt projects the EXACT §2 schema (all 12 fields, no more).
* AC #2 — gate disabled -> byte-identical prior contract response (both surfaces).
* AC #4 — a consumer that ignores ``evidence_receipt`` reads a byte-identical
  prior response; the PII allowlist is enforced (a receipt carrying a non-schema
  key fails closed rather than leaking it).
* §6 fail-closed — gate on but no/invalid receipt -> ``receipt_status:
  "unavailable"`` (never fabricated, never leaked).

Every function under test is pure projection I/O off every scoring path; golden
N=5 delta = 0 by construction (no scoring/rubric/corpus/band/weight touched).
"""
from __future__ import annotations

from copy import deepcopy

import pytest

from api import evidence_receipt
from api.ci_scan_ext import ci_plugin_result
from api.evidence_receipt import ALLOWED_FIELDS, FEATURE_FLAG, project
from api.scan_handlers import _customer_result_response
from scanners.live_probe_orchestrator import ScanResult


# --------------------------------------------------------------------------- #
# Fixtures / helpers
# --------------------------------------------------------------------------- #
def valid_receipt() -> dict[str, str]:
    """A schema-exact evidence-receipt-v1 §2 object."""
    return {
        "version": "evidence_receipt_v1",
        "receipt_id": "11111111-2222-3333-4444-555555555555",
        "scan_id": "scan-abc",
        "tenant_ref": "tref-opaque-0xdeadbeef",
        "evidence_bundle_sha256": "sha256:" + "a" * 64,
        "probe_manifest_sha256": "sha256:" + "b" * 64,
        "score_snapshot_sha256": "sha256:" + "c" * 64,
        "scanner_build_digest": "sha256:" + "d" * 64,
        "received_at": "2026-07-29T10:00:00Z",
        "signing_key_id": "backbond-receipt-2026-07",
        "signature_alg": "ed25519",
        "signature": "c2lnbmF0dXJlLWJhc2U2NA==",
    }


class _FakeRuntime:
    """Minimal ScanRuntime stand-in exposing ``current_kickoff_row``."""

    def __init__(self, row):
        self._row = row

    def current_kickoff_row(self, scan_id):  # noqa: D401 - test double
        return self._row


def _score_json() -> dict:
    return {
        "are_total": 84,
        "band": "B",
        "subscores": {"GOVERNANCE": 21, "ROBUSTNESS": 22, "BIAS_FAIRNESS": 20, "OPS": 21},
        "scanner_version": "0.4.1",
        "corpus_version": "0.2.0",
        "seed": 0,
        "model_versions": {},
        "evidence_refs": [],
        "confidence": 0.9,
        "uncovered_dimensions": [],
    }


def _scan_result() -> ScanResult:
    return ScanResult(
        scan_id="scan-abc",
        status="completed",
        scanner_version="0.4.1",
        corpus_version="0.2.0",
        rows_emitted=3,
    )


CI_KWARGS = dict(
    scan_id="scan-abc",
    dim_labels={"P1.1": "Prompt Injection"},
    dim_raws={"P1.1": 0.9},
    band="B",
    disposition="gate_pass",
    dashboard_base_url="https://dash.example",
)


# --------------------------------------------------------------------------- #
# Shared projection unit tests
# --------------------------------------------------------------------------- #
def test_gate_off_projects_nothing(monkeypatch):
    monkeypatch.delenv(FEATURE_FLAG, raising=False)
    assert project(valid_receipt()) == {}


def test_valid_receipt_projects_exact_schema(monkeypatch):
    monkeypatch.setenv(FEATURE_FLAG, "1")
    out = project(valid_receipt())
    assert set(out) == {"evidence_receipt"}
    receipt = out["evidence_receipt"]
    # AC #1 — EXACT §2 schema: keys equal the allowlist, nothing more/less.
    assert tuple(receipt.keys()) == ALLOWED_FIELDS
    assert receipt == valid_receipt()


def test_input_receipt_is_not_mutated(monkeypatch):
    monkeypatch.setenv(FEATURE_FLAG, "1")
    src = valid_receipt()
    before = deepcopy(src)
    project(src)
    assert src == before


@pytest.mark.parametrize("truthy", ["1", "true", "TRUE", "Yes"])
def test_gate_truthy_values(monkeypatch, truthy):
    monkeypatch.setenv(FEATURE_FLAG, truthy)
    assert "evidence_receipt" in project(valid_receipt())


@pytest.mark.parametrize("falsy", ["0", "off", "no", "", "  "])
def test_gate_falsy_values(monkeypatch, falsy):
    monkeypatch.setenv(FEATURE_FLAG, falsy)
    assert project(valid_receipt()) == {}


def test_gate_on_no_receipt_fails_closed(monkeypatch):
    monkeypatch.setenv(FEATURE_FLAG, "1")
    assert project(None) == {"receipt_status": "unavailable"}


def test_pii_allowlist_extra_key_fails_closed(monkeypatch):
    """A receipt carrying a non-schema (PII) key is NEVER projected."""
    monkeypatch.setenv(FEATURE_FLAG, "1")
    leaky = valid_receipt()
    leaky["customer_name"] = "Acme Corp"  # PII that must never ride out
    out = project(leaky)
    assert out == {"receipt_status": "unavailable"}
    assert "Acme Corp" not in repr(out)
    assert "evidence_receipt" not in out


@pytest.mark.parametrize(
    "mutate",
    [
        lambda r: r.pop("signature"),                                   # missing field
        lambda r: r.__setitem__("version", "evidence_receipt_v2"),      # wrong version
        lambda r: r.__setitem__("signature_alg", "rsa"),                # wrong alg
        lambda r: r.__setitem__("evidence_bundle_sha256", "deadbeef"),  # no sha256: prefix
        lambda r: r.__setitem__("probe_manifest_sha256", "sha256:zz"),  # bad hex/len
        lambda r: r.__setitem__("scan_id", ""),                          # empty required string
        lambda r: r.__setitem__("received_at", 12345),                   # wrong type
    ],
)
def test_invalid_receipt_fails_closed(monkeypatch, mutate):
    monkeypatch.setenv(FEATURE_FLAG, "1")
    receipt = valid_receipt()
    mutate(receipt)
    assert project(receipt) == {"receipt_status": "unavailable"}


def test_is_valid_receipt_predicate():
    assert evidence_receipt.is_valid_receipt(valid_receipt()) is True
    assert evidence_receipt.is_valid_receipt({}) is False
    assert evidence_receipt.is_valid_receipt(None) is False  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# CI plugin (ci_plugin_result) integration
# --------------------------------------------------------------------------- #
def test_ci_plugin_gate_off_byte_identical(monkeypatch):
    monkeypatch.delenv(FEATURE_FLAG, raising=False)
    baseline = ci_plugin_result(**CI_KWARGS)
    with_receipt_arg = ci_plugin_result(**CI_KWARGS, evidence_receipt=valid_receipt())
    # AC #2 — gate off: byte-identical regardless of a supplied receipt.
    assert with_receipt_arg == baseline
    assert "evidence_receipt" not in baseline
    assert "receipt_status" not in baseline


def test_ci_plugin_gate_on_adds_receipt_and_stays_byte_identical_otherwise(monkeypatch):
    monkeypatch.delenv(FEATURE_FLAG, raising=False)
    baseline = ci_plugin_result(**CI_KWARGS)
    monkeypatch.setenv(FEATURE_FLAG, "1")
    projected = ci_plugin_result(**CI_KWARGS, evidence_receipt=valid_receipt())
    # AC #4 — a consumer that ignores evidence_receipt reads the prior response.
    assert {k: projected[k] for k in baseline} == baseline
    assert projected["evidence_receipt"] == valid_receipt()


def test_ci_plugin_gate_on_no_receipt_fails_closed(monkeypatch):
    monkeypatch.setenv(FEATURE_FLAG, "1")
    out = ci_plugin_result(**CI_KWARGS)  # no evidence_receipt supplied
    assert out["receipt_status"] == "unavailable"
    assert "evidence_receipt" not in out


# --------------------------------------------------------------------------- #
# Terminal webhook (_customer_result_response) integration
# --------------------------------------------------------------------------- #
def test_webhook_gate_off_byte_identical(monkeypatch):
    monkeypatch.delenv(FEATURE_FLAG, raising=False)
    row = {"score_json": _score_json(), "evidence_receipt": valid_receipt()}
    runtime = _FakeRuntime(row)
    out = _customer_result_response("scan-abc", _scan_result(), runtime)
    # AC #2 — even with a receipt on the row, gate off -> no new keys.
    assert "evidence_receipt" not in out
    assert "receipt_status" not in out


def test_webhook_gate_on_projects_receipt_from_row(monkeypatch):
    monkeypatch.delenv(FEATURE_FLAG, raising=False)
    row = {"score_json": _score_json(), "evidence_receipt": valid_receipt()}
    baseline = _customer_result_response("scan-abc", _scan_result(), _FakeRuntime(row))
    monkeypatch.setenv(FEATURE_FLAG, "1")
    projected = _customer_result_response("scan-abc", _scan_result(), _FakeRuntime(row))
    # AC #4 — additive: every prior key is byte-identical.
    assert {k: projected[k] for k in baseline} == baseline
    assert projected["evidence_receipt"] == valid_receipt()


def test_webhook_gate_on_no_receipt_on_row_fails_closed(monkeypatch):
    monkeypatch.setenv(FEATURE_FLAG, "1")
    row = {"score_json": _score_json()}  # receipt service (Child 2) not wired yet
    out = _customer_result_response("scan-abc", _scan_result(), _FakeRuntime(row))
    assert out["receipt_status"] == "unavailable"
    assert "evidence_receipt" not in out


@pytest.mark.parametrize("seed", range(5))
def test_n5_projection_preserves_score_envelope(monkeypatch, seed):
    """Golden-shaped N=5 check: the scored envelope is byte-identical with the
    projection on vs off for every pre-existing key (delta=0 by construction)."""
    score = _score_json()
    score["seed"] = seed
    row = {"score_json": score, "evidence_receipt": valid_receipt()}
    monkeypatch.delenv(FEATURE_FLAG, raising=False)
    baseline = _customer_result_response("scan-abc", _scan_result(), _FakeRuntime(row))
    monkeypatch.setenv(FEATURE_FLAG, "1")
    candidate = _customer_result_response("scan-abc", _scan_result(), _FakeRuntime(row))
    assert candidate["are_total"] == baseline["are_total"]
    assert candidate["subscores"] == baseline["subscores"]
    assert {k: candidate[k] for k in baseline} == baseline
