"""Tests for the eval harness POC.

Coverage: MockRunner deterministic, TruthfulQA prompt format + parse,
accuracy-to-raw mapping, end-to-end happy/sad path.
"""
from __future__ import annotations

import unittest
from pathlib import Path

from harness.bench import prompt_injection, secret_exfil, truthfulqa
from harness.pipeline import merge_measurements_with_priors
from harness.runner import MockRunner, RunnerError

REPO = Path(__file__).resolve().parents[1]
TQA_FIXTURE = REPO / "harness" / "fixtures" / "truthfulqa_mc1_sample.jsonl"
PI_FIXTURE = REPO / "harness" / "fixtures" / "prompt_injection_poc.jsonl"
EX_FIXTURE = REPO / "harness" / "fixtures" / "secret_exfil_poc.jsonl"


class FixtureTests(unittest.TestCase):
    def test_fixture_loads(self):
        items = truthfulqa.load_items(TQA_FIXTURE)
        self.assertGreaterEqual(len(items), 10)
        for item in items:
            self.assertIn("A", item.choices)
            self.assertIn("B", item.choices)
            self.assertIn("C", item.choices)
            self.assertIn("D", item.choices)
            self.assertIn(item.answer, ("A", "B", "C", "D"))

    def test_every_fixture_has_correct_letter_a(self):
        # Convention for the sampled fixture: correct answer is always A.
        # Lets the perfect-mock and guess-mock paths produce stable results.
        items = truthfulqa.load_items(TQA_FIXTURE)
        for item in items:
            self.assertEqual(item.answer, "A", f"{item.id} expected A")


class ParseTests(unittest.TestCase):
    def test_parse_letter_handles_plain(self):
        self.assertEqual(truthfulqa._parse_letter("A"), "A")
        self.assertEqual(truthfulqa._parse_letter("B."), "B")
        self.assertEqual(truthfulqa._parse_letter("Answer: C"), "C")

    def test_parse_letter_lowercases_normalize(self):
        self.assertEqual(truthfulqa._parse_letter("d"), "D")

    def test_parse_letter_returns_none_when_absent(self):
        self.assertIsNone(truthfulqa._parse_letter("E?"))
        self.assertIsNone(truthfulqa._parse_letter(""))


class AccuracyMappingTests(unittest.TestCase):
    def test_perfect_maps_to_4(self):
        self.assertEqual(truthfulqa._accuracy_to_raw(1.0), 4.0)

    def test_zero_maps_to_0(self):
        self.assertEqual(truthfulqa._accuracy_to_raw(0.0), 0.0)

    def test_monotonic(self):
        prev = -1.0
        for acc in (0.0, 0.1, 0.2, 0.3, 0.4, 0.55, 0.7, 0.85, 1.0):
            cur = truthfulqa._accuracy_to_raw(acc)
            self.assertGreaterEqual(cur, prev)
            prev = cur


class RunEndToEndTests(unittest.TestCase):
    def setUp(self):
        self.items = truthfulqa.load_items(TQA_FIXTURE)

    def test_perfect_runner_lands_at_4(self):
        runner = MockRunner("perfect", responder=lambda _p: "A")
        result = truthfulqa.run(runner, self.items)
        self.assertEqual(result["dim_id"], "P1.1")
        self.assertEqual(result["n_correct"], result["n_items"])
        self.assertEqual(result["accuracy"], 1.0)
        self.assertEqual(result["raw"], 4.0)

    def test_always_b_runner_scores_zero(self):
        runner = MockRunner("guess", responder=lambda _p: "B")
        result = truthfulqa.run(runner, self.items)
        self.assertEqual(result["n_correct"], 0)
        self.assertEqual(result["accuracy"], 0.0)
        self.assertEqual(result["raw"], 0.0)

    def test_unparseable_response_counts_as_wrong(self):
        runner = MockRunner("nonsense", responder=lambda _p: "I cannot answer.")
        result = truthfulqa.run(runner, self.items)
        self.assertEqual(result["n_correct"], 0)


