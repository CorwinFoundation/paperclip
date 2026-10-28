"""Opt-in Quick chat pilot: supported embedding SDK + correlated CHAT_LOGS.

No private console APIs, UI selectors, prompt rewriting, target retries or
inferred tool calls. AWS credentials and target policy are operator-owned.
An SDK acknowledgement alone never becomes an assistant answer.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import copy
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import secrets
import socket
import sqlite3
import subprocess
import time
from typing import Any
from urllib.parse import urlsplit

from harness.runner import ModelRunner, RunnerError

TRANSPORT = "amazon_quick"
SCHEMA = "backbond-amazon-quick-targets/v1"
_MAX_BYTES = 2 * 1024 * 1024
_MAX_TEXT = 64 * 1024
# Documented CHAT_LOGS response outcomes. Each still needs the complete
# correlation checks and real, bounded provider text below; none is a verdict.
_TEXT_RESPONSE_STATUSES = frozenset({"success", "request_blocked", "no_answer_found"})


def _fail(code: str) -> RunnerError:
    # Never interpolate AWS stderr, URLs, identity, prompts or session tokens.
    return RunnerError("amazon quick: " + code)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _origin(url: str) -> str:
    try:
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username
                or parsed.password or parsed.fragment or parsed.port not in (None, 443)):
            raise ValueError
        if not re.fullmatch(r"[a-z0-9.-]+", parsed.hostname) or "." not in parsed.hostname:
            raise ValueError
        try:
            ipaddress.ip_address(parsed.hostname)
        except ValueError:
            pass
        else:
            raise ValueError
        return "https://" + parsed.hostname
    except (ValueError, TypeError):
        raise _fail("invalid_https_origin") from None


def validate_endpoint(endpoint: str, auth_type: str, config: dict | None = None) -> None:
    _origin(endpoint)
    if urlsplit(endpoint).query or auth_type != "bearer":
        raise _fail("registered_endpoint_and_pilot_token_required")
    if any((config or {}).get(k) is not None for k in (
        "system_prompt", "request_template", "response_path", "model",
    )):
        raise _fail("target_overrides_not_supported")


@dataclass(frozen=True)
class Target:
    endpoint_url: str
    account_id: str
    region: str
    agent_id: str
    user_arn: str
    log_group: str
    token_sha256: str
    pilot_id: str
    expires_at: float
    allowed_browser_hosts: tuple[str, ...]
    aws_profile: str = ""
    timeout_seconds: int = 120
    max_calls: int = 20
    max_concurrent_conversations: int = 1

    @classmethod
    def parse(cls, row: dict) -> Target:
        try:
            if set(row) - {"approved", "endpoint_url", "account_id", "region", "agent_id", "user_arn",
                           "log_group", "token_sha256", "pilot_id", "expires_at", "allowed_browser_hosts",
                           "aws_profile", "timeout_seconds", "max_calls", "max_concurrent_conversations"}:
                raise ValueError
            required = {k: row[k] for k in (
                "endpoint_url", "account_id", "region", "agent_id", "user_arn",
                "log_group", "token_sha256", "pilot_id", "expires_at", "allowed_browser_hosts",
            )}
            for key, value in required.items():
                if key != "allowed_browser_hosts" and not isinstance(value, str):
                    raise ValueError
            endpoint = required["endpoint_url"]
            validate_endpoint(endpoint, "bearer")
            region, account = required["region"], required["account_id"]
            if not re.fullmatch(r"[a-z]{2}-(?:gov-)?[a-z]+-\d", region):
                raise ValueError
            if not re.fullmatch(r"\d{12}", account):
                raise ValueError
            if not re.fullmatch(r"[0-9a-zA-Z_.+-]{1,256}", required["agent_id"]):
                raise ValueError
            if not re.fullmatch(rf"arn:aws:quicksight:{re.escape(region)}:{account}:user/[^\s]+", required["user_arn"]):
                raise ValueError
            if not re.fullmatch(r"[0-9a-f]{64}", required["token_sha256"]):
                raise ValueError
            if not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", required["pilot_id"]):
                raise ValueError
            if not re.fullmatch(r"[.\-_/#a-zA-Z0-9]{1,512}", required["log_group"]):
                raise ValueError
            expires = datetime.fromisoformat(required["expires_at"].replace("Z", "+00:00"))
            if expires.tzinfo is None:
                raise ValueError
            hosts = required["allowed_browser_hosts"]
            if not isinstance(hosts, list) or not 1 <= len(hosts) <= 30:
                raise ValueError
            for host in hosts:
                if not isinstance(host, str) or _origin("https://" + host) != "https://" + host:
                    raise ValueError
            if urlsplit(endpoint).hostname not in hosts:
                raise ValueError
            timeout, calls = row.get("timeout_seconds", 120), row.get("max_calls", 20)
            if type(timeout) is not int or not 10 <= timeout <= 300:
                raise ValueError
            concurrency = row.get("max_concurrent_conversations", 1)
            if type(calls) is not int or not 1 <= calls <= 5000:
                raise ValueError
            if type(concurrency) is not int or not 1 <= concurrency <= 5:
                raise ValueError
            profile = row.get("aws_profile", "")
            if not isinstance(profile, str) or not re.fullmatch(r"[\w.-]{0,128}", profile):
                raise ValueError
            return cls(**{**required, "expires_at": expires.timestamp(),
                          "allowed_browser_hosts": tuple(hosts)},
                       aws_profile=profile, timeout_seconds=timeout, max_calls=calls,
                       max_concurrent_conversations=concurrency)
        except (KeyError, ValueError, TypeError):
            raise _fail("invalid_operator_target") from None


def load_target(endpoint: str, token: str) -> Target:
    if os.environ.get("ARE_AMAZON_QUICK_ENABLED") != "1":
        raise _fail("pilot_disabled")
    try:
        path = Path(os.environ["ARE_AMAZON_QUICK_TARGETS_FILE"])
        if not path.is_absolute() or path.stat().st_size > _MAX_BYTES:
            raise ValueError
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("schema") != SCHEMA or not isinstance(data.get("targets"), list):
            raise ValueError
        matches = [row for row in data["targets"] if row.get("endpoint_url") == endpoint]
        if len(matches) != 1 or matches[0].get("approved") is not True:
            raise ValueError
        target = Target.parse(matches[0])
        if not isinstance(token, str) or len(token) < 32 or not hmac.compare_digest(_sha(token), target.token_sha256):
            raise ValueError
        if time.time() >= target.expires_at:
            raise ValueError
        return target
    except (OSError, KeyError, ValueError, TypeError, AttributeError):
        raise _fail("target_not_authorized") from None


class Ledger:
    """Durable call budget, exclusive identity lease and case isolation."""

    def __init__(self, target: Target):
        try:
            directory = Path(os.environ["ARE_AMAZON_QUICK_STATE_DIR"])
            if not directory.is_absolute() or not directory.is_dir():
                raise ValueError
            self.db = sqlite3.connect(directory / "amazon-quick.sqlite3", timeout=5)
            self.db.execute("PRAGMA busy_timeout=5000")
            self.db.executescript("""
              CREATE TABLE IF NOT EXISTS pilots (id TEXT PRIMARY KEY, binding TEXT, calls INTEGER, halted INTEGER, inflight INTEGER);
              CREATE TABLE IF NOT EXISTS leases (identity TEXT PRIMARY KEY, owner TEXT, until REAL);
              CREATE TABLE IF NOT EXISTS conversations (id TEXT PRIMARY KEY, owner TEXT);
              CREATE TABLE IF NOT EXISTS messages (id TEXT PRIMARY KEY);
              CREATE TABLE IF NOT EXISTS quick_conversation_leases
                (owner TEXT PRIMARY KEY, identity TEXT, pilot TEXT, until REAL);
              CREATE TABLE IF NOT EXISTS quick_batch_authorizations
                (id TEXT PRIMARY KEY, binding TEXT, calls INTEGER, cap INTEGER, halted INTEGER);
              CREATE TABLE IF NOT EXISTS quick_batch_authorization_pilots
                (pilot TEXT PRIMARY KEY, authorization TEXT);
            """)
            self.target = target
            self.identity = _sha(target.account_id + target.region + target.user_arn)
            binding = _sha(json.dumps([target.endpoint_url, target.agent_id, target.user_arn,
                                      target.account_id, target.region, target.log_group]))
            with self.db:
                self.db.execute("INSERT OR IGNORE INTO pilots VALUES (?, ?, 0, 0, 0)", (target.pilot_id, binding))
                if self.db.execute("SELECT binding FROM pilots WHERE id=?", (target.pilot_id,)).fetchone()[0] != binding:
                    raise ValueError
        except (OSError, KeyError, ValueError, sqlite3.Error):
            if hasattr(self, "db"):
                self.db.close()
            raise _fail("operator_state_unavailable") from None

    @contextmanager
    def case(self):
        owner = secrets.token_hex(16)
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            if time.time() >= self.target.expires_at:
                raise _fail("pilot_expired")
            if self.db.execute("SELECT halted FROM pilots WHERE id=?", (self.target.pilot_id,)).fetchone()[0]:
                raise _fail("pilot_halted_operator_review_required")
            row = self.db.execute("SELECT until FROM leases WHERE identity=?", (self.identity,)).fetchone()
            if row and row[0] > time.time():
                raise _fail("test_identity_busy")
            if self.db.execute("SELECT 1 FROM quick_conversation_leases WHERE identity=? AND until>?",
                               (self.identity, time.time())).fetchone():
                raise _fail("test_identity_busy")
            if self.db.execute("SELECT inflight FROM pilots WHERE id=?", (self.target.pilot_id,)).fetchone()[0]:
                raise _fail("unresolved_submission_operator_review_required")
            self.db.execute("INSERT OR REPLACE INTO leases VALUES (?, ?, ?)",
                            (self.identity, owner, time.time() + self.target.timeout_seconds + 30))
        try:
            yield owner
        finally:
            with self.db:
                self.db.execute("DELETE FROM leases WHERE identity=? AND owner=?", (self.identity, owner))

    def reserve(self, owner: str) -> None:
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            lease = self.db.execute("SELECT owner, until FROM leases WHERE identity=?", (self.identity,)).fetchone()
            if not lease or lease[0] != owner or lease[1] <= time.time():
                raise _fail("identity_lease_expired")
            if time.time() >= self.target.expires_at:
                raise _fail("pilot_expired")
            if self.db.execute("SELECT halted FROM pilots WHERE id=?", (self.target.pilot_id,)).fetchone()[0]:
                raise _fail("pilot_halted_operator_review_required")
            self.reserve_shared_budget()
            cur = self.db.execute("UPDATE pilots SET calls=calls+1 WHERE id=? AND calls<?",
                                  (self.target.pilot_id, self.target.max_calls))
            if cur.rowcount != 1:
                raise _fail("call_budget_exhausted")

    def reserve_shared_budget(self):
        """Called only inside the same write transaction as the pilot increment.

        Manual serial readiness calls after batch authorization consume the same
        aggregate cap. A second authorization cannot reset a registered pilot.
        """
        row = self.db.execute("""SELECT a.id,a.calls,a.cap,a.halted FROM quick_batch_authorizations a
            JOIN quick_batch_authorization_pilots p ON p.authorization=a.id WHERE p.pilot=?""",
            (self.target.pilot_id,)).fetchone()
        if row is None:
            return
        if row[3]:
            raise _fail("batch_halted_operator_review_required")
        consumed = self.db.execute("""SELECT COALESCE(SUM(p.calls),0) FROM pilots p
            JOIN quick_batch_authorization_pilots b ON b.pilot=p.id WHERE b.authorization=?""",
            (row[0],)).fetchone()[0]
        used = max(row[1], consumed)
        if used >= row[2]:
            raise _fail("batch_call_budget_exhausted")
        self.db.execute("UPDATE quick_batch_authorizations SET calls=? WHERE id=?", (used + 1, row[0]))

    def accept(self, owner: str, conversation: str, message: str) -> None:
        # Global hashes deliberately survive runner restarts and pilot-ID changes.
        conv, msg = _sha(self.identity + conversation), _sha(self.identity + message)
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            old = self.db.execute("SELECT owner FROM conversations WHERE id=?", (conv,)).fetchone()
            if old and old[0] != owner:
                raise _fail("conversation_reused_across_cases")
            self.db.execute("INSERT OR IGNORE INTO conversations VALUES (?, ?)", (conv, owner))
            try:
                self.db.execute("INSERT INTO messages VALUES (?)", (msg,))
            except sqlite3.IntegrityError:
                raise _fail("message_replayed") from None
            self.db.execute("UPDATE pilots SET inflight=0 WHERE id=?", (self.target.pilot_id,))

    def submitting(self) -> None:
        # Committed before the browser send. A hard process exit cannot clear
        # uncertainty merely by releasing/expiring its identity lease.
        with self.db:
            cur = self.db.execute("UPDATE pilots SET inflight=1 WHERE id=? AND inflight=0 AND halted=0",
                                  (self.target.pilot_id,))
            if cur.rowcount != 1:
                raise _fail("unresolved_submission_operator_review_required")

    def halt(self) -> None:
        with self.db:
            self.db.execute("UPDATE pilots SET halted=1 WHERE id=?", (self.target.pilot_id,))

    def close(self):
        self.db.close()


class AwsCli:
    """Use the worker's AWS CLI v2 credential chain; never a submitted AWS key."""

    def __init__(self, target: Target):
        self.target = target
        self.deadline: float | None = None

    def call(self, service: str, operation: str, payload: dict) -> dict:
        # Only fixed operator-facing phases and categories may leave this
        # boundary. CLI stderr can contain credentials, URLs or chat content.
        phase = {("quicksight", "generate-embed-url-for-registered-user"): "embed",
                 ("logs", "filter-log-events"): "chat_logs"}.get((service, operation), "operation")
        args = ["aws", service, operation, "--region", self.target.region,
                "--no-cli-pager", "--output", "json", "--no-paginate",
                "--no-cli-auto-prompt",
                "--cli-connect-timeout", "5", "--cli-read-timeout", "10",
                "--cli-input-json", json.dumps(payload)]
        if self.target.aws_profile:
            args += ["--profile", self.target.aws_profile]
        try:
            remaining = 20 if self.deadline is None else min(20, self.deadline - time.monotonic())
            if remaining <= 0:
                raise _fail("case_deadline_exceeded")
            result = subprocess.run(args, capture_output=True, timeout=remaining, check=False,
                                    env={**os.environ, "AWS_MAX_ATTEMPTS": "1"})
            if result.returncode:
                stderr = result.stderr.decode("utf-8", errors="replace") if isinstance(result.stderr, bytes) else result.stderr
                match = re.search(r"An error occurred \(([A-Za-z0-9]+)\) when calling the [A-Za-z0-9]+ operation:", stderr or "")
                category = {
                    "AccessDenied": "access_denied", "AccessDeniedException": "access_denied",
                    "UnauthorizedOperation": "access_denied", "ExpiredToken": "credentials_expired",
                    "ExpiredTokenException": "credentials_expired", "InvalidClientTokenId": "credentials_invalid",
                    "Throttling": "throttled", "ThrottlingException": "throttled",
                    "TooManyRequestsException": "throttled", "RequestLimitExceeded": "throttled",
                    "ResourceNotFoundException": "resource_not_found", "ValidationException": "invalid_configuration",
                    "InvalidParameterValueException": "invalid_configuration",
                    "InternalFailure": "service_error", "InternalServerError": "service_error",
                    "InternalServerException": "service_error", "ServiceUnavailableException": "service_error",
                }.get(match.group(1) if match else "", "command_failed")
                raise _fail("aws_" + phase + "_" + category)
            if len(result.stdout) > _MAX_BYTES:
                raise ValueError
            parsed = json.loads(result.stdout)
            if not isinstance(parsed, dict):
                raise ValueError
            return parsed
        except subprocess.TimeoutExpired:
            raise _fail("aws_" + phase + "_timeout") from None
        except OSError:
            raise _fail("aws_" + phase + "_unavailable") from None
        except (ValueError, subprocess.SubprocessError):
            raise _fail("aws_" + phase + "_invalid_response") from None

    def embed_url(self) -> str:
        result = self.call("quicksight", "generate-embed-url-for-registered-user", {
            "AwsAccountId": self.target.account_id, "UserArn": self.target.user_arn,
            "ExperienceConfiguration": {"QuickChat": {}},
            "AllowedDomains": [_origin(self.target.endpoint_url)], "SessionLifetimeInMinutes": 15,
        })
        url = result.get("EmbedUrl")
        if not isinstance(url, str) or _origin(url).removeprefix("https://") not in self.target.allowed_browser_hosts:
            raise _fail("embed_host_not_registered")
        if not re.fullmatch(r"/embedding/[^/]+/quick/chat/?", urlsplit(url).path):
            raise _fail("unexpected_embed_experience")
        return url

    def logs(self, start_ms: int, end_ms: int) -> list[dict]:
        payload = {"logGroupName": self.target.log_group, "startTime": start_ms,
                   "endTime": end_ms, "limit": 100,
                   "filterPattern": '{ $.agent_id = ' + json.dumps(self.target.agent_id) +
                       ' && $.user_arn = ' + json.dumps(self.target.user_arn) + ' }'}
        events: list[dict] = []
        seen_tokens = set()
        # A busy/misconfigured log source fails closed instead of selecting a
        # convenient first result from an incompletely searched interval.
        for _ in range(10):
            page = self.call("logs", "filter-log-events", payload)
            rows = page.get("events")
            if not isinstance(rows, list):
                raise _fail("invalid_log_page")
            for row in rows:
                try:
                    record = json.loads(row["message"])
                    if not isinstance(record, dict):
                        raise ValueError
                    events.append(record)
                except (KeyError, TypeError, ValueError):
                    raise _fail("invalid_chat_log") from None
            token = page.get("nextToken")
            if not token:
                return events
            if not isinstance(token, str) or token in seen_tokens:
                raise _fail("log_pagination_incomplete")
            seen_tokens.add(token)
            payload["nextToken"] = token
        raise _fail("log_pagination_incomplete")


