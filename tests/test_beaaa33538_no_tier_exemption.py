"""BEAAA-33538: no tier is exempt from D2.

``row_blocks_scoring_activation`` used to early-return ``False`` for any row
carrying ``dev_scoring_active: True``, so neither D2 predicate was ever
evaluated against a dev-tier row. BEAAA-33507 (PR #569) put the rank-2
ledger's P3.4 mapping and its target-measured evidence onto those rows, so
they clear D2 on their own fields and the exemption stopped being
load-bearing. These tests fail if any future exemption re-introduces an
unconditional bypass of D2 for a tier.
"""
import importlib

import tools.active_test_registry as registry
from tools.founder_dev_mode import FOUNDER_DEV_MODE_ENV

# The only two fields D2 is allowed to read. Anything else flipping the verdict
# is a tier exemption by another name.
D2_FIELDS = frozenset({"candidate_rubric_mapping_status", "execution_evidenced"})

BLOCKING_ROW = {
    "candidate_rubric_mapping_status": registry.UNMAPPED_CANDIDATE_RUBRIC_STATUS,
    "execution_evidenced": False,
}


def _rows(monkeypatch, flag):
    monkeypatch.delenv(FOUNDER_DEV_MODE_ENV, raising=False)
    if flag is not None:
        monkeypatch.setenv(FOUNDER_DEV_MODE_ENV, flag)
    importlib.reload(registry)
    return registry.build_registry(include_content_candidates=True)


def test_dev_tier_row_is_not_exempt():
    """The deleted carve-out, stated as an assertion."""
    row = {**BLOCKING_ROW, "dev_scoring_active": True}
    assert registry.row_blocks_scoring_activation(row) is True
    row = {**BLOCKING_ROW, "dev_scoring_active": True, "attestation_tier": registry.ATTESTATION_FOUNDER_DEV}
    assert registry.row_blocks_scoring_activation(row) is True


def test_no_field_observed_in_the_registry_can_unblock_a_d2_blocked_row(monkeypatch):
    """Generic pin: replay every field/value the real registry carries.

    A blocked row stays blocked no matter which non-D2 field is set to which
    real value, so a new ``if row.get("<tier>"): return False`` cannot pass.
    """
    rows = _rows(monkeypatch, "on")
    assert rows, "empty registry proves nothing"
    seen: dict[str, list] = {}
    for row in rows:
        for key, value in row.items():
            if key in D2_FIELDS:
                continue
            values = seen.setdefault(key, [])
            if not any(existing == value for existing in values):
                values.append(value)
    assert "dev_scoring_active" in seen, "no dev-tier rows in the registry to replay"
    for key, values in seen.items():
        for value in values:
            probe = {**BLOCKING_ROW, key: value}
            assert registry.row_blocks_scoring_activation(probe) is True, (
                f"{key}={value!r} bypasses D2"
            )


def _d2_violations(rows):
    return [
        row
        for row in rows
        if row.get("scoring_active") is True
        and (
            row.get("candidate_rubric_mapping_status")
            == registry.UNMAPPED_CANDIDATE_RUBRIC_STATUS
            or row.get("execution_evidenced") is not True
        )
    ]


def test_no_scoring_active_row_violates_d2_with_the_flag_on(monkeypatch):
    violations = _d2_violations(_rows(monkeypatch, "on"))
    assert violations == [], f"{len(violations)} scoring-active rows fail D2"


def test_no_scoring_active_row_violates_d2_with_the_flag_off(monkeypatch):
    violations = _d2_violations(_rows(monkeypatch, None))
    assert violations == [], f"{len(violations)} scoring-active rows fail D2"
