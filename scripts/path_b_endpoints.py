"""Path B — Direct-API customer-deployment scan runner (BEAAA-68 + BEAAA-130).

Scan-ready presets for the v2.1 Path B target vendors plus the public-chat
connector slate added on BEAAA-130:

  - botpress         — Botpress Cloud Chat API                    (customer-deployment)
  - voiceflow        — Voiceflow general-runtime interact API     (customer-deployment)
  - rasa             — Rasa Pro / Rasa OSS REST channel webhook   (customer-deployment)
  - yellowai         — Yellow.ai integrations chat API            (customer-deployment)
  - syllable         — Syllable apichat webchat API               (customer-deployment)
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
    python3 scripts/path_b_endpoints.py --vendor syllable
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

syllable:
  Optional: SYLLABLE_CLIENT_ID_KEY — Syllable-Client-Id-Key header
                                     (default: showcase demo agent key
                                     extracted from public webchat HTML)
  Optional: SYLLABLE_HOST_KEY      — Syllable-Host-Key header
                                     (default: https://syllable.ai)
  Optional: SYLLABLE_LABEL         — readable label suffix
                                     (default: meridian-insurance-claims)
  Endpoint shape:
    POST https://apichat.syllable.cloud/api/v1/chats/start_session  ->  { id }
    POST https://apichat.syllable.cloud/api/v1/chats/send_message
         { session_id, message }                                    ->  reply
  Auth: none — both header keys are public webchat demo keys (no
        customer credentials). Insurance-claims showcase agent.

huggingchat (public-chat):
  Prefers HF_TOKEN against router.huggingface.co (true HF infra path);
  falls back to OPENROUTER_API_KEY against openrouter.ai when HF_TOKEN
  is unset. Same reference model in both paths so the ARE is comparable.
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
import hashlib
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


from harness.bench import indirect_injection, prompt_injection, secret_exfil, truthfulqa  # noqa: E402
from harness.pipeline import load_priors, merge_measurements_with_priors  # noqa: E402
from harness.runner import (  # noqa: E402
    BotpressChatRunner,
    HTTPEndpointRunner,
    RunnerError,
    SyllableRunner,
    VoiceflowRunner,
)
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


def _build_syllable() -> SyllableRunner:
    """Syllable.cloud public webchat API (apichat.syllable.cloud).

    Both keys are public (extracted from the published webchat HTML +
    iframe handshake). Defaults target the Syllable showcase insurance-claims
    agent ("Jordan from Meridian Insurance"), confirmed live from this host
    in BEAAA-217.
    """
    client_id_key = os.environ.get("SYLLABLE_CLIENT_ID_KEY", "hlw3qyTAx5inYzm2")
    host_key = os.environ.get("SYLLABLE_HOST_KEY", "https://syllable.ai")
    label_suffix = os.environ.get("SYLLABLE_LABEL", "meridian-insurance-claims")
    return SyllableRunner(
        client_id_key=client_id_key,
        host_key=host_key,
        label=f"syllable:{label_suffix}",
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
    "syllable": (_build_syllable, "syllable_demo"),
    "huggingchat": (_build_huggingchat, "huggingchat"),
    "openai-compat": (_build_openai_compatible, "anthropic_claude"),
}

PUBLIC_CHAT_VENDORS = {"huggingchat", "openai-compat"}

# BEAAA-221: forensic_bundle_uri default for runs that have not yet been
# uploaded as a Paperclip attachment. Distinct from the historical
# "no-bundle" placeholder so the render-time guard can still reject the
# legacy sentinel while allowing pre-upload artifacts to be written to
# disk.
_FORENSIC_BUNDLE_PENDING = "pending-attachment-upload"

# Substrings the render-time audit guard rejects (matched in
# report_card.render.render). Mirrored here so we can reject card output
# at the CLI level too, before write.
_PLACEHOLDER_SNAPSHOT_TOKEN = "not-a-real-snapshot"
_PLACEHOLDER_BUNDLE_TOKEN = "no-bundle"


def _canonical_payload(*, vendor: str, label: str, provenance: str,
                       prior_id: str, eval_outputs: dict,
                       rubric_version: str) -> bytes:
    """Return the canonical JSON byte string that snapshot_hash hashes.

    Excludes time-varying fields (scored_at) and the hash/uri pair itself
    so the same agent + same scanner version + same priors yields the
    same hash run-over-run. Sorted keys + compact separators give us a
    stable byte-form across Python versions.
    """
    canon = {
        "vendor": vendor,
        "label": label,
        "provenance": provenance,
        "prior_id": prior_id,
        "eval_outputs": eval_outputs,
        "rubric_version": rubric_version,
    }
    return json.dumps(canon, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _compute_snapshot_hash(*, vendor: str, label: str, provenance: str,
                           prior_id: str, eval_outputs: dict,
                           rubric_version: str) -> str:
    payload = _canonical_payload(
        vendor=vendor, label=label, provenance=provenance,
        prior_id=prior_id, eval_outputs=eval_outputs,
        rubric_version=rubric_version,
    )
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _render_only(args) -> int:
    """BEAAA-221: render the card from a saved run artifact (no live scan).

    Used after uploading the artifact as a Paperclip attachment so the card
    snapshot_hash matches the canonical content of the bundle URL points at.
    """
    if not args.card_out:
        print("--render-only requires --card-out", file=sys.stderr)
        return 2
    if not args.forensic_bundle:
        print("--render-only requires --forensic-bundle <attachment-url>", file=sys.stderr)
        return 2
    bundle_uri = args.forensic_bundle
    if (_PLACEHOLDER_BUNDLE_TOKEN in bundle_uri
            or "beaai.local" in bundle_uri
            or _PLACEHOLDER_SNAPSHOT_TOKEN in bundle_uri):
        print(
            f"--forensic-bundle {bundle_uri!r} matches a legacy placeholder "
            "pattern; refusing to emit.", file=sys.stderr,
        )
        return 2

    artifact_path = Path(args.runs_dir) / f"{args.vendor}.json"
    if not artifact_path.exists():
        print(f"no saved run artifact at {artifact_path}; run a live scan first",
              file=sys.stderr)
        return 2
    record = json.loads(artifact_path.read_text())
    eval_outputs = record.get("eval_outputs")
    if eval_outputs is None:
        print(f"{artifact_path} predates BEAAA-221 (no eval_outputs); re-run scan",
              file=sys.stderr)
        return 2

    rubric = Rubric.load(RUBRIC_PATH)
    # Recompute the snapshot_hash from canonical inputs; must match the saved
    # one. If it doesn't, the artifact was tampered with (or a rubric/version
    # bump invalidated the cached hash) — fail loud rather than ship a card
    # whose hash diverges from the bundle.
    recomputed = _compute_snapshot_hash(
        vendor=record["vendor"],
        label=record["label"],
        provenance=record["provenance"],
        prior_id=record["prior_id"],
        eval_outputs=eval_outputs,
        rubric_version=rubric.version,
    )
    if recomputed != record.get("snapshot_hash"):
        print(
            f"snapshot_hash mismatch: artifact={record.get('snapshot_hash')!r} "
            f"recomputed={recomputed!r}. Refusing to render — re-run scan.",
            file=sys.stderr,
        )
        return 2

    scored_at = datetime.fromisoformat(record["scored_at"])
    result = score(
        rubric, eval_outputs,
        snapshot_hash=recomputed,
        forensic_bundle_uri=bundle_uri,
        scored_at=scored_at,
    )

    from report_card.render import render  # noqa: E402  (lazy)
    Path(args.card_out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.card_out).write_text(
        render(
            result, rubric,
            provenance=record["provenance"],
            dim_provenance=record.get("dim_provenance"),
        )
    )
    # Update artifact in place with the now-known bundle URL so subsequent
    # rehydrations carry the real value, not "pending-attachment-upload".
    record["forensic_bundle_uri"] = bundle_uri
    artifact_path.write_text(json.dumps(record, indent=2))
    print(f"# Path B — {record['vendor']} (render-only, BEAAA-221)")
    print(f"_BMI: **{record['bmi']}** band: **{record['band']}** "
          f"snapshot: `{recomputed}`_")
    print(f"_Bundle: {bundle_uri}_")
    print(f"_Card: {args.card_out}_")
    return 0


def main() -> int:
    _load_dotenv(REPO / ".env")

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
    parser.add_argument("--forensic-bundle", default=None,
                        help="forensic_bundle_uri to emit in the report card "
                             "and run artifact. Required when --card-out is "
                             "set. Must be a real attachment URL (typically "
                             "/api/attachments/{id}/content); legacy "
                             "'no-bundle' / 'beaai.local' placeholders are "
                             "rejected. Without --card-out, the run artifact "
                             "records 'pending-attachment-upload'.")
    parser.add_argument("--render-only", action="store_true",
                        help="Skip the live scan and render the card from the "
                             "saved run artifact at <runs-dir>/<vendor>.json. "
                             "Use this after uploading the artifact as an "
                             "attachment so the card's snapshot_hash matches "
                             "the bundle's content (BEAAA-221 audit-trail "
                             "consistency). Requires --card-out and "
                             "--forensic-bundle.")
    args = parser.parse_args()

    if args.render_only:
        return _render_only(args)

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
    raw_scores, dim_provenance = merge_measurements_with_priors(measurements, prior_scores)
    eval_outputs = {
        d: {"raw": r, "evidence_uri": f"https://path-b.beaai.local/{args.vendor}/{d}"}
        for d, r in raw_scores.items()
    }

    # BEAAA-221: real snapshot_hash = sha256 over canonical run inputs.
    snapshot_hash = _compute_snapshot_hash(
        vendor=args.vendor,
        label=runner.model_id(),
        provenance=provenance,
        prior_id=prior_id,
        eval_outputs=eval_outputs,
        rubric_version=rubric.version,
    )

    # forensic_bundle_uri: real attachment URL when emitting a card; the
    # pre-upload artifact records the pending marker (no placeholder).
    if args.forensic_bundle is not None:
        bundle_uri = args.forensic_bundle
        if (_PLACEHOLDER_BUNDLE_TOKEN in bundle_uri
                or "beaai.local" in bundle_uri
                or _PLACEHOLDER_SNAPSHOT_TOKEN in bundle_uri):
            print(
                f"--forensic-bundle {bundle_uri!r} matches a legacy placeholder "
                "pattern; refusing to emit. Pass a real attachment URL "
                "(see BEAAA-221).",
                file=sys.stderr,
            )
            return 2
    else:
        if args.card_out:
            print(
                "--card-out requires --forensic-bundle <real-attachment-url> "
                "(see BEAAA-221). The card cannot ship with placeholder audit "
                "values. Run the scan first without --card-out, upload the "
                "run artifact as an issue attachment, then re-run with "
                "--card-out and --forensic-bundle /api/attachments/<id>/content.",
                file=sys.stderr,
            )
            return 2
        bundle_uri = _FORENSIC_BUNDLE_PENDING

    result = score(
        rubric, eval_outputs,
        snapshot_hash=snapshot_hash,
        forensic_bundle_uri=bundle_uri,
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
        "rubric_version": rubric.version,
        "snapshot_hash": snapshot_hash,
        "forensic_bundle_uri": bundle_uri,
        "scored_at": scored_at.isoformat(),
        "bmi": result["bmi"],
        "band": result["band"]["display"],
        "posture": result["band"]["posture"],
        "premium": premium_str,
        "status": status,
        # BEAAA-221: persist the canonical inputs so the card can be re-rendered
        # post-upload (--render-only) without re-running the live scan. The
        # snapshot_hash above is sha256 over (vendor, label, provenance,
        # prior_id, eval_outputs, rubric_version) — all of which are saved
        # here, so a re-render reproduces the same hash by construction.
        "eval_outputs": eval_outputs,
        "dim_provenance": dim_provenance,
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
        Path(args.card_out).write_text(
            render(result, rubric, provenance=provenance, dim_provenance=dim_provenance)
        )

    print(f"# Path B — {args.vendor} (BEAAA-68 / BEAAA-130)")
    print()
    print(f"_Provenance: **{provenance}**_")
    print()
    ms = {m["dim_id"]: m for m in measurements}
    tqa = f"{ms['P1.1']['n_correct']}/{ms['P1.1']['n_items']}" if "P1.1" in ms else "-"
    inj = f"{ms['P3.1']['n_resisted']}/{ms['P3.1']['n_items']}" if "P3.1" in ms else "-"
    iinj = f"{ms['P3.2']['n_resisted']}/{ms['P3.2']['n_items']}" if "P3.2" in ms else "-"
    exf = f"{ms['P3.3']['n_resisted']}/{ms['P3.3']['n_items']}" if "P3.3" in ms else "-"
    print("| Vendor | TQA | Direct Inj | Indirect Inj | Exfil | ARE | Band | Premium |")
    print("|--------|----:|-----------:|-------------:|------:|----:|:----:|:--------|")
    print(f"| **{record['label']}** | {tqa} | {inj} | {iinj} | {exf} | **{record['bmi']}** | **{record['band']}** | {record['premium']} |")
    print()
    print(f"_Run artifact: {artifact}_")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
