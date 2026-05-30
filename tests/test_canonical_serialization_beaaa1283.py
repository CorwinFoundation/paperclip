"""ADR-0017 §7 Q7c canonical-JSON serialization spec — fixture-pinned CI gate.

Per `are-scanner-api-v1@v1.2.0` (BEAAA-1283 / WS7), the `snapshot_hash`
preimage on `POST /scanner/v1/rescans` is the RFC 8785 JCS canonical form
of the response body (minus `snapshot_hash` itself). Drift in any of:

  * the canonical byte form of the fixture (key ordering, escape rules,
    float precision, UTF-8 handling, null-vs-absent semantics)
  * the SHA-256 hash of that byte form
  * the JCS implementation in ``api/jcs.py``

invalidates the chain of `snapshot_hash` values committed to cold storage
under the Object Lock retention policy (ADR-0017 §7 Q7a). The test below
is the CI gate that catches that drift before merge.

This test is part of the v1.2.0 supervision contract surface — it is NOT
optional and MUST NOT be skipped. If the JCS implementation is
intentionally changed, regenerating these fixtures requires a contract
bump per ADR-0017 §2.3.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import sys
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from api.jcs import canonicalize  # noqa: E402

FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "canonical_serialization"
FIXTURE_NAMES = ("adr0017_q7c_minimal", "ws7_stress")


class CanonicalSerializationFixtureTests(unittest.TestCase):
    """Each fixture asserts byte-exact canonical form + SHA-256 match."""

    def _run_fixture(self, name: str) -> None:
        input_path = FIXTURE_DIR / f"{name}.input.json"
        canonical_path = FIXTURE_DIR / f"{name}.canonical.txt"
        sha_path = FIXTURE_DIR / f"{name}.expected-sha256"

        self.assertTrue(input_path.exists(), f"missing fixture input: {input_path}")
        self.assertTrue(canonical_path.exists(), f"missing canonical: {canonical_path}")
        self.assertTrue(sha_path.exists(), f"missing expected sha: {sha_path}")

        obj = json.loads(input_path.read_text(encoding="utf-8"))
        actual_canonical = canonicalize(obj)
        expected_canonical = canonical_path.read_bytes()

        self.assertEqual(
            actual_canonical,
            expected_canonical,
            f"JCS canonical form drift on {name!r}: "
            f"the canonical bytes produced by api.jcs.canonicalize() no longer "
            f"match the pinned fixture. Drift here invalidates persisted "
            f"snapshot_hash values per ADR-0017 §2.3.",
        )

        expected_sha = sha_path.read_text(encoding="utf-8").strip().lower()
        actual_sha = hashlib.sha256(actual_canonical).hexdigest()
        self.assertEqual(
            actual_sha,
            expected_sha,
            f"SHA-256 of canonical form drifted on {name!r}: "
            f"expected {expected_sha}, got {actual_sha}",
        )
        # snapshot_hash field on the wire carries the "sha256:" prefix.
        self.assertRegex(
            f"sha256:{actual_sha}",
            r"^sha256:[0-9a-f]{64}$",
            "wire shape of snapshot_hash must be 'sha256:<64-lowercase-hex>'",
        )

    def test_adr0017_q7c_minimal_fixture(self) -> None:
        """The exact fixture pinned in ADR-0017 §7 Q7c."""
        self._run_fixture("adr0017_q7c_minimal")

    def test_ws7_stress_fixture(self) -> None:
        """Stress fixture per BEAAA-1283 Scope #1: nested-unsorted, null vs.
        absent, escapable strings, UTF-8 non-ASCII, normalized floats."""
        self._run_fixture("ws7_stress")

    def test_null_present_vs_absent_distinct(self) -> None:
        """Sanity check at runtime (not fixture-pinned): present-with-null
        appears in the canonical form, absent keys do not."""
        with_null = canonicalize({"a": 1, "b": None})
        without_b = canonicalize({"a": 1})
        self.assertEqual(with_null, b'{"a":1,"b":null}')
        self.assertEqual(without_b, b'{"a":1}')
        self.assertNotEqual(with_null, without_b)


if __name__ == "__main__":  # pragma: no cover - convenience entrypoint
    unittest.main()
