#!/usr/bin/env python3
"""BEAAA-35661: the construction-integrity predicate, wired into promotion.

``tools/construction-audit/audit_construction_integrity.py`` decides, per row,
whether the text that actually reaches the target carries the announce/narrate
construction BEAAA-35020 retired. Until now nothing invoked it except a person
choosing to. This module turns that verdict into a promotion bar, so a slice
cannot be activated while any row in it flags.

Fail closed on both failure shapes:

* ``flagged``      - the construction trips a signature. Never promotable, and
                     no roster, tier or override exempts it.
* ``indeterminate`` - the construction could not be read. Not promotable either:
                     an unread row is an unmeasured row, not a clean one.
* unaudited        - a row absent from the dispositions file has never been put
                     to the predicate at all, which is the same thing as
                     indeterminate and is barred for the same reason.

``judge_channel_hits`` is deliberately not an input. The audit tags every
extracted literal with the channel it is passed to, and a signature match on a
``judge`` literal is recorded and reported but never blocks: a grader that names
its own rubric is the grader knowing its rubric, not the subject being told what
is being tested. The exclusion is keyed on the channel, never on any particular
prompt text, so the day a judge string starts reaching ``subject.complete()``
its literal is tagged ``target`` and this gate refuses it with no change here.

The one concession to history is ``unreadable_construction_roster.v1.json``: the
exact production-scored rows that were already carrying weight when this gate
landed and whose construction is unreadable by design - upstream framework code,
an unvendored benchmark corpus, or a row with no model interaction at all. Each
carries a recorded reason. That roster is frozen at a commit and pinned to an
exact membership by test, so it can bar the past without licensing the future.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

REPO = Path(__file__).resolve().parents[1]
DISPOSITIONS_PATH = REPO / "artifacts" / "beaaa-35661" / "construction_dispositions.json"
ROSTER_PATH = (
    REPO / "tools" / "construction-audit" / "unreadable_construction_roster.v1.json"
)

CLEAR = "clear"
FLAGGED = "flagged"
INDETERMINATE = "indeterminate"
UNAUDITED = "unaudited"

#: Verdicts that permit a row to carry production weight.
PROMOTABLE_VERDICTS = frozenset({CLEAR})
#: Verdicts the legacy roster may excuse. ``flagged`` is absent on purpose.
ROSTER_EXCUSABLE_VERDICTS = frozenset({INDETERMINATE, UNAUDITED})


class ConstructionGateError(RuntimeError):
    """The gate's own inputs are missing or malformed."""


def _load(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        # Fail closed loudly. A gate that silently degrades to "everything is
        # clear" when its evidence goes missing is worse than no gate.
        raise ConstructionGateError(f"cannot read construction gate input: {path}") from exc
    if not isinstance(data, dict):
        raise ConstructionGateError(f"construction gate input is not an object: {path}")
    return data


def construction_key(row: Mapping[str, Any]) -> str:
    """The identity the audit ledger is keyed by: ``case_id``, on its own.

    The first cut of this gate keyed on ``source_pack`` plus ``case_id`` on the
    assumption that ``case_id`` alone collides. Measured, it does not: the 2,507
    registry rows carry 2,507 distinct ``case_id`` values, which
    ``test_construction_key_is_unique_across_the_registry`` re-checks rather
    than assumes.

    The pair was worse than redundant, it was wrong. ``source_pack`` is a path,
    so a registry built from a relocated mapping directory - which is what
    ``build_registry(tmp_path)`` does throughout the suite - produced keys that
    matched nothing, and every such row came back ``unaudited`` and was barred.
    The construction of a row is a property of the row, not of the directory the
    pack was read from, so the key must not carry a location.
    """
    return str(row.get("case_id") or "")


def load_dispositions(path: Path = DISPOSITIONS_PATH) -> dict[str, str]:
    payload = _load(path)
    dispositions = payload.get("dispositions")
    if not isinstance(dispositions, dict) or not dispositions:
        raise ConstructionGateError("construction dispositions file carries no rows")
    return {str(k): str(v) for k, v in dispositions.items()}


def load_roster(path: Path = ROSTER_PATH) -> frozenset[str]:
    payload = _load(path)
    rows = payload.get("rows")
    if not isinstance(rows, list):
        raise ConstructionGateError("unreadable-construction roster carries no rows")
    keys = frozenset(str(entry.get("key")) for entry in rows if isinstance(entry, dict))
    if len(keys) != payload.get("row_count"):
        raise ConstructionGateError("unreadable-construction roster count does not match its rows")
    return keys


def construction_verdict(
    row: Mapping[str, Any],
    dispositions: Mapping[str, str] | None = None,
) -> str:
    """The audited verdict for a row, or ``unaudited`` if it was never put."""
    table = load_dispositions() if dispositions is None else dispositions
    return table.get(construction_key(row), UNAUDITED)


def row_blocks_construction_activation(
    row: Mapping[str, Any],
    dispositions: Mapping[str, str] | None = None,
    roster: frozenset[str] | None = None,
) -> bool:
    """Whether a row is barred from carrying weight on construction grounds."""
    verdict = construction_verdict(row, dispositions)
    if verdict in PROMOTABLE_VERDICTS:
        return False
    if verdict in ROSTER_EXCUSABLE_VERDICTS:
        table = load_roster() if roster is None else roster
        return construction_key(row) not in table
    # flagged, or any verdict this gate does not recognise.
    return True


def refusals(
    rows: list[Mapping[str, Any]],
    dispositions: Mapping[str, str] | None = None,
    roster: frozenset[str] | None = None,
) -> list[dict[str, str]]:
    """Every scoring-active row this gate refuses, with its verdict. Slice-level."""
    table = load_dispositions() if dispositions is None else dispositions
    seat = load_roster() if roster is None else roster
    out = []
    for row in rows:
        if row.get("scoring_active") is not True:
            continue
        if row_blocks_construction_activation(row, table, seat):
            out.append({
                "key": construction_key(row),
                "verdict": construction_verdict(row, table),
                "case_id": str(row.get("case_id") or ""),
            })
    return out
