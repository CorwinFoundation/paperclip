"""Build a REAL-DATA Trust Page v1 render (BEAAA-11926 / WI-3).

Proves the dynamic wiring end-to-end against real Phase-1 data sources:

  1. Runs rubric.scoring.score() on a golden calibration fixture against the LOCKED
     rubric 1.2.0 (locked_through 2026-08-04, WI-1 / BEAAA-11915) -> real are_total,
     band, and reproducibility envelope. (A golden fixture is a real scan on a locked
     rubric; per Trust Page spec Red Line 6 it is NOT external production proof, so
     the render is watermarked internal.)
  2. Runs post_bind_ingest.continuity_gate on a synthetic-but-real event stream ->
     real monitoring-continuity status (WI-2 / BEAAA-11916).
  3. Evaluates the WI-6 §2 warranty-eligibility rule (BEAAA-11920) -> pending.

Usage (from repo root, both on PYTHONPATH):
    PYTHONPATH=.:tools/post-bind-ingest/src python3 -m tools.trust_page.build_sample \
        --out-html /tmp/trust-page-v1.html --out-json /tmp/sample_record.json --now 2026-07-05T12:00:00+00:00
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from rubric.scoring import Rubric, score
from post_bind_ingest.continuity_gate import evaluate_attach_renew_continuity

from .verification_record import build_record
from .warranty_eligibility import evaluate_warranty_eligibility

REPO = Path(__file__).resolve().parents[2]
DEFAULT_FIXTURE = "G1"       # golden known-good fixture (Standard band)
VERIFICATION_CADENCE_DAYS = 30

WATERMARK = (
    "INTERNAL — real scan on locked rubric 1.2.0 against golden calibration fixture "
    "{fid}. Not a partner production scan (Trust Page Red Line 6). Visual harmonized to "
    "the BEAAA-11928 locked design. Not for external send until a real partner intake "
    "scan on the locked rubric and outside-counsel sign-off complete (BEAAA-11917 lane)."
)


def _score_fixture(rubric: Rubric, fid: str) -> dict:
    bdir = REPO / "artifacts" / "beaaa-387" / fid
    baseline = json.loads((bdir / "baseline.json").read_text())
    eval_outputs = json.loads((bdir / "eval_outputs.json").read_text())
    kw = dict(
        snapshot_hash=baseline.get("snapshot_hash"),
        forensic_bundle_uri=baseline.get("forensic_bundle_uri"),
        scanner_version=baseline.get("scanner_version"),
        corpus_version=baseline.get("corpus_version"),
        seed=baseline.get("seed"),
        model_versions=baseline.get("model_versions"),
    )
    if baseline.get("scored_at"):
        kw["scored_at"] = datetime.fromisoformat(baseline["scored_at"])
    app_path = bdir / "applicability.json"
    if app_path.exists():
        app = json.loads(app_path.read_text())
        kw["applicability_decisions"] = app.get("applicability_decisions", app)
        kw["applicability_policy_version"] = baseline.get("applicability_policy_version")
    kw = {k: v for k, v in kw.items() if v is not None}
    return score(rubric, eval_outputs, **kw)


def _continuity(now: datetime) -> str:
    """A real, contiguous, fresh heartbeat stream -> healthy."""
    def ev(seq: int, ago_s: int) -> dict:
        t = (now - timedelta(seconds=ago_s)).isoformat()
        return {
            "event_type": "agent_invocation",
            "logged_at": t,
            "payload": {
                "tenant_id": "vendor-demo", "agent_id": "agent-demo", "deployment_id": "prod",
                "collector_id": "default", "batch_seq": seq,
                "action_class": "telemetry_heartbeat", "heartbeat_at": t,
            },
        }
    events = [ev(1, 5400), ev(2, 3600), ev(3, 1200), ev(4, 120)]
    decision = evaluate_attach_renew_continuity(
        events, action_class="renew", tenant_id="vendor-demo",
        agent_id="agent-demo", deployment_id="prod", evaluated_at=now,
    )
    return decision.continuity_status


def build(fid: str, now: datetime, generated_at: str):
    rubric = Rubric.load(REPO / "rubric" / "rubric.yaml")
    locked_through = yaml.safe_load((REPO / "rubric" / "rubric.yaml").read_text())["locked_through"]
    sr = _score_fixture(rubric, fid)
    continuity_status = _continuity(now)
    warranty = evaluate_warranty_eligibility(
        continuity_status=continuity_status, band_letter=sr["band"]["letter"],
    )
    record = build_record(
        agent_name="Atlas Support Agent",
        vendor_name="Northwind AI, Inc.",
        score_response=sr,
        rubric_locked_through=locked_through,
        continuity_status=continuity_status,
        warranty=warranty,
        verification_cadence_days=VERIFICATION_CADENCE_DAYS,
        now=now,
        generated_at=generated_at,
        evidence_refs={
            "band": f"golden fixture {fid} · snapshot {sr.get('snapshot_hash','')[:12]}…",
            "continuity": "continuity_gate v0.1.0 (evidence-profile-v1@v1.0)",
            "warranty": warranty.source,
        },
    )
    return record, sr


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixture", default=DEFAULT_FIXTURE)
    ap.add_argument("--now", default="2026-07-05T12:00:00+00:00")
    ap.add_argument("--out-html", default="/tmp/trust-page-v1.html")
    ap.add_argument("--out-json", default="/tmp/sample_record.json")
    args = ap.parse_args()
    now = datetime.fromisoformat(args.now)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    generated_at = args.now

    from .render import render_html
    record, sr = build(args.fixture, now, generated_at)
    watermark = WATERMARK.format(fid=args.fixture)
    html_doc = render_html(record, watermark=watermark)

    Path(args.out_html).write_text(html_doc)
    Path(args.out_json).write_text(json.dumps(record.to_dict(), indent=2, sort_keys=True))
    print(f"fixture={args.fixture} are_total={sr['are_total']} band={record.band_display} "
          f"continuity={record.monitoring_continuity} warranty={record.warranty['status']} "
          f"stale_state={record.stale_state}")
    print(f"envelope={json.dumps(record.envelope, sort_keys=True)}")
    print(f"wrote {args.out_html} ({len(html_doc)} bytes), {args.out_json}")


if __name__ == "__main__":
    main()
