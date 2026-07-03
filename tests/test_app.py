"""End-to-end tests for the FastAPI app (smoke + red-line wiring)."""
from __future__ import annotations

import unittest
from unittest import mock
from pathlib import Path

try:
    from fastapi.testclient import TestClient
except ImportError as exc:  # pragma: no cover
    raise unittest.SkipTest(f"fastapi not installed: {exc}")

from api.app import create_app
from api.app import _http_error
from api.bundle import BundleStore
from api.intake_guard import HOSTED_RUNNER
from rubric.scoring import Rubric

REPO = Path(__file__).resolve().parents[1]
RUBRIC_PATH = REPO / "rubric" / "rubric.yaml"

TENANT = "11111111-1111-1111-1111-111111111111"
RUN_ID = "22222222-2222-2222-2222-222222222222"
SNAPSHOT_HASH = "sha256:" + "a" * 64


def _request(rubric: Rubric, raw: float = 2.0) -> dict:
    eval_outputs = {}
    for did in rubric.active_dim_ids:
        entry = {
            "raw": raw,
            "evidence_uri": f"https://evidence.local/{did}",
            "runner": HOSTED_RUNNER,
            "eval_run_id": RUN_ID,
        }
        eval_outputs[did] = entry
    return {
        "tenant_id": TENANT,
        "snapshot": {
            "model_id": "acme-llm",
            "model_version": "v1.2.3",
            "prompt_version": "p7",
            "snapshot_hash": SNAPSHOT_HASH,
        },
        "eval_outputs": eval_outputs,
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
        b = self.client.get(
            f"/forensic_bundle/{bundle_id}",
            headers={"X-Tenant-Id": TENANT},
        )
        self.assertEqual(b.status_code, 200, b.text)
        self.assertEqual(b.json()["bundle_id"], bundle_id)

    def test_cross_tenant_bundle_fetch_404(self):
        r = self.client.post(
            "/score",
            json=_request(self.rubric),
            headers={"X-Tenant-Id": TENANT},
        )
        self.assertEqual(r.status_code, 200, r.text)
        bundle_id = r.json()["forensic_bundle_uri"].rsplit("/", 1)[-1]
        b = self.client.get(
            f"/forensic_bundle/{bundle_id}",
            headers={"X-Tenant-Id": "33333333-3333-3333-3333-333333333333"},
        )
        self.assertEqual(b.status_code, 404, b.text)

    def test_valid_token_for_other_tenant_cannot_fetch_bundle(self):
        other_tenant = "33333333-3333-3333-3333-333333333333"
        with mock.patch.dict(
            "os.environ",
            {"ARE_API_AUTH_TOKENS_JSON": f'{{"token-a":"{TENANT}","token-b":"{other_tenant}"}}'},
            clear=False,
        ):
            app = create_app(rubric=self.rubric, bundles=BundleStore())
            client = TestClient(app)

            score_response = client.post(
                "/score",
                json=_request(self.rubric),
                headers={"Authorization": "Bearer token-a"},
            )
            self.assertEqual(score_response.status_code, 200, score_response.text)
            bundle_id = score_response.json()["forensic_bundle_uri"].rsplit("/", 1)[-1]

            forbidden = client.get(
                f"/forensic_bundle/{bundle_id}",
                headers={"Authorization": "Bearer token-b"},
            )
            self.assertEqual(forbidden.status_code, 404, forbidden.text)

            allowed = client.get(
                f"/forensic_bundle/{bundle_id}",
                headers={"Authorization": "Bearer token-a"},
            )
            self.assertEqual(allowed.status_code, 200, allowed.text)

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
        r = self.client.get(
            "/forensic_bundle/00000000-0000-0000-0000-000000000000",
            headers={"X-Tenant-Id": TENANT},
        )
        self.assertEqual(r.status_code, 404)

    def test_auth_token_required_when_token_map_configured(self):
        with mock.patch.dict(
            "os.environ",
            {"ARE_API_AUTH_TOKENS_JSON": '{"token-1":"tenant-auth"}'},
            clear=False,
        ):
            app = create_app(rubric=self.rubric, bundles=BundleStore())
            client = TestClient(app)

            missing = client.post("/score", json=_request(self.rubric))
            self.assertEqual(missing.status_code, 401, missing.text)

            invalid = client.post(
                "/score",
                json=_request(self.rubric),
                headers={"Authorization": "Bearer wrong-token", "X-Tenant-Id": TENANT},
            )
            self.assertEqual(invalid.status_code, 401, invalid.text)

    def test_api_edge_redacts_secret_bearing_error_detail(self):
        exc = _http_error(
            500,
            {
                "error": "upstream returned api_key=EDGE_SENTINEL_TOKEN_1234567890"
            },
        )
        self.assertNotIn("EDGE_SENTINEL_TOKEN_1234567890", str(exc.detail))
        self.assertIn("[REDACTED_SECRET]", str(exc.detail))

    def test_unexpected_portal_error_500_hides_exception_detail(self):
        sentinel = "D6_INTERNAL_EXCEPTION_SENTINEL_SHOULD_NOT_LEAK"
        client = TestClient(self.app, raise_server_exceptions=False)

        with mock.patch("api.app.score_handler", side_effect=RuntimeError(sentinel)):
            response = client.post(
                "/score",
                json=_request(self.rubric),
                headers={"X-Tenant-Id": TENANT},
            )

        self.assertEqual(response.status_code, 500, response.text)
        body = response.json()
        self.assertEqual(body["error"], "internal_error")
        self.assertRegex(body["correlation_id"], r"^[0-9a-f-]{36}$")
        self.assertEqual(response.headers["X-Correlation-Id"], body["correlation_id"])
        self.assertNotIn(sentinel, response.text)
        self.assertNotIn("RuntimeError", response.text)

    def test_auth_token_binds_tenant_not_x_tenant_header(self):
        with mock.patch.dict(
            "os.environ",
            {"ARE_API_AUTH_TOKENS_JSON": f'{{"token-1":"{TENANT}"}}'},
            clear=False,
        ):
            app = create_app(rubric=self.rubric, bundles=BundleStore())
            client = TestClient(app)

            mismatch = client.post(
                "/score",
                json=_request(self.rubric),
                headers={
                    "Authorization": "Bearer token-1",
                    "X-Tenant-Id": "33333333-3333-3333-3333-333333333333",
                },
            )
            self.assertEqual(mismatch.status_code, 409, mismatch.text)

            ok = client.post(
                "/score",
                json=_request(self.rubric),
                headers={"Authorization": "Bearer token-1"},
            )
            self.assertEqual(ok.status_code, 200, ok.text)

    def test_quote_payload_tenant_must_match_auth_context(self):
        with mock.patch.dict(
            "os.environ",
            {"ARE_API_AUTH_TOKENS_JSON": f'{{"token-1":"{TENANT}"}}'},
            clear=False,
        ):
            app = create_app(rubric=self.rubric, bundles=BundleStore())
            client = TestClient(app)
            body = {
                "tenant_id": "33333333-3333-3333-3333-333333333333",
                "score": {
                    "bmi": 78,
                    "band": {
                        "letter": "B",
                        "modifier": "neutral",
                        "modifier_factor": 1.0,
                    },
                },
                "customer": {
                    "sector": "ENT_SAAS",
                    "annual_revenue_usd": 100_000_000,
                    "limit": {"per_claim_usd": 1_000_000, "aggregate_usd": 2_000_000},
                },
            }

            response = client.post(
                "/quote",
                json=body,
                headers={"Authorization": "Bearer token-1"},
            )
            self.assertEqual(response.status_code, 409, response.text)


if __name__ == "__main__":
    unittest.main()
