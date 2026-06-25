"""BEAAA-793 B1 Path-B InflectionRunner tests.

No live Inflection calls are made here; these tests pin the adapter contract
that the bias-pack runner can use later once approval/credentials are present.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from harness.runner import InflectionRunner, RunnerError


class _Resp:
    def __init__(self, status_code: int, payload: object | None = None, text: str = ""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self) -> object:
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class InflectionRunnerTests(unittest.TestCase):
    def test_missing_api_key_fails_closed(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(RunnerError) as ctx:
                InflectionRunner()
        self.assertIn("INFLECTION_API_KEY", str(ctx.exception))

    def test_posts_stateless_chat_completion_shape(self) -> None:
        calls: list[dict] = []

        def fake_post(url, *, headers, json, timeout):  # noqa: A002
            calls.append({
                "url": url,
                "headers": headers,
                "json": json,
                "timeout": timeout,
            })
            return _Resp(
                200,
                {"choices": [{"message": {"content": "Pi response"}}]},
                text="ok",
            )

        with patch.dict("os.environ", {"INFLECTION_API_KEY": "test-key"}, clear=True):
            with patch("harness.egress.post", side_effect=fake_post):
                runner = InflectionRunner(timeout_s=12.5)
                self.assertEqual(runner.model_id(), "inflection:inflection_3_pi")
                self.assertEqual(runner.complete("hello"), "Pi response")

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["url"], "https://api.inflection.ai/v1/chat/completions")
        self.assertEqual(calls[0]["headers"]["Authorization"], "Bearer test-key")
        self.assertEqual(calls[0]["json"]["model"], "inflection_3_pi")
        self.assertEqual(calls[0]["json"]["messages"], [{"role": "user", "content": "hello"}])
        self.assertEqual(calls[0]["json"]["temperature"], 0.0)
        self.assertEqual(calls[0]["json"]["max_tokens"], 256)
        self.assertNotIn("conversation_id", calls[0]["json"])
        self.assertEqual(calls[0]["timeout"], 12.5)

    def test_env_url_override_and_custom_model(self) -> None:
        def fake_post(url, *, headers, json, timeout):  # noqa: A002
            self.assertEqual(url, "https://sandbox.example/v1/chat/completions")
            self.assertEqual(json["model"], "Pi-3.1")
            return _Resp(200, {"choices": [{"message": {"content": "preview"}}]})

        with patch.dict(
            "os.environ",
            {
                "INFLECTION_API_KEY": "test-key",
                "INFLECTION_API_URL": "https://sandbox.example/v1/chat/completions",
            },
            clear=True,
        ):
            with patch("harness.egress.post", side_effect=fake_post):
                runner = InflectionRunner(model="Pi-3.1")
                self.assertEqual(runner.complete("hello"), "preview")

    def test_http_error_surfaces_runner_error(self) -> None:
        with patch.dict("os.environ", {"INFLECTION_API_KEY": "test-key"}, clear=True):
            with patch("harness.egress.post", return_value=_Resp(402, text="credit exhausted")):
                with self.assertRaises(RunnerError) as ctx:
                    InflectionRunner().complete("hello")
        self.assertIn("inflection HTTP 402", str(ctx.exception))

    def test_unexpected_shape_fails_closed(self) -> None:
        with patch.dict("os.environ", {"INFLECTION_API_KEY": "test-key"}, clear=True):
            with patch("harness.egress.post", return_value=_Resp(200, {"choices": []})):
                with self.assertRaises(RunnerError) as ctx:
                    InflectionRunner().complete("hello")
        self.assertIn("response shape unexpected", str(ctx.exception))

    def test_empty_text_fails_closed(self) -> None:
        with patch.dict("os.environ", {"INFLECTION_API_KEY": "test-key"}, clear=True):
            with patch("harness.egress.post", return_value=_Resp(200, {"choices": [{"message": {"content": ""}}]})):
                with self.assertRaises(RunnerError) as ctx:
                    InflectionRunner().complete("hello")
        self.assertIn("empty response", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
