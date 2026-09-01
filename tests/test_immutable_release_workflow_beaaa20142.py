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

    def test_workflow_is_manual_signed_and_immutable(self):
        text = (ROOT / ".github/workflows/scanner-edge-image.yml").read_text()
        workflow = yaml.safe_load(text)
        self.assertEqual(
            workflow["permissions"],
            {"contents": "read", "id-token": "write", "packages": "write"},
        )
        self.assertEqual(set(workflow[True]), {"workflow_dispatch"})
        self.assertIn('image_tag="${IMAGE_REPOSITORY}:sha-${SOURCE_SHA}"', text)
        self.assertNotIn(":latest", text)
        self.assertIn("cosign sign --timeout 90s --yes --new-bundle-format=true", text)
        self.assertIn("COSIGN_WALL_TIMEOUT: 180s", text)
        self.assertEqual(
            text.count('timeout --signal=TERM --kill-after=15s "$COSIGN_WALL_TIMEOUT"'),
            6,
        )
        self.assertEqual(text.count("timeout-minutes: 4"), 6)
        self.assertEqual(text.count("--timeout 90s"), 6)
        self.assertEqual(text.count("cosign verify-blob-attestation"), 2)
        self.assertNotIn("cosign verify-attestation", text)
        self.assertIn("--check-claims=true", text)
        self.assertIn("scanner-edge-sbom-attestation.sigstore.json", text)
        self.assertIn("scanner-edge-provenance-attestation.sigstore.json", text)
        self.assertIn("scanner-edge-image.sigstore.sha256", text)
        self.assertIn(
            "oras-project/setup-oras@1d808f7d7f6995cc68b7bf507bfe5c5446e1dc9d",
            text,
        )
        self.assertIn("ghcr.io/corwinfoundation/are-scanner-release-evidence", text)
        self.assertIn("oras push", text)
        self.assertIn("oras pull", text)
        self.assertIn("verifiedByDigestPull:true", text)
        self.assertIn("independent_exact_candidate_qa", text)
        self.assertIn("fresh_founder_approval", text)
        self.assertIn(
            'dependsOn:["independent_exact_candidate_qa","fresh_founder_approval"]',
            text,
        )
        self.assertIn('deploymentInvoked:false', text)
        self.assertNotIn("srv1749248", text)
        self.assertNotIn("TARGET_HOST", text)
        self.assertNotIn("ssh ", text.lower())
        self.assertNotIn("scp ", text.lower())
        self.assertIn('manifest["build_sha"]', text)
        self.assertIn('manifest["release_tag"]', text)


if __name__ == "__main__":
    unittest.main()
