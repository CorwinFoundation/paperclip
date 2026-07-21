"""BEAAA-16230 — Sequence 20 Stage Gate provisioning-config validation.

Closes gaps 2-5 of the Founder Stage Gate FAIL (attachment d05e00d8) on the
operator-managed acceptance-provisioning config: the config schema must reject a
provisioning record that is missing a distinct second test tenant, points the
synthetic acceptance endpoint back at the scanner under test, uses a non-HTTPS
external acceptance URL, omits the deployed acceptance seed, or omits the
rollback identity. It must also keep secret *values* out of git/Paperclip — only
opaque references are allowed — and a fully-populated non-secret fixture must
emit a complete Stage Gate record.

Pure-Python; no network, no Docker, no scoring/rubric/corpus surface.
"""
from __future__ import annotations

import copy
import os

import pytest

from scripts.deploy import stage_gate_config as sgc

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURE = os.path.abspath(
    os.path.join(HERE, "..", "config", "stage-gate", "sequence-20-acceptance.example.toml")
)


@pytest.fixture()
def good_cfg() -> dict:
    """The committed non-secret example must itself be a valid config."""
    return sgc.load_config(FIXTURE)


def _codes(cfg: dict) -> list[str]:
    return [e.code for e in sgc.validate(cfg)]


# --------------------------------------------------------------------------- #
# The committed fixture is valid and emits a complete, non-secret record.
# --------------------------------------------------------------------------- #
def test_fixture_is_valid(good_cfg):
    assert sgc.validate(good_cfg) == [], "committed example config must validate clean"


def test_fixture_emits_complete_non_secret_record(good_cfg):
    record = sgc.build_record(good_cfg)
    # Every Stage Gate field the operator kit v0.2 requires must be populated.
    for field in sgc.STAGE_GATE_FIELDS:
        assert record.get(field), f"stage gate record missing field: {field}"
    # No secret value may appear anywhere in the emitted record.
    blob = repr(record)
    for marker in ("BEGIN PRIVATE", "secret_value", "-----", "AKIA"):
        assert marker not in blob
    # References stay opaque (scheme-prefixed), never raw tokens.
    assert record["tenant_a_token_ref"].split(":", 1)[0] in sgc.REF_SCHEMES
    assert record["tenant_b_token_ref"].split(":", 1)[0] in sgc.REF_SCHEMES


# --------------------------------------------------------------------------- #
# gap 2 — a distinct second (non-customer/non-vapi) tenant is required
# --------------------------------------------------------------------------- #
def test_missing_tenant_b_is_rejected(good_cfg):
    cfg = copy.deepcopy(good_cfg)
    del cfg["tenants"]["b"]
    assert "tenant_b_missing" in _codes(cfg)


def test_tenant_b_identical_to_a_is_rejected(good_cfg):
    cfg = copy.deepcopy(good_cfg)
    cfg["tenants"]["b"] = copy.deepcopy(cfg["tenants"]["a"])
    assert "tenants_not_distinct" in _codes(cfg)


def test_vapi_tenant_is_rejected(good_cfg):
    cfg = copy.deepcopy(good_cfg)
    cfg["tenants"]["b"]["alias"] = "vapi-prod"
    assert "tenant_vapi_forbidden" in _codes(cfg)


# --------------------------------------------------------------------------- #
# gap 4 — the synthetic endpoint must not point back at the scanner
# --------------------------------------------------------------------------- #
def test_self_referential_scanner_endpoint_is_rejected(good_cfg):
    cfg = copy.deepcopy(good_cfg)
    # Point the synthetic target host at the external edge host itself.
    from urllib.parse import urlparse

    edge_host = urlparse(cfg["edge"]["external_base_url"]).hostname
    cfg["synthetic_endpoint"]["host"] = edge_host
    assert "synthetic_endpoint_self_referential" in _codes(cfg)


# --------------------------------------------------------------------------- #
# gap 3 — the external acceptance URL must be HTTPS
# --------------------------------------------------------------------------- #
def test_non_https_external_url_is_rejected(good_cfg):
    cfg = copy.deepcopy(good_cfg)
    cfg["edge"]["external_base_url"] = "http://acceptance.edge.example"
    assert "external_base_url_not_https" in _codes(cfg)


# --------------------------------------------------------------------------- #
# gap — deployed acceptance seed read-back is required
# --------------------------------------------------------------------------- #
def test_missing_deployed_seed_is_rejected(good_cfg):
    cfg = copy.deepcopy(good_cfg)
    del cfg["deployed"]["seed"]
    assert "deployed_seed_missing" in _codes(cfg)


# --------------------------------------------------------------------------- #
# gap 5 — a retained rollback identity (commit + repo-qualified OCI digest)
# --------------------------------------------------------------------------- #
def test_missing_rollback_identity_is_rejected(good_cfg):
    cfg = copy.deepcopy(good_cfg)
    del cfg["rollback"]["image_digest"]
    assert "rollback_identity_missing" in _codes(cfg)


def test_rollback_digest_must_be_repository_qualified(good_cfg):
    cfg = copy.deepcopy(good_cfg)
    # Bare digest without a repository prefix is not a rollbackable reference.
    cfg["rollback"]["image_digest"] = "sha256:7c4ff251dd0cc4d41b88e9e148094355985dc4a39e7ac4e93e280171c4101121"
    assert "rollback_identity_missing" in _codes(cfg)


# --------------------------------------------------------------------------- #
# no secret VALUES in the config — only opaque references (part c)
# --------------------------------------------------------------------------- #
def test_literal_secret_value_is_rejected(good_cfg):
    cfg = copy.deepcopy(good_cfg)
    cfg["tenants"]["a"]["token"] = "sk-live-0123456789abcdef0123456789abcdef"
    assert "secret_value_in_config" in _codes(cfg)


def test_bare_unschemed_reference_is_rejected(good_cfg):
    cfg = copy.deepcopy(good_cfg)
    cfg["synthetic_endpoint"]["secret_ref"] = "0123456789abcdef0123456789abcdef"
    assert "secret_value_in_config" in _codes(cfg)


def test_validate_or_raise_reports_all_codes(good_cfg):
    cfg = copy.deepcopy(good_cfg)
    del cfg["tenants"]["b"]
    cfg["edge"]["external_base_url"] = "http://nope"
    with pytest.raises(sgc.StageGateConfigError) as exc:
        sgc.validate_or_raise(cfg)
    assert "tenant_b_missing" in exc.value.codes
    assert "external_base_url_not_https" in exc.value.codes
