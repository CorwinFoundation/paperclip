"""BEAAA-29793: promoting the production reason to data changes no outcome.

The CTO ruling attached a condition to touching
``tools.registered_case_executor._resolve_applicability``: unlike ``run_family``
it feeds the production scored set, so the structured-reason promotion must
leave every branch outcome byte-identical. Any flipped ``applicable`` boolean is
a calibration delta and takes an Underwriter co-sign before merge.

This file asserts the condition directly rather than by inspection. It carries a
verbatim copy of the pre-promotion implementation as a reference oracle and
sweeps it against the live resolver over every manifest shape the branch
structure distinguishes, for a gated family and an unconditional one. The
expected count of rows whose ``applicable`` boolean changes is zero.

The reason strings are asserted byte-identical too. They are a rendering now, but
they are also carried into N/A evidence, so drift there would be a silent change
to a production artifact.
"""

from typing import Any, Mapping

import pytest

from evals.aiuc_path_b import manifest as path_b_manifest
from tools import registered_case_executor as rce


def _legacy_resolve(test_id: str, registry_row: Mapping[str, Any]) -> tuple[bool, str]:
    """Verbatim pre-BEAAA-29793 body of ``rce._resolve_applicability``."""
    flag = rce._declared_applicability_flag(test_id)
    if flag is None:
        return True, "always"
    capability = registry_row.get("capability_manifest")
    if capability is None:
        return False, f"{flag}=absent_capability_manifest"
    if not isinstance(capability, Mapping):
        return False, f"{flag}=malformed_capability_manifest"
    declared = capability.get(flag)
    if declared is True:
        return True, f"{flag}=True"
    return False, f"{flag}={declared!r}"


def _gated_test_ids() -> list[str]:
    """Every gated family, not just the first one sorted.

    BEAAA-29793: this previously returned the first gated family and paired it with
    a hardcoded FLAG. That held only while mcp_tool_integrity was the sole gated
    family. Once PR #308 (aeaed3b2) gated human_escalation_flagging and
    customer_defined_risk_pack, the first sorted family became
    customer_defined_risk_pack, gated on a different flag — so every "declared_*"
    manifest below was keyed to a flag the chosen family does not read. The rows
    still passed, because they degenerated into the flag-missing case. They were
    not testing what they named.
    """
    out = [
        name
        for name, module in sorted(path_b_manifest.FAMILY_MODULES.items())
        if module.SPEC.applicability_flag is not None
    ]
    if not out:
        pytest.skip("no gated family in this tree")
    return out


GATED_TEST_IDS = _gated_test_ids()


def _manifest_shapes(flag: str) -> list[tuple[dict, str]]:
    """The eleven shapes the resolver's branch structure distinguishes, per flag."""
    return [
        ({}, "row_without_capability_manifest_key"),
        ({"capability_manifest": None}, "null_manifest"),
        ({"capability_manifest": []}, "malformed_list"),
        ({"capability_manifest": "yes"}, "malformed_str"),
        ({"capability_manifest": {}}, "empty_manifest"),
        ({"capability_manifest": {"has_tool_calling": True}}, "live_producer"),
        ({"capability_manifest": {flag: True}}, "declared_true"),
        ({"capability_manifest": {flag: False}}, "declared_false"),
        ({"capability_manifest": {flag: None}}, "declared_null"),
        ({"capability_manifest": {flag: "yes"}}, "declared_non_boolean"),
        ({"capability_manifest": {flag: 1}}, "declared_truthy_int"),
    ]


def _all_rows() -> list:
    """Cross-product of every gated family with every manifest shape."""
    params = []
    for test_id in GATED_TEST_IDS:
        flag = rce._declared_applicability_flag(test_id)
        assert flag is not None, f"{test_id} reported gated but declares no flag"
        for row, shape_id in _manifest_shapes(flag):
            params.append(pytest.param(test_id, row, id=f"{test_id}-{shape_id}"))
    return params


MANIFESTS = _all_rows()


@pytest.mark.parametrize("test_id,row", MANIFESTS)
def test_delta_zero_for_a_gated_family(test_id, row):
    legacy_applicable, legacy_reason = _legacy_resolve(test_id, row)
    resolution = rce._resolve_applicability(test_id, row)
    assert resolution.applicable is legacy_applicable, "applicable flipped"
    assert resolution.reason == legacy_reason, "reason string drifted"


@pytest.mark.parametrize(
    "row", [pytest.param(r, id=i) for r, i in _manifest_shapes("has_mcp_tool_integration")]
)
def test_delta_zero_for_an_unconditional_family(row):
    test_id = "definitely_not_a_path_b_family"
    legacy_applicable, legacy_reason = _legacy_resolve(test_id, row)
    resolution = rce._resolve_applicability(test_id, row)
    assert resolution.applicable is legacy_applicable
    assert resolution.reason == legacy_reason


def test_delta_zero_row_count_is_explicitly_zero():
    """The assertion the ruling asked for, stated as a count."""
    flips = 0
    rows = 0
    for param in MANIFESTS:
        test_id, row = param.values
        rows += 1
        if rce._resolve_applicability(test_id, row).applicable is not _legacy_resolve(test_id, row)[0]:
            flips += 1
    # Stated as a count, per the ruling. Row count is asserted too: a sweep that
    # silently shrank to zero rows would also report zero flips.
    assert rows == len(GATED_TEST_IDS) * 11
    assert flips == 0


def test_policy_default_na_is_never_an_attestation():
    """The distinction the promotion exists to make, unavailable from the string.

    An absent manifest and a declared decline both render as N/A and both used to
    be indistinguishable downstream. The registry's fail-closed default is nobody's
    claim; a decline is the target's. Rule C's attested/unattested split reads this.
    """
    for test_id in GATED_TEST_IDS:
        flag = rce._declared_applicability_flag(test_id)
        absent = rce._resolve_applicability(test_id, {"capability_manifest": None})
        declined = rce._resolve_applicability(test_id, {"capability_manifest": {flag: False}})
        assert absent.applicable is declined.applicable is False
        assert absent.source == "policy_default" and absent.attested is False, test_id
        assert declined.source == "target_manifest" and declined.attested is True, test_id
