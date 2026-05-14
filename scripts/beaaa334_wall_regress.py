"""BEAAA-334 — 3-wall regression probe for the residential-proxy POC.

Acceptance per BEAAA-334:
  - Bland.ai base URL — must NOT return Error B-1 / 403 cloud-provider
    (wall family 1: cloud-IP edge refusal)
  - Casetext base URL — must NOT return 403
    (wall family 1: cloud-IP edge refusal / CF block)
  - ChatLab session loop — must NOT crater to `content-length: 0`
    within first 50 sessions @ 2s pacing
    (wall family 2: CF WAF adaptive flag)

Usage
-----

    # Baseline (no proxy, expect 3/3 walls hit):
    python3 scripts/beaaa334_wall_regress.py --out /tmp/beaaa334_baseline.json

    # With proxy (expect >= 2/3 lift):
    BEAAA_RESI_PROXY_URL='http://user:pass@gate.example.com:7000' \\
    BEAAA_RESI_PROXY_ROTATE=per_session \\
    python3 scripts/beaaa334_wall_regress.py --out /tmp/beaaa334_proxied.json

Egress routing is via `harness.egress`, so a single env-var flip toggles the
proxy on/off for all three probes. No scoring, rubric, or corpus state is
touched by this script (BEAAA-334 AC #5).
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness import egress  # noqa: E402

BLAND_URL = "https://api.bland.ai/v1/me"
CASETEXT_URL = "https://www.casetext.com/"
CHATLAB_SESSION_URL = "https://api.chatlab.com/aichat/sessionV2"
CHATLAB_ORIGIN = "https://www.chatlab.com"
CHATLAB_APIKEY = "QXyeoVfwSt"  # public client-side key, lifted from scripts/scan_chatlab_ecommerce.py
CHATLAB_ENTRY_POINT = "https://www.chatlab.com/demo/ecommerce/"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

BLAND_WALL_NEEDLES = (
    "Error B-1",
    "We do not allow connections from select Cloud Providers",
    "Access Denied",
)
CASETEXT_WALL_NEEDLES = (
    "Just a moment",
    "Attention Required",
    "cf-mitigated",
    "challenge-platform",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def probe_bland(session_id: str, timeout: float = 20.0) -> dict:
    """One GET to Bland.ai. Walled if status == 403 or body contains a known
    cloud-IP refusal needle. Follows redirects to land on the terminal page."""
    started = time.monotonic()
    try:
        resp = egress.get(
            BLAND_URL,
            headers={"Authorization": "Bearer probe-token", "User-Agent": USER_AGENT},
            timeout=timeout,
            session_id=session_id,
            follow_redirects=True,
        )
        elapsed_ms = (time.monotonic() - started) * 1000
        body = resp.text[:2000]
        walled = resp.status_code == 403 or any(n in body for n in BLAND_WALL_NEEDLES)
        return {
            "wall": "bland",
            "family": "cloud-IP edge refusal (Tier 1)",
            "url": BLAND_URL,
            "status": resp.status_code,
            "elapsed_ms": round(elapsed_ms, 1),
            "body_excerpt": body[:300],
            "walled": walled,
            "wall_indicator": (
                "status==403"
                if resp.status_code == 403
                else next((n for n in BLAND_WALL_NEEDLES if n in body), None)
            ),
            "error": None,
        }
    except Exception as exc:
        return {
            "wall": "bland",
            "family": "cloud-IP edge refusal (Tier 1)",
            "url": BLAND_URL,
            "status": None,
            "elapsed_ms": None,
            "body_excerpt": None,
            "walled": True,
            "wall_indicator": f"transport_error:{type(exc).__name__}",
            "error": str(exc)[:300],
        }


def probe_casetext(session_id: str, timeout: float = 20.0) -> dict:
    """One GET to Casetext root. Walled if status == 403 or body contains a CF needle.

    `www.casetext.com` 301-redirects to `casetext.com`; the CF challenge is
    served on the apex. Follow redirects to land on the terminal page.
    """
    started = time.monotonic()
    try:
        resp = egress.get(
            CASETEXT_URL,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html"},
            timeout=timeout,
            session_id=session_id,
            follow_redirects=True,
        )
        elapsed_ms = (time.monotonic() - started) * 1000
        body = resp.text[:2000]
        walled = resp.status_code == 403 or any(n in body for n in CASETEXT_WALL_NEEDLES)
        return {
            "wall": "casetext",
            "family": "cloud-IP edge refusal (Tier 1)",
            "url": CASETEXT_URL,
            "status": resp.status_code,
            "elapsed_ms": round(elapsed_ms, 1),
            "body_excerpt": body[:300],
            "walled": walled,
            "wall_indicator": (
                "status==403"
                if resp.status_code == 403
                else next((n for n in CASETEXT_WALL_NEEDLES if n in body), None)
            ),
            "error": None,
        }
    except Exception as exc:
        return {
            "wall": "casetext",
            "family": "cloud-IP edge refusal (Tier 1)",
            "url": CASETEXT_URL,
            "status": None,
            "elapsed_ms": None,
            "body_excerpt": None,
            "walled": True,
            "wall_indicator": f"transport_error:{type(exc).__name__}",
            "error": str(exc)[:300],
        }


def probe_chatlab_loop(n_sessions: int = 50, pacing_s: float = 2.0, timeout: float = 20.0) -> dict:
    """`n_sessions` sequential POSTs to ChatLab sessionV2 at `pacing_s` interval.

    The CF WAF adaptive-flag for ChatLab manifests as `200 + content-length: 0` /
    empty body. Wall is declared if the empty-body rate over the first
    `n_sessions` exceeds 20%, or if any 403 occurs.
    """
    sessions: list[dict] = []
    empty_count = 0
    refused_count = 0
    headers = {
        "Content-Type": "application/json",
        "Origin": CHATLAB_ORIGIN,
        "Referer": CHATLAB_ORIGIN + "/",
        "User-Agent": USER_AGENT,
    }
    started_wall = time.monotonic()
    last_call_ts = 0.0
    for i in range(n_sessions):
        # Pace at >= pacing_s between calls.
        elapsed_since_last = time.monotonic() - last_call_ts
        if elapsed_since_last < pacing_s and i > 0:
            time.sleep(pacing_s - elapsed_since_last)
        last_call_ts = time.monotonic()
        per_session_id = secrets.token_hex(8)  # binds proxy-side sticky IP per session
        client_id = str(uuid.uuid4())
        started = time.monotonic()
        record: dict = {
            "i": i,
            "started_at": _now_iso(),
            "client_id": client_id,
            "session_id": per_session_id,
        }
        try:
            resp = egress.post(
                CHATLAB_SESSION_URL,
                headers=headers,
                json={
                    "apiKey": CHATLAB_APIKEY,
                    "clientId": client_id,
                    "windowLocationOrigin": CHATLAB_ORIGIN,
                    "entryPoint": CHATLAB_ENTRY_POINT,
                    "hostName": "www.chatlab.com",
                    "customEndUserParams": None,
                    "previousSessionId": None,
                    "adminMode": False,
                },
                timeout=timeout,
                session_id=per_session_id,
            )
            elapsed_ms = (time.monotonic() - started) * 1000
            body = resp.text
            body_len = len(body)
            record.update({
                "status": resp.status_code,
                "elapsed_ms": round(elapsed_ms, 1),
                "body_len": body_len,
                "body_excerpt": body[:120] if body else "",
            })
            if resp.status_code == 403:
                refused_count += 1
                record["wall_indicator"] = "status==403"
            elif body_len == 0 or (resp.status_code == 200 and body_len < 8):
                empty_count += 1
                record["wall_indicator"] = "content-length-0"
        except Exception as exc:
            refused_count += 1
            record.update({
                "status": None,
                "elapsed_ms": None,
                "body_len": None,
                "body_excerpt": None,
                "wall_indicator": f"transport_error:{type(exc).__name__}",
                "error": str(exc)[:200],
            })
        sessions.append(record)

    total_elapsed_s = round(time.monotonic() - started_wall, 1)
    empty_rate = empty_count / n_sessions
    refused_rate = refused_count / n_sessions
    walled = refused_count > 0 or empty_rate > 0.20
    return {
        "wall": "chatlab",
        "family": "CF WAF adaptive flag (Tier 2)",
        "url": CHATLAB_SESSION_URL,
        "n_sessions": n_sessions,
        "pacing_s": pacing_s,
        "total_elapsed_s": total_elapsed_s,
        "empty_count": empty_count,
        "refused_count": refused_count,
        "empty_rate": round(empty_rate, 3),
        "refused_rate": round(refused_rate, 3),
        "walled": walled,
        "wall_indicator": (
            "empty_rate>0.20"
            if empty_rate > 0.20
            else ("refused_count>0" if refused_count > 0 else None)
        ),
        "sessions": sessions,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="BEAAA-334 3-wall regression probe")
    parser.add_argument("--out", default="/tmp/beaaa334_walls.json",
                        help="Output JSON record path")
    parser.add_argument("--chatlab-sessions", type=int, default=50,
                        help="Number of ChatLab sessions to probe (default 50 per AC)")
    parser.add_argument("--chatlab-pacing-s", type=float, default=2.0,
                        help="Seconds between ChatLab sessions (default 2.0 per AC)")
    parser.add_argument("--skip-chatlab", action="store_true",
                        help="Skip the ChatLab session loop (faster smoke run)")
    args = parser.parse_args(argv)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    session_id = secrets.token_hex(8)
    record: dict = {
        "issue": "BEAAA-334",
        "started_at": _now_iso(),
        "proxy_enabled": egress.is_enabled(),
        "proxy_rotate": os.environ.get(egress.ROTATE_ENV, ""),
        "ac_threshold_lift": "2/3 walls must NOT be walled with proxy ON",
        "results": [],
    }

    print("BEAAA-334 wall regress — proxy_enabled=", record["proxy_enabled"], flush=True)
    print("  Bland.ai ...", end=" ", flush=True)
    bland = probe_bland(session_id=session_id)
    record["results"].append(bland)
    print("walled" if bland["walled"] else "NOT walled",
          f"(status={bland['status']}, indicator={bland.get('wall_indicator')})",
          flush=True)

    print("  Casetext ...", end=" ", flush=True)
    casetext = probe_casetext(session_id=session_id)
    record["results"].append(casetext)
    print("walled" if casetext["walled"] else "NOT walled",
          f"(status={casetext['status']}, indicator={casetext.get('wall_indicator')})",
          flush=True)

    if args.skip_chatlab:
        print("  ChatLab ... SKIPPED (--skip-chatlab)", flush=True)
    else:
        print(f"  ChatLab loop (n={args.chatlab_sessions}, pace={args.chatlab_pacing_s}s) ...",
              flush=True)
        chatlab = probe_chatlab_loop(
            n_sessions=args.chatlab_sessions,
            pacing_s=args.chatlab_pacing_s,
        )
        record["results"].append(chatlab)
        print(f"    empty_rate={chatlab['empty_rate']} refused_rate={chatlab['refused_rate']}"
              f" -> {'walled' if chatlab['walled'] else 'NOT walled'}",
              flush=True)

    walled_count = sum(1 for r in record["results"] if r["walled"])
    not_walled_count = len(record["results"]) - walled_count
    record["walled_count"] = walled_count
    record["not_walled_count"] = not_walled_count
    record["lift"] = f"{not_walled_count}/{len(record['results'])}"
    record["passes_ac"] = (
        record["proxy_enabled"]
        and not_walled_count >= 2
        and len(record["results"]) == 3  # full 3-wall run, not --skip-chatlab
    )
    record["finished_at"] = _now_iso()

    out_path.write_text(json.dumps(record, indent=2))
    print(f"\n  lift = {record['lift']} | proxy_enabled={record['proxy_enabled']}"
          f" | passes_ac={record['passes_ac']}", flush=True)
    print(f"  wrote {out_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
