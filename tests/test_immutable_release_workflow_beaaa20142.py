import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
SHA = "a" * 40


class ImmutableReleaseWorkflowTests(unittest.TestCase):
    def test_sbom_catalogs_the_transport_verified_archive_on_job_disk(self):
        workflow = yaml.safe_load((ROOT / ".github/workflows/scanner-edge-image.yml").read_text())
        steps = workflow["jobs"]["image-build"]["steps"]
        export = next(step for step in steps if step.get("name") == "Export the exact immutable image archive")
        sbom = next(step for step in steps if step.get("id") == "sbom")
        verified_archive = re.search(r"--archive\s+(\S+)", export["run"]).group(1)
        cataloged_archive = re.search(r'syft "docker-archive:([^"]+)"', sbom["run"]).group(1)
        self.assertEqual(cataloged_archive, verified_archive)
        self.assertLess(steps.index(export), steps.index(sbom))
        self.assertEqual(sbom["env"]["TMPDIR"], "${{ runner.temp }}")
        self.assertIn("verify_image_transport_link.py", export["run"])
        self.assertIn('--source-name "${{ steps.image.outputs.image_tag }}"', sbom["run"])

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

    def test_workflow_is_master_triggered_signed_and_immutable(self):
        text = (ROOT / ".github/workflows/scanner-edge-image.yml").read_text()
        workflow = yaml.safe_load(text)
        self.assertEqual(
            workflow["permissions"],
            {"contents": "read", "id-token": "write", "packages": "write"},
        )
        # BEAAA-28837: every master commit builds and signs automatically.
        # Manual dispatch is retained for the governed release-evidence lane.
        self.assertEqual(set(workflow[True]), {"push", "workflow_dispatch"})
        self.assertEqual(workflow[True]["push"], {"branches": ["master"]})
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
        deploy = workflow["jobs"]["deploy"]
        self.assertEqual(deploy["needs"], "image-build")
        self.assertEqual(deploy["if"], "${{ github.event_name == 'push' && github.ref == 'refs/heads/master' }}")
        self.assertEqual(deploy["env"]["DEPLOY_HOST"], "srv1749248.hstgr.cloud")
        self.assertEqual(deploy["env"]["DEPLOY_USER"], "are-deploy")
        self.assertEqual(deploy["env"]["DEPLOY_HELPER"], "/usr/local/sbin/scanner-deploy")
        self.assertNotIn("sudo install", text)
        self.assertIn('remote_helper="/var/tmp/${STAGED_HELPER}"', text)
        self.assertIn("sudo '${DEPLOY_HELPER}' refresh-helper '${SHA}'", text)
        self.assertIn("sudo '${DEPLOY_HELPER}' '${SHA}'", text)
        self.assertLess(
            text.index("sudo '${DEPLOY_HELPER}' refresh-helper '${SHA}'"),
            text.index("sudo '${DEPLOY_HELPER}' '${SHA}'"),
        )
        self.assertIn('.compatibility_contract_version == "scanner-operations-contract@1"', text)
        self.assertIn('ssh_dir="$RUNNER_TEMP/scanner-deploy-ssh"', text)
        self.assertIn("secrets.SCANNER_EDGE_DEPLOY_SSH_PRIVATE_KEY", text)
        self.assertIn("secrets.SCANNER_EDGE_DEPLOY_SSH_KNOWN_HOSTS", text)
        self.assertIn('chmod 0600 "$deploy_key" "$known_hosts"', text)
        self.assertIn('ssh-keygen -y -f "$deploy_key" >/dev/null', text)
        self.assertIn("python3 scripts/deploy/history/post_deploy_record.py", text)
        self.assertNotIn("secrets.SCANNER_DEPLOY_SSH_KEY", text)
        self.assertNotIn("secrets.SCANNER_DEPLOY_KNOWN_HOSTS", text)
        self.assertIn("-o StrictHostKeyChecking=yes", text)
        self.assertIn("deploy_authorized: true", text)
        self.assertIn('environment_promoted: "srv1749248"', text)
        self.assertNotIn("TARGET_HOST", text)
        self.assertIn('manifest["build_sha"]', text)
        self.assertIn('manifest["release_tag"]', text)
        self.assertIn("build_release_registry_marker", text)
        self.assertIn(
            'marker == build_release_registry_marker(pathlib.Path(".").resolve())',
            text,
        )
        self.assertNotIn('marker["published_executable_count"] ==', text)
        self.assertNotIn('marker["generated_executable_count"] ==', text)
        self.assertNotIn('marker["distinct_runtime_count"] ==', text)


if __name__ == "__main__":
    unittest.main()
