"""External alert adapters for the scan delivery monitor.

The monitor only gives these adapters a small, already-sanitized alert record.
Paperclip issue creation is reconciled by a stable marker before a POST, because
the issue API does not provide an atomic idempotency contract.  Founder push
uses the same stable key in both the request header and body.
"""
from __future__ import annotations

import json
import os
import re
from urllib.parse import quote, urlsplit


_IDENT = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_KEY = re.compile(r"[a-f0-9]{64}\Z")
_SAFE_STAGE = {"review", "workflow_write_back", "credential", "provider", "sent", "mailbox"}
_SAFE_KINDS = {"delivery_breach", "review_decision", "credential_expiry", "placement_failure"}


class AlertAdapterError(RuntimeError):
    """A transport or response that did not establish an alert receipt."""


def _key(value: str) -> str:
    if not isinstance(value, str) or not _KEY.fullmatch(value):
        raise AlertAdapterError("invalid_alert_idempotency_key")
    return value


def _configured_origin(value: str, *, allow_http_localhost: bool = True) -> bool:
    try:
        parsed = urlsplit(value or "")
    except ValueError:
        return False
    if parsed.scheme not in {"https", "http"} or not parsed.hostname:
        return False
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        return False
    return parsed.scheme == "https" or (allow_http_localhost and parsed.hostname in {"127.0.0.1", "localhost"})


def sanitize_alert(alert: dict) -> dict:
    """Return the closed alert vocabulary safe to send to either adapter."""
    if not isinstance(alert, dict) or alert.get("kind") not in _SAFE_KINDS:
        raise AlertAdapterError("invalid_alert_kind")
    if not isinstance(alert.get("key"), str) or not alert["key"]:
        raise AlertAdapterError("invalid_alert_key")
    value = {"key": alert["key"], "kind": alert["kind"]}
    for name in ("priority", "scan_id", "stage", "recipient", "completed_at", "key_id", "threshold", "expires_at"):
        if name in alert:
            item = alert[name]
            if isinstance(item, bool) or not isinstance(item, (str, int, float)):
                raise AlertAdapterError("invalid_alert_field")
            value[name] = item
    if "stage" in value and value["stage"] not in _SAFE_STAGE:
        raise AlertAdapterError("invalid_alert_stage")
    options = alert.get("options")
    if options is not None:
        if (not isinstance(options, list) or not options or len(options) > 8
                or any(not isinstance(item, str) or not item.strip() or len(item) > 160 for item in options)):
            raise AlertAdapterError("invalid_alert_options")
        value["options"] = list(options)
    return value


def _receipt(value, *, fields=("id", "receipt", "message_id", "event_id")):
    if isinstance(value, dict):
        for name in fields:
            candidate = value.get(name)
            if isinstance(candidate, str) and _IDENT.fullmatch(candidate):
                return candidate
    return None


