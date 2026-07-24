"""Tests for scanners.eval_framework_venv — BEAAA-13331 per-framework venv resolver.

Covers the backward-compat invariant (no venv configured -> byte-identical
"not-installed" resolution) and the offline materialization path (a fake venv
whose python probe reports a version). Uses a stub interpreter script so the
tests run with zero heavy framework deps installed.
"""
from __future__ import annotations

import os
import stat
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from scanners import eval_framework_venv as efv


def _write_fake_venv(root: Path, framework: str, *, version: str, ok: bool = True) -> None:
    """Materialize a minimal <root>/<framework>-venv/bin/python stub.

    The stub echoes `version` (ok=True) or exits non-zero (ok=False), so
    resolve_framework's subprocess probe can be exercised without installing
    the real framework.
    """
    vbin = root / f"{framework}-venv" / "bin"
    vbin.mkdir(parents=True, exist_ok=True)
    py = vbin / "python"
    if ok:
        # Ignore the `-c <code>` args; just print the version the probe expects.
        py.write_text(f'#!/usr/bin/env bash\nprintf %s "{version}"\n')
    else:
        py.write_text("#!/usr/bin/env bash\nexit 3\n")
    py.chmod(py.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


class ResolveFrameworkTests(unittest.TestCase):
    def test_unknown_framework_raises(self) -> None:
        with self.assertRaises(efv.EvalVenvError):
            efv.resolve_framework("not-a-framework")

    def test_no_venv_dir_is_not_installed(self) -> None:
        """The unconfigured path must be byte-identical to the historical
        `except ImportError` branch: (False, 'not-installed')."""
        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {efv._ENV_VAR: tmp}):
                for fw in efv.FRAMEWORKS:
                    self.assertEqual(
                        efv.resolve_framework(fw), (False, "not-installed")
                    )
                    self.assertFalse(efv.is_available(fw))

    def test_present_venv_reports_version(self) -> None:
        with TemporaryDirectory() as tmp:
            _write_fake_venv(Path(tmp), "pyrit", version="0.6.0")
            with patch.dict(os.environ, {efv._ENV_VAR: tmp}):
                self.assertTrue(efv.is_available("pyrit"))
                self.assertEqual(efv.resolve_framework("pyrit"), (True, "0.6.0"))

    def test_broken_venv_degrades_to_not_installed(self) -> None:
        with TemporaryDirectory() as tmp:
            _write_fake_venv(Path(tmp), "garak", version="", ok=False)
            with patch.dict(os.environ, {efv._ENV_VAR: tmp}):
                # interpreter exists but probe exits non-zero -> unavailable
                self.assertEqual(
                    efv.resolve_framework("garak"), (False, "not-installed")
                )

    def test_resolve_or_import_uses_in_process_module_first(self) -> None:
        class _FakeMod:
            __version__ = "9.9.9"

        # module present -> in-process fast path, no venv probe
        self.assertEqual(
            efv.resolve_or_import("pyrit", _FakeMod()), (True, "9.9.9")
        )

    def test_resolve_or_import_uses_distribution_metadata_without_dunder_version(self) -> None:
        class _FakeMod:
            pass

        with patch.object(efv.metadata, "version", return_value="0.1.35") as version:
            self.assertEqual(
                efv.resolve_or_import("agentdojo", _FakeMod()),
                (True, "0.1.35"),
            )
        version.assert_called_once_with("agentdojo")

    def test_resolve_or_import_falls_back_when_module_none(self) -> None:
        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {efv._ENV_VAR: tmp}):
                self.assertEqual(
                    efv.resolve_or_import("pyrit", None), (False, "not-installed")
                )


class VenvPathTests(unittest.TestCase):
    def test_venvs_root_env_override(self) -> None:
        with patch.dict(os.environ, {efv._ENV_VAR: "/custom/root"}):
            self.assertEqual(efv.venvs_root(), Path("/custom/root"))
            self.assertEqual(
                efv.venv_dir("garak"), Path("/custom/root/garak-venv")
            )

    def test_venvs_root_default(self) -> None:
        env = {k: v for k, v in os.environ.items() if k != efv._ENV_VAR}
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(efv.venvs_root(), Path(efv.DEFAULT_VENVS_ROOT))

    def test_run_in_venv_missing_raises(self) -> None:
        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {efv._ENV_VAR: tmp}):
                with self.assertRaises(efv.EvalVenvError):
                    efv.run_in_venv("inspect", ["-c", "print(1)"])


class FrameworkRegistryTests(unittest.TestCase):
    def test_pins_match_release_contract(self) -> None:
        # Guards against silent drift between the registry and the per-venv
        # requirements slices. A bump is a rule-change, not a code edit.
        expected = {
            "garak": "0.10.0",
            "pyrit": "0.6.0",
            "agentdojo": "0.1.35",
            "inspect": "0.3.220",
        }
        got = {k: v.pinned_version for k, v in efv.FRAMEWORKS.items()}
        self.assertEqual(got, expected)

    def test_import_names(self) -> None:
        self.assertEqual(efv.FRAMEWORKS["inspect"].import_name, "inspect_ai")
        self.assertEqual(efv.FRAMEWORKS["pyrit"].import_name, "pyrit")

    def test_distribution_names(self) -> None:
        self.assertEqual(efv.FRAMEWORKS["inspect"].distribution_name, "inspect-ai")
        self.assertEqual(efv.FRAMEWORKS["agentdojo"].distribution_name, "agentdojo")


if __name__ == "__main__":
    unittest.main()