def correlated_answer(records: list[dict], target: Target, prompt: str,
                      start_ms: int, end_ms: int, conversation: str | None = None,
                      *, allow_empty_response: bool = False) -> dict | None:
    if type(allow_empty_response) is not bool:
        raise _fail("invalid_empty_response_mode")
    matches: dict[tuple[str, str], dict] = {}
    for row in records:
        if (row.get("logType") != "CHAT_LOGS" or row.get("accountId") != target.account_id
                or row.get("user_arn") != target.user_arn or row.get("agent_id") != target.agent_id
                or row.get("user_message") != prompt):
            continue
        stamp = row.get("event_timestamp")
        if type(stamp) not in (int, float) or not math.isfinite(stamp) or not start_ms <= stamp <= end_ms:
            continue
        if conversation is not None and row.get("conversation_id") != conversation:
            raise _fail("conversation_changed_within_case")
        ids = tuple(row.get(key) for key in ("conversation_id", "user_message_id", "system_message_id"))
        if not all(isinstance(value, str) and value for value in ids):
            raise _fail("chat_log_identity_missing")
        key = ids[:2]
        if key in matches and matches[key] != row:
            raise _fail("conflicting_chat_evidence")
        matches[key] = row
    if len(matches) > 1:
        raise _fail("ambiguous_chat_evidence")
    if not matches:
        return None
    row = next(iter(matches.values()))
    status = row.get("status_code")
    if not isinstance(status, str) or status not in _TEXT_RESPONSE_STATUSES:
        raise _fail("chat_request_not_successful")
    answer = row.get("system_text_message")
    if (not isinstance(answer, str) or len(answer.encode()) > _MAX_TEXT
            or (not answer.strip() and not allow_empty_response)):
        raise _fail("chat_answer_missing_or_oversized")
    return row


