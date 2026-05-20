"""Telegram delivery shim for the Founder digest (BEAAA-599 deliverable).

Reads a rendered PDF from disk, posts it to a Telegram chat via the Bot API
``sendDocument`` endpoint with bounded retries, and surfaces the result via
exit code + optional Paperclip issue comments.

Behaviour:
- Success: prints a JSON line ``{"ok": true, "message_id": N, "delivered_at": ...}``
  and exits 0. If ``--run-issue-id`` and ``PAPERCLIP_API_KEY`` are present,
  posts a short comment on that run-issue with the message id + timestamp.
- 5xx response: up to 3 retries with exponential backoff (1s, 2s, 4s).
- Terminal failure (4xx after retries, network error, or 5xx exhaustion):
  prints a JSON line ``{"ok": false, "reason": ...}`` and exits non-zero.
  Does NOT delete the PDF. If ``PAPERCLIP_API_KEY`` is present, posts a
  failure comment on ``--parent-issue-id`` (defaults to BEAAA-597) with the
  filesystem path + failure reason so the failure is visible in the thread.

Required env:
    TELEGRAM_BOT_TOKEN         the Bot API token
    TELEGRAM_FOUNDER_CHAT_ID   destination chat id for the digest

Optional env (used only for posting Paperclip comments on the result):
    PAPERCLIP_API_URL
    PAPERCLIP_API_KEY
    PAPERCLIP_RUN_ID           added as X-Paperclip-Run-Id for audit
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
import time
import urllib.error
import urllib.request
from email.generator import _make_boundary
from pathlib import Path

TELEGRAM_API = "https://api.telegram.org"
DEFAULT_PARENT_ISSUE_ID = "d535e2f9-b013-485b-9a58-5a8a6092e226"  # BEAAA-597
RETRY_DELAYS = (1.0, 2.0, 4.0)


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _build_multipart(chat_id: str, caption: str, pdf_path: Path) -> tuple[bytes, str]:
    boundary = _make_boundary()
    pdf_bytes = pdf_path.read_bytes()
    crlf = b"\r\n"
    parts: list[bytes] = []

    def field(name: str, value: str) -> None:
        parts.append(f"--{boundary}".encode())
        parts.append(f'Content-Disposition: form-data; name="{name}"'.encode())
        parts.append(b"")
        parts.append(value.encode())

    field("chat_id", chat_id)
    if caption:
        field("caption", caption)

    parts.append(f"--{boundary}".encode())
    parts.append(
        f'Content-Disposition: form-data; name="document"; filename="{pdf_path.name}"'.encode()
    )
    parts.append(b"Content-Type: application/pdf")
    parts.append(b"")
    parts.append(pdf_bytes)
    parts.append(f"--{boundary}--".encode())
    parts.append(b"")
    body = crlf.join(parts)
    return body, boundary


def _send_document(token: str, chat_id: str, pdf_path: Path, caption: str, timeout: float = 60.0) -> dict:
    """Single attempt. Returns the parsed JSON response (whatever Telegram sent).

    Raises ``urllib.error.HTTPError`` for HTTP errors; the caller decides retry policy.
    """
    body, boundary = _build_multipart(chat_id, caption, pdf_path)
    url = f"{TELEGRAM_API}/bot{token}/sendDocument"
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    return json.loads(raw.decode("utf-8"))


def _post_paperclip_comment(issue_id: str, body: str) -> bool:
    api_url = os.environ.get("PAPERCLIP_API_URL")
    api_key = os.environ.get("PAPERCLIP_API_KEY")
    if not api_url or not api_key:
        return False
    payload = json.dumps({"body": body}).encode("utf-8")
    req = urllib.request.Request(
        f"{api_url}/api/issues/{issue_id}/comments",
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    run_id = os.environ.get("PAPERCLIP_RUN_ID")
    if run_id:
        req.add_header("X-Paperclip-Run-Id", run_id)
    try:
        with urllib.request.urlopen(req, timeout=20.0) as resp:
            resp.read()
        return True
    except (urllib.error.URLError, urllib.error.HTTPError) as exc:
        print(f"warn: failed to post paperclip comment to {issue_id}: {exc}", file=sys.stderr)
        return False


def _post_failure_comment(parent_id: str | None, run_id: str | None, body: str) -> None:
    """Try parent first (long-lived), fall back to run-issue.

    Cross-team policy may reject writes to the parent (e.g. BEAAA-597 is owned
    by another agent). The run-issue is always the current task and writable.
    """
    if parent_id and _post_paperclip_comment(parent_id, body):
        return
    if run_id and _post_paperclip_comment(run_id, body):
        return
    print(f"warn: could not post failure comment anywhere reachable", file=sys.stderr)


def deliver(pdf_path: Path, *, run_issue_id: str | None, parent_issue_id: str, caption: str) -> int:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_FOUNDER_CHAT_ID")
    missing = [k for k, v in (
        ("TELEGRAM_BOT_TOKEN", token),
        ("TELEGRAM_FOUNDER_CHAT_ID", chat_id),
    ) if not v]
    if missing:
        print(json.dumps({"ok": False, "reason": f"missing env: {', '.join(missing)}"}))
        return 2
    if not pdf_path.is_file():
        print(json.dumps({"ok": False, "reason": f"pdf not found at {pdf_path}"}))
        return 2

    last_error: str | None = None
    for attempt, delay in enumerate([0.0, *RETRY_DELAYS]):
        if delay:
            time.sleep(delay)
        try:
            resp = _send_document(token, chat_id, pdf_path, caption)
            if resp.get("ok"):
                message_id = resp["result"]["message_id"]
                delivered_at = _now_iso()
                print(json.dumps({
                    "ok": True,
                    "message_id": message_id,
                    "chat_id": chat_id,
                    "delivered_at": delivered_at,
                    "pdf": str(pdf_path),
                    "attempt": attempt,
                }))
                if run_issue_id:
                    _post_paperclip_comment(
                        run_issue_id,
                        f"## Founder digest delivered\n\n"
                        f"- Telegram `message_id`: `{message_id}`\n"
                        f"- Chat id: `{chat_id}`\n"
                        f"- Delivered at: `{delivered_at}`\n"
                        f"- PDF: `{pdf_path}`\n"
                        f"- Attempt: `{attempt}` (0 = first try, no retry needed)\n",
                    )
                return 0
            # ok=false from Telegram (e.g. bad chat id, payload too large) — terminal.
            last_error = f"telegram ok=false: {resp.get('description') or resp}"
            break
        except urllib.error.HTTPError as exc:
            status = exc.code
            try:
                body = exc.read().decode("utf-8", errors="replace")
            except Exception:
                body = ""
            last_error = f"http {status}: {body[:400]}"
            if 500 <= status < 600 and attempt < len(RETRY_DELAYS):
                continue  # retry
            break  # 4xx or 5xx exhausted -> terminal
        except urllib.error.URLError as exc:
            last_error = f"network error: {exc.reason}"
            if attempt < len(RETRY_DELAYS):
                continue
            break

    # Terminal failure
    print(json.dumps({
        "ok": False,
        "reason": last_error,
        "pdf": str(pdf_path),
    }))
    _post_failure_comment(
        parent_issue_id,
        run_issue_id,
        f"## Founder digest delivery failed\n\n"
        f"- PDF retained on disk: `{pdf_path}`\n"
        f"- Failure reason: `{last_error}`\n"
        f"- Timestamp: `{_now_iso()}`\n\n"
        f"Filesystem fallback is in place. Manual delivery: re-run "
        f"`tools/founder-digest/deliver.py --pdf {pdf_path}` once the "
        f"underlying issue is fixed.\n",
    )
    return 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="founder-digest-deliver")
    ap.add_argument("--pdf", required=True, type=Path, help="Rendered PDF path to deliver.")
    ap.add_argument("--run-issue-id", default=os.environ.get("PAPERCLIP_TASK_ID") or None,
                    help="Routine-fired run issue id; success comment is posted here. "
                         "Defaults to $PAPERCLIP_TASK_ID.")
    ap.add_argument("--parent-issue-id", default=DEFAULT_PARENT_ISSUE_ID,
                    help=f"Parent issue id for failure comments (defaults to BEAAA-597).")
    ap.add_argument("--caption", default="",
                    help="Optional caption shown under the PDF in Telegram.")
    args = ap.parse_args(argv)
    return deliver(
        pdf_path=args.pdf.resolve(),
        run_issue_id=args.run_issue_id or None,
        parent_issue_id=args.parent_issue_id,
        caption=args.caption,
    )


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
