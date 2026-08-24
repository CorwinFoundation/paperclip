from contextlib import redirect_stdout
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rollout_handoff_capsule import BEGIN, END, main


class HandoffCapsuleRolloutTests(unittest.TestCase):
    def test_rollout_defaults_to_dry_run(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            amendment = root / "amendment.md"
            amendment.write_text(f"{BEGIN}\npolicy\n{END}\n", encoding="utf-8")

            def fake_api(base, method, path, body=None, **kwargs):
                del base, body, kwargs
                self.assertEqual(method, "GET")
                self.assertIn("instructions-bundle/file", path)
                return {"content": "# Existing instructions\n"}

            with (
                patch(
                    "rollout_handoff_capsule.managed_agents",
                    return_value=[{"id": "agent-1", "name": "Agent One", "status": "active"}],
                ),
                patch("rollout_handoff_capsule.api", side_effect=fake_api),
                redirect_stdout(io.StringIO()) as output,
            ):
                self.assertEqual(
                    main(["--amendment", str(amendment), "--company-id", "company-1"]),
                    0,
                )

            self.assertIn('"result": "would-update"', output.getvalue())
            self.assertFalse((root / "backups").exists())


if __name__ == "__main__":
    unittest.main()
