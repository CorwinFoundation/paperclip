"""Playwright Fin Intercom Messenger capture (v2: target Intercom iframe properly)."""
from __future__ import annotations
import argparse, json, sys, time
from pathlib import Path
from datetime import datetime, timezone
from playwright.sync_api import sync_playwright, Request, Response

TARGET_URL = "https://fin.ai/view-demos"
USER_AGENT = "BEA-ARE-Spot-Scan/0.2 (+endpoint-recon-fin; agent@bea.local)"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="/tmp/artifacts/beaaa-202/fin_capture_v2.json")
    args = ap.parse_args()
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)

    reqs = []
    resps = {}
    def on_req(r: Request):
        if r.resource_type in {"image","font","stylesheet","media"}: return
        try: body = r.post_data
        except Exception: body = None
        reqs.append({"id": r.url+":"+r.method, "method": r.method, "url": r.url,
                     "resource_type": r.resource_type, "headers": dict(r.headers),
                     "post_data": body[:4000] if body else None})
    def on_resp(r: Response):
        if r.request.resource_type in {"image","font","stylesheet","media"}: return
        try: text = r.text()[:3000]
        except Exception: text = None
        resps[r.url+":"+r.request.method] = {"status": r.status, "headers": dict(r.headers), "body_snippet": text}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(user_agent=USER_AGENT, viewport={"width": 1366, "height": 768})
        page = ctx.new_page()
        page.on("request", on_req)
        page.on("response", on_resp)

        print(f"[v2] GET {TARGET_URL}", flush=True)
        page.goto(TARGET_URL, wait_until="networkidle", timeout=45_000)
        page.wait_for_timeout(3_000)

        # List all frames to find Intercom Messenger
        print("[v2] frames after load:", flush=True)
        for i, fr in enumerate(page.frames):
            print(f"   [{i}] url={fr.url[:120]}", flush=True)
        
        # Look for the Intercom launcher iframe and click it
        launcher_selectors = [
            'iframe[name="intercom-launcher-frame"]',
            'iframe[title*="Intercom" i]',
            'div[class*="intercom-launcher"]',
            '.intercom-launcher',
            '[aria-label*="Open Intercom" i]',
            '[aria-label*="Open chat" i]',
        ]
        opened = False
        for sel in launcher_selectors:
            try:
                el = page.locator(sel)
                cnt = el.count()
                if cnt:
                    print(f"[v2] clicking launcher selector={sel} ({cnt} matches)", flush=True)
                    el.first.click(timeout=5_000)
                    opened = True
                    break
            except Exception as e:
                print(f"[v2]   click failed {sel}: {e}", flush=True)
        
        if not opened:
            print("[v2] no launcher found — trying generic clickable element near bottom-right", flush=True)
            # Try clicking near bottom-right where launcher typically sits
            try:
                page.mouse.click(1300, 720)
                opened = True
            except Exception as e:
                print(f"[v2]   bottom-right click failed: {e}", flush=True)
        
        page.wait_for_timeout(4_000)
        print(f"[v2] frames after launcher click:", flush=True)
        intercom_frame = None
        for i, fr in enumerate(page.frames):
            print(f"   [{i}] url={fr.url[:120]}", flush=True)
            if "intercom" in fr.url.lower() and "messenger" in fr.url.lower():
                intercom_frame = fr
            elif "intercom" in fr.url.lower() and "frame" in fr.url.lower():
                intercom_frame = fr  
        
        if not intercom_frame:
            # Sometimes the messenger frame is named simply intercom-messenger-frame
            for fr in page.frames:
                if "intercom" in fr.url.lower():
                    intercom_frame = fr
                    print(f"[v2] picked fallback intercom frame: {fr.url[:120]}", flush=True)
                    break
        
        sent = False
        if intercom_frame:
            # Click "Ask a question" CTA if visible
            try:
                ask_btn = intercom_frame.locator('text="Ask a question"').first
                ask_btn.click(timeout=5_000)
                page.wait_for_timeout(2_000)
                print("[v2] clicked 'Ask a question'", flush=True)
            except Exception as e:
                print(f"[v2] no 'Ask a question' clicked: {e}", flush=True)
            
            # Now find the composer input
            for sel in ['textarea[placeholder*="message" i]', 'textarea', 'div[contenteditable="true"]']:
                try:
                    inp = intercom_frame.locator(sel).first
                    if inp.count() > 0:
                        inp.fill("Hi! Can you recommend a wireless headset under $100?")
                        page.wait_for_timeout(500)
                        inp.press("Enter")
                        sent = True
                        print(f"[v2] sent message via {sel}", flush=True)
                        break
                except Exception as e:
                    print(f"[v2]   send failed via {sel}: {e}", flush=True)
        
        if sent:
            print("[v2] waiting 20s for Fin response...", flush=True)
            page.wait_for_timeout(20_000)
        else:
            print("[v2] no message sent; finishing capture anyway", flush=True)
            page.wait_for_timeout(3_000)
        
        ctx.close()
        browser.close()

    interesting = [r for r in reqs if 'intercom' in r['url'].lower() or r['method'] in ['POST','PUT'] or 'ws://' in r['url'] or 'wss://' in r['url']]
    manifest = {"captured_at": datetime.now(timezone.utc).isoformat(),
                "target_url": TARGET_URL, "user_agent": USER_AGENT,
                "total": len(reqs), "interesting": len(interesting), "sent": sent,
                "requests": [{**r, "response": resps.get(r["id"])} for r in interesting]}
    Path(args.out).write_text(json.dumps(manifest, indent=2, sort_keys=True))
    print(f"[v2] wrote {args.out} ({len(interesting)} interesting / {len(reqs)} total)", flush=True)
    return 0

if __name__ == "__main__":
    sys.exit(main())
