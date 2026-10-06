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
        self.db.execute("""CREATE TABLE IF NOT EXISTS onboarding_sessions (
          token_digest TEXT PRIMARY KEY, account_email TEXT NOT NULL UNIQUE,
          created_at TEXT NOT NULL, expires_at TEXT NOT NULL,
          claimed_at TEXT, sender_email TEXT, revoked_at TEXT,
          upstream_token TEXT, grant_id TEXT)""")
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(onboarding_sessions)")}
        for name in ("upstream_token", "grant_id"):
            if name not in columns:
                self.db.execute(f"ALTER TABLE onboarding_sessions ADD COLUMN {name} TEXT")
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

    def issue_session(self, *, ttl: timedelta = timedelta(minutes=20)) -> tuple[str, str]:
        """Create an isolated, short-lived enrollment which Instinct may claim once."""
        if ttl <= timedelta(0):
            raise ValueError("session_ttl_invalid")
        token = secrets.token_urlsafe(32)
        account_email = f"demo-{secrets.token_hex(12)}@sessions.invalid"
        created = self.now()
        self.db.execute(
            "INSERT INTO onboarding_sessions "
            "(token_digest,account_email,created_at,expires_at,claimed_at,sender_email,revoked_at) "
            "VALUES (?,?,?,?,NULL,NULL,NULL)",
            (hashlib.sha256(token.encode()).hexdigest(), account_email,
             created.isoformat(), (created + ttl).isoformat()),
        )
        self.db.commit()
        return token, account_email

    def claim_session(self, token: str, sender_email: str) -> str:
        """Bind the first verified-looking sender; expiry and replay fail closed."""
        sender_email = sender_email.strip().lower()
        if "@" not in sender_email:
            raise ValueError("sender_address_invalid")
        digest = hashlib.sha256(token.encode()).hexdigest()
        row = self.db.execute(
            "SELECT * FROM onboarding_sessions WHERE token_digest=?", (digest,)
        ).fetchone()
        if (row is None or row["claimed_at"] or row["revoked_at"] or
                datetime.fromisoformat(row["expires_at"]) <= self.now()):
            raise PermissionError("enrollment_session_invalid_or_expired")
        changed = self.db.execute(
            "UPDATE onboarding_sessions SET claimed_at=?,sender_email=? "
            "WHERE token_digest=? AND claimed_at IS NULL AND revoked_at IS NULL",
            (self.now().isoformat(), sender_email, digest),
        ).rowcount
        self.db.commit()
        if changed != 1:
            raise PermissionError("enrollment_session_already_claimed")
        return str(row["account_email"])

    def session_is_readable(self, token: str) -> bool:
        """Validate an instruction link without claiming or otherwise mutating it."""
        digest = hashlib.sha256(token.encode()).hexdigest()
        row = self.db.execute(
            "SELECT expires_at,revoked_at FROM onboarding_sessions WHERE token_digest=?", (digest,)
        ).fetchone()
        return bool(
            row is not None
            and not row["revoked_at"]
            and datetime.fromisoformat(row["expires_at"]) > self.now()
        )

    def activate_session(self, token: str, upstream_token: str, grant_id: str) -> None:
        if not upstream_token or not grant_id:
            raise ValueError("session_activation_invalid")
        digest = hashlib.sha256(token.encode()).hexdigest()
        changed = self.db.execute(
            "UPDATE onboarding_sessions SET upstream_token=?,grant_id=? "
            "WHERE token_digest=? AND claimed_at IS NOT NULL AND revoked_at IS NULL",
            (upstream_token, grant_id, digest),
        ).rowcount
        self.db.commit()
        if changed != 1:
            raise PermissionError("enrollment_session_invalid_or_expired")

    def stop_credentials(self, token: str) -> tuple[str, str, str, str]:
        digest = hashlib.sha256(token.encode()).hexdigest()
        row = self.db.execute(
            "SELECT * FROM onboarding_sessions WHERE token_digest=?", (digest,)
        ).fetchone()
        if row is None or not row["claimed_at"] or not row["upstream_token"] or not row["grant_id"]:
            raise PermissionError("enrollment_session_not_active")
        return (str(row["account_email"]), str(row["sender_email"]),
                str(row["upstream_token"]), str(row["grant_id"]))

    def revoke_session(self, token: str) -> bool:
        digest = hashlib.sha256(token.encode()).hexdigest()
        changed = self.db.execute(
            "UPDATE onboarding_sessions SET revoked_at=? WHERE token_digest=? AND revoked_at IS NULL",
            (self.now().isoformat(), digest),
        ).rowcount
        self.db.commit()
        return changed == 1

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
