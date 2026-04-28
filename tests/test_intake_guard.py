"""Tests for BEAAA-22 §3 hosted-eval-only intake guard."""
from __future__ import annotations

import unittest

from api.intake_guard import HOSTED_RUNNER, IntakeError, enforce_hosted_eval_only


def _ok_entry(seed: str = "11111111-1111-1111-1111-111111111111") -> dict:
    return {"raw": 2.0, "evidence_uri": "https://e/x", "runner": HOSTED_RUNNER, "eval_run_id": seed}


class IntakeGuardTests(unittest.TestCase):
    def test_accepts_hosted_runner_with_uuid(self):
        enforce_hosted_eval_only({"P1.1": _ok_entry()})

    def test_rejects_empty(self):
        with self.assertRaisesRegex(IntakeError, "non-empty"):
            enforce_hosted_eval_only({})

    def test_rejects_missing_runner(self):
        e = _ok_entry()
        e.pop("runner")
        with self.assertRaisesRegex(IntakeError, "hosted-eval"):
            enforce_hosted_eval_only({"P1.1": e})

    def test_rejects_self_reported_runner(self):
        e = _ok_entry()
        e["runner"] = "customer-self-reported"
        with self.assertRaisesRegex(IntakeError, "hosted-eval"):
            enforce_hosted_eval_only({"P1.1": e})

    def test_rejects_non_uuid_run_id(self):
        e = _ok_entry()
        e["eval_run_id"] = "not-a-uuid"
        with self.assertRaisesRegex(IntakeError, "UUID"):
            enforce_hosted_eval_only({"P1.1": e})

    def test_rejects_non_dict_entry(self):
        with self.assertRaisesRegex(IntakeError, "object"):
            enforce_hosted_eval_only({"P1.1": "raw=2.0"})


if __name__ == "__main__":
    unittest.main()
