"""Concurrent faults are offline; no target, SDK, credentials or judge required."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import replace
import json
import os
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, Mock, patch

from harness.amazon_quick import Ledger, Target, _sha
from harness.amazon_quick_batch import (
    BatchCase, ConversationLedger, EvidenceSweep, PersistentBrowser, QuickBatchRunner,
)
from harness.runner import RunnerError


def target(**changes):
    return Target.parse({"endpoint_url": "https://pilot.example.test/index.html",
        "account_id": "111122223333", "region": "us-east-1", "agent_id": "synthetic-agent",
        "user_arn": "arn:aws:quicksight:us-east-1:111122223333:user/default/synthetic",
        "log_group": "/pilot/synthetic", "token_sha256": "a" * 64, "pilot_id": "pilot-batch",
        "expires_at": "2099-01-01T00:00:00Z", "max_calls": 100,
        "max_concurrent_conversations": 5, "allowed_browser_hosts": ["pilot.example.test"], **changes})


def row(t, prompt="hello", **changes):
    return {"logType": "CHAT_LOGS", "accountId": t.account_id, "user_arn": t.user_arn,
            "agent_id": t.agent_id, "user_message": prompt, "system_text_message": "answer",
            "event_timestamp": int(time.time() * 1000), "status_code": "success",
            "conversation_id": "c-one", "user_message_id": "m-one", "system_message_id": "s-one",
            "cited_resource": [], "user_selected_resources": [], "action_connectors": [], **changes}


class StateTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        env = patch.dict(os.environ, {"ARE_AMAZON_QUICK_STATE_DIR": self.temp.name})
        env.start()
        self.addCleanup(env.stop)
        self.target = target()

    def ledger(self, t=None, cap=100):
        t = t or self.target
        ledger = ConversationLedger(t)
        ledger.authorize("test-authorization", {"employee": t}, cap)
        self.addCleanup(ledger.close)
        return ledger

    def test_registration_caps_and_concurrency_are_explicit_and_bounded(self):
        self.assertEqual(target(max_calls=5000).max_calls, 5000)
        for change in ({"max_calls": 5001}, {"max_concurrent_conversations": 6},
                       {"max_concurrent_conversations": True}, {"max_concurrent_conversations": 0}):
            with self.subTest(change=change), self.assertRaises(RunnerError):
                target(**change)
        self.assertEqual(target(max_concurrent_conversations=1).max_concurrent_conversations, 1)

    def test_conversation_slots_block_legacy_serial_runner_and_other_connections(self):
        t = replace(self.target, max_concurrent_conversations=2)
        first, second = self.ledger(t), self.ledger(t)
        first.acquire("one")
        second.acquire("two")
        with self.assertRaisesRegex(RunnerError, "test_identity_busy"):
            second.acquire("three")
        legacy = Ledger(t)
        self.addCleanup(legacy.close)
        with self.assertRaisesRegex(RunnerError, "test_identity_busy"), legacy.case():
            pass
        first.release("one")
        second.release("two")

    def test_budget_is_atomic_across_threads_and_rolls_back_failed_reservations(self):
        t = replace(self.target, max_calls=3)
        ledger = self.ledger(t, cap=3)
        def reserve(index):
            local = ConversationLedger(t)
            try:
                local.authorize("test-authorization", {"employee": t}, 3)
                local.acquire(str(index))
                try:
                    local.reserve_turn(str(index), 0, "prompt" + str(index), 1, time.time() + 30)
                    local.accept_turn(str(index), 0, row(t, conversation_id=str(index), user_message_id=str(index)))
                    return True
                finally:
                    local.release(str(index))
            except RunnerError:
                return False
            finally:
                local.close()
        with ThreadPoolExecutor(max_workers=5) as pool:
            accepted = list(pool.map(reserve, range(12)))
        self.assertEqual(sum(accepted), 3)
        self.assertEqual(ledger.db.execute("SELECT calls FROM pilots").fetchone()[0], 3)
        self.assertEqual(ledger.db.execute("SELECT calls FROM quick_batch_authorizations").fetchone()[0], 3)

    def test_conversation_reuse_cross_case_and_changed_conversation_fail_closed(self):
        ledger = self.ledger()
        ledger.acquire("one")
        ledger.reserve_turn("one", 0, "one", 1, time.time() + 30)
        self.assertTrue(ledger.accept_turn("one", 0, row(self.target)))
        ledger.reserve_turn("one", 1, "two", 2, time.time() + 30)
        with self.assertRaisesRegex(RunnerError, "conversation_changed"):
            ledger.accept_turn("one", 1, row(self.target, conversation_id="other", user_message_id="second"))
        ledger.acquire("two")
        ledger.reserve_turn("two", 0, "three", 3, time.time() + 30)
        with self.assertRaisesRegex(RunnerError, "conversation_reused"):
            ledger.accept_turn("two", 0, row(self.target, user_message_id="third"))

    def test_manual_serial_calls_share_batch_cap_and_new_authorization_cannot_reset_it(self):
        ledger = self.ledger(cap=1)
        legacy = Ledger(self.target)
        self.addCleanup(legacy.close)
        with legacy.case() as owner:
            legacy.reserve(owner)
        self.assertEqual(ledger.db.execute("SELECT calls FROM quick_batch_authorizations").fetchone()[0], 1)
        ledger.acquire("one")
        with self.assertRaisesRegex(RunnerError, "batch_call_budget_exhausted"):
            ledger.reserve_turn("one", 0, "hello", 1, time.time() + 30)
        self.assertEqual(ledger.db.execute("SELECT calls FROM pilots").fetchone()[0], 1)
        with self.assertRaisesRegex(RunnerError, "already_bound"):
            ledger.authorize("replacement-authorization", {"employee": self.target}, 100)

    def test_uncertainty_persists_after_release_restart_and_new_pilot_id(self):
        ledger = self.ledger()
        ledger.acquire("one")
        ledger.reserve_turn("one", 0, "one", 1, time.time() + 30)
        ledger.release("one")  # simulate abrupt process loss without a halt handler
        with self.assertRaisesRegex(RunnerError, "unresolved_submission"):
            ledger.acquire("two")
        other = ConversationLedger(replace(self.target, pilot_id="replacement"))
        self.addCleanup(other.close)
        other.authorize("different-authorization", {"employee": other.target}, 100)
        with self.assertRaisesRegex(RunnerError, "unresolved_submission"):
            other.acquire("three")

    def test_capture_mismatch_halts_and_late_row_does_not_upgrade_timeout(self):
        ledger = self.ledger()
        ledger.acquire("one")
        ledger.reserve_turn("one", 0, "one", 1, time.time() + 30)
        ledger.capture("one", 0, "browser answer")
        with self.assertRaisesRegex(RunnerError, "evidence_mismatch"):
            ledger.accept_turn("one", 0, row(self.target))
        ledger.uncertain("one", 0, "answer_evidence_timeout_no_retry")
        self.assertFalse(ledger.accept_turn("one", 0, row(self.target)))
        self.assertEqual(ledger.db.execute("SELECT status FROM quick_batch_submissions").fetchone()[0], "timed_out")
        self.assertEqual(ledger.db.execute("SELECT COUNT(*) FROM quick_late_evidence").fetchone()[0], 1)
        with self.assertRaisesRegex(RunnerError, "halted"):
            ledger.reserve_turn("one", 1, "another", 2, time.time() + 30)


class AsyncStateTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        env = patch.dict(os.environ, {"ARE_AMAZON_QUICK_STATE_DIR": self.temp.name})
        env.start()
        self.addCleanup(env.stop)
        self.target = target()

    def fake_runtime(self, *, captured=None, send_failure=False, read_failures=0):
        state = {"contexts": [], "active": 0, "max_active": 0, "rows": [], "sends": [],
                 "reads": 0, "embeds": 0, "closed": False}
        class Aws:
            def __init__(self, t): self.t = t
            async def embed_url(self):
                state["embeds"] += 1
                return "synthetic-url"
            async def logs(self, start, end):
                state["reads"] += 1
                if state["reads"] <= read_failures:
                    raise RunnerError("amazon quick: aws_chat_logs_throttled")
                return [r for r in state["rows"] if start <= r["event_timestamp"] <= end]
        class Browser:
            launches = 1
            @asynccontextmanager
            async def context(self, t, deadline):
                index = len(state["contexts"])
                context = {"index": index, "cookies": {}, "closed": False}
                state["contexts"].append(context)
                state["active"] += 1
                state["max_active"] = max(state["max_active"], state["active"])
                class Chat:
                    async def open(self, url):
                        self.cookies = context["cookies"]
                        self.cookies["session"] = index
                    async def prepare(self): pass
                    async def send(self, prompt):
                        state["sends"].append((index, prompt))
                        if send_failure:
                            raise RunnerError("amazon quick: prompt_submission_unknown_no_retry")
                        state["rows"].append(row(t, prompt, conversation_id="c" + str(index),
                                                 user_message_id="m" + str(len(state["sends"]))))
                    async def capture(self):
                        return captured, "browser_capture_selector_failure_log_fallback" if captured is None else "browser_capture_pacing_only"
                try:
                    yield Chat()
                finally:
                    state["active"] -= 1
                    context["closed"] = True
            async def close(self): state["closed"] = True
        return state, Aws, Browser()

    async def test_parallel_contexts_have_distinct_conversations_and_fallback_is_visible(self):
        state, aws, browser = self.fake_runtime()
        runner = QuickBatchRunner({"employee": self.target}, authorization="test", max_calls=30,
                                 aws_factory=aws, browser=browser, sweep_seconds=0.005)
        report = await runner.run([BatchCase(str(i), "employee", (f"prompt-{i}",)) for i in range(10)])
        self.assertEqual(report["status"], "capture_complete")
        self.assertGreater(state["max_active"], 1)
        hashes = [r["turns"][0]["transport"]["conversation_sha256"] for r in report["results"]]
        self.assertEqual(len(set(hashes)), 10)
        self.assertTrue(all(c["closed"] for c in state["contexts"]))
        self.assertTrue(state["closed"])
        self.assertTrue(all("fallback" in r["turns"][0]["pacing"] for r in report["results"]))

    async def test_same_prompts_are_serialized_and_reverse_multiturn_order_cannot_deadlock(self):
        state, aws, browser = self.fake_runtime(captured="answer")
        runner = QuickBatchRunner({"employee": self.target}, authorization="test", max_calls=30,
                                 aws_factory=aws, browser=browser, sweep_seconds=0.005)
        report = await asyncio.wait_for(runner.run([
            BatchCase("one", "employee", ("first", "second")),
            BatchCase("two", "employee", ("second", "first"))]), timeout=2)
        self.assertEqual(report["status"], "capture_complete")
        self.assertEqual(state["max_active"], 1)
        self.assertEqual(len(state["sends"]), 4)

    async def test_uncertain_first_submission_stops_queued_cases_without_retries(self):
        state, aws, browser = self.fake_runtime(send_failure=True)
        runner = QuickBatchRunner({"employee": self.target}, authorization="test", max_calls=30,
                                 aws_factory=aws, browser=browser, sweep_seconds=0.005)
        report = await runner.run([BatchCase(str(i), "employee", (f"prompt-{i}",)) for i in range(10)])
        self.assertEqual(report["status"], "incomplete")
        self.assertEqual(len(state["sends"]), 1)
        self.assertEqual(state["embeds"], 1)
        self.assertTrue(all(r["status"] == "could_not_run" for r in report["results"]))

    async def test_browser_hash_mismatch_never_becomes_observed(self):
        state, aws, browser = self.fake_runtime(captured="DIFFERENT")
        runner = QuickBatchRunner({"employee": self.target}, authorization="test", max_calls=3,
                                 aws_factory=aws, browser=browser, sweep_seconds=0.005)
        report = await runner.run([BatchCase("one", "employee", ("hello",))])
        self.assertEqual(report["status"], "incomplete")
        self.assertEqual(report["results"][0]["reason"], "evidence_mismatch")
        self.assertEqual(len(state["sends"]), 1)

    async def test_one_sweep_accepts_duplicate_identical_rows_once_and_records_late_timeout(self):
        ledger = ConversationLedger(self.target)
        self.addCleanup(ledger.close)
        ledger.authorize("test", {"employee": self.target}, 10)
        ledger.acquire("one")
        now = int(time.time() * 1000)
        ledger.reserve_turn("one", 0, "hello", now, time.time() + 30)
        records = [row(self.target)]
        class Aws:
            async def logs(self, start, end): return records + records
        sweep = EvidenceSweep(self.target, Aws(), ledger, asyncio.Event())
        item = sweep.add("one", 0, "hello", now, time.monotonic() + 30)
        await sweep.sweep()
        self.assertEqual(item.future.result()["system_text_message"], "answer")
        self.assertEqual(ledger.db.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 1)
        ledger.reserve_turn("one", 1, "missing", now, time.time() - 1)
        missing = sweep.add("one", 1, "missing", now, time.monotonic() - 0.1)
        await sweep.sweep()
        with self.assertRaisesRegex(RunnerError, "answer_evidence_timeout_no_retry"):
            missing.future.result()
        records[:] = [row(self.target, "missing", user_message_id="late")]
        await sweep.sweep()
        self.assertEqual(ledger.db.execute("SELECT status FROM quick_batch_submissions WHERE turn=1").fetchone()[0], "timed_out")
        self.assertEqual(ledger.db.execute("SELECT COUNT(*) FROM quick_late_evidence").fetchone()[0], 1)

    async def test_throttled_sweeps_retry_reads_only_and_checkpoint_each_case(self):
        state, aws, browser = self.fake_runtime(read_failures=2)
        checkpoints = []
        runner = QuickBatchRunner({"employee": self.target}, authorization="test", max_calls=10,
                                 aws_factory=aws, browser=browser, sweep_seconds=0.005,
                                 on_result=lambda result: checkpoints.append(json.loads(json.dumps(result))))
        report = await runner.run([BatchCase(str(i), "employee", (f"prompt-{i}",)) for i in range(3)])
        self.assertEqual(report["status"], "capture_complete")
        self.assertEqual(state["reads"], 3)
        self.assertEqual(state["embeds"], 3)
        self.assertEqual(len(state["sends"]), 3)
        self.assertEqual(len(checkpoints), 3)
        self.assertEqual(report["sweeps"]["employee"]["read_retries"], 2)

    async def test_failed_evidence_checkpoint_stops_new_sends_and_closes_all_contexts(self):
        state, aws, browser = self.fake_runtime()
        runner = QuickBatchRunner({"employee": self.target}, authorization="test", max_calls=20,
            aws_factory=aws, browser=browser, sweep_seconds=0.005, concurrency=1,
            on_result=Mock(side_effect=OSError("synthetic disk full")))
        with self.assertRaisesRegex(RunnerError, "evidence_checkpoint_failed"):
            await runner.run([BatchCase(str(i), "employee", (f"prompt-{i}",)) for i in range(10)])
        self.assertEqual(len(state["sends"]), 1)
        self.assertTrue(state["closed"])
        self.assertTrue(all(c["closed"] for c in state["contexts"]))

    async def test_persistent_browser_closes_contexts_and_recycles_only_when_idle(self):
        pool = PersistentBrowser(recycle_after=2)
        contexts, browsers = [], []
        async def launch():
            if pool.browser is None:
                browser = Mock()
                browser.close = AsyncMock()
                async def context(**kwargs):
                    c = Mock()
                    c.close, c.route, c.route_web_socket = AsyncMock(), AsyncMock(), AsyncMock()
                    c.new_page = AsyncMock(return_value=Mock())
                    c.cookies = {}
                    contexts.append(c)
                    return c
                browser.new_context = context
                browsers.append(browser)
                pool.browser, pool.opened = browser, 0
                pool.launches += 1
        with patch.object(pool, "_launch", side_effect=launch):
            for _ in range(3):
                async with pool.context(self.target, time.monotonic() + 30):
                    self.assertEqual(pool.active, 1)
                self.assertEqual(pool.active, 0)
            await pool.close()
        self.assertEqual(len(contexts), 3)
        self.assertEqual(len(browsers), 2)
        self.assertTrue(all(c.close.await_count == 1 for c in contexts))
        self.assertTrue(all(b.close.await_count == 1 for b in browsers))


if __name__ == "__main__":
    unittest.main()
