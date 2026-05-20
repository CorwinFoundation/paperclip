"""Bootstrap helper: print recent Telegram chat ids visible to the bot.

Calls the Bot API ``getUpdates`` endpoint and prints, in chronological order,
each ``(chat_id, chat_type, title_or_username, last_message_preview)`` tuple
that the bot has been able to see since the last cleared cursor.

Useful when onboarding a new Founder/operator: ask them to message the bot
once, then run this to read off the chat id to drop into
``.secrets/telegram.env``.

Required env:
    TELEGRAM_BOT_TOKEN
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request

TELEGRAM_API = "https://api.telegram.org"


def main() -> int:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        print("error: TELEGRAM_BOT_TOKEN unset", file=sys.stderr)
        return 2
    url = f"{TELEGRAM_API}/bot{token}/getUpdates"
    with urllib.request.urlopen(url, timeout=20.0) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if not data.get("ok"):
        print(f"error: telegram getUpdates returned ok=false: {data}", file=sys.stderr)
        return 1
    results = data.get("result", [])
    if not results:
        print("(no updates — ask the user to send /start or any message to the bot, then re-run)")
        return 0
    seen: set[int] = set()
    print(f"{'chat_id':>16}  {'type':<10}  identity")
    print("-" * 64)
    for upd in results:
        msg = upd.get("message") or upd.get("channel_post") or {}
        chat = msg.get("chat") or {}
        chat_id = chat.get("id")
        if chat_id is None or chat_id in seen:
            continue
        seen.add(chat_id)
        chat_type = chat.get("type", "?")
        identity = (
            chat.get("title")
            or chat.get("username")
            or " ".join(filter(None, (chat.get("first_name"), chat.get("last_name"))))
            or "(unknown)"
        )
        preview = (msg.get("text") or "")[:60].replace("\n", " ")
        print(f"{chat_id:>16}  {chat_type:<10}  {identity}    last: {preview!r}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