class PaperclipIncidentAdapter:
    """Create or reconcile one critical/high Paperclip issue per alert key."""

    def __init__(self, base_url, company_id, token, post, get=None, *, assignee_agent_id="", timeout=20):
        self.base_url = (base_url or "").rstrip("/")
        self.company_id = company_id or ""
        self.token = token or ""
        self.post = post
        self.get = get
        self.assignee_agent_id = assignee_agent_id or ""
        self.timeout = timeout

    @classmethod
    def from_env(cls, post, get=None):
        return cls(
            os.environ.get("PAPERCLIP_API_URL", ""),
            os.environ.get("PAPERCLIP_COMPANY_ID", ""),
            os.environ.get("PAPERCLIP_API_KEY", ""),
            post,
            get,
            assignee_agent_id=os.environ.get("QA_ALERT_ASSIGNEE_AGENT_ID", ""),
        )

    @property
    def configured(self):
        return bool(_configured_origin(self.base_url) and self.company_id and self.token and self.get is not None)

    def __call__(self, alert, *, idempotency_key):
        key = _key(idempotency_key)
        value = sanitize_alert(alert)
        if not self.configured:
            raise AlertAdapterError("paperclip_alert_not_configured")
        marker = "AUTOMATED SCANNER ALERT " + value["key"]
        route = self.base_url + "/api/companies/" + quote(self.company_id, safe="") + "/issues"
        headers = {"Authorization": "Bearer " + self.token, "Idempotency-Key": key}
        matches = self._find(route, marker, headers)
        if len(matches) > 1:
            raise AlertAdapterError("duplicate_alert_incidents")
        if matches:
            return self._issue_receipt(matches[0])
        payload = self._payload(value, marker)
        response = self.post(route, json=payload, headers=headers, timeout=self.timeout, follow_redirects=False)
        if response.status_code not in {200, 201}:
            raise AlertAdapterError("paperclip_alert_rejected")
        try:
            created = response.json()
        except Exception as exc:
            raise AlertAdapterError("paperclip_alert_malformed") from exc
        if not isinstance(created, dict) or created.get("companyId") not in {None, self.company_id}:
            raise AlertAdapterError("paperclip_alert_company_mismatch")
        return self._issue_receipt(created)

    def _find(self, route, marker, headers):
        try:
            response = self.get(route + "?q=" + quote(marker, safe=""), headers=headers,
                                timeout=self.timeout, follow_redirects=False)
            if response.status_code != 200:
                raise AlertAdapterError("paperclip_alert_lookup_failed")
            records = response.json()
        except AlertAdapterError:
            raise
        except Exception as exc:
            raise AlertAdapterError("paperclip_alert_lookup_failed") from exc
        if not isinstance(records, list):
            raise AlertAdapterError("paperclip_alert_lookup_malformed")
        return [item for item in records if isinstance(item, dict)
                and item.get("companyId") == self.company_id
                and isinstance(item.get("description"), str)
                and item["description"].startswith(marker + "\n")]

    def _payload(self, alert, marker):
        description = marker + "\n" + json.dumps(alert, sort_keys=True, separators=(",", ":"), allow_nan=False)
        payload = {"title": self._title(alert), "description": description,
                   "status": "todo", "priority": alert.get("priority", "high")}
        if self.assignee_agent_id:
            payload["assigneeAgentId"] = self.assignee_agent_id
        return payload

    @staticmethod
    def _title(alert):
        if alert["kind"] == "review_decision":
            return "Founder decision required: scan report review hold " + str(alert.get("scan_id", "unknown"))
        if alert["kind"] == "credential_expiry":
            return "Scanner credential expiry: " + str(alert.get("key_id", "unknown"))
        return "Critical scan delivery breach: " + str(alert.get("scan_id", "unknown"))

    @staticmethod
    def _issue_receipt(value):
        receipt = _receipt(value)
        if not receipt:
            raise AlertAdapterError("paperclip_alert_receipt_missing")
        return "paperclip-issue:" + receipt


class FounderPushAdapter:
    """Push one sanitized alert to the configured Founder notification relay."""

    def __init__(self, url, token, post, *, timeout=20):
        self.url = url or ""
        self.token = token or ""
        self.post = post
        self.timeout = timeout

    @classmethod
    def from_env(cls, post):
        return cls(
            os.environ.get("QA_FOUNDER_PUSH_URL") or os.environ.get("FOUNDER_PUSH_URL", ""),
            os.environ.get("QA_FOUNDER_PUSH_TOKEN") or os.environ.get("FOUNDER_PUSH_TOKEN", ""),
            post,
        )

    @property
    def configured(self):
        return bool(_configured_origin(self.url) and self.token)

    def __call__(self, alert, *, idempotency_key):
        key = _key(idempotency_key)
        value = sanitize_alert(alert)
        if not self.configured:
            raise AlertAdapterError("founder_push_not_configured")
        payload = {"schema_version": "backbond.scanner.alert/v1", "event": "founder_alert",
                   "idempotency_key": key, "alert": value}
        response = self.post(self.url, json=payload, headers={
            "Authorization": "Bearer " + self.token,
            "Content-Type": "application/json", "Idempotency-Key": key,
        }, timeout=self.timeout, follow_redirects=False)
        if response.status_code not in {200, 201, 202}:
            raise AlertAdapterError("founder_push_rejected")
        try:
            body = response.json()
        except Exception as exc:
            body = None
        receipt = _receipt(body)
        if not receipt:
            headers = getattr(response, "headers", {}) or {}
            candidate = headers.get("X-Alert-Receipt") or headers.get("X-Receipt-Id")
            receipt = candidate if isinstance(candidate, str) and _IDENT.fullmatch(candidate) else None
        if not receipt:
            raise AlertAdapterError("founder_push_receipt_missing")
        return "founder-push:" + receipt
