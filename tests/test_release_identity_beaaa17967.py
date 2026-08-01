from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from release_identity import (
    RELEASE_IDENTITY,
    ReleaseManifestError,
    release_identity_evidence,
    validate_repository,
    validate_tag,
)

ROOT = Path(__file__).resolve().parents[1]
SHA = "0123456789abcdef0123456789abcdef01234567"


class ReleaseIdentityTest(unittest.TestCase):
    def test_canonical_identity_and_evidence(self):
        evidence = release_identity_evidence(ROOT, build_sha=SHA)
        self.assertEqual(evidence["scanner_version"], "0.5.0")
        self.assertEqual(evidence["corpus_version"], "0.2.0")
        self.assertEqual(evidence["rubric_version"], "1.3.0")
        self.assertEqual(evidence["contract_version"], "are-scanner-api-v1@v1.3.0")
        self.assertEqual(evidence["api_app_version"], "1.1.0")
        self.assertEqual(
            evidence["model_versions"]["primary"],
            "retell:agent_1e7015ae5f87ea825a3d1fac11:gpt-4.1-mini:temperature-0",
        )
        self.assertEqual(evidence["build_sha"], SHA)
        self.assertIsNone(evidence["release_tag"])
        self.assertRegex(evidence["manifest_sha256"], r"^[0-9a-f]{64}$")

    def test_tag_semantics(self):
        validate_tag("scanner-v0.5.0", RELEASE_IDENTITY, SHA)
        validate_tag("scanner-v0.5.0-rc.1", RELEASE_IDENTITY, SHA)
        validate_tag("deploy/staging/20260726/0123456789ab", RELEASE_IDENTITY, SHA)
        for tag in (
            "v0.5.0",
            "scanner-v0.5.1",
            "scanner-v0.5.0-rc.0",
            "deploy/staging/20260726/ffffffffffff",
        ):
            with self.subTest(tag=tag), self.assertRaises(ReleaseManifestError):
                validate_tag(tag, RELEASE_IDENTITY, SHA)

    def _copy_minimum(self) -> Path:
        temp = Path(tempfile.mkdtemp())
        for relative in (
            "release/backbond-scanner-release.json",
            "contracts/are-scanner-api-v1.md",
            "rubric/rubric.yaml",
            "scanners/__init__.py",
            "api/app.py",
            "bin/are-scan",
            "tools/backbond-scanner",
            "tools/portal_v1/portal.py",
            "Dockerfile",
            "scripts/backbond_deploy_agent.py",
        ):
            target = temp / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / relative, target)
        self.addCleanup(shutil.rmtree, temp)
        return temp

    def test_each_consumer_drift_fails_independently(self):
        mutations = {
            "runtime": ("scanners/__init__.py", "from release_identity import RELEASE_IDENTITY", "# drift"),
            "api": ("api/app.py", "RELEASE_IDENTITY.api_app_version", '"1.1.0"'),
            "cli": ("bin/are-scan", "RELEASE_IDENTITY.scanner_version", '"0.5.0"'),
            "fixture_entrypoint": (
                "tools/backbond-scanner",
                "RELEASE_IDENTITY.model_versions",
                '{"primary": "drift", "judges": ["drift"]}',
            ),
            "portal": ("tools/portal_v1/portal.py", "from release_identity import RELEASE_IDENTITY", "# drift"),
            "container": ("Dockerfile", "scripts/render_release_manifest.py", "scripts/drift_release_manifest.py"),
            "deploy": (
                "scripts/backbond_deploy_agent.py",
                "release_identity_evidence(REPO_ROOT, build_sha=candidate.commit_sha)",
                "drift_evidence(REPO_ROOT, build_sha=candidate.commit_sha)",
            ),
        }
        for name, (relative, old, new) in mutations.items():
            with self.subTest(consumer=name):
                root = self._copy_minimum()
                path = root / relative
                path.write_text(path.read_text().replace(old, new))
                with self.assertRaisesRegex(ReleaseManifestError, name):
                    validate_repository(root, build_sha=SHA)

    def test_each_manifest_pin_drift_fails(self):
        mutations = {
            "rubric_version": "9.9.9",
            "contract_version": "are-scanner-api-v1@v9.9.9",
            "build_sha": "f" * 40,
        }
        for field, value in mutations.items():
            with self.subTest(field=field):
                root = self._copy_minimum()
                path = root / "release/backbond-scanner-release.json"
                manifest = json.loads(path.read_text())
                manifest[field] = value
                path.write_text(json.dumps(manifest))
                with self.assertRaises(ReleaseManifestError):
                    validate_repository(root, build_sha=SHA)


if __name__ == "__main__":
    unittest.main()
