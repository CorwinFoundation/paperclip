"""Adapts the Full Scan conversation bank to the shared variable-form engine.

Strata are (family_id, rubric_point_id) pairs under one rubric version. Each
stratum's quota comes from the versioned coverage policy below and never from
the exposure-history setting. Each scenario is an existing test
specification (test_spec_id == case_id); a form never adds unique tests.
Payloads stay private in the engine's store; callers export report_summary only.
"""
from __future__ import annotations

import dataclasses
from collections import defaultdict
from typing import Any, Iterable, Mapping, Sequence

from scanners.full_scan_conversation_en import CASES as CASES_S1
from scanners.full_scan_conversation_en_s2 import CASES_S2
from scanners.full_scan_conversation_en_s3 import CASES_S3
from scanners.full_scan_conversation_en_s4 import CASES_S4
from scanners.full_scan_conversation_en_s5 import CASES_S5
from scanners.variable_test_forms import FormStore, Scenario, Stratum, target_input_hash

PRODUCT = "full"
RUBRIC_VERSION = "v1"
# Conversation cases carry no difficulty/severity grading yet; the engine
# requires nonempty values, so the stratum records that honestly.
UNRATED = "unrated"

# Measurement policy, version 1. Keep this independent of bank size and
# history_window: changing privacy history must not silently change coverage.
# 64 leaves at least 32 spare candidates per current stratum after two
# disjoint forms (the smallest strata contain 160 candidates).
FULL_SCAN_FORM_BUDGET_VERSION = "full-scan-form-budget/v1"
FULL_SCAN_FORM_BUDGET_V1: dict[str, int] = {
    "authentication|P1.4|v1": 64,
    "conduct|P3.5|v1": 64,
    "data_leak|P3.3|v1": 64,
    "fairness_pair|P2.5|v1": 64,
    "grounded_policy|P1.1|v1": 64,
    "hallucinated_action|P3.1|v1": 64,
    "over_refusal|P1.4|v1": 64,
    "prompt_injection_direct|P3.2|v1": 64,
    "prompt_injection_indirect|P3.2|v1": 64,
    "system_prompt_leak|P3.3|v1": 64,
    "tool_misuse|P3.4|v1": 64,
}


def all_conv_en_cases() -> tuple[Any, ...]:
    return CASES_S1 + CASES_S2 + CASES_S3 + CASES_S4 + CASES_S5


def _stratum_id(case: Any) -> str:
    return f"{case.family_id}|{case.rubric_point_id}|{RUBRIC_VERSION}"


def _unique(cases: Iterable[Any]) -> list[Any]:
    seen: set[str] = set()
    out = []
    for case in cases:
        if case.case_id not in seen:
            seen.add(case.case_id)
            out.append(case)
    return out


def scenarios_from_cases(cases: Iterable[Any]) -> list[Scenario]:
    out = []
    for case in _unique(cases):
        payload = {k: v for k, v in dataclasses.asdict(case).items()
                   if k not in {"case_id", "calibration_status", "provenance"}}
        out.append(Scenario(
            scenario_id=case.case_id,
            stratum_id=_stratum_id(case),
            payload=payload,
            calibration_status=case.calibration_status,
            test_spec_id=case.case_id,
        ))
    return out


def strata_from_cases(cases: Iterable[Any], *, history_window: int = 1) -> list[Stratum]:
    supply: dict[str, list[Any]] = defaultdict(list)
    for case in _unique(cases):
        supply[_stratum_id(case)].append(case)
    out = []
    for stratum_id in sorted(FULL_SCAN_FORM_BUDGET_V1):
        family_id, rubric_id, rubric_version = stratum_id.split("|", 2)
        out.append(Stratum(
            stratum_id=stratum_id,
            family_id=family_id,
            rubric_id=rubric_id,
            rubric_version=rubric_version,
            difficulty=UNRATED,
            severity=UNRATED,
            count=FULL_SCAN_FORM_BUDGET_V1[stratum_id],
        ))
    return out


def create_full_scan_form(
    form_store: FormStore,
    *,
    scope: tuple[str, str],
    bank_version: str,
    history_window: int = 1,
    require_calibrated: bool = False,
    form_id: str | None = None,
    cases: Sequence[Any] | None = None,
) -> dict[str, Any]:
    bank = tuple(cases) if cases is not None else all_conv_en_cases()
    return form_store.create(
        scope=scope,
        product=PRODUCT,
        bank_version=bank_version,
        strata=strata_from_cases(bank),
        scenarios=scenarios_from_cases(bank),
        history_window=history_window,
        require_calibrated=require_calibrated,
        form_id=form_id,
    )


def form_test_spec_ids(form: Mapping[str, Any]) -> frozenset[str]:
    return frozenset(row["test_spec_id"] for row in form["cases"])


def assert_form_payloads_current(
    form: Mapping[str, Any], cases: Sequence[Any] | None = None
) -> None:
    """Fail when a private payload no longer matches its reserved wire hash.

    ``cases`` is retained for caller compatibility but intentionally ignored.
    A saved form is the replay source of truth: later bank edits or removals
    must not alter an already-reserved run.
    """
    del cases
    drifted = sorted(
        str(row.get("test_spec_id") or "")
        for row in form["cases"]
        if not isinstance(row.get("payload"), Mapping)
        or row.get("input_hash") != target_input_hash(row["payload"])
    )
    if drifted:
        raise ValueError(
            "reserved form payload_hash_mismatch=" + repr(drifted)
        )


def _scenario_input_hash(scenario: Scenario) -> str:
    """Use the shared variable-form wire-hash definition."""
    return target_input_hash(scenario.payload)
