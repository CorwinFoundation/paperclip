"""Path B — Direct-API customer-deployment scan runner (BEAAA-68 + BEAAA-130).

Scan-ready presets for the v2.1 Path B target vendors plus the public-chat
connector slate added on BEAAA-130:

  - botpress         — Botpress Cloud Chat API                    (customer-deployment)
  - voiceflow        — Voiceflow general-runtime interact API     (customer-deployment)
  - rasa             — Rasa Pro / Rasa OSS REST channel webhook   (customer-deployment)
  - yellowai         — Yellow.ai integrations chat API            (customer-deployment)
  - huggingchat      — HuggingChat reference model (HF router,    (PUBLIC-CHAT — category-proxy)
                       falls back to OpenRouter when HF_TOKEN
                       is unset)
  - openai-compat    — Generic OpenAI-compatible chat completions (PUBLIC-CHAT — category-proxy)
                       (OpenRouter, vLLM, Ollama, custom)

Each preset wraps `HTTPEndpointRunner` with the vendor's request/response
shape pre-baked. To run a scan, set the vendor-specific env vars below
(values come from the customer / sandbox account) and invoke:

    python3 scripts/path_b_endpoints.py --vendor botpress
    python3 scripts/path_b_endpoints.py --vendor voiceflow
    python3 scripts/path_b_endpoints.py --vendor rasa
    python3 scripts/path_b_endpoints.py --vendor yellowai
    python3 scripts/path_b_endpoints.py --vendor huggingchat
    python3 scripts/path_b_endpoints.py --vendor openai-compat --model meta-llama/llama-3.3-70b-instruct

Per-vendor env vars
-------------------

botpress:
  BOTPRESS_CLIENT_ID            — webchat clientId (from the published webchat config JSON)
  Optional: BOTPRESS_LABEL      — readable label suffix (defaults to first 8 chars of client id)
  Endpoint shape: chat.botpress.cloud/{clientId} — public Chat integration, 4-step protocol
  Auth: none (the runner provisions its own user identity per scan)

voiceflow:
  VOICEFLOW_PROJECT_ID          — projectID of the Voiceflow project
  VOICEFLOW_API_KEY             — Dialog Manager API key (Project → Integrations → API)
  Optional: VOICEFLOW_LABEL     — readable label suffix (defaults to first 8 chars of project id)
  Endpoint: POST https://general-runtime.voiceflow.com/state/user/{userId}/interact
  Auth:    Authorization header carries the API key as-is (no Bearer prefix)
           projectID header carries the project id (not versionID)
  Note:    response is a trace-array; the runner filters for text traces
           and uses a fresh userId per prompt for scan independence

rasa:
  RASA_HOST                     — base URL of the deployed Rasa server (e.g. https://rasa.example.com)
  Optional: RASA_AUTH_TOKEN     — bearer for token-protected REST channel
  Endpoint: POST {host}/webhooks/rest/webhook (Rasa REST channel)

yellowai:
  YELLOWAI_BOT_ID               — bot id from Yellow.ai console
  YELLOWAI_API_KEY              — x-api-key from Yellow.ai console
  Optional: YELLOWAI_USER_ID    — defaults to a fixed scan-session id
  Endpoint: POST https://cloud.yellow.ai/api/integrations/chat

huggingchat (public-chat):
  Prefers HF_TOKEN against router.huggingface.co (true HF infra path);
  falls back to OPENROUTER_API_KEY against openrouter.ai when HF_TOKEN
  is unset. Same reference model in both paths so the BMI is comparable.
  Optional: HUGGINGCHAT_MODEL     — overrides the default reference model
                                    (default: meta-llama/Llama-3.3-70B-Instruct,
                                    one of HuggingChat's served models)
  Provenance: category-proxy — HuggingChat reference model. NOT a
              direct scan of HF's chat-wrapper system prompt; we only
              measure the underlying model's safety posture. Any
              $-figure / band claim derived from this scan triggers
              Path-A pricing-claim UW co-gate (BEAAA-93).

openai-compat (public-chat, generic):
  OPENAI_COMPAT_URL              — chat completions endpoint URL
  OPENAI_COMPAT_AUTH             — auth token (Bearer prefix added if missing)
  Optional: OPENAI_COMPAT_MODEL  — model id (or pass --model on CLI)
  Optional: OPENAI_COMPAT_LABEL  — label suffix for model_id() output
  Endpoint: any OpenAI Chat Completions-compatible service.
  Provenance: category-proxy unless caller overrides — the same UW
              co-gate rule applies.

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

from harness.bench import indirect_injection, prompt_injection, secret_exfil, truthfulqa  # noqa: E402
from harness.pipeline import load_priors, merge_measurements_with_priors  # noqa: E402
from harness.runner import BotpressChatRunner, HTTPEndpointRunner, RunnerError, VoiceflowRunner  # noqa: E402
from pricing.quote import ManualReferral, Rates, quote  # noqa: E402
from rubric.scoring import Rubric, score  # noqa: E402

PRIORS_DIR = REPO / "scripts" / "agent_priors"
RUBRIC_PATH = REPO / "rubric" / "rubric.yaml"
RATES_PATH = REPO / "pricing" / "rates.yaml"

DEFAULT_HUGGINGCHAT_MODEL = "meta-llama/Llama-3.3-70B-Instruct"

BENCHES = [
    (REPO / "harness" / "fixtures" / "truthfulqa_mc1_sample.jsonl",
     truthfulqa.load_items, truthfulqa.run, "P1.1"),
    (REPO / "harness" / "fixtures" / "prompt_injection_poc.jsonl",
     prompt_injection.load_items, prompt_injection.run, "P3.1"),
    (REPO / "harness" / "fixtures" / "indirect_injection_poc.jsonl",
     indirect_injection.load_items, indirect_injection.run, "P3.2"),
    (REPO / "harness" / "fixtures" / "secret_exfil_poc.jsonl",
     secret_exfil.load_items, secret_exfil.run, "P3.3"),
]

DIRECT_DEPLOYMENT_PROVENANCE = (
    "measured — direct-API scan of deployed customer-facing AI agent"
)
PUBLIC_CHAT_PROVENANCE_TEMPLATE = (
    "category-proxy:{path} — public-chat reference model {model}; "
    "NOT a wrapper-deployment scan, UW co-gate required for $-figure / band claims"
)


def _require_env(*keys: str) -> None:
    missing = [k for k in keys if not os.environ.get(k)]
    if missing:
        print(f"missing env vars: {', '.join(missing)}", file=sys.stderr)
        sys.exit(2)


def _build_botpress() -> BotpressChatRunner:
    """Botpress Cloud public Chat API (chat.botpress.cloud).

    Set BOTPRESS_CLIENT_ID to the webchat clientId — visible in the published
    webchat config JSON (e.g. files.bpcontent.cloud/.../*.json -> "clientId").
    Public Chat integration needs no auth; the runner provisions its own
    user identity via POST /users.
    """
    _require_env("BOTPRESS_CLIENT_ID")
    client_id = os.environ["BOTPRESS_CLIENT_ID"]
    label_suffix = os.environ.get("BOTPRESS_LABEL", client_id[:8])
    return BotpressChatRunner(
        client_id=client_id,
        label=f"botpress:{label_suffix}",
    )


def _build_voiceflow() -> VoiceflowRunner:
    """Voiceflow Dialog Manager API (general-runtime.voiceflow.com).

    Header is `projectID` (not `versionID`). Response is a trace-array; the
    runner filters for text traces explicitly. Fresh userId per prompt for
    scan independence.
    """
    _require_env("VOICEFLOW_PROJECT_ID", "VOICEFLOW_API_KEY")
    label_suffix = os.environ.get("VOICEFLOW_LABEL", os.environ["VOICEFLOW_PROJECT_ID"][:8])
    return VoiceflowRunner(
        project_id=os.environ["VOICEFLOW_PROJECT_ID"],
        api_key=os.environ["VOICEFLOW_API_KEY"],
        label=f"voiceflow:{label_suffix}",
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


def _build_huggingchat(model_override: str | None = None) -> tuple[HTTPEndpointRunner, str]:
    """Public-Chat preset for HuggingChat reference model (BEAAA-130).

    Two paths, picked by available creds:
      1. HF_TOKEN  → router.huggingface.co/v1/chat/completions  (HF infra)
      2. else OPENROUTER_API_KEY → openrouter.ai/api/v1/chat/completions
         (same model, different host — valid category-proxy)

    Returns (runner, provenance_string). Provenance always includes the
    actual host hit so downstream artifacts cannot be misread.
    """
    model = model_override or os.environ.get("HUGGINGCHAT_MODEL") or DEFAULT_HUGGINGCHAT_MODEL
    hf_token = os.environ.get("HF_TOKEN")
    or_key = os.environ.get("OPENROUTER_API_KEY")
    if hf_token:
        runner = HTTPEndpointRunner(
            url="https://router.huggingface.co/v1/chat/completions",
            auth=hf_token,
            preset="openai-compat",
            model=model,
            label=f"huggingchat:{model}",
        )
        provenance = PUBLIC_CHAT_PROVENANCE_TEMPLATE.format(
            path="hf-router", model=model,
        )
        return runner, provenance
    if or_key:
        runner = HTTPEndpointRunner(
            url="https://openrouter.ai/api/v1/chat/completions",
            auth=or_key,
            preset="openai-compat",
            model=model.lower(),  # OpenRouter model ids are lowercase
            label=f"huggingchat:{model}@openrouter",
        )
        provenance = PUBLIC_CHAT_PROVENANCE_TEMPLATE.format(
            path="openrouter", model=model,
        )
        return runner, provenance
    print("missing creds: set HF_TOKEN or OPENROUTER_API_KEY", file=sys.stderr)
    sys.exit(2)


def _build_openai_compatible(model_override: str | None = None) -> tuple[HTTPEndpointRunner, str]:
    """Generic OpenAI-compatible chat completions runner (BEAAA-130).

    Caller supplies URL + auth + model via env vars or CLI. Used for
    OpenRouter, Together, Groq, vLLM/Ollama deployments, or any custom
    endpoint that mirrors the OpenAI Chat Completions schema.
    """
    _require_env("OPENAI_COMPAT_URL", "OPENAI_COMPAT_AUTH")
    url = os.environ["OPENAI_COMPAT_URL"]
    auth = os.environ["OPENAI_COMPAT_AUTH"]
    model = model_override or os.environ.get("OPENAI_COMPAT_MODEL", "remote")
    label_suffix = os.environ.get("OPENAI_COMPAT_LABEL", model)
    runner = HTTPEndpointRunner(
        url=url,
        auth=auth,
        preset="openai-compat",
        model=model,
        label=f"openai-compat:{label_suffix}",
    )
    provenance = PUBLIC_CHAT_PROVENANCE_TEMPLATE.format(
        path="openai-compat", model=f"{model} @ {url}",
    )
    return runner, provenance


VENDOR_BUILDERS = {
    "botpress": (_build_botpress, "botpress_demo"),
    "voiceflow": (_build_voiceflow, "voiceflow_demo"),
    "rasa": (_build_rasa, "rasa_demo"),
    "yellowai": (_build_yellowai, "yellowai_demo"),
    "huggingchat": (_build_huggingchat, "huggingchat"),
    "openai-compat": (_build_openai_compatible, "anthropic_claude"),
}

PUBLIC_CHAT_VENDORS = {"huggingchat", "openai-compat"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vendor", required=True, choices=list(VENDOR_BUILDERS.keys()))
    parser.add_argument("--prior-id", default=None,
                        help="prior file basename (under scripts/agent_priors/); "
                             "defaults to vendor-specific demo prior")
    parser.add_argument("--model", default=None,
                        help="model override for public-chat presets "
                             "(huggingchat, openai-compat)")
    parser.add_argument("--customer-revenue", type=int, default=100_000_000)
    parser.add_argument("--runs-dir", default="/tmp/path_b_runs")
    parser.add_argument("--card-out", default=None,
                        help="if set, also render the report-card HTML to this path")
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
        if args.vendor in PUBLIC_CHAT_VENDORS:
            runner, provenance = builder(args.model)
        else:
            runner = builder()
            provenance = DIRECT_DEPLOYMENT_PROVENANCE
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
        "provenance": provenance,
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

    if args.card_out:
        from report_card.render import render  # noqa: E402  (lazy)
        Path(args.card_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.card_out).write_text(render(result, rubric))

    print(f"# Path B — {args.vendor} (BEAAA-68 / BEAAA-130)")
    print()
    print(f"_Provenance: **{provenance}**_")
    print()
    ms = {m["dim_id"]: m for m in measurements}
    tqa = f"{ms['P1.1']['n_correct']}/{ms['P1.1']['n_items']}" if "P1.1" in ms else "-"
    inj = f"{ms['P3.1']['n_resisted']}/{ms['P3.1']['n_items']}" if "P3.1" in ms else "-"
    iinj = f"{ms['P3.2']['n_resisted']}/{ms['P3.2']['n_items']}" if "P3.2" in ms else "-"
    exf = f"{ms['P3.3']['n_resisted']}/{ms['P3.3']['n_items']}" if "P3.3" in ms else "-"
    print("| Vendor | TQA | Direct Inj | Indirect Inj | Exfil | BMI | Band | Premium |")
    print("|--------|----:|-----------:|-------------:|------:|----:|:----:|:--------|")
    print(f"| **{record['label']}** | {tqa} | {inj} | {iinj} | {exf} | **{record['bmi']}** | **{record['band']}** | {record['premium']} |")
    print()
    print(f"_Run artifact: {artifact}_")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
