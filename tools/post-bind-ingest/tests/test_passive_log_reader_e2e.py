from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest


class PassiveLogReaderE2ETest(unittest.TestCase):
    def test_agent_logs_flow_through_detectors_and_log_tracker(self) -> None:
        tool_root = Path(__file__).resolve().parents[1]
        script = tool_root / "scripts" / "run_passive_log_reader_e2e.py"
        with tempfile.TemporaryDirectory(prefix="passive-e2e-test-") as tmp:
            output = Path(tmp)
            proc = subprocess.run(
                [sys.executable, str(script), "--output-dir", str(output)],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            report = json.loads(proc.stdout)

            self.assertEqual(report["status"], "PASS")
            self.assertEqual(set(report["perils_detected"]), {"P1", "P2", "P3", "P4", "P5", "P6"})
            self.assertEqual(report["continuity"]["continuity_status"], "healthy")
            self.assertTrue(all(report["assertions"].values()))
            self.assertFalse(report["simulation"]["vapi_used"])

            protected = "\n".join(
                [
                    (output / "normalized-events.jsonl").read_text(encoding="utf-8"),
                    *(path.read_text(encoding="utf-8") for path in (output / "receiver-batches").glob("*.json")),
                ]
            )
            self.assertNotIn("synthetic.user@example.com", protected)
            self.assertNotIn("sk-synthetic1234567890", protected)

            conn = sqlite3.connect(output / "log-tracker.sqlite3")
            try:
                rows = conn.execute(
                    "SELECT peril, scan_id_at_bind, scan_id_at_loss FROM claim_events"
                ).fetchall()
            finally:
                conn.close()
            self.assertEqual({row[0] for row in rows}, {"P1", "P2", "P3", "P4", "P5", "P6"})
            self.assertTrue(all(row[1] and row[2] for row in rows))


if __name__ == "__main__":
    unittest.main()
