"""Handler-level tests for /score and /forensic_bundle (Task A)."""
from __future__ import annotations

import copy
import unittest
from datetime import datetime, timezone
from pathlib import Path

from api.bundle import BundleStore, derive_bundle_id
from api.handlers import HandlerError, forensic_bundle_handler, score_handler
from api.intake_guard import HOSTED_RUNNER
from rubric.scoring import Rubric

REPO = Path(__file__).resolve().parents[1]
RUBRIC_PATH = REPO / "rubric" / "rubric.yaml"

TENANT = "11111111-1111-1111-1111-111111111111"
RUN_ID = "22222222-2222-2222-2222-222222222222"
SNAPSHOT_HASH = "sha256:" + "a" * 64
SCORED_AT = datetime(2026, 4, 28, tzinfo=timezone.utc)


def _eval_outputs(rubric: Rubric, raw: float = 2.0) -> dict:
    return {
        did: {
            "raw": raw,
            "evidence_uri": f"https://evidence.local/{did}",
            "runner": HOSTED_RUNNER,
            "eval_run_id": RUN_ID,
        }
        for did in rubric.active_dim_ids
    }


def _request(rubric: Rubric, raw: float = 2.0) -> dict:
    return {
        "tenant_id": TENANT,
        "snapshot": {
            "model_id": "acme-llm",
            "model_version": "v1.2.3",
            "prompt_version": "p7",
            "snapshot_hash": SNAPSHOT_HASH,
        },
        "eval_outputs": _eval_outputs(rubric, raw),
    }


class ScoreHandlerTests(unittest.TestCase):
    def setUp(self):
        self.rubric = Rubric.load(RUBRIC_PATH)
        self.bundles = BundleStore()

    def _call(self, body, *, tenant_header=TENANT, scored_at=SCORED_AT):
        return score_handler(
            body, tenant_header=tenant_header, rubric=self.rubric,
            bundles=self.bundles, scored_at=scored_at,
        )

    def test_happy_path_returns_200_and_score_response(self):
        status, body = self._call(_request(self.rubric, raw=2.0))
        self.assertEqual(status, 200)
        self.assertEqual(body["bmi"], 50)
        self.assertEqual(body["snapshot_hash"], SNAPSHOT_HASH)
        self.assertTrue(body["forensic_bundle_uri"].endswith(
            body["forensic_bundle_uri"].rsplit("/", 1)[-1]
        ))
        self.assertEqual(set(body["pillars"]), {"OPS", "BIAS_FAIRNESS", "ROBUSTNESS", "GOVERNANCE"})
        # ARE 50 → D+ (decline + remediation): letter D, modifier +.
        self.assertEqual(body["band"]["letter"], "D")
        self.assertEqual(body["band"]["modifier"], "+")
        self.assertEqual(body["band"]["display"], "D+")

    def test_determinism_same_inputs_same_response_and_bundle(self):
        s1, b1 = self._call(_request(self.rubric, raw=2.0))
        s2, b2 = self._call(_request(self.rubric, raw=2.0))
        self.assertEqual(s1, s2)
        self.assertNotEqual(b1["forensic_bundle_uri"], b2["forensic_bundle_uri"])

    def test_tenant_isolation_mismatch_409(self):
        with self.assertRaises(HandlerError) as ctx:
            self._call(_request(self.rubric), tenant_header="33333333-3333-3333-3333-333333333333")
        self.assertEqual(ctx.exception.status, 409)

    def test_tenant_isolation_missing_header_409(self):
        with self.assertRaises(HandlerError) as ctx:
            self._call(_request(self.rubric), tenant_header=None)
        self.assertEqual(ctx.exception.status, 409)

    def test_snapshot_integrity_bad_hash_422(self):
        body = _request(self.rubric)
        body["snapshot"]["snapshot_hash"] = "not-a-sha256"
        with self.assertRaises(HandlerError) as ctx:
            self._call(body)
        self.assertEqual(ctx.exception.status, 422)

    def test_snapshot_missing_field_422(self):
        body = _request(self.rubric)
        body["snapshot"].pop("model_id")
        with self.assertRaises(HandlerError) as ctx:
            self._call(body)
        self.assertEqual(ctx.exception.status, 422)

    def test_intake_guard_rejects_self_reported_422(self):
        body = _request(self.rubric)
        body["eval_outputs"]["P1.1"]["runner"] = "customer-self-reported"
        with self.assertRaises(HandlerError) as ctx:
            self._call(body)
        self.assertEqual(ctx.exception.status, 422)

    def test_missing_request_field_422(self):
        body = _request(self.rubric)
        body.pop("eval_outputs")
        with self.assertRaises(HandlerError) as ctx:
            self._call(body)
        self.assertEqual(ctx.exception.status, 422)

    def test_engine_error_surfaces_as_422(self):
        body = _request(self.rubric)
        body["eval_outputs"]["P1.1"]["raw"] = 5.0
        with self.assertRaises(HandlerError) as ctx:
            self._call(body)
        self.assertEqual(ctx.exception.status, 422)