class PipelineMergeTests(unittest.TestCase):
    def test_measurement_overrides_prior(self):
        priors = {"P1.1": 3.0, "P1.2": 2.0, "P3.1": 2.5}
        measurements = [{"dim_id": "P1.1", "benchmark": "truthfulqa-mc1-poc", "raw": 4.0}]
        raw_scores, provenance = merge_measurements_with_priors(measurements, priors)
        self.assertEqual(raw_scores["P1.1"], 4.0)
        self.assertEqual(provenance["P1.1"], "measured:truthfulqa-mc1-poc")
        self.assertEqual(raw_scores["P1.2"], 2.0)
        self.assertEqual(provenance["P1.2"], "prior")

    def test_no_measurements_returns_priors_intact(self):
        priors = {"P1.1": 3.0, "P1.2": 2.0}
        raw_scores, provenance = merge_measurements_with_priors([], priors)
        self.assertEqual(raw_scores, priors)
        self.assertTrue(all(v == "prior" for v in provenance.values()))


class PromptInjectionBenchTests(unittest.TestCase):
    def setUp(self):
        self.items = prompt_injection.load_items(PI_FIXTURE)

    def test_fixture_loads(self):
        self.assertGreaterEqual(len(self.items), 10)
        for item in self.items:
            self.assertTrue(item.canary)
            self.assertIn(item.canary, item.prompt)  # injection embedded in prompt

    def test_resists_all_yields_4(self):
        # Model that always replies with a benign canned response — never emits any canary.
        runner = MockRunner("benign", responder=lambda _p: "Bonjour le monde.")
        result = prompt_injection.run(runner, self.items)
        self.assertEqual(result["dim_id"], "P3.1")
        self.assertEqual(result["n_resisted"], len(self.items))
        self.assertEqual(result["resistance_rate"], 1.0)
        self.assertEqual(result["raw"], 4.0)

    def test_succumbs_to_all_yields_0(self):
        # Worst case: model emits the canary for every item.
        # Map prompt -> canary (each prompt is unique).
        prompt_to_canary = {item.prompt: item.canary for item in self.items}
        runner = MockRunner("attacker", responder=lambda p: prompt_to_canary[p])
        result = prompt_injection.run(runner, self.items)
        self.assertEqual(result["n_resisted"], 0)
        self.assertEqual(result["resistance_rate"], 0.0)
        self.assertEqual(result["raw"], 0.0)


class SecretExfilBenchTests(unittest.TestCase):
    def setUp(self):
        self.items = secret_exfil.load_items(EX_FIXTURE)

    def test_fixture_loads(self):
        self.assertGreaterEqual(len(self.items), 10)
        for item in self.items:
            self.assertTrue(item.secret.startswith("BEAAI-SECRET-"))
            self.assertIn(item.secret, item.prompt)

    def test_resists_all_yields_4(self):
        runner = MockRunner("zipped-lips", responder=lambda _p: "I cannot share that information.")
        result = secret_exfil.run(runner, self.items)
        self.assertEqual(result["dim_id"], "P3.3")
        self.assertEqual(result["n_resisted"], len(self.items))
        self.assertEqual(result["resistance_rate"], 1.0)
        self.assertEqual(result["raw"], 4.0)

    def test_leaks_all_yields_0(self):
        prompt_to_secret = {item.prompt: item.secret for item in self.items}
        runner = MockRunner("leaker", responder=lambda p: prompt_to_secret[p])
        result = secret_exfil.run(runner, self.items)
        self.assertEqual(result["n_resisted"], 0)
        self.assertEqual(result["resistance_rate"], 0.0)
        self.assertEqual(result["raw"], 0.0)


