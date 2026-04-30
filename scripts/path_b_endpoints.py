"""Path B — Direct-API customer-deployment scan runner (BEAAA-68).

Scan-ready presets for the 4 v2.1 Path B target vendors:

  - botpress     — Botpress Cloud Chat API
  - voiceflow    — Voiceflow general-runtime interact API
  - rasa         — Rasa Pro / Rasa OSS REST channel webhook
  - yellowai     — Yellow.ai integrations chat API

Each preset wraps `HTTPEndpointRunner` with the vendor's request/response
shape pre-baked. To run a scan, set the vendor-specific env vars below
(values come from the customer / sandbox account) and invoke:

    python3 scripts/path_b_endpoints.py --vendor botpress
    python3 scripts/path_b_endpoints.py --vendor voiceflow
    python3 scripts/path_b_endpoints.py --vendor rasa
    python3 scripts/path_b_endpoints.py --vendor yellowai

Per-vendor env vars
-------------------

botpress:
  BOTPRESS_BOT_ID               — bot id from Botpress Cloud
  BOTPRESS_API_KEY              — Personal Access Token (Settings → API keys)
  Optional: BOTPRESS_USER_KEY   — created on first message; auto-generated if unset
  Endpoint shape: POST https://chat.botpress.cloud/{webhookId}/messages

voiceflow:
  VOICEFLOW_PROJECT_ID          — versionID of the published Voiceflow project
  VOICEFLOW_API_KEY             — Dialog Manager API key (Project → Integrations → API)
  Optional: VOICEFLOW_USER_ID   — defaults to a fixed scan-session id
  Endpoint: POST https://general-runtime.voiceflow.com/state/user/{user}/interact?logs=on

rasa:
  RASA_HOST                     — base URL of the deployed Rasa server (e.g. https://rasa.example.com)
  Optional: RASA_AUTH_TOKEN     — bearer for token-protected REST channel
  Endpoint: POST {host}/webhooks/rest/webhook (Rasa REST channel)

yellowai:
  YELLOWAI_BOT_ID               — bot id from Yellow.ai console
  YELLOWAI_API_KEY              — x-api-key from Yellow.ai console
  Optional: YELLOWAI_USER_ID    — defaults to a fixed scan-session id
  Endpoint: POST https://cloud.yellow.ai/api/integrations/chat

Exit codes
----------
  0 — scan completed
  2 — env vars missing (vendor not configured) — does NOT consume API budget
  3 — vendor-side HTTP error before harness loop completed
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ[key.strip()] = value.strip().strip("'").strip('"')


_load_dotenv(REPO / ".env")

from harness.bench import prompt_injection, secret_exfil, truthfulqa  # noqa: E402
from harness.pipeline import load_priors, merge_measurements_with_priors  # noqa: E402
from harness.runner import HTTPEndpointRunner, RunnerError  # noqa: E402
from pricing.quote import ManualReferral, Rates, quote  # noqa: E402
from rubric.scoring import Rubric, score  # noqa: E402

PRIORS_DIR = REPO / "scripts" / "agent_priors"
RUBRIC_PATH = REPO / "rubric" / "rubric.yaml"
RATES_PATH = REPO / "pricing" / "rates.yaml"

BENCHES = [
    (REPO / "harness" / "fixtures" / "truthfulqa_mc1_sample.jsonl",
     truthfulqa.load_items, truthfulqa.run, "P1.1"),
    (REPO / "harness" / "fixtures" / "prompt_injection_poc.jsonl",
     prompt_injection.load_items, prompt_injection.run, "P3.1"),
    (REPO / "harness" / "fixtures" / "secret_exfil_poc.jsonl",
     secret_exfil.load_items, secret_exfil.run, "P3.3"),
]

PROVENANCE = "measured — direct-API scan of deployed customer-facing AI agent"


def _require_env(*keys: str) -> None:
    missing = [k for k in keys if not os.environ.get(k)]
    if missing:
        print(f"missing env vars: {', '.join(missing)}", file=sys.stderr)
        sys.exit(2)


def _build_botpress() -> HTTPEndpointRunner:
    _require_env("BOTPRESS_BOT_ID", "BOTPRESS_API_KEY")
    bot_id = os.environ["BOTPRESS_BOT_ID"]
    api_key = os.environ["BOTPRESS_API_KEY"]
    # Botpress Cloud Chat API is conversation-stateful; for a one-shot
    # turn the simplest path is the personal-access-token-authed bot
    # API. NOTE: confirm exact endpoint at scan-time — Botpress has
    # multiple shapes (chat.botpress.cloud webhook vs. api.botpress.cloud
    # bot-as-a-service). This preset targets the latter.
    url = f"https://api.botpress.cloud/v1/chat/bots/{bot_id}/converse"
    return HTTPEndpointRunner(
        url=url,
        auth=api_key,
        request_template='{"text": "{prompt}"}',
        response_path="responses[0].text",
        label=f"botpress:{bot_id}",
    )


def _build_voiceflow() -> HTTPEndpointRunner:
    _require_env("VOICEFLOW_PROJECT_ID", "VOICEFLOW_API_KEY")
    user_id = os.environ.get("VOICEFLOW_USER_ID", "beaai-scan-session")
    api_key = os.environ["VOICEFLOW_API_KEY"]
    project_id = os.environ["VOICEFLOW_PROJECT_ID"]
    url = f"https://general-runtime.voiceflow.com/state/user/{user_id}/interact?logs=on"
    return HTTPEndpointRunner(
        url=url,
        request_template='{"action": {"type": "text", "payload": "{prompt}"}, "config": {"tts": false, "stripSSML": true}}',
        response_path="[0].payload.message",
        extra_headers={
            "Authorization": api_key,
            "versionID": project_id,
        },
        # No auth via constructor — already in extra_headers above.
        auth=None,
        label=f"voiceflow:{project_id}",
    )


def _build_rasa() -> HTTPEndpointRunner:
    _require_env("RASA_HOST")
    host = os.environ["RASA_HOST"].rstrip("/")
    token = os.environ.get("RASA_AUTH_TOKEN", "")
    url = f"{host}/webhooks/rest/webhook"
    extra_headers: dict[str, str] = {}
    if token:
        extra_headers["Authorization"] = f"Bearer {token}"
    return HTTPEndpointRunner(
        url=url,
        request_template='{"sender": "beaai-scan-session", "message": "{prompt}"}',
        response_path="[0].text",
        extra_headers=extra_headers,
        auth=None,
        label=f"rasa:{host}",
    )


def _build_yellowai() -> HTTPEndpointRunner:
    _require_env("YELLOWAI_BOT_ID", "YELLOWAI_API_KEY")
    bot_id = os.environ["YELLOWAI_BOT_ID"]
    api_key = os.environ["YELLOWAI_API_KEY"]
    user_id = os.environ.get("YELLOWAI_USER_ID", "beaai-scan-session")
    url = "https://cloud.yellow.ai/api/integrations/chat"
    return HTTPEndpointRunner(
        url=url,
        request_template=(
            '{"botId": "' + bot_id + '", "userId": "' + user_id + '", '
            '"text": "{prompt}", "channel": "api"}'
        ),
        response_path="data.text",
        extra_headers={"x-api-key": api_key},
        auth=None,
        label=f"yellowai:{bot_id}",
    )


VENDOR_BUILDERS = {
    "botpress": (_build_botpress, "botpress_demo"),
    "voiceflow": (_build_voiceflow, "voiceflow_demo"),
    "rasa": (_build_rasa, "rasa_demo"),
    "yellowai": (_build_yellowai, "yellowai_demo"),
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vendor", required=True, choices=list(VENDOR_BUILDERS.keys()))
    parser.add_argument("--prior-id", default=None,
                        help="prior file basename (under scripts/agent_priors/); "
                             "defaults to vendor-specific demo prior")
    parser.add_argument("--customer-revenue", type=int, default=100_000_000)
    parser.add_argument("--runs-dir", default="/tmp/path_b_runs")
    args = parser.parse_args()

    builder, default_prior = VENDOR_BUILDERS[args.vendor]
    prior_id = args.prior_id or default_prior

    rubric = Rubric.load(RUBRIC_PATH)
    rates = Rates.load(RATES_PATH)
    customer = {
        "sector": "ENT_SAAS",
        "annual_revenue_usd": args.customer_revenue,
        "limit": {"per_claim_usd": 1_000_000, "aggregate_usd": 2_000_000},
    }
    scored_at = datetime.now(timezone.utc)

    try:
        runner = builder()
    except RunnerError as exc:
        print(f"runner build failed: {exc}", file=sys.stderr)
        return 3

    print(f"  scanning {runner.model_id()} ...", file=sys.stderr, flush=True)
    measurements = []
    for fixture, loader, run_fn, _dim in BENCHES:
        items = loader(fixture)
        try:
            measurement = run_fn(runner, items)
        except RunnerError as exc:
            print(f"  vendor-side HTTP failure on {_dim}: {exc}", file=sys.stderr)
            return 3
        measurements.append(measurement)

    prior_path = PRIORS_DIR / f"{prior_id}.json"
    if not prior_path.exists():
        # Fall back to a generic frontier prior; flag in output.
        prior_path = PRIORS_DIR / "anthropic_claude.json"
        print(f"  [warn] no prior file for '{args.prior_id or default_prior}' — "
              f"using anthropic_claude.json as generic", file=sys.stderr)
    _, prior_scores = load_priors(prior_path)
    raw_scores, _ = merge_measurements_with_priors(measurements, prior_scores)
    eval_outputs = {
        d: {"raw": r, "evidence_uri": f"https://path-b.beaai.local/{args.vendor}/{d}"}
        for d, r in raw_scores.items()
    }
    result = score(
        rubric, eval_outputs,
        snapshot_hash=f"sha256:path-b-{args.vendor}-not-a-real-snapshot",
        forensic_bundle_uri=f"https://path-b.beaai.local/{args.vendor}/no-bundle",
        scored_at=scored_at,
    )
    try:
        q = quote(result, customer, rates=rates)
        annual = q["annual_premium_usd"]
        total = q["total_with_endorsements_usd"]
        premium_str = f"${annual:,}" if annual == total else f"${annual:,} (+${total - annual:,} = ${total:,})"
        status = "indicative"
    except ManualReferral as exc:
        premium_str = f"manual referral — {exc.reason[:80]}"
        status = "manual_referral"

    record = {
        "vendor": args.vendor,
        "label": runner.model_id(),
        "provenance": PROVENANCE,
        "prior_id": prior_id,
        "bmi": result["bmi"],
        "band": result["band"]["display"],
        "posture": result["band"]["posture"],
        "premium": premium_str,
        "status": status,
        "measurements": [
            {"dim_id": m["dim_id"], "raw": m["raw"], **{k: m[k] for k in m if k.startswith("n_") or k.endswith("_rate")}}
            for m in measurements
        ],
    }
    runs_dir = Path(args.runs_dir)
    runs_dir.mkdir(parents=True, exist_ok=True)
    artifact = runs_dir / f"{args.vendor}.json"
    artifact.write_text(json.dumps(record, indent=2))

    print(f"# Path B — {args.vendor} (BEAAA-68)")
    print()
    print(f"_Provenance: **{PROVENANCE}**_")
    print()
    ms = {m["dim_id"]: m for m in measurements}
    tqa = f"{ms['P1.1']['n_correct']}/{ms['P1.1']['n_items']}" if "P1.1" in ms else "-"
    inj = f"{ms['P3.1']['n_resisted']}/{ms['P3.1']['n_items']}" if "P3.1" in ms else "-"
    exf = f"{ms['P3.3']['n_resisted']}/{ms['P3.3']['n_items']}" if "P3.3" in ms else "-"
    print("| Vendor | TQA | Inject | Exfil | BMI | Band | Premium |")
    print("|--------|----:|------:|-----:|----:|:----:|:--------|")
    print(f"| **{record['label']}** | {tqa} | {inj} | {exf} | **{record['bmi']}** | **{record['band']}** | {record['premium']} |")
    print()
    print(f"_Run artifact: {artifact}_")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
