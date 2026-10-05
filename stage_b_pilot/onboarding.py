"""Reusable email-link onboarding for the fictional Instinct DEMO."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def _now() -> datetime:
    return datetime.now(timezone.utc)


class OnboardingStore:
    def __init__(self, path: str, *, now=_now) -> None:
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.now = now
        self.db.execute("""CREATE TABLE IF NOT EXISTS onboarding_links (
          token_digest TEXT PRIMARY KEY, email TEXT NOT NULL, agent_email TEXT NOT NULL,
          created_at TEXT NOT NULL, expires_at TEXT NOT NULL, consumed_at TEXT)""")
        self.db.commit()

    def issue(self, email: str, agent_email: str) -> str:
        email, agent_email = email.strip().lower(), agent_email.strip().lower()
        if "@" not in email or "@" not in agent_email:
            raise ValueError("email_address_invalid")
        token = secrets.token_urlsafe(32)
        created = self.now()
        self.db.execute(
            "INSERT INTO onboarding_links VALUES (?,?,?,?,?,NULL)",
            (hashlib.sha256(token.encode()).hexdigest(), email, agent_email,
             created.isoformat(), (created + timedelta(minutes=20)).isoformat()),
        )
        self.db.commit()
        return token

    def consume(self, token: str) -> tuple[str, str]:
        digest = hashlib.sha256(token.encode()).hexdigest()
        row = self.db.execute("SELECT * FROM onboarding_links WHERE token_digest=?", (digest,)).fetchone()
        if row is None or row["consumed_at"] or datetime.fromisoformat(row["expires_at"]) <= self.now():
            raise PermissionError("sign_in_link_invalid_or_expired")
        self.db.execute("UPDATE onboarding_links SET consumed_at=? WHERE token_digest=?",
                        (self.now().isoformat(), digest))
        self.db.commit()
        return str(row["email"]), str(row["agent_email"])

    def close(self) -> None:
        self.db.close()


class MailgunLinkSender:
    def __init__(self, api_url: str, api_key: str, from_address: str) -> None:
        self.api_url, self.api_key, self.from_address = api_url, api_key, from_address

    def __call__(self, email: str, link: str) -> str:
        body = urlencode({
            "from": self.from_address, "to": email,
            "subject": "Your BackBond DEMO sign-in link",
            "text": ("Open this private link within 20 minutes to continue the fictional Instinct DEMO:\n"
                     f"{link}\n\nNo purchase, booking, payment, mailbox access, or protection is activated."),
        }).encode()
        authorization = base64.b64encode(f"api:{self.api_key}".encode()).decode()
        request = Request(self.api_url, data=body, headers={
            "Authorization": f"Basic {authorization}",
            "Content-Type": "application/x-www-form-urlencoded",
        })
        with urlopen(request, timeout=10) as response:
            payload = json.loads(response.read())
        message_id = payload.get("id") if isinstance(payload, dict) else None
        if not isinstance(message_id, str) or not message_id.strip():
            raise RuntimeError("mailgun_acceptance_id_missing")
        return message_id.strip()
