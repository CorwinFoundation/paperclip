"""BEAAA-13692 HalluSquatting promptware probe mapping tests."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

import yaml

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from _p5_helpers import load_probe_set  # noqa: E402

_PROBE_SET_PATH = _HERE / "probe_sets" / "p5_hallusquatting_promptware_v1.jsonl"
_MAPPING_PATH = _HERE.parent / "rubric" / "probe_mappings" / "hallusquatting_promptware.yaml"
_UPSTREAM = "hallusquatting-promptware-fixtures"
_UPSTREAM_VERSION = "0.1.0"
_FAMILY = "hallusquatting_promptware"


class HalluSquattingPromptwareMappingTests(unittest.TestCase):
    def test_probe_set_covers_required_promptware_classes(self) -> None:
        specs = load_probe_set(
            _PROBE_SET_PATH,
            family=_FAMILY,
            expected_upstream=_UPSTREAM,
            expected_version=_UPSTREAM_VERSION,
        )
        rows = [
            json.loads(line)
            for line in _PROBE_SET_PATH.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")
        ]
        self.assertEqual(len(specs), 4)
        self.assertEqual(
            {row["filter_predicate"] for row in rows},
            {
                "hallucinated_package_identifier",
                "hallucinated_documentation_domain",
                "hallucinated_script_or_plugin_fetch",
                "known_good_confined_fetch",
            },
        )
        self.assertTrue(any(row["probe_id"].endswith(".good") for row in rows))
        self.assertGreaterEqual(sum(row["probe_id"].endswith(".bad") for row in rows), 3)

    def test_probe_set_crosswalks_to_existing_evidence_ids(self) -> None:
        allowed = {
            "P3.2",
            "P3.6",
            "ROBUSTNESS.TOOL_MISUSE.UNAUTHORIZED_CALL",
            "ROBUSTNESS.TOOL_MISUSE.PARAM_TAMPER",
        }
        for line in _PROBE_SET_PATH.read_text(encoding="utf-8").splitlines():
            if not line.strip() or line.startswith("#"):
                continue
            row = json.loads(line)
            ids = {row["rubric_point_id"], *row.get("crosswalk_rubric_point_ids", [])}
            self.assertTrue(ids <= allowed, row)
            self.assertIn(row["expected_disposition"], {"refuse"})
            self.assertIn("expected_evidence", row)

    def test_mapping_uses_existing_contract_ids_without_score_shape_change(self) -> None:
        mapping = yaml.safe_load(_MAPPING_PATH.read_text(encoding="utf-8"))
        self.assertEqual(mapping["benchmark"], _UPSTREAM)
        self.assertEqual(mapping["version"], _UPSTREAM_VERSION)
        self.assertEqual(mapping["binding"]["scanner_contract"], "are-scanner-api-v1@v1.0")
        self.assertIn("does not change score weights", mapping["description"])
        self.assertEqual(mapping["reproducibility"]["judge_model"], "none")
        self.assertEqual(mapping["reproducibility"]["n_runs"], 5)

        probes = mapping["probes"]
        self.assertEqual(len(probes), 3)
        self.assertEqual({probe["pillar"] for probe in probes}, {"ROBUSTNESS"})
        self.assertEqual(
            {probe["dim"] for probe in probes},
            {"P3.2", "P3.6"},
        )
        evidence_ids = {
            evidence["rubric_point_id"]
            for probe in probes
            for evidence in probe["evidence_refs"]
        }
        self.assertEqual(
            evidence_ids,
            {
                "P3.2",
                "P3.6",
                "ROBUSTNESS.TOOL_MISUSE.UNAUTHORIZED_CALL",
                "ROBUSTNESS.TOOL_MISUSE.PARAM_TAMPER",
            },
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
