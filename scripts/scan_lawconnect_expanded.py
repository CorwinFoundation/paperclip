"""Path B — LawConnect *expanded* P1 scan (BEAAA-197 Track 1).

Drives P1.2 (citation integrity), P1.3 (perturbation), and P1.5 (confidence
calibration) on any of three LawConnect surfaces (atticus / ailawyer /
matter_types). Mirrors the per-prompt etiquette of the existing
scan_lawconnect_atticus.py / scan_lawconnect_ailawyer.py scripts.

Per BEAAA-197 plan, this script:
  - Imports the LawConnectAtticusRunner + LawConnectAILawyerRunner classes
    from the existing adapters (no duplication of HTTP protocol code).
  - Adds a LawConnectMatterTypesRunner for the 3rd surface
    (matter-types stage, where the board surfaced the `Bonjour`-reproduces
    finding in BEAAA-182 r2).
  - Calls into harness.bench.{citation_integrity_legal,perturbation,
    confidence_calibration}.run via a single dispatcher.
  - Supports --mock-judge for dry-run validation when ANTHROPIC_API_KEY is
    not present; output artifact records `judge_model="mock"` so the result
    is never confused with a real measurement.

Usage:

    # Dry-run (mock judge, --limit 3):
    python3 scripts/scan_lawconnect_expanded.py \\
        --surface atticus --dim p1_5 --limit 3 --mock-judge \\
        --out-dir /tmp/lawconnect_expanded

    # Real run (requires ANTHROPIC_API_KEY):
    python3 scripts/scan_lawconnect_expanded.py \\
        --surface ailawyer --dim p1_2 --limit 30 \\
        --out-dir artifacts/beaaa-197/ailawyer

Compute envelope:
  - Mock-judge: $0 (subject calls still cost cents-per-call to LawConnect,
    but no Anthropic spend).
  - Real-judge dry-run (--limit 3 × 3 surfaces × 3 dims): ~30 subject calls +
    ~50 judge calls ≈ $2-3.
  - Full 5/15 run (30 P1.2 + 100 P1.5 + 20×5 P1.3) × 3 surfaces ≈ $35-50.

BEAAA-197 stays under the BEAAA-146 $100 cap.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from harness.bench import citation_integrity_legal  # noqa: E402
from harness.bench import confidence_calibration  # noqa: E402
from harness.bench import perturbation  # noqa: E402
from harness.runner import AnthropicRunner, MockRunner, ModelRunner, RunnerError  # noqa: E402

# Reuse the existing runners (don't duplicate the HTTP protocol).
sys.path.insert(0, str(REPO / "scripts"))
from scan_lawconnect_atticus import (  # noqa: E402
    ATTICUS_BASE,
    SYDNEY_LOCATION_ID,
    USER_AGENT,
    LawConnectAtticusRunner,
)
from scan_lawconnect_ailawyer import (  # noqa: E402
    LawConnectAILawyerRunner,
)


# ----------------------------------------------------------------------------
# 3rd surface: LawConnect matter-types stage.
# This surface emits an LLM response on the `/api/atticus` matter-types call
# (location-set → matter-type list). Board surfaced the `Bonjour`-reproduces
# finding here in BEAAA-182 r2.
# ----------------------------------------------------------------------------

class LawConnectMatterTypesRunner(ModelRunner):
    """Runner for the matter-types stage of atticus.ask.lawconnect.com.

    Per-prompt protocol:
      1. POST /api/atticus  event=session:init
      2. POST /api/atticus  event=location  (with `prompt` packed in a structured
         location-set call) → returns LLM-generated matter-type list

    The matter-type list is the LLM output we score.

    NOTE: this surface is REACHABLE-PENDING-VERIFY. The protocol shape is
    inferred from the BEAAA-182 r2 finding; first live call should be
    smoke-tested with --smoke before running a full --limit N scan.
    """

    def __init__(
        self,
        *,
        location_id: str = SYDNEY_LOCATION_ID,
        timeout_s: float = 30.0,
        rate_limit_s: float = 1.0,
        label: str | None = None,
    ):
        self._location_id = location_id
        self._timeout_s = timeout_s
        self._rate_limit_s = rate_limit_s
        self._label = label or "lawconnect-atticus:matter-types-stage"
        self._last_call_ts = 0.0

    def model_id(self) -> str:
        return self._label

    def _polite_sleep(self) -> None:
        elapsed = time.monotonic() - self._last_call_ts
        if elapsed < self._rate_limit_s:
            time.sleep(self._rate_limit_s - elapsed)
        self._last_call_ts = time.monotonic()

    def complete(self, prompt: str) -> str:
        try:
            import httpx  # type: ignore
        except ImportError as exc:
            raise RunnerError("httpx not installed; pip install httpx") from exc

        client_sid = str(uuid.uuid4())
        headers = {
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
            "Origin": ATTICUS_BASE,
            "Referer": f"{ATTICUS_BASE}/lawconnect",
        }

        with httpx.Client(timeout=self._timeout_s, follow_redirects=False) as client:
            # 1. session:init
            self._polite_sleep()
            r = client.post(
                f"{ATTICUS_BASE}/api/atticus",
                params={"sessionId": client_sid},
                headers=headers,
                json={
                    "event": "session:init",
                    "sessionId": client_sid,
                    "payload": {
                        "page_width": "1280",
                        "language": "en-AU",
                        "referrer": "https://lawconnect.com/chat",
                        "user_agent": USER_AGENT,
                    },
                },
            )
            if r.status_code >= 400:
                raise RunnerError(
                    f"matter-types session:init HTTP {r.status_code}: {r.text[:300]}"
                )
            init_data = r.json()
            lead_sid = init_data["sessionId"]

            # 2. location with prompt embedded as the user_description hint.
            # The matter-type list returned by the LLM is conditioned on this
            # free-text description (per BEAAA-182 r2 manual repro).
            self._polite_sleep()
            r = client.post(
                f"{ATTICUS_BASE}/api/atticus",
                params={"sessionId": lead_sid},
                headers=headers,
                json={
                    "event": "location",
                    "sessionId": lead_sid,
                    "payload": {
                        "locationId": self._location_id,
                        "user_description": prompt,
                    },
                },
            )
            if r.status_code >= 400:
                raise RunnerError(
                    f"matter-types location HTTP {r.status_code}: {r.text[:300]}"
                )
            loc_data = r.json()
            return self._extract_matter_types(loc_data)

    @staticmethod
    def _extract_matter_types(loc_data: dict) -> str:
        """Pull the matter-type list (labels + descriptions) into a single string."""
        chunks: list[str] = []
        lead = loc_data.get("lead", {})
        state = lead.get("state", "?")
        chunks.append(f"[state={state}]")
        for q in lead.get("questions", []):
            for sub in q.get("questions", []) or [q]:
                qtext = sub.get("question", "")
                if qtext:
                    chunks.append(qtext)
                for opt in sub.get("options", []) or []:
                    label = opt.get("label", "")
                    if label:
                        chunks.append(f"- {label}")
                    desc = opt.get("description", "")
                    if desc:
                        chunks.append(f"  {desc}")
        return "\n".join(chunks).strip() or "[matter-types stage returned empty]"


# ----------------------------------------------------------------------------
# Mock-judge: used for dry-run validation when no Anthropic creds available.
# Always emits structured YES/YES so the bench wrappers exercise the full
# scoring path without inflating real-world raw scores.
# ----------------------------------------------------------------------------

def _mock_judge_responder(prompt: str) -> str:
    if "PLAUSIBILITY:" in prompt or "Axis 1" in prompt:
        return "PLAUSIBILITY: YES\nSEMANTIC: YES"
    if "RESPONSE" in prompt and "BUCKET" in prompt:
        # Count response markers, return all in bucket 1
        n_responses = prompt.count("[Response ")
        if n_responses == 0:
            n_responses = 15  # fallback
        return "\n".join(f"RESPONSE {i + 1}: BUCKET 1" for i in range(n_responses))
    # Generic fallback
    return "YES"


def make_judge(*, mock: bool, label_suffix: str = "") -> ModelRunner:
    if mock:
        return MockRunner("p1-expanded-mock-judge" + label_suffix, _mock_judge_responder)
    return AnthropicRunner("claude-haiku-4-5-20251001", max_tokens=256, temperature=0.0)


# ----------------------------------------------------------------------------
# Subject-runner factory.
# ----------------------------------------------------------------------------

def make_subject(surface: str, *, mock: bool, rate_limit_s: float = 1.0) -> ModelRunner:
    if mock:
        return MockRunner(
            f"lawconnect-{surface}-mock",
            lambda p: f"[mock-{surface}] received: {p[:80]}",
        )
    if surface == "atticus":
        return LawConnectAtticusRunner(rate_limit_s=rate_limit_s)
    if surface == "ailawyer":
        return LawConnectAILawyerRunner(rate_limit_s=rate_limit_s)
    if surface == "matter_types":
        return LawConnectMatterTypesRunner(rate_limit_s=rate_limit_s)
    raise ValueError(f"unknown surface: {surface}")


# Subject runners that don't expose a temperature knob need a runner factory
# that ignores temperature. The perturbation family loses the temperature axis
# (so 5 paraphrasings × 1 effective sample = 5 responses per seed instead of
# 15) but keeps the paraphrase axis. Result artifact records the degradation.

def make_subject_factory(surface: str, *, mock: bool, rate_limit_s: float = 1.0):
    def _factory(_t: float) -> ModelRunner:
        return make_subject(surface, mock=mock, rate_limit_s=rate_limit_s)
    return _factory


# ----------------------------------------------------------------------------
# Calibration corpus loader (P1.5).
# ----------------------------------------------------------------------------

def _load_calibration_items(yaml_path: Path, limit: int | None) -> list[confidence_calibration.CalibrationItem]:
    import yaml

    data = yaml.safe_load(yaml_path.read_text())
    corpus = data.get("corpus", {})
    items_raw = corpus.get("items", [])
    if limit:
        items_raw = items_raw[:limit]
    out: list[confidence_calibration.CalibrationItem] = []
    for item in items_raw:
        accept_substrings = [s.lower() for s in item.get("accept_substrings", [])]
        reject_substrings = [s.lower() for s in item.get("reject_substrings", [])]

        def _make_check(accepts: list[str], rejects: list[str]):
            def _check(response: str) -> bool:
                # Strip trailing confidence float, lowercase for substring match
                lines = [ln for ln in response.strip().splitlines() if ln.strip()]
                # Drop last line if it's a bare float
                if lines:
                    last = lines[-1].strip()
                    try:
                        v = float(last)
                        if 0.0 <= v <= 1.0:
                            lines = lines[:-1]
                    except ValueError:
                        pass
                cleaned = "\n".join(lines).lower()
                # Reject substrings override
                if any(rs in cleaned for rs in rejects):
                    return False
                return any(ac in cleaned for ac in accepts)

            return _check

        out.append(
            confidence_calibration.CalibrationItem(
                id=item["id"],
                prompt=item["prompt"],
                correct_check=_make_check(accept_substrings, reject_substrings),
                gold=", ".join(item.get("accept_substrings", []))[:120],
            )
        )
    return out


# ----------------------------------------------------------------------------
# Dim dispatcher.
# ----------------------------------------------------------------------------

def run_p1_2(subject: ModelRunner, judge: ModelRunner, *, limit: int | None) -> dict[str, Any]:
    """P1.2 via v0.1 surface (judge-priors-bounded; no citation-index lookup)."""
    yaml_path = REPO / "rubric" / "probe_mappings" / "citation_integrity_legal.yaml"
    seeds = citation_integrity_legal.load_seeds(yaml_path)
    if limit:
        seeds = seeds[:limit]
    return citation_integrity_legal.run(subject, judge, seeds, ensemble_n=1)


def run_p1_3(
    surface: str, judge: ModelRunner, *, mock: bool, rate_limit_s: float, limit: int | None
) -> dict[str, Any]:
    """P1.3 perturbation. Temperature axis degrades to 1 sample on customer surfaces."""
    yaml_path = REPO / "rubric" / "probe_mappings" / "perturbation_seeds.yaml"
    seeds = perturbation.load_seeds(yaml_path)
    if limit:
        seeds = seeds[:limit]
    factory = make_subject_factory(surface, mock=mock, rate_limit_s=rate_limit_s)
    # Customer surface doesn't expose temperature → collapse to single sample per paraphrase.
    return perturbation.run(factory, judge, seeds, temperatures=(0.0,))


def run_p1_5(subject: ModelRunner, *, limit: int | None) -> dict[str, Any]:
    """P1.5 confidence calibration via 100-item BEAAA-208 corpus."""
    yaml_path = REPO / "rubric" / "probe_mappings" / "confidence_calibration.yaml"
    items = _load_calibration_items(yaml_path, limit)
    return confidence_calibration.run(subject, items)


# ----------------------------------------------------------------------------
# Main.
# ----------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--surface",
        choices=("atticus", "ailawyer", "matter_types"),
        required=True,
        help="LawConnect surface to scan",
    )
    parser.add_argument(
        "--dim",
        choices=("p1_2", "p1_3", "p1_5", "all"),
        default="all",
        help="Which P1 dim to score (default: all 3)",
    )
    parser.add_argument(
        "--limit", type=int, default=0, help="cap items per dim (0=all)"
    )
    parser.add_argument(
        "--mock-judge",
        action="store_true",
        help="use deterministic mock judge instead of Anthropic (for dry-runs)",
    )
    parser.add_argument(
        "--mock-subject",
        action="store_true",
        help="use deterministic mock subject instead of live LawConnect HTTP",
    )
    parser.add_argument(
        "--out-dir", default="/tmp/lawconnect_expanded", help="output directory"
    )
    parser.add_argument(
        "--rate-limit-s",
        type=float,
        default=1.0,
        help="min seconds between subject POSTs",
    )
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="single-prompt smoke test against the surface, no scoring",
    )
    args = parser.parse_args()

    if not args.mock_judge and not os.environ.get("ANTHROPIC_API_KEY"):
        print(
            "ERROR: ANTHROPIC_API_KEY not set. Re-run with --mock-judge for dry-run, "
            "or export the key for a real-judge measurement.",
            file=sys.stderr,
        )
        return 2

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    subject = make_subject(
        args.surface, mock=args.mock_subject, rate_limit_s=args.rate_limit_s
    )

    if args.smoke:
        print(f"--- smoke test on surface={args.surface} ---", file=sys.stderr)
        text = subject.complete("My landlord wants to evict me without notice. What are my rights?")
        print(text)
        return 0

    judge = make_judge(mock=args.mock_judge)
    limit: int | None = args.limit if args.limit else None
    dims_to_run = ["p1_2", "p1_3", "p1_5"] if args.dim == "all" else [args.dim]

    started_at = datetime.now(timezone.utc)
    results: dict[str, Any] = {
        "issue": "BEAAA-197",
        "track": "Track 1",
        "surface": args.surface,
        "subject_id": subject.model_id(),
        "judge_id": judge.model_id(),
        "mock_judge": args.mock_judge,
        "mock_subject": args.mock_subject,
        "started_at": started_at.isoformat(),
        "scan_kind": "ARE Spot-Scan (P1.2+P1.3+P1.5 expanded)",
        "dims": {},
    }

    if "p1_2" in dims_to_run:
        print(f"  P1.2 citation integrity (subject={args.surface})...", file=sys.stderr)
        results["dims"]["P1.2"] = run_p1_2(subject, judge, limit=limit)
    if "p1_3" in dims_to_run:
        print(f"  P1.3 perturbation (subject={args.surface})...", file=sys.stderr)
        results["dims"]["P1.3"] = run_p1_3(
            args.surface, judge, mock=args.mock_subject,
            rate_limit_s=args.rate_limit_s, limit=limit,
        )
    if "p1_5" in dims_to_run:
        print(f"  P1.5 confidence calibration (subject={args.surface})...", file=sys.stderr)
        results["dims"]["P1.5"] = run_p1_5(subject, limit=limit)

    results["finished_at"] = datetime.now(timezone.utc).isoformat()
    suffix = "-MOCK" if args.mock_judge or args.mock_subject else ""
    artifact = out_dir / f"lawconnect_{args.surface}_expanded{suffix}.json"
    artifact.write_text(json.dumps(results, indent=2, default=str))

    # Summary table
    print()
    print(f"# BEAAA-197 expanded — lawconnect:{args.surface}")
    print()
    print(f"_judge={results['judge_id']}, subject={results['subject_id']}_")
    print()
    print("| Dim   | Raw 0-4 | Notes |")
    print("|-------|--------:|-------|")
    for dim_id, dim_result in results["dims"].items():
        raw = dim_result.get("raw")
        if raw is None:
            raw = dim_result.get("dim_raw")
        raw_str = f"**{raw:.1f}**" if isinstance(raw, (int, float)) else "**N/A**"
        notes = []
        if "n_prompts" in dim_result:
            notes.append(f"n={dim_result['n_prompts']}")
        if "n_items" in dim_result:
            notes.append(f"n={dim_result['n_items']}")
        if "n_seeds" in dim_result:
            notes.append(f"seeds={dim_result['n_seeds']}")
        if "coverage_label" in dim_result:
            notes.append(dim_result["coverage_label"])
        print(f"| {dim_id} | {raw_str} | {'; '.join(notes)} |")
    print()
    print(f"_Run artifact: {artifact}_")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
