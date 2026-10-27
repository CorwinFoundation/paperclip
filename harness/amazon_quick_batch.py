"""Opt-in concurrent Quick conversations; only correlated CHAT_LOGS finalize answers.

Independent browser contexts share a bounded persistent Chromium process.
Browser text can pace another turn, but cannot replace provider evidence.
No prompt, embed request or uncertain submission is retried.
"""
from __future__ import annotations

import asyncio
from collections import Counter
from contextlib import asynccontextmanager
from dataclasses import dataclass
import json
import secrets
import sqlite3
import time
from typing import Any
from urllib.parse import urlsplit

from harness.amazon_quick import (
    BrowserChat, Ledger, Target, _MAX_BYTES, _MAX_TEXT, _fail, _origin, _sha,
    correlated_answer, knowledge_evidence, load_target,
)
from harness.runner import RunnerError


@dataclass(frozen=True)
class BatchCase:
    case_id: str
    role: str
    turns: tuple[str, ...]


class ConversationLedger(Ledger):
    """Transactional, restart-safe budgets and per-conversation identity slots."""

    def __init__(self, target: Target):
        super().__init__(target)
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS quick_batch_authorizations
            (id TEXT PRIMARY KEY, binding TEXT, calls INTEGER, cap INTEGER, halted INTEGER);
          CREATE TABLE IF NOT EXISTS quick_batch_cases
            (owner TEXT PRIMARY KEY, authorization TEXT, pilot TEXT, identity TEXT, conversation TEXT);
          CREATE TABLE IF NOT EXISTS quick_batch_submissions
            (owner TEXT, turn INTEGER, prompt_hash TEXT, start_ms INTEGER, deadline REAL,
             status TEXT, answer_hash TEXT, captured_hash TEXT, reason TEXT,
             PRIMARY KEY(owner,turn));
          CREATE TABLE IF NOT EXISTS quick_late_evidence
            (owner TEXT, turn INTEGER, evidence_hash TEXT, observed_ms INTEGER,
             PRIMARY KEY(owner,turn,evidence_hash));
        """)

    def authorize(self, authorization: str, targets: dict[str, Target], cap: int):
        import re
        if (not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", authorization)
                or type(cap) is not int or not 1 <= cap <= 5000
                or len({t.pilot_id for t in targets.values()}) != len(targets)):
            raise _fail("invalid_batch_authorization")
        binding = _sha(json.dumps(sorted((t.pilot_id, t.endpoint_url, t.user_arn,
                                        t.agent_id, t.log_group, t.max_calls,
                                        t.max_concurrent_conversations)
                                       for t in targets.values())))
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            consumed = sum(self.db.execute("SELECT calls FROM pilots WHERE id=?",
                                          (t.pilot_id,)).fetchone()[0] for t in targets.values())
            self.db.execute("INSERT OR IGNORE INTO quick_batch_authorizations VALUES (?,?,?,?,0)",
                            (authorization, binding, consumed, cap))
            row = self.db.execute("SELECT binding,cap,halted FROM quick_batch_authorizations WHERE id=?",
                                  (authorization,)).fetchone()
            if row != (binding, cap, 0):
                raise _fail("batch_authorization_changed_or_halted")
            for target in targets.values():
                self.db.execute("INSERT OR IGNORE INTO quick_batch_authorization_pilots VALUES (?,?)",
                                (target.pilot_id, authorization))
                if self.db.execute("SELECT authorization FROM quick_batch_authorization_pilots WHERE pilot=?",
                                   (target.pilot_id,)).fetchone()[0] != authorization:
                    raise _fail("pilot_already_bound_to_batch_authorization")
        self.authorization = authorization

    def check(self):
        if time.time() >= self.target.expires_at:
            raise _fail("pilot_expired")
        if self.db.execute("SELECT halted FROM pilots WHERE id=?", (self.target.pilot_id,)).fetchone()[0]:
            raise _fail("pilot_halted_operator_review_required")
        if self.db.execute("SELECT halted FROM quick_batch_authorizations WHERE id=?",
                           (self.authorization,)).fetchone()[0]:
            raise _fail("batch_halted_operator_review_required")

    def acquire(self, owner: str):
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            self.check()
            now = time.time()
            old = self.db.execute("SELECT until FROM leases WHERE identity=?", (self.identity,)).fetchone()
            if old and old[0] > now:
                raise _fail("test_identity_busy")
            # Unresolved submissions from a crashed/finished worker cannot be
            # cleared by lease expiry or by switching pilot IDs.
            unresolved = self.db.execute("""SELECT 1 FROM quick_batch_submissions s
                JOIN quick_batch_cases c ON c.owner=s.owner
                LEFT JOIN quick_conversation_leases l ON l.owner=c.owner
                WHERE c.identity=? AND s.status IN ('submitting','timed_out','unknown')
                  AND (l.until IS NULL OR l.until<=?)""",
                (self.identity, now)).fetchone()
            if unresolved or self.db.execute("SELECT inflight FROM pilots WHERE id=?",
                                            (self.target.pilot_id,)).fetchone()[0] and not self.db.execute(
                    "SELECT 1 FROM quick_conversation_leases WHERE pilot=? AND until>?",
                    (self.target.pilot_id, now)).fetchone():
                raise _fail("unresolved_submission_operator_review_required")
            count = self.db.execute("SELECT COUNT(*) FROM quick_conversation_leases WHERE identity=? AND until>?",
                                    (self.identity, now)).fetchone()[0]
            if count >= self.target.max_concurrent_conversations:
                raise _fail("test_identity_busy")
            self.db.execute("INSERT INTO quick_conversation_leases VALUES (?,?,?,?)",
                            (owner, self.identity, self.target.pilot_id, now + self.target.timeout_seconds + 30))
            self.db.execute("INSERT INTO quick_batch_cases VALUES (?,?,?,?,NULL)",
                            (owner, self.authorization, self.target.pilot_id, self.identity))

    def reserve_turn(self, owner: str, index: int, prompt: str, start_ms: int, deadline: float):
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            self.check()
            lease = self.db.execute("SELECT until FROM quick_conversation_leases WHERE owner=? AND identity=?",
                                    (owner, self.identity)).fetchone()
            if not lease or lease[0] <= time.time():
                raise _fail("identity_lease_expired")
            self.reserve_shared_budget()
            if self.db.execute("UPDATE pilots SET calls=calls+1,inflight=inflight+1 WHERE id=? AND calls<? AND halted=0",
                               (self.target.pilot_id, self.target.max_calls)).rowcount != 1:
                raise _fail("call_budget_exhausted")
            self.db.execute("INSERT INTO quick_batch_submissions VALUES (?,?,?,?,?,'submitting',NULL,NULL,NULL)",
                            (owner, index, _sha(prompt), start_ms, deadline))

    def capture(self, owner: str, index: int, text: str):
        with self.db:
            self.db.execute("UPDATE quick_batch_submissions SET captured_hash=? WHERE owner=? AND turn=?",
                            (_sha(text), owner, index))

    def accept_turn(self, owner: str, index: int, row: dict):
        conv = _sha(self.identity + row["conversation_id"])
        msg = _sha(self.identity + row["user_message_id"])
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            state = self.db.execute("SELECT status,captured_hash FROM quick_batch_submissions WHERE owner=? AND turn=?",
                                    (owner, index)).fetchone()
            if state is None:
                raise _fail("submission_missing")
            if state[0] in {"timed_out", "unknown"}:
                self.db.execute("INSERT OR IGNORE INTO quick_late_evidence VALUES (?,?,?,?)",
                                (owner, index, _sha(json.dumps(row, sort_keys=True)), int(time.time() * 1000)))
                return False
            if state[0] != "submitting":
                return False
            existing = self.db.execute("SELECT owner FROM conversations WHERE id=?", (conv,)).fetchone()
            if existing and existing[0] != owner:
                raise _fail("conversation_reused_across_cases")
            bound = self.db.execute("SELECT conversation FROM quick_batch_cases WHERE owner=?", (owner,)).fetchone()[0]
            if bound is not None and bound != conv:
                raise _fail("conversation_changed_within_case")
            if state[1] is not None and state[1] != _sha(row["system_text_message"]):
                raise _fail("evidence_mismatch")
            self.db.execute("INSERT OR IGNORE INTO conversations VALUES (?,?)", (conv, owner))
            try:
                self.db.execute("INSERT INTO messages VALUES (?)", (msg,))
            except sqlite3.IntegrityError:
                raise _fail("message_replayed") from None
            self.db.execute("UPDATE quick_batch_cases SET conversation=? WHERE owner=?", (conv, owner))
            self.db.execute("UPDATE quick_batch_submissions SET status='verified',answer_hash=? WHERE owner=? AND turn=?",
                            (_sha(row["system_text_message"]), owner, index))
            self.db.execute("UPDATE pilots SET inflight=inflight-1 WHERE id=?", (self.target.pilot_id,))
            return True

    def uncertain(self, owner: str, index: int, reason: str):
        status = "timed_out" if reason == "answer_evidence_timeout_no_retry" else "unknown"
        with self.db:
            self.db.execute("UPDATE quick_batch_submissions SET status=?,reason=? WHERE owner=? AND turn=? AND status='submitting'",
                            (status, reason, owner, index))
            self.db.execute("UPDATE quick_batch_authorizations SET halted=1 WHERE id=?", (self.authorization,))
            self.db.execute("""UPDATE pilots SET halted=1 WHERE id IN
                (SELECT pilot FROM quick_batch_cases WHERE authorization=?)""",
                (self.authorization,))
            if reason == "evidence_mismatch":
                self.db.execute("UPDATE quick_batch_submissions SET status='mismatch',reason=? WHERE owner=? AND turn=? AND status='verified'",
                                (reason, owner, index))

    def release(self, owner: str):
        with self.db:
            self.db.execute("DELETE FROM quick_conversation_leases WHERE owner=?", (owner,))


class BotoEvidence:
    """In-process clients, no implicit SDK retries; log retries belong to sweeps."""

    def __init__(self, target: Target):
        import boto3
        from botocore.config import Config
        self.target = target
        session = boto3.Session(profile_name=target.aws_profile or None, region_name=target.region)
        options = Config(retries={"total_max_attempts": 1}, connect_timeout=5, read_timeout=10)
        self.quick = session.client("quicksight", config=options)
        self.logs_client = session.client("logs", config=options)
        self.log_api_calls = self.embed_api_calls = 0

    @staticmethod
    def error(exc, phase):
        code = getattr(exc, "response", {}).get("Error", {}).get("Code", "")
        category = {"ThrottlingException": "throttled", "TooManyRequestsException": "throttled",
                    "ServiceUnavailableException": "service_error", "InternalFailure": "service_error",
                    "InternalServerException": "service_error", "AccessDeniedException": "access_denied",
                    "ExpiredTokenException": "credentials_expired"}.get(code, "command_failed")
        if type(exc).__name__ in {"ReadTimeoutError", "ConnectTimeoutError"}:
            category = "timeout"
        return _fail("aws_" + phase + "_" + category)

    def _embed(self):
        self.embed_api_calls += 1
        try:
            result = self.quick.generate_embed_url_for_registered_user(
                AwsAccountId=self.target.account_id, UserArn=self.target.user_arn,
                ExperienceConfiguration={"QuickChat": {}},
                AllowedDomains=[_origin(self.target.endpoint_url)], SessionLifetimeInMinutes=15)
            url = result.get("EmbedUrl")
            if (not isinstance(url, str) or urlsplit(_origin(url)).hostname not in self.target.allowed_browser_hosts
                    or not __import__("re").fullmatch(r"/embedding/[^/]+/quick/chat/?", urlsplit(url).path)):
                raise _fail("embed_host_not_registered")
            return url
        except RunnerError:
            raise
        except Exception as exc:
            raise self.error(exc, "embed") from None

    async def embed_url(self):
        return await asyncio.to_thread(self._embed)

    def _logs(self, start_ms, end_ms):
        payload = {"logGroupName": self.target.log_group, "startTime": start_ms, "endTime": end_ms,
                   "limit": 1000, "filterPattern": '{ $.agent_id = ' + json.dumps(self.target.agent_id)
                   + ' && $.user_arn = ' + json.dumps(self.target.user_arn) + ' }'}
        rows, tokens, size = [], set(), 0
        for _ in range(10):
            self.log_api_calls += 1
            try:
                page = self.logs_client.filter_log_events(**payload)
            except Exception as exc:
                raise self.error(exc, "chat_logs") from None
            if not isinstance(page.get("events"), list):
                raise _fail("invalid_log_page")
            for event in page["events"]:
                try:
                    message = event["message"]
                    size += len(message.encode())
                    if size > 16 * _MAX_BYTES:
                        raise ValueError
                    row = json.loads(message)
                    if not isinstance(row, dict):
                        raise ValueError
                    rows.append(row)
                except (KeyError, TypeError, ValueError):
                    raise _fail("invalid_chat_log") from None
            token = page.get("nextToken")
            if not token:
                return rows
            if not isinstance(token, str) or token in tokens:
                raise _fail("log_pagination_incomplete")
            tokens.add(token)
            payload["nextToken"] = token
        raise _fail("log_pagination_incomplete")

    async def logs(self, start_ms, end_ms):
        return await asyncio.to_thread(self._logs, start_ms, end_ms)


class PersistentBrowser:
    """One Chromium, fresh storage per conversation, bounded recycling."""

    def __init__(self, *, recycle_after=50, capture_spec=None):
        self.recycle_after = recycle_after
        self.capture_spec = capture_spec
        self.pw = self.browser = None
        self.active = self.opened = self.launches = 0
        self.condition = asyncio.Condition()

    async def _launch(self):
        if self.pw is None:
            from playwright.async_api import async_playwright
            self.pw = await async_playwright().start()
        if self.browser is None:
            self.browser = await self.pw.chromium.launch(headless=True)
            self.launches += 1
            self.opened = 0

    @asynccontextmanager
    async def context(self, target, deadline):
        async with self.condition:
            while self.opened >= self.recycle_after and self.active:
                await self.condition.wait()
            if self.opened >= self.recycle_after and self.browser is not None:
                await self.browser.close()
                self.browser = None
            await self._launch()
            self.opened += 1
            self.active += 1
        context = None
        try:
            context = await self.browser.new_context(service_workers="block", accept_downloads=False)
            policy = BrowserChat(target)
            async def route(request_route):
                if await asyncio.to_thread(policy.allowed, request_route.request.url):
                    await request_route.continue_()
                else:
                    await request_route.abort()
            async def socket_route(socket):
                if await asyncio.to_thread(policy.allowed, socket.url):
                    # Playwright returns the server route synchronously, also
                    # in its async API. Forwarding itself remains automatic.
                    socket.connect_to_server()
                else:
                    await socket.close()
            await context.route("**/*", route)
            await context.route_web_socket("**/*", socket_route)
            page = await context.new_page()
            yield AsyncChat(page, target, deadline, self.capture_spec)
        finally:
            if context is not None:
                await context.close()
            async with self.condition:
                self.active -= 1
                self.condition.notify_all()

    async def close(self):
        try:
            if self.browser is not None:
                await self.browser.close()
        finally:
            if self.pw is not None:
                await self.pw.stop()
            self.pw = self.browser = None


class AsyncChat:
    def __init__(self, page, target, deadline, capture_spec):
        self.page, self.target, self.deadline = page, target, deadline
        self.capture_spec, self.before = capture_spec, 0
        self.startup_stage, self.startup_error_kind = "not_started", None

    def remaining_ms(self, maximum_seconds=30):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise _fail("case_deadline_exceeded")
        return max(1, int(min(maximum_seconds, remaining) * 1000))

    async def open(self, embed_url):
        try:
            self.startup_stage = "navigation"
            await self.page.goto(self.target.endpoint_url, wait_until="domcontentloaded", timeout=self.remaining_ms())
            if self.page.url != self.target.endpoint_url:
                raise ValueError
            self.startup_stage = "sdk_mount"
            ready = await self.page.evaluate("p => window.backbondQuickPilot.mount(p)",
                                            {"embedUrl": embed_url, "agentId": self.target.agent_id,
                                             "timeoutMs": self.remaining_ms(60)})
            if ready != {"ready": True, "version": 1}:
                self.startup_stage = "unexpected_ready"
                raise ValueError
            self.startup_stage = "sdk_ready"
        except Exception as exc:
            kind = type(exc).__name__
            self.startup_error_kind = kind if kind in {"Error", "TimeoutError", "ValueError", "RunnerError"} else "unknown"
            raise _fail("browser_harness_unavailable") from None

    def frame(self):
        if not self.capture_spec:
            return None
        frames = [frame for frame in self.page.frames
                  if frame != self.page.main_frame
                  and frame.url.startswith(self.capture_spec["frame_url_prefix"])
                  and urlsplit(frame.url).hostname in self.target.allowed_browser_hosts]
        return frames[0] if len(frames) == 1 else None

    async def prepare(self):
        frame = self.frame()
        self.before = await frame.locator(self.capture_spec["assistant_selector"]).count() if frame else 0

    async def send(self, prompt):
        try:
            ack = await self.page.evaluate("p => window.backbondQuickPilot.send(p)",
                                           {"prompt": prompt, "timeoutMs": self.remaining_ms()})
            if ack != {"submitted": True}:
                raise ValueError
        except Exception:
            raise _fail("prompt_submission_unknown_no_retry") from None

    async def capture(self):
        # All selectors are operator-reviewed, versioned configuration. No
        # guessed selector can silently turn into an authoritative answer.
        frame = self.frame()
        if frame is None:
            return None, "browser_capture_unconfigured_or_frame_changed_log_fallback"
        last, stable = None, time.monotonic()
        try:
            while time.monotonic() < self.deadline:
                locator = frame.locator(self.capture_spec["assistant_selector"])
                if await locator.count() <= self.before:
                    await asyncio.sleep(0.25)
                    continue
                text = await locator.last.inner_text(timeout=self.remaining_ms())
                generating = await frame.locator(self.capture_spec["generating_selector"]).count()
                if text != last or generating:
                    last, stable = text, time.monotonic()
                if text and text.strip() and len(text.encode()) <= _MAX_TEXT and not generating and time.monotonic() - stable >= 2:
                    return text, "browser_capture_pacing_only"
                await asyncio.sleep(0.25)
        except Exception:
            pass
        return None, "browser_capture_selector_failure_log_fallback"


@dataclass
class PendingTurn:
    owner: str
    index: int
    prompt: str
    start_ms: int
    deadline: float
    future: Any
    captured: str | None = None
    pacing: str = "awaiting_provider_evidence"


class EvidenceSweep:
    """One bounded query per user/group sweep, shared by all its conversations."""

    def __init__(self, target, aws, ledger, stop, *, interval=15, allow_empty_responses=False):
        self.target, self.aws, self.ledger, self.stop = target, aws, ledger, stop
        self.interval, self.pending = interval, []
        self.sweeps = self.read_retries = self.late_rows = 0
        self.accepted_by_owner = {}
        self.closed = False
        self.allow_empty_responses = allow_empty_responses

    def add(self, owner, index, prompt, start_ms, deadline):
        item = PendingTurn(owner, index, prompt, start_ms, deadline, asyncio.get_running_loop().create_future())
        self.pending.append(item)
        return item

    def fail(self, item, code):
        if not item.future.done():
            self.stop.set()
            try:
                self.ledger.uncertain(item.owner, item.index, code)
            except sqlite3.Error:
                # The previously committed pending submission remains a
                # restart gate even if the halt write cannot be committed.
                code = "persistent_state_failure_operator_review_required"
            item.future.set_exception(_fail(code))

    async def sweep(self):
        # Completed rows are retained in the durable ledger, not repeatedly
        # fetched over an ever-growing window. Keep failed rows briefly for
        # late-evidence recording without changing their original outcome.
        self.pending = [item for item in self.pending if not item.future.done()
                        or (item.future.exception() is not None and time.monotonic() <= item.deadline + 30)]
        if not self.pending:
            return
        now_ms = int(time.time() * 1000)
        start = min(item.start_ms for item in self.pending)
        rows = None
        self.sweeps += 1
        for attempt in range(3):
            try:
                rows = await self.aws.logs(start, now_ms)
                break
            except RunnerError as exc:
                retryable = {"amazon quick: aws_chat_logs_" + suffix
                             for suffix in ("timeout", "throttled", "service_error", "command_failed")}
                if str(exc) not in retryable or attempt == 2:
                    for item in self.pending:
                        self.fail(item, "evidence_sweep_failed_no_target_retry")
                    return
                self.read_retries += 1
                await asyncio.sleep(min(2 ** (attempt + 1), self.interval))
        for item in self.pending:
            if item.future.done() and not item.future.cancelled() and item.future.exception() is None:
                continue
            try:
                prior = self.accepted_by_owner.get(item.owner, set())
                candidates = [row for row in rows if row.get("user_message_id") not in prior]
                row = correlated_answer(candidates, self.target, item.prompt, item.start_ms, now_ms,
                                        allow_empty_response=self.allow_empty_responses)
                if item.future.done():
                    if row is not None:
                        self.ledger.accept_turn(item.owner, item.index, row)
                        self.late_rows += 1
                    continue
                if time.monotonic() >= item.deadline or time.time() >= self.target.expires_at:
                    self.fail(item, "answer_evidence_timeout_no_retry")
                    if row is not None:
                        self.ledger.accept_turn(item.owner, item.index, row)
                    continue
                if row is not None:
                    self.ledger.accept_turn(item.owner, item.index, row)
                    self.accepted_by_owner.setdefault(item.owner, set()).add(row["user_message_id"])
                    item.future.set_result(row)
            except RunnerError as exc:
                code = str(exc).removeprefix("amazon quick: ")
                self.fail(item, code)

    async def run(self):
        try:
            while not self.closed:
                await asyncio.sleep(self.interval)
                await self.sweep()
        finally:
            for item in self.pending:
                self.fail(item, "sweep_closed_with_unresolved_submission")


class QuickBatchRunner:
    def __init__(self, targets, *, authorization, max_calls, aws_factory=BotoEvidence,
                 browser=None, sweep_seconds=15, max_wall_seconds=7200, registered_tokens=None,
                 concurrency=None, on_result=None, start_together=False, allow_empty_responses=False):
        if not targets or not 0 < sweep_seconds <= 15 or not 1 <= max_wall_seconds <= 14400:
            raise _fail("invalid_batch_limits")
        self.targets, self.authorization = targets, authorization
        if type(allow_empty_responses) is not bool:
            raise _fail("invalid_empty_response_mode")
        self.allow_empty_responses = allow_empty_responses
        self.max_calls, self.max_wall_seconds = max_calls, max_wall_seconds
        self.browser, self.aws_factory = browser or PersistentBrowser(), aws_factory
        self.registered_tokens = registered_tokens
        self.interval = sweep_seconds
        if concurrency is not None and (type(concurrency) is not int or not 1 <= concurrency <= 5
                                        or any(concurrency > t.max_concurrent_conversations for t in targets.values())):
            raise _fail("concurrency_exceeds_registration")
        self.concurrency = concurrency
        self.on_result = on_result
        self.ledgers, self.aws, self.sweeps = {}, {}, {}
        self.stop = asyncio.Event()
        self.prompt_locks = {}
        # Serialize submit acknowledgements, not answers. Once a submission is
        # uncertain no later queued send can pass this gate.
        self.submit_gate = asyncio.Lock()
        # Parallel iframe initialization can exhaust the bounded worker before
        # its mount deadline. Pace startup independently of live conversations:
        # at most three initializers, with one initializer per registered user.
        self.startup_slots = asyncio.Semaphore(3)
        self.startup_locks = {}
        if type(start_together) is not bool:
            raise _fail("invalid_batch_start_barrier")
        self.start_together = start_together
        self.ready_barrier = None
        self.initialized_together = 0

    def check_registration(self, role):
        ledger = self.ledgers[role]
        ledger.check()
        if self.stop.is_set():
            raise _fail("batch_halted_operator_review_required")
        if self.registered_tokens is not None:
            target = self.targets[role]
            if load_target(target.endpoint_url, self.registered_tokens[role]) != target:
                raise _fail("operator_target_changed")

    @asynccontextmanager
    async def _initialized_chat(self, case, ledger, owner, deadline, events):
        """Retry at most one SDK mount before yielding a ready chat."""
        reserved = False
        for attempt in (1, 2):
            self.check_registration(case.role)
            if time.monotonic() >= deadline:
                raise _fail("case_deadline_exceeded")
            async with self.browser.context(self.targets[case.role], deadline) as chat:
                async with self.submit_gate:
                    self.check_registration(case.role)
                    if not reserved:
                        ledger.reserve_turn(owner, 0, case.turns[0], int(time.time() * 1000),
                                            time.time() + max(0, deadline - time.monotonic()))
                        reserved = True
                    embed_url = await self.aws[case.role].embed_url()
                try:
                    await chat.open(embed_url)
                except RunnerError as exc:
                    if str(exc) != "amazon quick: browser_harness_unavailable":
                        raise
                    phase = getattr(chat, "startup_stage", "unknown")
                    kind = getattr(chat, "startup_error_kind", "unknown")
                    events.append({"attempt": attempt, "outcome": "sdk_unavailable",
                        "phase": phase if phase in {"not_started", "navigation", "sdk_mount", "unexpected_ready"} else "unknown",
                        "error_kind": kind if kind in {"Error", "TimeoutError", "ValueError", "RunnerError"} else "unknown"})
                    if attempt == 1 and time.monotonic() < deadline:
                        continue  # Close this context before generating a fresh URL.
                    raise
                events.append({"attempt": attempt, "outcome": "ready"})
                # Exceptions thrown after yield include uncertain sends. They
                # propagate directly; only chat.open above is eligible for retry.
                yield chat
                return

    async def _case(self, case, semaphore, deadline):
        async with semaphore:
            result = {"case_id": case.case_id, "role": case.role, "status": "could_not_run",
                      "turns": [], "reason": "not_submitted", "initialization": []}
            ledger, target = self.ledgers[case.role], self.targets[case.role]
            owner, items, locks = secrets.token_hex(16), [], []
            acquired = False
            startup_lock = self.startup_locks.setdefault(ledger.identity, asyncio.Lock())
            startup_locked = startup_slot = False
            try:
                self.check_registration(case.role)
                # Acquire all distinct prompt locks in a stable order. This
                # prevents both ambiguous attribution and multi-turn lock
                # inversion (case A->B running beside case B->A).
                for prompt_hash in sorted({_sha(p) for p in case.turns}):
                    lock = self.prompt_locks.setdefault((ledger.identity, prompt_hash), asyncio.Lock())
                    await lock.acquire()
                    locks.append(lock)
                self.check_registration(case.role)
                await startup_lock.acquire()
                startup_locked = True
                await self.startup_slots.acquire()
                startup_slot = True
                self.check_registration(case.role)
                # Queueing consumes the overall batch deadline, but does not
                # consume a conversation lease or its answer deadline.
                ledger.acquire(owner)
                acquired = True
                case_deadline = min(deadline, time.monotonic() + target.timeout_seconds)
                if time.monotonic() >= case_deadline:
                    raise _fail("case_deadline_exceeded")
                async with self._initialized_chat(case, ledger, owner, case_deadline, result["initialization"]) as chat:
                    self.startup_slots.release()
                    startup_slot = False
                    startup_lock.release()
                    startup_locked = False
                    if self.ready_barrier is not None:
                        await asyncio.wait_for(self.ready_barrier.wait(),
                            timeout=max(0, case_deadline-time.monotonic()))
                        self.initialized_together = self.ready_barrier.parties
                    for index, prompt in enumerate(case.turns):
                        if index and prompt in case.turns[:index]:
                            await asyncio.gather(*(item.future for item in items))
                        await chat.prepare()
                        async with self.submit_gate:
                            self.check_registration(case.role)
                            if time.monotonic() >= case_deadline:
                                raise _fail("case_deadline_exceeded")
                            start_ms = int(time.time() * 1000)
                            if index:
                                ledger.reserve_turn(owner, index, prompt, start_ms,
                                                    time.time() + max(0, case_deadline - time.monotonic()))
                            else:
                                with ledger.db:
                                    ledger.db.execute("UPDATE quick_batch_submissions SET start_ms=? WHERE owner=? AND turn=0",
                                                      (start_ms, owner))
                            item = self.sweeps[case.role].add(owner, index, prompt, start_ms, case_deadline)
                            items.append(item)
                            try:
                                await chat.send(prompt)
                            except BaseException:
                                self.sweeps[case.role].fail(item, "prompt_submission_unknown_no_retry")
                                raise
                        capture = asyncio.create_task(chat.capture())
                        try:
                            done, _ = await asyncio.wait((capture, item.future), return_when=asyncio.FIRST_COMPLETED)
                            if capture in done:
                                text, pacing = capture.result()
                                item.pacing = pacing
                                if text is not None:
                                    item.captured = text
                                    ledger.capture(owner, index, text)
                                    if item.future.done() and item.future.exception() is None:
                                        if _sha(text) != _sha(item.future.result()["system_text_message"]):
                                            ledger.uncertain(owner, index, "evidence_mismatch")
                                            self.stop.set()
                                            raise _fail("evidence_mismatch")
                                else:
                                    await item.future
                            else:
                                await item.future
                        finally:
                            if not capture.done():
                                capture.cancel()
                            await asyncio.gather(capture, return_exceptions=True)
                    rows = await asyncio.gather(*(item.future for item in items))
                    result.update(status="observed", reason="correlated_provider_answers", turns=[
                        {"prompt": item.prompt, "reply": row["system_text_message"],
                         "transport": knowledge_evidence(row, target), "pacing": item.pacing,
                         "browser_response_sha256": _sha(item.captured) if item.captured is not None else None}
                        for item, row in zip(items, rows)])
                    if any(not turn["reply"].strip() for turn in result["turns"]):
                        result["reason"] = "correlated_provider_empty_text"
            except (Exception, asyncio.CancelledError) as exc:
                self.stop.set()
                if self.ready_barrier is not None:
                    await self.ready_barrier.abort()
                result["reason"] = (str(exc).removeprefix("amazon quick: ") if isinstance(exc, RunnerError)
                                    else "batch_case_runtime_failed")
                if acquired:
                    try:
                        ledger.uncertain(owner, len(items) - 1 if items else 0, result["reason"])
                    except sqlite3.Error:
                        result["reason"] = "persistent_state_failure_operator_review_required"
                for item in items:
                    self.sweeps[case.role].fail(item, result["reason"])
                result["attempted_turns"] = len(items)
                result["turns"] = [
                    {"prompt": item.prompt, "reply": item.future.result()["system_text_message"],
                     "transport": knowledge_evidence(item.future.result(), target), "pacing": item.pacing,
                     "browser_response_sha256": _sha(item.captured) if item.captured is not None else None}
                    for item in items if item.future.done() and not item.future.cancelled()
                    and item.future.exception() is None]
                if isinstance(exc, asyncio.CancelledError):
                    raise
            finally:
                if startup_slot:
                    self.startup_slots.release()
                if startup_locked:
                    startup_lock.release()
                for item in items:
                    if item.future.done() and not item.future.cancelled():
                        item.future.exception()  # consume errors; preserve durable uncertainty
                if acquired:
                    try:
                        ledger.release(owner)
                    except sqlite3.Error:
                        self.stop.set()
                for lock in locks:
                    lock.release()
                if self.on_result is not None:
                    try:
                        self.on_result(result)
                    except Exception:
                        self.stop.set()
                        try:
                            ledger.halt()
                        except sqlite3.Error:
                            pass
                        raise _fail("evidence_checkpoint_failed_operator_review_required") from None
            return result

    async def run(self, cases):
        cases = tuple(cases)
        if (len({case.case_id for case in cases}) != len(cases)
                or any(case.role not in self.targets or not 1 <= len(case.turns) <= 10
                       or any(not isinstance(p, str) or not p.strip() or len(p.encode()) > _MAX_TEXT for p in case.turns)
                       for case in cases)):
            raise _fail("invalid_batch_cases")
        if self.start_together:
            # Qualification-only barrier: every case must fit the registered
            # concurrent slots and have a unique prompt, or it could deadlock
            # behind the per-user/prompt locks before reaching the barrier.
            counts = Counter(c.role for c in cases)
            if (not cases or any(len(c.turns) != 1 for c in cases)
                    or len({(c.role, c.turns[0]) for c in cases}) != len(cases)
                    or any(count > (self.concurrency or self.targets[role].max_concurrent_conversations)
                           for role, count in counts.items())
                    or len({t.user_arn for t in self.targets.values()}) != len(self.targets)):
                raise _fail("invalid_batch_start_barrier")
            self.ready_barrier = asyncio.Barrier(len(cases))
        tasks, case_tasks = [], []
        started = time.monotonic()
        deadline = started + self.max_wall_seconds
        try:
            # Initialize every pilot before binding the shared authorization.
            for role, target in self.targets.items():
                self.ledgers[role] = ConversationLedger(target)
            for role, target in self.targets.items():
                ledger = self.ledgers[role]
                ledger.authorize(self.authorization, self.targets, self.max_calls)
                self.aws[role] = self.aws_factory(target)
                self.sweeps[role] = EvidenceSweep(target, self.aws[role], ledger, self.stop, interval=self.interval,
                                                  allow_empty_responses=self.allow_empty_responses)
                tasks.append(asyncio.create_task(self.sweeps[role].run()))
            semaphores = {role: asyncio.Semaphore(self.concurrency or target.max_concurrent_conversations)
                          for role, target in self.targets.items()}
            async with asyncio.timeout(self.max_wall_seconds):
                case_tasks = [asyncio.create_task(self._case(case, semaphores[case.role], deadline)) for case in cases]
                results = await asyncio.gather(*case_tasks)
            return {"schema": "backbond-quick-batch-capture/v1", "authorization": self.authorization,
                    "status": "incomplete" if self.stop.is_set() else "capture_complete",
                    "elapsed_seconds": round(time.monotonic() - started, 3), "results": results,
                    "browser_launches": getattr(self.browser, "launches", None),
                    "initialized_together": self.initialized_together,
                    "api_calls": {role: {"embedding": getattr(client, "embed_api_calls", None),
                                         "logs": getattr(client, "log_api_calls", None)} for role, client in self.aws.items()},
                    "sweeps": {role: {"queries": sweep.sweeps, "read_retries": sweep.read_retries,
                                       "late_rows": sweep.late_rows} for role, sweep in self.sweeps.items()},
                    "backbond_score": "N/A"}
        finally:
            if any(not task.done() for task in case_tasks):
                self.stop.set()
                for task in case_tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*case_tasks, return_exceptions=True)
            for sweep in self.sweeps.values():
                sweep.closed = True
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await self.browser.close()
            for ledger in self.ledgers.values():
                ledger.close()
