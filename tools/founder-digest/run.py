"""CLI for the daily Founder PDF digest (BEAAA-598 deliverable).

Usage:
    python3 tools/founder-digest/run.py --as-of now \
        --out dist/founder-digest/2026-05-20.pdf

Environment:
    PAPERCLIP_API_URL    base URL of the Paperclip API
    PAPERCLIP_API_KEY    bearer token
    PAPERCLIP_COMPANY_ID company id

The directory `founder-digest` has a hyphen by spec, so this is not a
python package. Sibling modules are loaded by adding the directory to
sys.path.
"""
from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import argparse
import datetime as _dt
import os

from fetcher import PaperclipClient, fetch
from render import render_html, render_pdf


def _parse_as_of(value: str) -> _dt.datetime:
    if value.lower() == "now":
        return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0)
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    dt = _dt.datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_dt.timezone.utc)
    return dt.astimezone(_dt.timezone.utc)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="founder-digest")
    ap.add_argument("--as-of", default="now",
                    help="ISO timestamp or 'now'. The digest renders as if it were this moment.")
    ap.add_argument("--since", default=None,
                    help="ISO timestamp. Defaults to --as-of minus 24h.")
    ap.add_argument("--out", required=True, type=Path,
                    help="Destination PDF path.")
    ap.add_argument("--html-out", type=Path, default=None,
                    help="Optional: also write the intermediate HTML to this path.")
    ap.add_argument("--api-url", default=os.environ.get("PAPERCLIP_API_URL"))
    ap.add_argument("--api-key", default=os.environ.get("PAPERCLIP_API_KEY"))
    ap.add_argument("--company-id", default=os.environ.get("PAPERCLIP_COMPANY_ID"))
    ap.add_argument("--strict", action="store_true",
                    help="Exit non-zero if any house-style or canonical-term warning fires.")
    ap.add_argument("--snapshot-save", type=Path, default=None,
                    help="After fetching live state, write a JSON snapshot to this path.")
    ap.add_argument("--snapshot-load", type=Path, default=None,
                    help="Skip the API entirely and replay state from this JSON snapshot.")
    args = ap.parse_args(argv)

    as_of = _parse_as_of(args.as_of)
    since = _parse_as_of(args.since) if args.since else as_of - _dt.timedelta(hours=24)

    if args.snapshot_load:
        from fetcher import load_snapshot
        state = load_snapshot(args.snapshot_load, as_of=as_of, since=since)
    else:
        if not args.api_url or not args.api_key or not args.company_id:
            print("error: PAPERCLIP_API_URL, PAPERCLIP_API_KEY, PAPERCLIP_COMPANY_ID required.",
                  file=sys.stderr)
            return 2
        client = PaperclipClient(args.api_url, args.api_key, args.company_id)
        state = fetch(client, as_of=as_of, since=since)
        if args.snapshot_save:
            from fetcher import save_snapshot
            args.snapshot_save.parent.mkdir(parents=True, exist_ok=True)
            save_snapshot(state, args.snapshot_save)
            print(f"wrote snapshot {args.snapshot_save}", file=sys.stderr)
    html, footer_entries, warnings = render_html(state)

    html_path = args.html_out or args.out.with_suffix(".html")
    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(html, encoding="utf-8")
    print(f"wrote {html_path} ({len(html)} bytes)", file=sys.stderr)

    source_epoch = int(as_of.timestamp())
    rc, stderr = render_pdf(html_path, args.out, source_date_epoch=source_epoch)
    if rc != 0 or not args.out.exists():
        print(f"chrome pdf render failed rc={rc}", file=sys.stderr)
        print(stderr[:600], file=sys.stderr)
        return 3
    size = args.out.stat().st_size
    print(f"wrote {args.out} ({size} bytes)", file=sys.stderr)

    if warnings:
        print("WARNINGS:", file=sys.stderr)
        for w in warnings:
            print(f"  - {w}", file=sys.stderr)
        if args.strict:
            return 4

    print(f"footer ticket index: {len({e.ticket_id for e in footer_entries})} entries",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