def knowledge_evidence(row: dict, target: Target) -> dict:
    """Bounded AWS metadata for the opt-in knowledge profile, never tool proof.

    Missing/malformed fields stay explicit; truncation cannot make a complete
    permission or citation check. No arbitrary log fields or credentials pass.
    """
    result = {"source": "aws_chat_logs", "native_status_code": row["status_code"],
              "user_arn_sha256": _sha(target.user_arn),
              "account_id_sha256": _sha(target.account_id), "agent_id_sha256": _sha(target.agent_id),
              "prompt_sha256": _sha(row["user_message"]),
              "response_sha256": _sha(row["system_text_message"]),
              "conversation_sha256": _sha(row["conversation_id"]),
              "message_sha256": _sha(row["user_message_id"]),
              "event_timestamp": row["event_timestamp"], "metadata_complete": True}
    if not row["system_text_message"].strip():
        # Literal empty text is an observation, never a fabricated refusal or
        # evidence of safe behavior. Only the assessment opts into collecting it.
        result["native_text_state"] = "empty"
    fields = {"cited_resource": ("citedResourceType", "citedResourceId", "citedResourceName"),
              "user_selected_resources": ("resourceId", "resourceType"),
              "action_connectors": ("actionConnectorId",)}
    for name, keys in fields.items():
        values = row.get(name)
        if not isinstance(values, list) or len(values) > 30:
            result["metadata_complete"] = False
            result[name] = []
            continue
        clean = []
        for item in values:
            if not isinstance(item, dict) or any(
                value is not None and (not isinstance(value, str) or not value.strip()
                    or len(value) > 512 or any(ord(char) < 32 for char in value))
                for key, value in item.items() if key in keys
            ) or not any(item.get(key) for key in keys):
                result["metadata_complete"] = False
                continue
            clean.append({key: item[key] for key in keys if key in item})
        result[name] = clean
    return result


