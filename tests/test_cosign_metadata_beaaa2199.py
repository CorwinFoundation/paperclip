"""BEAAA-2199 — `cosign_metadata` block at the contract output surface.

Coverage:
  - `cosign_metadata` is present as a top-level key on the
    `to_contract_output(...)` / `run_scan(...)` dict on the G1 golden
    fixture.
  - Block carries the required top-level keys per
    `contracts/are-scanner-api-v1.md@v1.6.0 §cosign_metadata`.
  - `cosign_metadata.scanner_version` / `contract_version` /
    `rubric_version` / `applicability_policy_version` mirror the live
    same-named response fields (block-level invariant — drift means the
    manifest is stale and the build MUST NOT ship).
  - `rule_changes[]` is present, is a list, every entry resolves the
    auditor-readable fields (`ticket`, `classification`,
    `cto_cosign_ref`, plus `adr_ref` OR a documented
    `founder_required_rationale`).
  - `build_cosign_metadata(...)` direct unit assertions on the field
    set, so a future field rename / removal is caught even without a
    full end-to-end CLI run.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from harness.cli_runner import run_scan  # noqa: E402
from harness.cosign_manifest import (  # noqa: E402
    SCANNER_COSIGN_MANIFEST,
    build_cosign_metadata,
)

G1_DIR = REPO / "tests" / "fixtures" / "golden_agents" / "G1"
G1_EVAL = G1_DIR / "eval_outputs.json"
RUBRIC_PATH = REPO / "rubric" / "rubric.yaml"
FIXED_SCORED_AT = datetime(2026, 5, 17, 12, 0, 0, tzinfo=timezone.utc)


def _materialize_g1(tmp: Path) -> Path:
    """Copy G1 eval_outputs.json → eval_results.json in a tempdir.

    Mirrors `tests/test_cli_runner_beaaa464.py::_g1_dir` setup so the
    CLI runner finds its expected `eval_results.json` filename.
    """
    d = tmp / "G1"
    d.mkdir(parents=True)
    (d / "eval_results.json").write_text(G1_EVAL.read_text())
    return d


REQUIRED_BLOCK_KEYS = frozenset({
    "auditor_readable",
    "block_version",
    "block_ticket_ref",
    "scanner_version",
    "contract_version",
    "rubric_version",
    "applicability_policy_version",
    "rule_changes",
})


REQUIRED_RULE_CHANGE_KEYS = frozenset({
    "ticket",
    "title",
    "classification",
    "classification_table_row",
    "rubric_point_ids",
    "scanner_version_at_activation",
    "cto_cosign_ref",
    "head_of_uw_cosign_ref",
    "founder_cosign_ref",
    "founder_required",
    "implementation_tickets",
})


class BuildCosignMetadataUnitTests(unittest.TestCase):
    """Direct factory-function unit tests — fast, no CLI scoring path."""

    def test_field_set_matches_contract(self) -> None:
        block = build_cosign_metadata(
            scanner_version="0.3.9",
            contract_version="are-scanner-api-v1@v1.6.0",
            rubric_version="1.0.1",
            applicability_policy_version="0.1.1",
        )
        self.assertEqual(set(block.keys()), set(REQUIRED_BLOCK_KEYS))

    def test_top_level_field_values_mirror_kwargs(self) -> None:
        block = build_cosign_metadata(
            scanner_version="0.3.9",
            contract_version="are-scanner-api-v1@v1.6.0",
            rubric_version="1.0.1",
            applicability_policy_version="0.1.1",
        )
        self.assertTrue(block["auditor_readable"])
        self.assertEqual(block["block_version"], "0.1.0")
        self.assertEqual(block["scanner_version"], "0.3.9")
        self.assertEqual(block["contract_version"], "are-scanner-api-v1@v1.6.0")
        self.assertEqual(block["rubric_version"], "1.0.1")
        self.assertEqual(block["applicability_policy_version"], "0.1.1")

    def test_rule_changes_is_independent_copy(self) -> None:
        block = build_cosign_metadata(
            scanner_version="0.3.9",
            contract_version="are-scanner-api-v1@v1.6.0",
            rubric_version="1.0.1",
            applicability_policy_version="0.1.1",
        )
        self.assertIsInstance(block["rule_changes"], list)
        self.assertGreater(len(block["rule_changes"]), 0)
        # Mutating the response MUST NOT propagate back into the static
        # SCANNER_COSIGN_MANIFEST snapshot (tamper-resistance of source).
        block["rule_changes"][0]["ticket"] = "MUTATED"
        self.assertNotEqual(SCANNER_COSIGN_MANIFEST[0]["ticket"], "MUTATED")

    def test_each_rule_change_carries_auditor_fields(self) -> None:
        block = build_cosign_metadata(
            scanner_version="0.3.9",
            contract_version="are-scanner-api-v1@v1.6.0",
            rubric_version="1.0.1",
            applicability_policy_version="0.1.1",
        )
        for entry in block["rule_changes"]:
            missing = REQUIRED_RULE_CHANGE_KEYS - set(entry.keys())
            self.assertEqual(
                missing,
                set(),
                f"rule_changes entry for {entry.get('ticket')} missing keys: {missing}",
            )
            self.assertIn(entry["classification"], {"rule-change", "rule-execution"})
            self.assertIsNotNone(
                entry["cto_cosign_ref"],
                f"rule_changes entry for {entry.get('ticket')} has null cto_cosign_ref",
            )
            # adr_ref OR documented exemption — see ADR-BEAAA-2199.
            has_adr = bool(entry.get("adr_ref"))
            has_exemption = bool(entry.get("founder_required_rationale"))
            self.assertTrue(
                has_adr or has_exemption,
                f"rule_changes entry for {entry.get('ticket')} has neither adr_ref nor founder_required_rationale",
            )


class CosignMetadataInContractOutputTests(unittest.TestCase):
    """End-to-end CLI scoring path — `to_contract_output` must include the block."""

    def _run_g1(self, tmp: Path) -> dict:
        input_dir = _materialize_g1(tmp)
        return run_scan(
            agent_id="G1",
            input_dir=input_dir,
            seed=42,
            corpus_version="0.1.0",
            rubric_path=RUBRIC_PATH,
            scored_at=FIXED_SCORED_AT,
        )

    def test_cosign_metadata_present_at_top_level(self) -> None:
        with tempfile.TemporaryDirectory() as tmpd:
            out = self._run_g1(Path(tmpd))
        self.assertIn("cosign_metadata", out, "cosign_metadata missing from contract output")

    def test_cosign_metadata_top_level_field_set(self) -> None:
        with tempfile.TemporaryDirectory() as tmpd:
            out = self._run_g1(Path(tmpd))
        block = out["cosign_metadata"]
        missing = REQUIRED_BLOCK_KEYS - set(block.keys())
        self.assertEqual(missing, set(), f"cosign_metadata missing keys: {missing}")

    def test_block_invariant_scanner_version_matches_live(self) -> None:
        """Block-level invariant per §cosign_metadata.

        `cosign_metadata.scanner_version` MUST equal the live
        `scanner_version` field at the response root. Drift means the
        manifest is stale.
        """
        with tempfile.TemporaryDirectory() as tmpd:
            out = self._run_g1(Path(tmpd))
        self.assertEqual(
            out["cosign_metadata"]["scanner_version"],
            out.get("scanner_version"),
        )

    def test_block_invariant_rubric_version_matches_live(self) -> None:
        with tempfile.TemporaryDirectory() as tmpd:
            out = self._run_g1(Path(tmpd))
        self.assertEqual(
            out["cosign_metadata"]["rubric_version"],
            out.get("rubric_version"),
        )

    def test_block_invariant_applicability_policy_version_matches_live(self) -> None:
        with tempfile.TemporaryDirectory() as tmpd:
            out = self._run_g1(Path(tmpd))
        self.assertEqual(
            out["cosign_metadata"]["applicability_policy_version"],
            out.get("applicability_policy_version"),
        )

    def test_rule_changes_present_nonempty(self) -> None:
        with tempfile.TemporaryDirectory() as tmpd:
            out = self._run_g1(Path(tmpd))
        rc = out["cosign_metadata"]["rule_changes"]
        self.assertIsInstance(rc, list)
        self.assertGreater(len(rc), 0)


if __name__ == "__main__":
    unittest.main()
