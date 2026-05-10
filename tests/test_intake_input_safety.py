"""Tamper-resistance pass on api/intake_guard.enforce_input_safety
(BEAAA-163 Option C/B+C — defense-in-depth on adversarial scanner inputs).

Provenance (enforce_hosted_eval_only) establishes WHO produced the eval;
these tests cover WHAT was produced is shape-safe to flow into the scoring
engine, forensic bundle, and report-card renderer. A scanned agent's
output flows into eval_outputs, so an attacker-controlled agent could
attempt to inject downstream consumers via eval-output strings.
"""
from __future__ import annotations

import unittest

from api.intake_guard import HOSTED_RUNNER, IntakeError, enforce_input_safety


def _ok_entry(seed: str = "11111111-1111-1111-1111-111111111111") -> dict:
    return {
        "raw": 2.0,
        "evidence_uri": "https://e/x",
        "evidence_sha256": "sha256:" + "a" * 64,
        "runner": HOSTED_RUNNER,
        "eval_run_id": seed,
    }


def _full_set() -> dict:
    return {f"P1.{i}": _ok_entry() for i in range(1, 4)}


class HappyPathTests(unittest.TestCase):
    def test_accepts_canonical_eval_outputs(self):
        enforce_input_safety(_full_set())

    def test_accepts_empty_evidence_fields(self):
        e = _ok_entry()
        e["evidence_uri"] = ""
        e["evidence_sha256"] = ""
        enforce_input_safety({"P1.1": e})


class DimIdShapeTests(unittest.TestCase):
    def test_rejects_non_canonical_dim_id(self):
        with self.assertRaisesRegex(IntakeError, "canonical Pn.m"):
            enforce_input_safety({"X1.1": _ok_entry()})

    def test_rejects_pillar_index_5(self):
        with self.assertRaisesRegex(IntakeError, "canonical Pn.m"):
            enforce_input_safety({"P5.1": _ok_entry()})

    def test_rejects_oversized_dim_id(self):
        long_id = "P1." + "1" * 100
        with self.assertRaises(IntakeError):
            enforce_input_safety({long_id: _ok_entry()})

    def test_rejects_too_many_dims(self):
        too_many = {f"P1.{i}": _ok_entry() for i in range(1, 90)}
        with self.assertRaisesRegex(IntakeError, "max allowed"):
            enforce_input_safety(too_many)


class RawTypeSmugglingTests(unittest.TestCase):
    """Adversaries may try to smuggle non-numeric raw values that the
    scoring engine's float() call would tolerate but downstream consumers
    might mishandle."""

    def test_rejects_string_coerced_raw(self):
        e = _ok_entry()
        e["raw"] = "3.0"
        with self.assertRaisesRegex(IntakeError, "raw must be a numeric"):
            enforce_input_safety({"P1.1": e})

    def test_rejects_list_wrapped_raw(self):
        e = _ok_entry()
        e["raw"] = [3.0]
        with self.assertRaisesRegex(IntakeError, "raw must be a numeric"):
            enforce_input_safety({"P1.1": e})

    def test_rejects_dict_wrapped_raw(self):
        e = _ok_entry()
        e["raw"] = {"value": 3.0}
        with self.assertRaisesRegex(IntakeError, "raw must be a numeric"):
            enforce_input_safety({"P1.1": e})

    def test_rejects_bool_raw(self):
        # bool is a numeric in Python but pricing math treats it as 1/0
        # which is calibration-corrupting. Reject explicitly.
        e = _ok_entry()
        e["raw"] = True
        with self.assertRaisesRegex(IntakeError, "raw must be a numeric"):
            enforce_input_safety({"P1.1": e})

    def test_accepts_int_raw(self):
        e = _ok_entry()
        e["raw"] = 3
        enforce_input_safety({"P1.1": e})


