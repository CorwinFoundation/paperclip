import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from tools.wave0_case_inventory import (
    FAMILY_PATHS,
    ROOT,
    SOURCE_PIN_DIR,
    SOURCE_REVISION,
    family_drift,
    load_inventory,
    mapping_rows,
    pin_manifest,
    pinned_bytes,
    scenario_rows,
    verify,
    verify_signed_artifact,
    verify_source_pin,
    write_jsonl,
)


def test_canonical_wave0_inventory() -> None:
    inventory = ROOT / "artifacts/beaaa-15688/wave0-case-inventory.jsonl"
    reconciliation = json.loads(
        (ROOT / "artifacts/beaaa-15688/wave0-reconciliation.json").read_text(
            encoding="utf-8"
        )
    )
    assert load_inventory(inventory) == list(mapping_rows()) + list(scenario_rows())
    assert verify(load_inventory(inventory), reconciliation) == {
        "definitions": 715,
        "unique_hashes": 715,
        "duplicate_hashes": 0,
        "stable_ids": 715,
        "required_fields_complete": 715,
        "reconciled_current_behaviors": 180,
    }


def test_inventory_io_is_utf8_and_byte_stable() -> None:
    inventory = Path("inventory.jsonl")
    row = {"stable_case_id": "wave0.legal.\u00a7"}
    payload = json.dumps(row, ensure_ascii=False) + "\n"

    with patch.object(Path, "read_text", return_value=payload) as read_text:
        assert load_inventory(inventory) == [row]
    read_text.assert_called_once_with(encoding="utf-8")

    with patch.object(Path, "write_text") as write_text:
        write_jsonl(inventory, [row])
    write_text.assert_called_once_with(
        json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def test_committed_inventory_matches_its_signed_manifest_hash() -> None:
    assert verify_signed_artifact() == {
        "inventory_sha256": "ac3ff731bae3801e7ef884438700690b016a6e63dcf532c4623771ecb99493fb",
        "source_revision": SOURCE_REVISION,
    }


def test_pinned_sources_match_their_recorded_hashes() -> None:
    assert verify_source_pin() == {"pinned_sources": len(FAMILY_PATHS) + 1}


def test_pinned_source_read_fails_closed_on_tamper(tmp_path: Path) -> None:
    rel = "rubric/probe_mappings/p1_1_factuality_golden.yaml"
    original = (SOURCE_PIN_DIR / rel).read_bytes()
    try:
        (SOURCE_PIN_DIR / rel).write_bytes(original + b"\n# tamper\n")
        with pytest.raises(ValueError, match="pinned sha256="):
            pinned_bytes(rel)
    finally:
        (SOURCE_PIN_DIR / rel).write_bytes(original)
    assert pinned_bytes(rel) == original


def test_source_pin_is_byte_identical_to_source_revision() -> None:
    """The vendored snapshot must be the real bytes at SOURCE_REVISION, not a re-typing."""
    if subprocess.run(
        ["git", "cat-file", "-e", f"{SOURCE_REVISION}^{{commit}}"],
        cwd=ROOT, capture_output=True,
    ).returncode:
        pytest.skip("SOURCE_REVISION not present (shallow clone)")
    for rel, entry in pin_manifest()["files"].items():
        if entry["git_blob_sha1"] is None:
            continue  # postdates SOURCE_REVISION; hash-pinned only
        blob = subprocess.run(
            ["git", "show", f"{SOURCE_REVISION}:{rel}"],
            cwd=ROOT, capture_output=True, check=True,
        ).stdout
        assert (SOURCE_PIN_DIR / rel).read_bytes() == blob, rel


def test_drift_reports_every_family_and_never_raises() -> None:
    """The pre-BEAAA-24052 derivation raised on the first count mismatch, so one
    stale family masked the next. The reporter must cover all of them."""
    report = family_drift()
    assert [row["family_id"] for row in report] == list(FAMILY_PATHS)
    assert all({"pinned_objects", "live_objects", "drifted"} <= set(row) for row in report)


def test_drift_reports_all_mismatches_not_just_the_first() -> None:
    stale = dict(FAMILY_PATHS)
    for family in ("p1_1_factuality_golden", "p1_3_consistency_golden"):
        target, rel, paths = stale[family]
        stale[family] = (target + 1000, rel, paths)
    with patch.dict("tools.wave0_case_inventory.FAMILY_PATHS", stale, clear=True):
        drifted = [row["family_id"] for row in family_drift() if row["drifted"]]
    assert {"p1_1_factuality_golden", "p1_3_consistency_golden"} <= set(drifted)


def test_live_rubric_growth_does_not_change_the_frozen_inventory() -> None:
    """BEAAA-24052 regression: the P1.1/P1.4 rosters grew in BEAAA-22121 and the
    derivation followed the live tree, so a frozen artifact went red on a change
    it does not describe. Derivation must read the pin only."""
    live = yaml_probe_count("rubric/probe_mappings/p1_1_factuality_golden.yaml")
    pinned = FAMILY_PATHS["p1_1_factuality_golden"][0]
    assert live >= pinned, "roster shrank; the pin no longer describes a prefix of the live tree"
    assert sum(1 for row in mapping_rows() if row["family_id"] == "p1_1_factuality_golden") == pinned


def yaml_probe_count(rel: str) -> int:
    import yaml

    return len(yaml.safe_load((ROOT / rel).read_text(encoding="utf-8"))["probes"])