class BrowserChat:
    """Control our SDK harness; read answers only from the AWS evidence path."""

    def __init__(self, target: Target):
        self.target = target
        self.deadline: float | None = None
        self.pw = self.browser = self.context = self.page = None

    def remaining_ms(self) -> int:
        remaining = 30 if self.deadline is None else self.deadline - time.monotonic()
        if remaining <= 0:
            raise _fail("case_deadline_exceeded")
        return max(1, int(min(30, remaining) * 1000))

    def allowed(self, url: str) -> bool:
        try:
            parsed = urlsplit(url)
            check_url = "https:" + url[4:] if parsed.scheme == "wss" else url
            host = urlsplit(_origin(check_url)).hostname
            if host not in self.target.allowed_browser_hosts:
                return False
            addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
            return bool(addresses) and all(ipaddress.ip_address(row[4][0]).is_global for row in addresses)
        except (OSError, ValueError, RunnerError):
            return False

    def open(self, embed_url: str) -> None:
        try:
            from playwright.sync_api import sync_playwright
            self.pw = sync_playwright().start()
            self.browser = self.pw.chromium.launch(headless=True)
            self.context = self.browser.new_context(service_workers="block", accept_downloads=False)
            self.context.route("**/*", lambda route: route.continue_() if self.allowed(route.request.url) else route.abort())
            self.context.route_web_socket("**/*", lambda route: route.connect_to_server() if self.allowed(route.url) else route.close())
            self.page = self.context.new_page()
            self.page.goto(self.target.endpoint_url, wait_until="domcontentloaded", timeout=self.remaining_ms())
            if self.page.url != self.target.endpoint_url:
                raise ValueError
            ready = self.page.evaluate("p => window.backbondQuickPilot.mount(p)",
                                       {"embedUrl": embed_url, "agentId": self.target.agent_id,
                                        "timeoutMs": self.remaining_ms()})
            if ready != {"ready": True, "version": 1}:
                raise ValueError
        except Exception:
            self.close()
            raise _fail("browser_harness_unavailable") from None

    def send(self, prompt: str) -> None:
        try:
            ack = self.page.evaluate("p => window.backbondQuickPilot.send(p)",
                                     {"prompt": prompt, "timeoutMs": self.remaining_ms()})
            if ack != {"submitted": True}:
                raise ValueError
        except Exception:
            raise _fail("prompt_submission_unknown_no_retry") from None

    def close(self):
        for obj in (self.context, self.browser, self.pw):
            if obj is not None:
                try:
                    obj.close() if hasattr(obj, "close") else obj.stop()
                except Exception:
                    pass
        self.context = self.browser = self.pw = self.page = None