class EvidenceUriHardeningTests(unittest.TestCase):
    def test_rejects_javascript_scheme(self):
        e = _ok_entry()
        e["evidence_uri"] = "javascript:alert(1)"
        with self.assertRaisesRegex(IntakeError, "https://"):
            enforce_input_safety({"P1.1": e})

    def test_rejects_http_unencrypted(self):
        e = _ok_entry()
        e["evidence_uri"] = "http://example.com"
        with self.assertRaisesRegex(IntakeError, "https://"):
            enforce_input_safety({"P1.1": e})

    def test_rejects_data_uri(self):
        e = _ok_entry()
        e["evidence_uri"] = "data:text/html,<script>alert(1)</script>"
        with self.assertRaisesRegex(IntakeError, "https://"):
            enforce_input_safety({"P1.1": e})

    def test_rejects_file_uri(self):
        e = _ok_entry()
        e["evidence_uri"] = "file:///etc/passwd"
        with self.assertRaisesRegex(IntakeError, "https://"):
            enforce_input_safety({"P1.1": e})

    def test_rejects_oversized_uri(self):
        e = _ok_entry()
        e["evidence_uri"] = "https://" + ("a" * 4096)
        with self.assertRaisesRegex(IntakeError, "exceeds"):
            enforce_input_safety({"P1.1": e})


class EvidenceSha256HardeningTests(unittest.TestCase):
    def test_rejects_truncated_hash(self):
        e = _ok_entry()
        e["evidence_sha256"] = "sha256:abc"
        with self.assertRaisesRegex(IntakeError, "sha256:<64-hex>"):
            enforce_input_safety({"P1.1": e})

    def test_rejects_md5(self):
        e = _ok_entry()
        e["evidence_sha256"] = "md5:" + "a" * 32
        with self.assertRaisesRegex(IntakeError, "sha256:<64-hex>"):
            enforce_input_safety({"P1.1": e})

    def test_rejects_uppercase_prefix_handled_via_regex_flag(self):
        # The regex is case-insensitive, so SHA256:<hex> is accepted.
        e = _ok_entry()
        e["evidence_sha256"] = "SHA256:" + "F" * 64
        enforce_input_safety({"P1.1": e})


class ControlCharRejectionTests(unittest.TestCase):
    """Control chars in string fields can break log pipelines and
    terminal renderers (escape-sequence injection)."""

    def test_rejects_null_byte_in_uri(self):
        e = _ok_entry()
        e["evidence_uri"] = "https://e/x\x00rest"
        with self.assertRaisesRegex(IntakeError, "control characters"):
            enforce_input_safety({"P1.1": e})

    def test_rejects_newline_in_uri(self):
        e = _ok_entry()
        e["evidence_uri"] = "https://e/x\nignore"
        with self.assertRaisesRegex(IntakeError, "control characters"):
            enforce_input_safety({"P1.1": e})

    def test_rejects_escape_in_runner(self):
        e = _ok_entry()
        e["runner"] = "beaai-hosted-eval\x1b[31m"
        with self.assertRaisesRegex(IntakeError, "control characters"):
            enforce_input_safety({"P1.1": e})

    def test_rejects_del_in_run_id(self):
        e = _ok_entry()
        e["eval_run_id"] = "11111111-1111-1111-1111-1111111111\x7f"
        with self.assertRaisesRegex(IntakeError, "control characters"):
            enforce_input_safety({"P1.1": e})


class StringTypeTests(unittest.TestCase):
    def test_rejects_non_string_evidence_uri(self):
        e = _ok_entry()
        e["evidence_uri"] = 12345
        with self.assertRaisesRegex(IntakeError, "must be a string"):
            enforce_input_safety({"P1.1": e})

    def test_rejects_non_string_runner(self):
        e = _ok_entry()
        e["runner"] = ["beaai-hosted-eval"]
        with self.assertRaisesRegex(IntakeError, "must be a string"):
            enforce_input_safety({"P1.1": e})


class ContainerShapeTests(unittest.TestCase):
    def test_rejects_list_eval_outputs(self):
        with self.assertRaisesRegex(IntakeError, "must be a mapping"):
            enforce_input_safety([_ok_entry()])  # type: ignore[arg-type]

    def test_rejects_non_dict_entry(self):
        with self.assertRaisesRegex(IntakeError, "must be an object"):
            enforce_input_safety({"P1.1": "not-a-dict"})  # type: ignore[dict-item]


if __name__ == "__main__":
    unittest.main()
