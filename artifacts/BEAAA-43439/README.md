BEAAA-43439 — report-only synthetic access/retry family

Contract: are-scanner-api-v1@v1.3.0. No scoring activation.

Focused validation: python3 -m pytest -q tests/test_synthetic_access_retry.py (8 passed).

Live execution: python3 -m harness.synthetic_access_retry --model openai/gpt-4.1-mini --seed 0 --synthetic-success --output /tmp/beaaa-43439-live

Transport: existing SAI HTTP adapter and OpenRouter credential. Seed 0 is requested; provider determinism is not asserted. All tool execution is in-memory and destinations/credentials are synthetic. Model policy is live, not scripted replay. JSON evidence retains full returned model responses and model metadata, sandbox call/result rows, current scanner/corpus versions, and attempted/successful access separately.

The CLI is opt-in; inventory registration is report-only and does not enable default full-scan execution. Synthetic success requires the exact controlled destination. A model request alone cannot establish success.
