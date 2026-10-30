"""Startup and cleanup faults use safe labels without replaying a request."""
import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from harness.amazon_quick_batch import BatchCase, QuickBatchRunner
from tests import test_amazon_quick_batch as fixtures


SECRET = "private https://example.test/embedding/session?token=do-not-export"


class RuntimeDiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        env = patch.dict(os.environ, {"ARE_AMAZON_QUICK_STATE_DIR": self.temp.name})
        env.start()
        self.addCleanup(env.stop)
        self.target = fixtures.target()

    def fault_runtime(self, stage, exception, *, cleanup_exception=None, checkpoint=None):
        state, base_aws, base_browser = fixtures.AsyncStateTest.fake_runtime(self)

        class Aws(base_aws):
            async def embed_url(self):
                if stage == "embedding_url":
                    state["embeds"] += 1
                    raise exception
                return await super().embed_url()

        class Browser:
            launches = 1

            @asynccontextmanager
            async def context(self, t, deadline):
                async with base_browser.context(t, deadline) as chat:
                    if stage == "browser_context_enter":
                        raise exception
                    method = {"sdk_open": "open", "prepare": "prepare",
                              "send": "send", "capture": "capture"}.get(stage)
                    if method:
                        original = getattr(chat, method)

                        async def fault(*args):
                            if stage == "send":
                                await original(*args)  # Request may already have left.
                            raise exception
                        setattr(chat, method, fault)
                    try:
                        yield chat
                    finally:
                        if cleanup_exception is not None:
                            raise cleanup_exception
                        if stage == "browser_context_exit":
                            raise exception

            async def close(self):
                await base_browser.close()

        runner = QuickBatchRunner({"employee": self.target}, authorization="test", max_calls=30,
                                  aws_factory=Aws, browser=Browser(), sweep_seconds=0.005,
                                  on_result=checkpoint)
        return state, runner

    def counters(self):
        db = sqlite3.connect(Path(self.temp.name) / "amazon-quick.sqlite3")
        try:
            return {"pilot": db.execute("SELECT calls,halted,inflight FROM pilots").fetchone(),
                    "authorization": db.execute("SELECT calls,halted FROM quick_batch_authorizations").fetchone(),
                    "intents": db.execute("SELECT count(*) FROM quick_batch_send_intents").fetchone()[0],
                    "submissions": db.execute("SELECT status FROM quick_batch_submissions").fetchall(),
                    "leases": db.execute("SELECT count(*) FROM quick_conversation_leases").fetchone()[0]}
        finally:
            db.close()

    async def check_fault(self, stage, exception, *, cleanup_exception=None):
        state, runner = self.fault_runtime(stage, exception, cleanup_exception=cleanup_exception)
        report = await runner.run([BatchCase("one", "employee", ("one prompt",))])
        self.assertEqual(report["status"], "incomplete")
        self.assertNotIn(SECRET, json.dumps(report))
        self.assertEqual(len(state["contexts"]), 1)
        self.assertTrue(state["closed"])
        self.assertTrue(all(c["closed"] for c in state["contexts"]))
        self.assertEqual(self.counters()["leases"], 0)
        return state, report["results"][0]

    async def test_context_entry_failure_charges_nothing_and_preserves_halt(self):
        state, result = await self.check_fault("browser_context_enter", AttributeError(SECRET))
        self.assertEqual(result["runtime_failure"]["stage"], "browser_context_enter")
        self.assertEqual(result["runtime_failure"]["exception_type"], "AttributeError")
        self.assertEqual(state["embeds"], 0)
        self.assertEqual(state["sends"], [])
        self.assertEqual(self.counters()["authorization"], (0, 1))

    async def test_embedding_failure_is_not_retried_and_keeps_reservation(self):
        state, result = await self.check_fault("embedding_url", KeyError(SECRET))
        self.assertEqual(result["runtime_failure"]["stage"], "embedding_url")
        self.assertEqual(result["runtime_failure"]["exception_type"], "KeyError")
        self.assertEqual(state["embeds"], 1)
        self.assertEqual(state["sends"], [])
        self.assertEqual(self.counters()["pilot"], (1, 1, 1))
        self.assertEqual(self.counters()["intents"], 0)
        self.assertEqual(self.counters()["submissions"], [("unknown",)])

    async def test_generic_open_failure_is_not_an_initialization_retry(self):
        state, result = await self.check_fault("sdk_open", TypeError(SECRET))
        self.assertEqual(result["runtime_failure"]["stage"], "sdk_open")
        self.assertEqual(result["runtime_failure"]["initialization_attempt"], 1)
        self.assertEqual(state["embeds"], 1)
        self.assertEqual(state["sends"], [])
        self.assertEqual(self.counters()["pilot"], (1, 1, 1))

    async def test_cleanup_cannot_replace_first_open_failure_metadata(self):
        _, result = await self.check_fault("sdk_open", KeyError(SECRET),
                                          cleanup_exception=OSError("second private cleanup error"))
        self.assertEqual(result["runtime_failure"]["stage"], "sdk_open")
        self.assertEqual(result["runtime_failure"]["exception_type"], "KeyError")
        self.assertEqual(result["reason"], "batch_case_runtime_failed")

    async def test_prepare_failure_does_not_create_a_send_intent(self):
        state, result = await self.check_fault("prepare", ValueError(SECRET))
        self.assertEqual(result["runtime_failure"]["stage"], "prepare")
        self.assertEqual(state["sends"], [])
        self.assertEqual(self.counters()["intents"], 0)
        self.assertEqual(self.counters()["pilot"], (1, 1, 1))

    async def test_after_intent_send_failure_is_never_retried_or_refunded(self):
        state, result = await self.check_fault("send", TypeError(SECRET))
        self.assertEqual(result["runtime_failure"]["stage"], "send")
        self.assertEqual(len(state["sends"]), 1)
        self.assertEqual(result["attempted_turns"], 1)
        self.assertEqual(self.counters()["intents"], 1)
        self.assertEqual(self.counters()["pilot"], (1, 1, 1))
        self.assertEqual(self.counters()["submissions"], [("unknown",)])

    async def test_context_exit_fault_is_distinct_and_does_not_resend_accepted_answer(self):
        state, result = await self.check_fault("browser_context_exit", OSError(SECRET))
        self.assertEqual(result["runtime_failure"]["stage"], "browser_context_exit")
        self.assertEqual(len(state["sends"]), 1)
        self.assertEqual(self.counters()["pilot"], (1, 1, 0))
        self.assertEqual(self.counters()["submissions"], [("verified",)])

    async def test_cancellation_metadata_reaches_checkpoint_and_cancellation_still_propagates(self):
        saved = []
        state, runner = self.fault_runtime("sdk_open", asyncio.CancelledError(SECRET), checkpoint=saved.append)
        with self.assertRaises(asyncio.CancelledError):
            await runner.run([BatchCase("one", "employee", ("one prompt",))])
        self.assertEqual(saved[0]["runtime_failure"]["exception_type"], "CancelledError")
        self.assertEqual(saved[0]["runtime_failure"]["stage"], "sdk_open")
        self.assertNotIn(SECRET, json.dumps(saved))
        self.assertEqual(state["sends"], [])
        self.assertEqual(self.counters()["pilot"], (1, 1, 1))


if __name__ == "__main__":
    unittest.main()
