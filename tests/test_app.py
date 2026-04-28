"""End-to-end tests for the FastAPI app (smoke + red-line wiring)."""
from __future__ import annotations

import unittest
from pathlib import Path

try:
    from fastapi.testclient import TestClient
except ImportError as exc:  # pragma: no cover
    raise unittest.SkipTest(f"fastapi not installed: {exc}")

from api.app import create_app
from api.bundle import BundleStore
from api.intake_guard import HOSTED_RUNNER
from rubric.scoring import Rubric

REPO = Path(__file__).resolve().parents[1]
RUBRIC_PATH = REPO / "rubric" / "rubric.yaml"

TENANT = "11111111-1111-1111-1111-111111111111"
RUN_ID = "22222222-2222-2222-2222-222222222222"
SNAPSHOT_HASH = "sha256:" + "a" * 64


def _request(rubric: Rubric, raw: float = 2.0) -> dict:
    return {
        "tenant_id": TENANT,
        "snapshot": {
            "model_id": "acme-llm",
            "model_version": "v1.2.3",
            "prompt_version": "p7",
            "snapshot_hash": SNAPSHOT_HASH,
        },
        "eval_outputs": {
            did: {
                "raw": raw,
                "evidence_uri": f"https://evidence.local/{did}",
                "runner": HOSTED_RUNNER,
                "eval_run_id": RUN_ID,
            }
            for did in rubric.active_dim_ids
        },
    }


class AppTests(unittest.TestCase):
    def setUp(self):
        self.rubric = Rubric.load(RUBRIC_PATH)
        self.app = create_app(rubric=self.rubric, bundles=BundleStore())
        self.client = TestClient(self.app)

    def test_score_then_fetch_bundle(self):
        r = self.client.post(
            "/score",
            json=_request(self.rubric),
            headers={"X-Tenant-Id": TENANT},
        )
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        bundle_id = body["forensic_bundle_uri"].rsplit("/", 1)[-1]
        b = self.client.get(f"/forensic_bundle/{bundle_id}")
        self.assertEqual(b.status_code, 200, b.text)
        self.assertEqual(b.json()["bundle_id"], bundle_id)

    def test_score_tenant_mismatch_409(self):
        r = self.client.post(
            "/score",
            json=_request(self.rubric),
            headers={"X-Tenant-Id": "33333333-3333-3333-3333-333333333333"},
        )
        self.assertEqual(r.status_code, 409, r.text)

    def test_score_bad_snapshot_422(self):
        body = _request(self.rubric)
        body["snapshot"]["snapshot_hash"] = "bogus"
        r = self.client.post("/score", json=body, headers={"X-Tenant-Id": TENANT})
        self.assertEqual(r.status_code, 422, r.text)

    def test_score_self_reported_eval_422(self):
        body = _request(self.rubric)
        body["eval_outputs"]["P1.1"]["runner"] = "customer-self-reported"
        r = self.client.post("/score", json=body, headers={"X-Tenant-Id": TENANT})
        self.assertEqual(r.status_code, 422, r.text)

    def test_unknown_bundle_404(self):
        r = self.client.get("/forensic_bundle/00000000-0000-0000-0000-000000000000")
        self.assertEqual(r.status_code, 404)


if __name__ == "__main__":
    unittest.main()
