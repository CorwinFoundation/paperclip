from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tools.active_test_registry import build_registry
from tools.release_registry_marker import (
    _normalized_registry_bytes,
    build_release_registry_marker,
)


REPO = Path(__file__).resolve().parents[1]
# Re-pinned for BEAAA-31092: the 67 evidenced rank-2 rows opt in to development-mode
# scoring through `attestation_tier=founder_dev_mode` + `dev_scoring_active`, which
# changes those rows' normalized bytes. The row census does not move -- counts stay
# 2,353 / 2,353 / 2,352 and the exclusion proof stays at 137 rows -- so only the
# digest is re-pinned. Previous pin ca712717...69bf8 came from BEAAA-31091 (ii-015
# indirect-injection fixture prompt).
EXPECTED_NORMALIZED_REGISTRY_SHA256 = (
    # Rehashed after BEAAA-33568 re-admitted approval_freshness_replay's 22 rows
    # to the rank-2 ledger's scored set, which flips those rows to the dev-scored
    # mapping and evidence fields and so changes their normalized bytes. Prior pin
    # fcde4065...97bef came from the BEAAA-33507 dev-tier mapping rewrite.
    #
    # Re-pinned again for BEAAA-33810: the agent-escape incident tranche adds 8
    # executable rows (OOSC-INC-01..08) to out_of_scope_containment, so unlike the
    # re-pins above this one DOES move the census -- 2,353 / 2,353 / 2,352 becomes
    # 2,361 / 2,361 / 2,360, all asserted below. The digest is not the proof that
    # only the tranche moved; the row-level diff is. Regenerating the registry from
    # a clean origin/master (213f7133) reproduces the committed artifact with 0 rows
    # added and 0 removed, and the branch adds exactly 11 rows over that baseline:
    # the 8 executable cases plus 3 fixture_test_only fixture files. Prior pin
    # 774b614b...e715f came from BEAAA-33568.
    "b4b6e143783185d591099ca9b04a8f3cc9a3b876d692c00066f989aa7e686331"
)


def test_marker_derives_counts_and_hash_from_same_generated_registry() -> None:
    rows = build_registry(REPO / "rubric" / "probe_mappings")
    marker = build_release_registry_marker(REPO)

    # Exact combined-tree measurement after the Day-1 supporting-family
    # registration. The new rows remain reported-not-scored.
    assert marker["published_executable_count"] == 2_361
    assert marker["generated_executable_count"] == 2_361
    assert marker["distinct_runtime_count"] == 2_360
    assert marker["normalized_registry_sha256"] == hashlib.sha256(
        _normalized_registry_bytes(rows)
    ).hexdigest()
    assert marker["normalized_registry_sha256"] == EXPECTED_NORMALIZED_REGISTRY_SHA256
    assert all("\\" not in str(row["source_pack"]) for row in rows)
    # The 64 original direct-family rows and the 32 new supporting rows are
    # deliberately report-only and appear in the excluded-row proof. BEAAA-29742
    # gated 192 rows as unbound and the proof grew by 185, so seven of them were
    # already carried here under another disposition. Binding the 40 cyber misuse and
    # 60 harmful-output rows and 60 counterfactual-stability rows remove them from
    # this exclusion proof. The pii_attestation.evidence_pack row is now bound
    # through the production adapter. Binding the two demographic-parity rows
    # reduces the exclusion proof by two more.
    assert len(marker["excluded_rows"]) == 137
    assert {row["disposition"] for row in marker["excluded_rows"]} == {
        "incompatible",
        "reported_not_scored",
    }


def test_marker_fails_closed_if_published_count_drifts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("tools.release_registry_marker.load_published_count", lambda _path: 2_211)
    with pytest.raises(ValueError, match="generated executable count does not match"):
        build_release_registry_marker(REPO)


def test_rendered_manifest_embeds_exact_marker(tmp_path: Path) -> None:
    output = tmp_path / "release.json"
    from scripts.render_release_manifest import render

    render(
        REPO / "release" / "backbond-scanner-release.json",
        output,
        "a" * 40,
    )
    manifest = json.loads(output.read_text(encoding="utf-8"))
    assert manifest["build_sha"] == "a" * 40
    assert manifest["scanner_version"] == "0.5.0"
    assert manifest["corpus_version"] == "0.2.0"
    assert manifest["registry_marker"] == build_release_registry_marker(REPO)