class AmazonQuickRunner(ModelRunner):
    # This transport observes conversational behavior, not native tool calls.
    has_tools = False

    def __init__(self, endpoint: str, secret: str, endpoint_config: dict | None = None,
                 *, target: Target | None = None, aws: Any = None, browser_factory: Any = None):
        config = endpoint_config or {}
        validate_endpoint(endpoint, config.get("auth", {}).get("type", "bearer"), config)
        self.target = target or load_target(endpoint, secret)
        self.ledger = Ledger(self.target)
        self.aws = aws or AwsCli(self.target)
        self.browser_factory = browser_factory or BrowserChat
        self._include_rows = bool(config.get("_include_result_rows"))
        self._include_knowledge = bool(config.get("_include_knowledge_evidence"))
        self._knowledge: list[dict] = []
        self._pending = None
        self._attempted: list[str] = []
        self._rows: list[dict] = []
        self._evidence: list[dict] = []
        self._redact = tuple(value for value in (secret,) if value)
        self._secret = secret
        self._registered = target is None
        self._scan_deadline: float | None = None

    def set_scan_deadline(self, deadline_monotonic: float) -> None:
        self._scan_deadline = deadline_monotonic

    def model_id(self) -> str:
        return "amazon-quick:" + _sha(self.target.account_id + self.target.region + self.target.agent_id)[:24]

    def complete(self, prompt: str) -> str:
        return self.complete_conversation((prompt,))[0]

    def complete_conversation(self, turns: tuple[str, ...] | list[str]) -> tuple[str, ...]:
        self._pending = None
        self._knowledge = []
        self._attempted = []
        if not isinstance(turns, (tuple, list)) or not 1 <= len(turns) <= 10 or any(not isinstance(p, str) or not p.strip() or len(p.encode()) > _MAX_TEXT for p in turns):
            raise _fail("invalid_case_turns")
        replies, evidence = [], []
        if self._registered and load_target(self.target.endpoint_url, self._secret) != self.target:
            raise _fail("operator_target_changed")
        with self.ledger.case() as owner:
            deadline = time.monotonic() + self.target.timeout_seconds
            if self._scan_deadline is not None:
                deadline = min(deadline, self._scan_deadline)
            self.aws.deadline = deadline
            browser = self.browser_factory(self.target)
            browser.deadline = deadline
            submitted = False
            try:
                if time.monotonic() >= deadline:
                    raise _fail("case_deadline_exceeded")
                # Reserve before even minting an embed session. Exhausted
                # pilots must not incur further embedding activity.
                self.ledger.reserve(owner)
                browser.open(self.aws.embed_url())
                conversation = None
                prior_messages = set()
                for index, prompt in enumerate(turns):
                    if time.monotonic() >= deadline:
                        raise _fail("case_deadline_exceeded")
                    if self._registered and load_target(self.target.endpoint_url, self._secret) != self.target:
                        raise _fail("operator_target_changed")
                    if index:
                        self.ledger.reserve(owner)
                    self.ledger.submitting()
                    submitted = True
                    self._attempted.append(prompt)
                    start = int(time.time() * 1000)
                    browser.send(prompt)
                    log_read_retries = 0
                    while time.monotonic() < deadline:
                        end = int(time.time() * 1000)
                        try:
                            records = self.aws.logs(start, end)
                        except RunnerError as exc:
                            # Only the read-only evidence query may be retried.
                            # Never resend a prompt or mint another embedding.
                            retryable = {"amazon quick: aws_chat_logs_" + suffix for suffix in
                                         ("timeout", "throttled", "service_error", "command_failed")}
                            if str(exc) not in retryable or log_read_retries >= 2:
                                raise
                            log_read_retries += 1
                            time.sleep(min(2.0 ** log_read_retries, max(0.0, deadline - time.monotonic())))
                            continue
                        rows = [item for item in records
                                if item.get("user_message_id") not in prior_messages]
                        row = correlated_answer(rows, self.target, prompt, start, end, conversation)
                        if row is not None:
                            break
                        time.sleep(min(1.0, max(0.0, deadline - time.monotonic())))
                    else:
                        raise _fail("answer_evidence_timeout_no_retry")
                    if time.monotonic() >= deadline or time.time() >= self.target.expires_at:
                        raise _fail("case_deadline_exceeded")
                    self.ledger.accept(owner, row["conversation_id"], row["user_message_id"])
                    conversation = row["conversation_id"]
                    prior_messages.add(row["user_message_id"])
                    replies.append(row["system_text_message"])
                    if self._include_knowledge:
                        self._knowledge.append(knowledge_evidence(row, self.target))
                    evidence.append({"conversation_sha256": _sha(conversation),
                                     "message_sha256": _sha(row["user_message_id"]),
                                     "prompt_sha256": _sha(row["user_message"]),
                                     "response_sha256": _sha(row["system_text_message"]),
                                     "event_timestamp": row["event_timestamp"], "source": "aws_chat_logs",
                                     "native_status_code": row["status_code"],
                                     "log_read_retries": log_read_retries})
            except Exception:
                if submitted:
                    self.ledger.halt()
                raise
            finally:
                browser.close()
        self._evidence.extend(evidence)
        self._pending = (tuple(turns), tuple(replies), tuple(evidence))
        return tuple(replies)

    def record_starter_verdict(self, *, test_id: str, family: str, verdict: str, reason: str = "") -> None:
        if self._include_rows:
            prompts, replies, evidence = self._pending or (tuple(self._attempted), (), ())
            def clean(text):
                for value in self._redact:
                    text = text.replace(value, "[REDACTED]")
                return text if len(text) <= 4000 else text[:3988] + "\n[TRUNCATED]"
            row = {"test_id": test_id, "family": family, "verdict": verdict,
                   "prompt": clean("\n".join(prompts)), "reason": clean(reason),
                   "transport_evidence": evidence}
            if self._pending:
                row["reply"] = clean("\n".join(replies))
            self._rows.append(row)
        self._pending = None
        self._attempted = []

    def starter_result_rows(self) -> tuple[dict, ...]:
        return tuple(dict(row) for row in self._rows)

    def evidence_receipts(self) -> tuple[dict, ...]:
        return tuple(dict(row) for row in self._evidence)

    def knowledge_evidence_receipts(self) -> tuple[dict, ...]:
        return tuple(copy.deepcopy(self._knowledge))

    def close(self):
        self.ledger.close()
        self._secret = ""
        self._redact = ()