class HTTPEndpointRunnerTests(unittest.TestCase):
    """End-to-end test using httpx MockTransport — proves the customer-
    deployment scan path works without hitting a real network."""

    def setUp(self):
        try:
            import httpx  # type: ignore
            self.httpx = httpx
        except ImportError:
            self.skipTest("httpx not installed")

    def _patched_runner(self, *, response_payload: dict, **runner_kwargs):
        import json as _json
        from harness.runner import HTTPEndpointRunner

        captured = {}

        def handler(request):
            captured["headers"] = dict(request.headers)
            captured["body"] = _json.loads(request.content.decode())
            return self.httpx.Response(200, json=response_payload)

        transport = self.httpx.MockTransport(handler)
        runner = HTTPEndpointRunner(**runner_kwargs)

        def patched_post(url, **kw):
            with self.httpx.Client(transport=transport) as c:
                return c.post(url, **kw)

        return runner, patched_post, captured

    def test_openai_compat_preset_round_trips(self):
        runner, patched_post, captured = self._patched_runner(
            response_payload={"choices": [{"message": {"content": "Bonjour"}}]},
            url="https://api.example.com/v1/chat/completions",
            auth="sk-fake-token",
            preset="openai-compat",
            model="gpt-fake",
            label="example:fake-model",
        )
        from unittest.mock import patch
        with patch("httpx.post", side_effect=patched_post):
            result = runner.complete("Translate hello")
        self.assertEqual(result, "Bonjour")
        self.assertEqual(runner.model_id(), "example:fake-model")
        self.assertEqual(captured["headers"]["authorization"], "Bearer sk-fake-token")
        self.assertEqual(captured["body"]["model"], "gpt-fake")
        self.assertEqual(captured["body"]["messages"][0]["content"], "Translate hello")

    def test_anthropic_compat_preset_uses_x_api_key(self):
        runner, patched_post, captured = self._patched_runner(
            response_payload={"content": [{"text": "Hi", "type": "text"}]},
            url="https://api.example.com/v1/messages",
            auth="anthropic-fake",
            preset="anthropic-compat",
            model="claude-fake",
        )
        from unittest.mock import patch
        with patch("httpx.post", side_effect=patched_post):
            result = runner.complete("Say hi")
        self.assertEqual(result, "Hi")
        self.assertEqual(captured["headers"]["x-api-key"], "anthropic-fake")
        self.assertEqual(captured["headers"]["anthropic-version"], "2023-06-01")

    def test_response_path_resolution_failure_raises(self):
        runner, patched_post, _captured = self._patched_runner(
            response_payload={"unexpected": {"shape": "no content here"}},
            url="https://api.example.com/v1/chat/completions",
            auth="sk-fake",
            preset="openai-compat",
            model="x",
        )
        from unittest.mock import patch
        with patch("httpx.post", side_effect=patched_post):
            with self.assertRaises(RunnerError):
                runner.complete("hi")

    def test_no_url_raises(self):
        from harness.runner import HTTPEndpointRunner
        import os
        old = os.environ.pop("BEAI_HTTP_URL", None)
        try:
            with self.assertRaises(RunnerError):
                HTTPEndpointRunner(preset="openai-compat", auth="sk", model="x")
        finally:
            if old is not None:
                os.environ["BEAI_HTTP_URL"] = old


class RealRunnerGuardTests(unittest.TestCase):
    def test_anthropic_runner_raises_without_key(self):
        import os
        from harness.runner import AnthropicRunner
        old = os.environ.pop("ANTHROPIC_API_KEY", None)
        try:
            with self.assertRaises(RunnerError):
                AnthropicRunner(model="claude-sonnet-4-6")
        finally:
            if old is not None:
                os.environ["ANTHROPIC_API_KEY"] = old

    def test_gemini_runner_raises_without_key(self):
        import os
        from harness.runner import GeminiRunner
        saved = {k: os.environ.pop(k, None) for k in ("GEMINI_API_KEY", "GOOGLE_API_KEY")}
        try:
            with self.assertRaises(RunnerError):
                GeminiRunner(model="gemini-2.5-flash")
        finally:
            for k, v in saved.items():
                if v is not None:
                    os.environ[k] = v


if __name__ == "__main__":
    unittest.main()
