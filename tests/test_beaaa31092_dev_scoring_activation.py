"""BEAAA-31092: the rank-2 slice is dev-scored only, and only where the ledger says."""
import importlib
import json
from pathlib import Path

import tools.active_test_registry as registry
from tools.founder_dev_mode import FOUNDER_DEV_MODE_ENV

ROOT = Path(__file__).resolve().parents[1]
LEDGER = json.loads((ROOT / "artifacts/beaaa-31092/rank2-ledger.json").read_text(encoding="utf-8"))
SCORED = {row["dedupe_sha256"] for row in LEDGER["rows"] if row["disposition"] == "scored"}
# BEAAA-35380 activates 23 TOOL_MISUSE rows on its own ledger and its own N=5
# evidence. Some of them sit in this roster as held rows. They are named here
# from that ledger rather than waved through by a predicate, so any OTHER held
# row that escapes this slice's hold still fails the assertion below.
BEAAA35380 = json.loads(
    (ROOT / "artifacts/beaaa-35380/tool-misuse-dev-activation-ledger.json").read_text(encoding="utf-8")
)
BEAAA35380_SCORED = {
    row["dedupe_sha256"] for row in BEAAA35380["rows"] if row["disposition"] == "scored"
}


def _rows(monkeypatch, flag):
    monkeypatch.delenv(FOUNDER_DEV_MODE_ENV, raising=False)
    if flag is not None:
        monkeypatch.setenv(FOUNDER_DEV_MODE_ENV, flag)
    importlib.reload(registry)
    return registry.build_registry(include_content_candidates=True)


def test_ledger_scored_set_is_the_only_promotion_source():
    assert SCORED, "the ledger promotes nothing, so the registry must promote nothing"
    assert registry._beaaa31092_dev_scored_digests() == frozenset(SCORED)
    assert LEDGER["underwriting_active"] is False
    assert LEDGER["priced_score_weight"] == 0.0


def test_flag_off_leaves_every_rank_2_row_reported_not_scored(monkeypatch):
    rows = _rows(monkeypatch, None)
    promoted = [row for row in rows if row.get("candidate_dedupe_sha256") in SCORED]
    assert len(promoted) == len(SCORED)
    assert {row["scoring_active"] for row in promoted} == {False}
    assert {row["reporting_status"] for row in promoted} == {"reported_not_scored"}


def test_flag_on_promotes_exactly_the_ledger_rows(monkeypatch):
    rows = _rows(monkeypatch, "on")
    # Scope the assertion to this roster: other families are dev-promoted through
    # FOUNDER_DEV_SCORED_FAMILIES and are none of this slice's business.
    roster = {row["dedupe_sha256"] for row in LEDGER["rows"]}
    mine = [row for row in rows if row.get("candidate_dedupe_sha256") in roster]
    assert len(mine) == len(roster)
    # The dev promotion this issue owns is exactly the ledger's scored set. Other
    # rank-2 rows are already scoring_active as zero_weight_observational under
    # production_signed attestation; that predates this change and is not ours to
    # move, so the invariant is stated over the dev tier, not over scoring_active.
    dev = {row["candidate_dedupe_sha256"] for row in mine
           if row.get("dev_scoring_active") is True}
    # this slice's dev set is exactly its own ledger, plus the rows BEAAA-35380
    # names on its ledger - nothing else may appear
    assert dev - BEAAA35380_SCORED == frozenset(SCORED)
    assert {row["attestation_tier"] for row in mine
            if row["candidate_dedupe_sha256"] in SCORED} == {"founder_dev_mode"}
    assert all(row["scoring_active"] is True for row in mine
               if row["candidate_dedupe_sha256"] in SCORED)
    # every held row stays out of the dev promotion - what the tier gate buys -
    # except the ones BEAAA-35380 names on its own ledger with its own evidence
    assert not ((roster - SCORED) - BEAAA35380_SCORED) & dev


def test_active_executable_count_is_identical_with_the_flag_off(monkeypatch):
    rows = _rows(monkeypatch, None)
    assert registry.count_active_executable(rows) == 2373
