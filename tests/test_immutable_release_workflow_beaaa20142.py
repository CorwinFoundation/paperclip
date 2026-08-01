import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
SHA = "a" * 40


class ImmutableReleaseWorkflowTests(unittest.TestCase):
    def test_manifest_template_and_rendered_identity(self):
        source = json.loads((ROOT / "release/backbond-scanner-release.json").read_text())
        self.assertEqual(source["scanner_version"], "0.5.0")
        self.assertEqual(source["corpus_version"], "0.2.0")
        self.assertIsNone(source["build_sha"])
        self.assertIsNone(source["release_tag"])
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "release.json"
            subprocess.run(
                [sys.executable, "scripts/render_release_manifest.py", "--build-sha", SHA, "--output", str(output)],
                cwd=ROOT,
                check=True,
            )
            rendered = json.loads(output.read_text())
        self.assertEqual(rendered["build_sha"], SHA)
        self.assertEqual(rendered["release_tag"], f"sha-{SHA}")

    def test_workflow_is_unsigned_and_immutable(self):
        text = (ROOT / ".github/workflows/scanner-edge-image.yml").read_text()
        workflow = yaml.safe_load(text)
        self.assertEqual(workflow["permissions"], {"contents": "read", "packages": "write"})
        self.assertIn("ghcr.io/corwinfoundation/are-scanner:sha-${{ github.sha }}", text)
        self.assertNotIn(":latest", text)
        self.assertNotIn("cosign", text.lower())
        self.assertNotIn("id-token", text)
        self.assertIn('manifest["build_sha"]', text)
        self.assertIn('manifest["release_tag"]', text)


if __name__ == "__main__":
    unittest.main()
