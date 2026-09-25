"""BEAAA-35691: deleting a dev activation ledger rolls its slice back.

The standing per-slice rollback on the dev-scoring lane is "restore
``reported_not_scored``, remove dev-score family membership" -- in practice,
delete the activation ledger. Both loaders used to read their ledger through
``_load_evidence_json`` at ``required=True``, so deleting one did not roll the
slice back: it raised ``MissingReleaseEvidenceError`` out of every
``build_registry()`` call and took down every consumer with it. Both digest
docstrings asserted the opposite ("A missing or unreadable ledger promotes
nothing"), so the rollback was documented but never true.

Absent and corrupt stay distinct. An absent ledger promotes nothing, because
that is the rollback. A ledger that is present but unparseable still raises,
because a corrupt activation record is ambiguous rather than absent, and
silently scoring nothing would hide it.
"""
import importlib
import json
from pathlib import Path

import pytest

import tools.active_test_registry as registry
from tools.founder_dev_mode import FOUNDER_DEV_MODE_ENV

ROOT = Path(__file__).resolve().parents[1]

# (constant name on the registry module, ledger path relative to repo root)
LEDGERS = [
    ("BEAAA35380_LEDGER_RELPATH", "artifacts/beaaa-35380/tool-misuse-dev-activation-ledger.json"),
    ("BEAAA31092_LEDGER_RELPATH", "artifacts/beaaa-31092/rank2-ledger.json"),
]


def _ledger_digests(relpath: str) -> tuple[frozenset[str], frozenset[str]]:
    """(digests the ledger promotes, digests it merely describes).

    Only ``disposition == "scored"`` rows are promoted; both loaders filter on
    it. The rest matter here as a control: the BEAAA-31092 ledger carries 187
    rows, and 38 of the 144 it does not promote are scoring-active for reasons
    that have nothing to do with this ledger. A rollback must leave those alone.
    """
    payload = json.loads((ROOT / relpath).read_text(encoding="utf-8"))
    promoted, described = set(), set()
    for row in payload["rows"]:
        target = promoted if row.get("disposition") == "scored" else described
        target.add(str(row["dedupe_sha256"]))
    return frozenset(promoted), frozenset(described - promoted)


def _reload(monkeypatch, flag="on"):
    """Reload the registry under the dev flag, resetting its lru_caches.

    Kept separate from _build so a test can reload once and then refer to
    registry.MissingReleaseEvidenceError: reloading rebinds the class, so a
    pytest.raises captured before the reload would not match the class the
    reloaded module raises.
    """
    monkeypatch.delenv(FOUNDER_DEV_MODE_ENV, raising=False)
    if flag is not None:
        monkeypatch.setenv(FOUNDER_DEV_MODE_ENV, flag)
    importlib.reload(registry)


def _build(monkeypatch, *, const=None, value=None, flag="on"):
    """Build the registry under the dev flag, optionally relocating a ledger.

    The constants are absolute-path friendly: the loaders join them onto the
    repo root, and joining an absolute path replaces the root, so a temp path
    stands in for a deleted ledger without touching artifacts/.
    """
    _reload(monkeypatch, flag)
    if const is not None:
        monkeypatch.setattr(registry, const, value)
    return registry.build_registry(include_content_candidates=True)


@pytest.mark.parametrize("const,relpath", LEDGERS, ids=[c.split("_")[0] for c, _ in LEDGERS])
def test_absent_ledger_rolls_rows_back_instead_of_failing_the_build(
    monkeypatch, tmp_path, const, relpath
):
    promoted, described = _ledger_digests(relpath)
    assert promoted, "fixture guard: ledger should promote rows"

    before = {
        str(r.get("candidate_dedupe_sha256") or ""): r["reporting_status"]
        for r in _build(monkeypatch, flag="on")
    }
    assert {before[d] for d in promoted} == {"scoring_active"}, (
        "fixture guard: with the ledger in place these rows are promoted"
    )

    rows = _build(monkeypatch, const=const, value=str(tmp_path / "deleted.json"))
    after = {str(r.get("candidate_dedupe_sha256") or ""): r for r in rows}

    mine = [after[d] for d in promoted if d in after]
    assert len(mine) == len(promoted), "rows stay registered, they just stop scoring"
    assert {r["reporting_status"] for r in mine} == {"reported_not_scored"}
    assert {bool(r.get("scoring_active")) for r in mine} == {False}
    assert {bool(r.get("dev_scoring_active")) for r in mine} == {False}

    # Scoped: rows this ledger does not promote are untouched by its removal.
    assert all(
        after[d]["reporting_status"] == before[d] for d in described if d in after
    ), "removing the ledger changed a row it never promoted"


@pytest.mark.parametrize("const,relpath", LEDGERS, ids=[c.split("_")[0] for c, _ in LEDGERS])
def test_absent_ledger_leaves_the_production_count_unchanged(
    monkeypatch, tmp_path, const, relpath
):
    """These are dev-tier rows, so production is unchanged with or without them.

    Compared against the same build with the ledger in place rather than a
    frozen literal, so the invariant is "the rollback moves nothing" and not a
    count that drifts with unrelated rows.
    """
    with_ledger = registry.count_active_executable(_build(monkeypatch, flag="on"))
    without_ledger = registry.count_active_executable(
        _build(monkeypatch, const=const, value=str(tmp_path / "deleted.json"))
    )
    assert without_ledger == with_ledger


@pytest.mark.parametrize("const,relpath", LEDGERS, ids=[c.split("_")[0] for c, _ in LEDGERS])
def test_corrupt_ledger_still_raises(monkeypatch, tmp_path, const, relpath):
    corrupt = tmp_path / "corrupt.json"
    corrupt.write_text("{not json", encoding="utf-8")
    _reload(monkeypatch)
    monkeypatch.setattr(registry, const, str(corrupt))
    with pytest.raises(registry.MissingReleaseEvidenceError):
        registry.build_registry(include_content_candidates=True)
