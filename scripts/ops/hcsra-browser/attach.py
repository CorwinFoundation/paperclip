#!/usr/bin/env python3
"""BEAAA-46811: attach to the running hcsra Chrome over CDP (same context Ari solved in).

Do NOT launch a new browser: the Radware clearance may be fingerprint-bound.
Usage (as beai-agent on AriClaw):  python3 attach.py   -> prints the hcsra page title.
In your own code:
    b = p.chromium.connect_over_cdp("http://127.0.0.1:9333")
    ctx = b.contexts[0]           # the persistent profile context
    page = next(pg for pg in ctx.pages if "hcsra" in pg.url)
Never call b.close() on this browser; just let the script exit (disconnects only).
"""
from playwright.sync_api import sync_playwright

CDP = "http://127.0.0.1:9333"

with sync_playwright() as p:
    b = p.chromium.connect_over_cdp(CDP)
    ctx = b.contexts[0]
    pages = [pg for pg in ctx.pages if "hcsra" in pg.url]
    print(f"contexts={len(b.contexts)} pages={len(ctx.pages)} hcsra_pages={len(pages)}")
    for pg in pages:
        print("title:", pg.title())
