from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from handoff_capsule import (
    CapsuleError,
    LEGACY_INDEX_SCHEMA,
    LEGACY_SCHEMA,
    build_capsule,
    verify_index,
)


class HandoffCapsuleTests(unittest.TestCase):
    def test_verifier_accepts_legacy_capsules_during_company_migration(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            artifact = root / "result.txt"
            artifact.write_text("legacy durable bytes", encoding="utf-8")
            with (
                patch("handoff_capsule.SCHEMA", LEGACY_SCHEMA),
                patch("handoff_capsule.INDEX_SCHEMA", LEGACY_INDEX_SCHEMA),
            ):
                index = build_capsule(
                    output_dir=root / "out",
                    producer_issue="ALPHA-1",
                    qa_issue="ALPHA-2",
                    artifacts=[str(artifact)],
                    created_at="2026-01-01T00:00:00Z",
                )
            result = verify_index(index, verify_git=False)
            self.assertTrue(result["ok"])
            self.assertEqual(result["producer_issue"], "ALPHA-1")

    def test_artifact_capsule_round_trip_and_tamper_detection(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            evidence = root / "n5.json"
            evidence.write_text('{"seeds":[0,1,2,3,4],"delta":0}\n', encoding="utf-8")
            out = root / "out"
            index = build_capsule(
                output_dir=out,
                producer_issue="ALPHA-1",
                qa_issue="ALPHA-2",
                artifacts=[f"evidence/n5.json={evidence}"],
                commands=["pytest -q"],
                created_at="2026-01-01T00:00:00Z",
                part_bytes=1024,
            )
            result = verify_index(index, verify_git=False)
            self.assertTrue(result["ok"])
            self.assertEqual(result["producer_issue"], "ALPHA-1")
            document = json.loads(index.read_text(encoding="utf-8"))
            self.assertEqual(document["manifest_hash_semantics"], "raw-file-sha256")
            part = out / document["parts"][0]["name"]
            part.write_bytes(part.read_bytes() + b"tamper")
            with self.assertRaisesRegex(CapsuleError, "(?:size|checksum) mismatch"):
                verify_index(index, verify_git=False)

    def test_git_bundle_is_self_contained(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            repo = root / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
            (repo / "value.txt").write_text("base\n", encoding="utf-8")
            subprocess.run(["git", "add", "value.txt"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "base"], cwd=repo, check=True)
            base = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
            (repo / "value.txt").write_text("candidate\n", encoding="utf-8")
            subprocess.run(["git", "commit", "-qam", "candidate"], cwd=repo, check=True)
            index = build_capsule(
                output_dir=root / "out",
                producer_issue="OPS7-3",
                qa_issue="OPS7-4",
                artifacts=[],
                repo=repo,
                candidate="HEAD",
                base=base,
                created_at="2026-01-01T00:00:00Z",
                part_bytes=2048,
            )
            result = verify_index(index)
            self.assertEqual(result["candidate_sha"], subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip())

    def test_rejects_unsafe_or_duplicate_names(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            value = root / "x.txt"
            value.write_text("x", encoding="utf-8")
            with self.assertRaises(CapsuleError):
                build_capsule(
                    output_dir=root / "out",
                    producer_issue="A",
                    qa_issue="B",
                    artifacts=[f"../x={value}"],
                )

    def test_rejects_empty_or_self_review_capsules(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            with self.assertRaisesRegex(CapsuleError, "at least one artifact"):
                build_capsule(
                    output_dir=root / "empty",
                    producer_issue="OPS-1",
                    qa_issue="OPS-2",
                    artifacts=[],
                )
            artifact = root / "result.txt"
            artifact.write_text("result", encoding="utf-8")
            with self.assertRaisesRegex(CapsuleError, "must be different"):
                build_capsule(
                    output_dir=root / "self-review",
                    producer_issue="OPS-1",
                    qa_issue="ops-1",
                    artifacts=[str(artifact)],
                )

    def test_rejects_incorrect_part_size_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            artifact = root / "result.txt"
            artifact.write_text("result", encoding="utf-8")
            index = build_capsule(
                output_dir=root / "out",
                producer_issue="OPS-1",
                qa_issue="OPS-2",
                artifacts=[str(artifact)],
                created_at="2026-01-01T00:00:00Z",
            )
            document = json.loads(index.read_text(encoding="utf-8"))
            document["parts"][0]["byte_size"] += 1
            index.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaisesRegex(CapsuleError, "part size mismatch"):
                verify_index(index, verify_git=False)


if __name__ == "__main__":
    unittest.main()