class ForensicBundleHandlerTests(unittest.TestCase):
    def setUp(self):
        self.rubric = Rubric.load(RUBRIC_PATH)
        self.bundles = BundleStore()

    def test_get_after_score_returns_bundle(self):
        status, score_body = score_handler(
            _request(self.rubric), tenant_header=TENANT, rubric=self.rubric,
            bundles=self.bundles, scored_at=SCORED_AT,
        )
        self.assertEqual(status, 200)
        bundle_id = score_body["forensic_bundle_uri"].rsplit("/", 1)[-1]
        gstatus, bundle = forensic_bundle_handler(
            bundle_id,
            tenant_header=TENANT,
            bundles=self.bundles,
        )
        self.assertEqual(gstatus, 200)
        self.assertEqual(bundle["bundle_id"], bundle_id)
        self.assertEqual(bundle["tenant_id"], TENANT)
        self.assertEqual(bundle["snapshot_hash"], SNAPSHOT_HASH)
        self.assertTrue(bundle["signed_manifest"].startswith("hmac-sha256:"))

    def test_cross_tenant_get_returns_404(self):
        status, score_body = score_handler(
            _request(self.rubric), tenant_header=TENANT, rubric=self.rubric,
            bundles=self.bundles, scored_at=SCORED_AT,
        )
        self.assertEqual(status, 200)
        bundle_id = score_body["forensic_bundle_uri"].rsplit("/", 1)[-1]
        with self.assertRaises(HandlerError) as ctx:
            forensic_bundle_handler(
                bundle_id,
                tenant_header="33333333-3333-3333-3333-333333333333",
                bundles=self.bundles,
            )
        self.assertEqual(ctx.exception.status, 404)

    def test_legacy_derived_bundle_id_does_not_fetch(self):
        status, score_body = score_handler(
            _request(self.rubric), tenant_header=TENANT, rubric=self.rubric,
            bundles=self.bundles, scored_at=SCORED_AT,
        )
        self.assertEqual(status, 200)
        actual_bundle_id = score_body["forensic_bundle_uri"].rsplit("/", 1)[-1]
        legacy_bundle_id = derive_bundle_id(
            snapshot_hash=SNAPSHOT_HASH,
            tenant_id=TENANT,
            scored_at=SCORED_AT.isoformat(),
        )
        self.assertNotEqual(actual_bundle_id, legacy_bundle_id)
        with self.assertRaises(HandlerError) as ctx:
            forensic_bundle_handler(
                legacy_bundle_id,
                tenant_header=TENANT,
                bundles=self.bundles,
            )
        self.assertEqual(ctx.exception.status, 404)

    def test_get_unknown_404(self):
        with self.assertRaises(HandlerError) as ctx:
            forensic_bundle_handler(
                "00000000-0000-0000-0000-000000000000",
                tenant_header=TENANT,
                bundles=self.bundles,
            )
        self.assertEqual(ctx.exception.status, 404)


if __name__ == "__main__":
    unittest.main()
