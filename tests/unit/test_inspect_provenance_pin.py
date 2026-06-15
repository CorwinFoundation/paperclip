"""BEAAA-5488 — inspect-ai provenance pin must equal the installed version.

Per parent BEAAA-5484 P3: ``requirements.txt`` pins ``inspect-ai==0.3.220`` but
``scanners/wraps/inspect_section1_v0.py:34`` recorded ``inspect_ai==0.3.69`` —
the recorded provenance had drifted from the actually-installed runtime, which
violates the "pin everything" discipline in ``docs/cto-os.md`` § Technical
principles.

This test imports the wrap module, reads the recorded pin, and asserts equality
with ``importlib.metadata.version("inspect-ai")`` (i.e. whatever
``requirements.txt`` actually installed). Commit-N: this test fails on the
hardcoded string. Commit-N+1: the wrap reads the version dynamically via
``importlib.metadata`` and the test passes.
"""
from __future__ import annotations

import importlib.metadata as importlib_metadata
import unittest

from scanners.wraps import inspect_section1_v0


class InspectProvenancePinTests(unittest.TestCase):
    def test_pinned_version_matches_installed(self) -> None:
        installed = importlib_metadata.version("inspect-ai")
        expected = f"inspect_ai=={installed}"
        self.assertEqual(
            inspect_section1_v0.INSPECT_AI_PINNED_VERSION,
            expected,
            (
                "Recorded provenance has drifted from installed runtime. "
                f"Got {inspect_section1_v0.INSPECT_AI_PINNED_VERSION!r}; "
                f"expected {expected!r} from importlib.metadata."
            ),
        )


if __name__ == "__main__":
    unittest.main()
