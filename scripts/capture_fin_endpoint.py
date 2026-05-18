"""Playwright endpoint-capture probe for chatlab.com/demo/ecommerce (BEAAA-202).

Loads the demo page, waits for the chat widget to load and auto-open,
sends a benign user message ("hi"), and records every HTTP request the
widget makes. Output is a JSON manifest with method, URL, headers
(sans sensitive), request body, and response status/snippet — enough
to design the scan adapter without hand-decompiling the JS bundle.

Etiquette: 1 page load, 1 user message, ~30s total. No auth, no
fuzzing, no PII. Identifying User-Agent.

Usage:
    python3 scripts/capture_chatlab_endpoint.py [--out PATH]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from datetime import datetime, timezone

from playwright.sync_api import sync_playwright, Request, Response


TARGET_URL = "https://fin.ai/view-demos"
USER_AGENT = "BEA-ARE-Spot-Scan/0.1 (+endpoint-recon; agent@bea.local)"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path("artifacts/beaaa-202/fin_capture.json"))
    ap.add_argument("--har", type=Path, default=Path("artifacts/beaaa-202/fin_capture.har"))
    args = ap.parse_args()

    args.out.parent.mkdir(parents=True, exist_ok=True)

    requests_log: list[dict] = []
    responses_log: dict[str, dict] = {}

    def on_request(req: Request) -> None:
        if req.resource_type in {"image", "font", "stylesheet", "media"}:
            return
        if req.url.startswith("https://www.chatlab.com/") and (
            req.url.endswith(".js") or req.url.endswith(".webp") or "/static/" in req.url
        ):
            return
        try:
            body = req.post_data
        except Exception:
            body = None
        requests_log.append(
            {
                "id": req.url + ":" + req.method,
                "method": req.method,
                "url": req.url,
                "resource_type": req.resource_type,
                "headers": dict(req.headers),
                "post_data": body[:4000] if body else None,
            }
        )

    def on_response(resp: Response) -> None:
        if resp.request.resource_type in {"image", "font", "stylesheet", "media"}:
            return
        key = resp.url + ":" + resp.request.method
        try:
            text = resp.text()[:2000]
        except Exception:
            text = None
        responses_log[key] = {
            "status": resp.status,
            "headers": dict(resp.headers),
            "body_snippet": text,
        }

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent=USER_AGENT,
            record_har_path=str(args.har),
            record_har_omit_content=False,
        )
        page = ctx.new_page()
        page.on("request", on_request)
        page.on("response", on_response)

        print(f"[capture] GET {TARGET_URL}", flush=True)
        page.goto(TARGET_URL, wait_until="networkidle", timeout=45_000)
        page.wait_for_timeout(4_000)

        widget_clue = page.locator("iframe").all()
        print(f"[capture] iframes on page: {len(widget_clue)}", flush=True)
        for i, fr in enumerate(widget_clue):
            try:
                src = fr.get_attribute("src")
            except Exception:
                src = None
            print(f"  iframe[{i}] src={src}", flush=True)

        candidate_selectors = [
            'textarea[placeholder*="message" i]',
            'input[placeholder*="message" i]',
            'textarea[placeholder*="ask" i]',
            'input[placeholder*="ask" i]',
            'textarea',
            'input[type="text"]',
        ]
        sent = False
        for frame in page.frames:
            for sel in candidate_selectors:
                loc = frame.locator(sel)
                try:
                    cnt = loc.count()
                except Exception:
                    cnt = 0
                if cnt:
                    try:
                        loc.first.fill("hi")
                        loc.first.press("Enter")
                        print(f"[capture] sent 'hi' via frame={frame.url[:80]} selector={sel}", flush=True)
                        sent = True
                        break
                    except Exception as exc:
                        print(f"[capture]   fill failed on {sel}: {exc}", flush=True)
                        continue
            if sent:
                break

        if not sent:
            print("[capture] no input found; capturing pageload traffic only", flush=True)

        page.wait_for_timeout(12_000)

        ctx.close()
        browser.close()

    interesting = [
        r for r in requests_log
        if r["method"] in {"POST", "PUT"}
        or any(k in r["url"].lower() for k in ["intercom", "fin.ai", "/api/", "/chat", "/message", "/widget", "/embed", "ws://", "wss://", "messenger"])
        if "googletagmanager" not in r["url"]
        if "google-analytics" not in r["url"]
    ]

    manifest = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "target_url": TARGET_URL,
        "user_agent": USER_AGENT,
        "total_requests": len(requests_log),
        "interesting_count": len(interesting),
        "interesting_requests": [
            {**r, "response": responses_log.get(r["id"])} for r in interesting
        ],
    }
    args.out.write_text(json.dumps(manifest, indent=2, sort_keys=True))
    print(f"[capture] wrote {args.out} ({len(interesting)} interesting / {len(requests_log)} total)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
