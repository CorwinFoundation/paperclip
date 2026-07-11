"""BEAAA-13388 P3.6 output-as-attack-vector activation tests."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from _p5_helpers import load_probe_set  # noqa: E402

_PROBE_SET_PATH = _HERE / "probe_sets" / "p3_6_output_attack_v1.jsonl"
_RUBRIC_POINT_ID = "P3.6"
_UPSTREAM = "backbond-output-attack-fixtures"
_UPSTREAM_VERSION = "0.1.0"


class OutputAttackVectorActivationTests(unittest.TestCase):
    def test_probe_set_loads_with_p3_6_rubric_tag(self) -> None:
        specs = load_probe_set(
            _PROBE_SET_PATH,
            family="output_attack",
            expected_upstream=_UPSTREAM,
            expected_version=_UPSTREAM_VERSION,
        )
        self.assertEqual(len(specs), 4)
        self.assertEqual({s.rubric_point_id for s in specs}, {_RUBRIC_POINT_ID})
        self.assertEqual(
            {s.probe_id for s in specs},
            {
                "output_attack.html_script",
                "output_attack.sql_destructive",
                "output_attack.markdown_remote_exfil",
                "output_attack.shell_exfil",
            },
        )

    def test_p3_6_activation_recorded_in_rubric(self) -> None:
        rubric_yaml = _HERE.parent / "rubric" / "rubric.yaml"
        text = rubric_yaml.read_text(encoding="utf-8")
        robustness_idx = text.find("ROBUSTNESS:")
        deferred_idx = text.find("deferred_to_v02:")
        self.assertNotEqual(robustness_idx, -1)
        self.assertNotEqual(deferred_idx, -1)
        active_slice = text[robustness_idx:deferred_idx]
        deferred_slice = text[deferred_idx:text.find("GOVERNANCE:", deferred_idx)]
        self.assertIn("id: P3.6", active_slice)
        self.assertIn("rendered HTML/SQL/markdown/shell", active_slice)
        self.assertNotIn("id: P3.6", deferred_slice)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
