"""Offline protocol/security tests. No AWS calls or real customer sessions."""
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
import io
from contextlib import redirect_stdout
from unittest.mock import Mock, patch

from harness import amazon_quick as quick
from harness.customer_runner import customer_agent_http_runner_factory
from harness.runner import RunnerError
from api.scan_request_validation import validate_agent_endpoint

TOKEN = "TEST-ONLY-PILOT-TOKEN-" + "x" * 32
URL = "https://pilot.example.test/quick/index.html"


def target_row():
    return {"approved": True, "endpoint_url": URL, "account_id": "111122223333",
            "region": "us-east-1", "agent_id": "agent-123", "pilot_id": "pilot-one",
            "user_arn": "arn:aws:quicksight:us-east-1:111122223333:user/default/pilot",
            "log_group": "/pilot/test", "token_sha256": quick._sha(TOKEN),
            "expires_at": "2099-01-01T00:00:00Z", "max_calls": 20,
            "allowed_browser_hosts": ["pilot.example.test", "us-east-1.quicksight.aws.amazon.com"]}


def event(prompt="hello", **changes):
    row = target_row()
    return {"logType": "CHAT_LOGS", "accountId": row["account_id"], "agent_id": row["agent_id"],
            "user_arn": row["user_arn"], "user_message": prompt, "system_text_message": "answer",
            "status_code": "success", "conversation_id": "conversation-one", "user_message_id": "message-one",
            "system_message_id": "answer-one", "event_timestamp": 100, **changes}


class CorrelationTests(unittest.TestCase):
    def setUp(self):
        self.target = quick.Target.parse(target_row())

    def test_aws_agent_id_characters_and_length_keep_exact_native_correlation(self):
        for agent_id in ("agent.with+symbols_123-", "a" * 256):
            target = quick.Target.parse({**target_row(), "agent_id": agent_id})
            native = event(agent_id=agent_id)
            self.assertEqual(quick.correlated_answer([native], target, "hello", 100, 200), native)
            self.assertIsNone(quick.correlated_answer([event()], target, "hello", 100, 200))
        for agent_id in ("a" * 257, "agent/other", "agent\n", "agent'quoted", "agent with space", "агент"):
            with self.subTest(agent_id=agent_id), self.assertRaises(RunnerError):
                quick.Target.parse({**target_row(), "agent_id": agent_id})

    def test_only_exact_target_identity_prompt_and_time_are_accepted(self):
        valid = event()
        distractors = [event(**changes) for changes in (
            {"accountId": "999999999999"}, {"agent_id": "other"}, {"user_arn": "other"},
            {"user_message": "hello "}, {"event_timestamp": 99}, {"event_timestamp": 201},
            {"event_timestamp": True}, {"event_timestamp": float("nan")}, {"logType": "OTHER"},
        )]
        self.assertIsNone(quick.correlated_answer(distractors, self.target, "hello", 100, 200))
        self.assertEqual(quick.correlated_answer(distractors + [valid, dict(valid)], self.target, "hello", 100, 200), valid)

    def test_ambiguity_conflicting_delivery_and_changed_conversation_fail_closed(self):
        for rows, conversation in (([event(), event(user_message_id="different")], None),
                                   ([event(), event(system_text_message="different")], None),
                                   ([event()], "other")):
            with self.subTest(rows=rows), self.assertRaises(RunnerError):
                quick.correlated_answer(rows, self.target, "hello", 100, 200, conversation)

    def test_documented_native_outcomes_keep_real_correlated_text(self):
        for status in ("success", "request_blocked", "no_answer_found"):
            with self.subTest(status=status):
                native = event(status_code=status, system_text_message="Actual provider response")
                self.assertEqual(quick.correlated_answer([native], self.target, "hello", 100, 200), native)
                self.assertEqual(quick.knowledge_evidence(native, self.target)["native_status_code"], status)

    def test_unknown_status_missing_identifiers_empty_and_large_answers_fail_closed(self):
        for changes in ({"status_code": "error"}, {"status_code": "pending"}, {"status_code": None},
                        {"status_code": {"success": True}},
                        {"system_text_message": " "}, {"system_text_message": "x" * 65537},
                        {"system_message_id": ""}, {"conversation_id": None}):
            with self.subTest(changes=changes), self.assertRaises(RunnerError):
                quick.correlated_answer([event(**changes)], self.target, "hello", 100, 200)
        for status in ("request_blocked", "no_answer_found"):
            for changes in ({"system_text_message": ""}, {"system_message_id": ""}):
                with self.subTest(status=status, changes=changes), self.assertRaises(RunnerError):
                    quick.correlated_answer([event(status_code=status, **changes)], self.target, "hello", 100, 200)

    def test_assessment_opt_in_preserves_literal_empty_text_without_relaxing_default(self):
        for status in ("success", "request_blocked", "no_answer_found"):
            for text in ("", " \n"):
                with self.subTest(status=status, text=text):
                    native = event(status_code=status, system_text_message=text)
                    with self.assertRaises(RunnerError):
                        quick.correlated_answer([native], self.target, "hello", 100, 200)
                    self.assertEqual(quick.correlated_answer([native], self.target, "hello", 100, 200,
                                     allow_empty_response=True), native)
                    evidence = quick.knowledge_evidence(native, self.target)
                    self.assertEqual(evidence["native_text_state"], "empty")
                    self.assertEqual(evidence["response_sha256"], quick._sha(text))

    def test_empty_opt_in_does_not_accept_missing_oversized_or_conflicting_evidence(self):
        for changes in ({"system_text_message": None}, {"system_text_message": []},
                        {"system_text_message": "x" * 65537}, {"system_message_id": ""},
                        {"status_code": "pending"}):
            with self.subTest(changes=list(changes)), self.assertRaises(RunnerError):
                quick.correlated_answer([event(**changes)], self.target, "hello", 100, 200,
                                        allow_empty_response=True)
        with self.assertRaisesRegex(RunnerError, "conflicting_chat_evidence"):
            quick.correlated_answer([event(system_text_message=""), event()], self.target,
                                    "hello", 100, 200, allow_empty_response=True)

    def test_sdk_host_contract_runs_under_node(self):
        test = Path(__file__).resolve().parents[1] / "tools/amazon-quick-pilot/pilot.test.cjs"
        result = subprocess.run(["node", str(test)], capture_output=True, timeout=20, check=False)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))


class PilotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.registry = Path(self.tmp.name) / "targets.json"
        self.row = target_row()
        self.write_registry()
        self.env = patch.dict(os.environ, {"ARE_AMAZON_QUICK_ENABLED": "1",
            "ARE_AMAZON_QUICK_TARGETS_FILE": str(self.registry), "ARE_AMAZON_QUICK_STATE_DIR": self.tmp.name})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.addCleanup(self.tmp.cleanup)
        self.target = quick.Target.parse(self.row)

    def write_registry(self):
        self.registry.write_text(json.dumps({"schema": quick.SCHEMA, "targets": [self.row]}), encoding="utf-8")

    def runner(self, *, target=None, error=None, fixed_conversation=None, logs=None,
               knowledge=False, metadata=None):
        state = {"cases": 0, "sends": [], "closed": 0, "current": "", "number": 0}
        class Browser:
            def __init__(self, target):
                pass
            def open(self, url):
                state["cases"] += 1
            def send(self, prompt):
                state["sends"].append(prompt)
                state["current"] = prompt
                state["number"] += 1
                if error:
                    raise error
            def close(self):
                state["closed"] += 1
        class Aws:
            def embed_url(self):
                return "in-memory-test-url"
            def logs(self, start, end):
                if logs is not None:
                    return logs
                return [event(state["current"], event_timestamp=end,
                              conversation_id=fixed_conversation or f"conversation-{state['cases']}",
                              user_message_id=f"message-{state['number']}",
                              system_text_message="reply:" + state["current"], **(metadata or {}))]
        runner = quick.AmazonQuickRunner(URL, TOKEN, {"auth": {"type": "bearer"}, "_include_result_rows": True,
                                                    "_include_knowledge_evidence": knowledge},
                                        target=target or self.target, aws=Aws(), browser_factory=Browser)
        self.addCleanup(runner.close)
        return runner, state

    def test_opt_in_knowledge_metadata_is_correlated_copied_and_cleared_for_each_case(self):
        metadata = {"cited_resource": [{"citedResourceId": "source-one", "citedResourceName": "leave.txt"}],
                    "user_selected_resources": [{"resourceId": "space-one", "resourceType": "space"}],
                    "action_connectors": [{"actionConnectorId": "available-not-invoked"}]}
        runner, _ = self.runner(knowledge=True, metadata=metadata)
        runner.complete("first")
        receipt = runner.knowledge_evidence_receipts()[0]
        self.assertEqual(receipt["prompt_sha256"], quick._sha("first"))
        self.assertEqual(receipt["user_arn_sha256"], quick._sha(self.target.user_arn))
        self.assertTrue(receipt["metadata_complete"])
        receipt["cited_resource"][0]["citedResourceId"] = "mutated"
        self.assertEqual(runner.knowledge_evidence_receipts()[0]["cited_resource"][0]["citedResourceId"], "source-one")
        runner.complete("second")
        self.assertEqual(len(runner.knowledge_evidence_receipts()), 1)
        self.assertEqual(runner.knowledge_evidence_receipts()[0]["prompt_sha256"], quick._sha("second"))

    def test_serial_blocked_response_retains_text_status_and_charge_without_retry(self):
        runner, state = self.runner(knowledge=True, metadata={"status_code": "request_blocked"})
        self.assertEqual(runner.complete("blocked-question"), "reply:blocked-question")
        self.assertEqual(state["sends"], ["blocked-question"])
        self.assertEqual(runner.evidence_receipts()[0]["native_status_code"], "request_blocked")
        self.assertEqual(runner.knowledge_evidence_receipts()[0]["native_status_code"], "request_blocked")
        self.assertEqual(runner.ledger.db.execute("SELECT calls,halted,inflight FROM pilots").fetchone(), (1, 0, 0))
        runner.aws.logs = Mock(side_effect=RunnerError("synthetic failure"))
        with self.assertRaises(RunnerError):
            runner.complete("third")
        self.assertEqual(runner.knowledge_evidence_receipts(), ())

    def test_registry_gate_token_expiry_approval_and_exact_endpoint(self):
        self.assertEqual(quick.load_target(URL, TOKEN), self.target)
        for change in ({"approved": False}, {"expires_at": "2000-01-01T00:00:00Z"}, {"token_sha256": "0" * 64}):
            original = dict(self.row)
            self.row.update(change)
            self.write_registry()
            with self.assertRaises(RunnerError):
                quick.load_target(URL, TOKEN)
            self.row = original
        self.write_registry()
        with self.assertRaises(RunnerError):
            quick.load_target(URL + "/other", TOKEN)
        with patch.dict(os.environ, {"ARE_AMAZON_QUICK_ENABLED": "0"}), self.assertRaises(RunnerError):
            quick.load_target(URL, TOKEN)

    def test_malformed_configuration_rejected_without_network(self):
        for changes in ({"allowed_browser_hosts": ["*.example.test"]}, {"endpoint_url": "https://127.0.0.1/"},
                        {"region": "us-east-1 --debug"}, {"max_calls": True}, {"timeout_seconds": 0},
                        {"user_arn": "arn:aws:quicksight:us-west-2:111122223333:user/default/pilot"}):
            with self.subTest(changes=changes), self.assertRaises(RunnerError):
                quick.Target.parse({**self.row, **changes})
        for auth in ("none", "custom_header"):
            with self.assertRaises(RunnerError):
                quick.validate_endpoint(URL, auth)
        with self.assertRaises(RunnerError):
            quick.validate_endpoint(URL, "bearer", {"system_prompt": "override"})

    def test_real_factory_and_api_validation_preserve_transport_and_secure_auth(self):
        config = validate_agent_endpoint({"url": URL, "transport": quick.TRANSPORT, "auth": {"type": "bearer"}})
        runner = customer_agent_http_runner_factory(URL, TOKEN, endpoint_config=config)
        self.addCleanup(runner.close)
        self.assertIsInstance(runner, quick.AmazonQuickRunner)
        for change in ({"auth": {"type": "none"}}, {"system_prompt": "override"}, {"url": URL + "?token=secret"}):
            with self.assertRaises(Exception):
                validate_agent_endpoint({**config, **change})
        with patch.dict(os.environ, {"ARE_AMAZON_QUICK_ENABLED": "0"}), self.assertRaises(RunnerError):
            customer_agent_http_runner_factory(URL, TOKEN, endpoint_config=config)

    def test_fresh_cases_multiturn_and_exact_prompts_have_correlated_receipts(self):
        runner, state = self.runner()
        prompt = 'Quote "hello"\nשלום {{prompt}}'
        self.assertEqual(runner.complete(prompt), "reply:" + prompt)
        runner.record_starter_verdict(test_id="case-1", family="instructions", verdict="passed")
        self.assertEqual(runner.complete_conversation(("first", "second")), ("reply:first", "reply:second"))
        self.assertEqual(state["sends"], [prompt, "first", "second"])
        self.assertEqual(state["cases"], 2)
        self.assertEqual(state["closed"], 2)
        receipts = runner.evidence_receipts()
        self.assertEqual([r["prompt_sha256"] for r in receipts], [quick._sha(p) for p in (prompt, "first", "second")])
        self.assertNotEqual(receipts[0]["conversation_sha256"], receipts[1]["conversation_sha256"])
        self.assertEqual(receipts[1]["conversation_sha256"], receipts[2]["conversation_sha256"])
        self.assertEqual(runner.starter_result_rows()[0]["reply"], "reply:" + prompt)

    def test_reused_conversation_halts_pilot_and_next_call_never_sends(self):
        runner, state = self.runner(fixed_conversation="stale-conversation")
        runner.complete("first")
        with self.assertRaisesRegex(RunnerError, "conversation_reused"):
            runner.complete("second")
        with self.assertRaisesRegex(RunnerError, "pilot_halted"):
            runner.complete("third")
        self.assertEqual(state["sends"], ["first", "second"])
        runner.record_starter_verdict(test_id="case-3", family="f", verdict="could_not_run")
        self.assertNotIn("reply", runner.starter_result_rows()[-1])

    def test_unknown_submission_consumes_budget_halts_across_restart_and_closes_browser(self):
        runner, state = self.runner(error=RunnerError("amazon quick: prompt_submission_unknown_no_retry"))
        with self.assertRaises(RunnerError):
            runner.complete("first")
        self.assertEqual(state["closed"], 1)
        replacement, other = self.runner()
        with self.assertRaisesRegex(RunnerError, "pilot_halted"):
            replacement.complete("first")
        self.assertEqual(other["sends"], [])
        self.assertEqual(replacement.ledger.db.execute("SELECT calls FROM pilots").fetchone()[0], 1)

    def test_process_interruption_leaves_durable_uncertainty_even_without_exception_handler(self):
        runner, _ = self.runner(error=KeyboardInterrupt())
        with self.assertRaises(KeyboardInterrupt):
            runner.complete("first")
        replacement, state = self.runner()
        with self.assertRaisesRegex(RunnerError, "unresolved_submission"):
            replacement.complete("first")
        self.assertEqual(state["sends"], [])

    def test_durable_budget_is_shared_between_runners(self):
        runner, state = self.runner(target=replace(self.target, max_calls=1))
        runner.complete("first")
        replacement, other = self.runner(target=replace(self.target, max_calls=1))
        with self.assertRaisesRegex(RunnerError, "call_budget_exhausted"):
            replacement.complete("second")
        self.assertEqual(other["sends"], [])

    def test_identity_lease_covers_different_targets_using_same_test_user(self):
        runner, _ = self.runner()
        other = quick.Ledger(replace(self.target, pilot_id="second-pilot", agent_id="other"))
        self.addCleanup(other.close)
        with runner.ledger.case():
            with self.assertRaisesRegex(RunnerError, "test_identity_busy"), other.case():
                self.fail("concurrent identity must not acquire a lease")

    def test_expired_case_and_scan_deadline_prevent_submission(self):
        runner, state = self.runner(target=replace(self.target, expires_at=time.time() - 1))
        with self.assertRaisesRegex(RunnerError, "pilot_expired"):
            runner.complete("first")
        self.assertEqual(state["sends"], [])
        other, state2 = self.runner()
        other.set_scan_deadline(time.monotonic() - 1)
        with self.assertRaisesRegex(RunnerError, "case_deadline"):
            other.complete("first")
        self.assertEqual(state2["sends"], [])
        with self.assertRaisesRegex(RunnerError, "invalid_case_turns"):
            other.complete_conversation("hello")

    def test_no_log_answer_never_returns_partial_or_sdk_ack(self):
        runner, state = self.runner(logs=[])
        clock = [0.0]
        def missing(*args):
            clock[0] = 121.0
            return []
        runner.aws.logs = missing
        with patch.object(quick.time, "monotonic", side_effect=lambda: clock[0]), patch.object(quick.time, "sleep"):
            with self.assertRaisesRegex(RunnerError, "evidence_timeout"):
                runner.complete("first")
        self.assertEqual(state["sends"], ["first"])
        self.assertEqual(runner.evidence_receipts(), ())

    def test_browser_network_blocks_unregistered_hosts_private_dns_and_plaintext(self):
        browser = quick.BrowserChat(self.target)
        with patch.object(quick.socket, "getaddrinfo", return_value=[(2, 1, 6, "", ("8.8.8.8", 443))]):
            self.assertTrue(browser.allowed(URL))
            self.assertFalse(browser.allowed("https://attacker.example/path"))
            self.assertFalse(browser.allowed(URL.replace("https", "http")))
        with patch.object(quick.socket, "getaddrinfo", return_value=[(2, 1, 6, "", ("169.254.169.254", 443))]):
            self.assertFalse(browser.allowed(URL))

    def test_aws_cli_is_bounded_no_shell_no_retry_and_sanitizes_errors(self):
        aws = quick.AwsCli(self.target)
        secret = "PRIVATE_AWS_STDERR"
        with patch.object(subprocess, "run", return_value=subprocess.CompletedProcess([], 1, b"", secret.encode())) as run:
            with self.assertRaises(RunnerError) as error:
                aws.call("logs", "filter-log-events", {})
            self.assertNotIn(secret, str(error.exception))
            self.assertIn("aws_chat_logs_command_failed", str(error.exception))
            self.assertEqual(run.call_args.kwargs["env"]["AWS_MAX_ATTEMPTS"], "1")
            self.assertNotIn("shell", run.call_args.kwargs)
        with patch.object(aws, "call", return_value={"EmbedUrl": "https://attacker.example/embedding/id/quick/chat?code=SECRET"}):
            with self.assertRaises(RunnerError) as error:
                aws.embed_url()
            self.assertNotIn("SECRET", str(error.exception))

    def test_aws_cli_reports_only_fixed_phase_and_error_categories(self):
        aws = quick.AwsCli(self.target)
        secret = "https://example.test/?credential=PRIVATE_SECRET"
        for code, category in [("AccessDeniedException", "access_denied"),
                               ("ThrottlingException", "throttled"),
                               ("ExpiredToken", "credentials_expired"),
                               ("PRIVATE_SECRET", "command_failed")]:
            stderr = f"An error occurred ({code}) when calling the GenerateEmbedUrlForRegisteredUser operation: {secret}"
            with self.subTest(code=code), patch.object(subprocess, "run", return_value=subprocess.CompletedProcess([], 1, b"", stderr.encode())):
                with self.assertRaises(RunnerError) as error:
                    aws.call("quicksight", "generate-embed-url-for-registered-user", {})
                self.assertEqual(str(error.exception), "amazon quick: aws_embed_" + category)
                self.assertNotIn(secret, str(error.exception))
        for failure, suffix in [(subprocess.TimeoutExpired("aws", 20, stderr=secret), "timeout"),
                                (FileNotFoundError(secret), "unavailable")]:
            with self.subTest(suffix=suffix), patch.object(subprocess, "run", side_effect=failure):
                with self.assertRaises(RunnerError) as error:
                    aws.call("logs", "filter-log-events", {})
                self.assertEqual(str(error.exception), "amazon quick: aws_chat_logs_" + suffix)
                self.assertNotIn(secret, str(error.exception))
        with patch.object(subprocess, "run", return_value=subprocess.CompletedProcess([], 0, secret.encode(), b"")):
            with self.assertRaisesRegex(RunnerError, "aws_chat_logs_invalid_response"):
                aws.call("logs", "filter-log-events", {})

    def test_transient_evidence_reads_retry_without_resending_or_new_embedding(self):
        runner, state = self.runner()
        original_logs = runner.aws.logs
        attempts = []
        def logs(start, end):
            attempts.append((start, end))
            if len(attempts) <= 2:
                raise RunnerError("amazon quick: aws_chat_logs_timeout")
            return original_logs(start, end)
        with patch.object(runner.aws, "logs", side_effect=logs), patch.object(runner.aws, "embed_url", wraps=runner.aws.embed_url) as embed, patch.object(quick.time, "sleep"):
            self.assertEqual(runner.complete("one prompt"), "reply:one prompt")
        self.assertEqual(state["sends"], ["one prompt"])
        self.assertEqual(embed.call_count, 1)
        self.assertEqual(len(attempts), 3)
        self.assertEqual(len({start for start, _ in attempts}), 1)
        self.assertEqual(runner.evidence_receipts()[0]["log_read_retries"], 2)

    def test_evidence_read_retry_limit_and_nontransient_errors_still_halt(self):
        for suffix, expected_reads in [("throttled", 3), ("access_denied", 1), ("invalid_response", 1)]:
            with self.subTest(suffix=suffix):
                isolated = replace(self.target, pilot_id="failure-" + suffix,
                                   user_arn=self.target.user_arn + "-" + suffix)
                runner, state = self.runner(target=isolated)
                with patch.object(runner.aws, "logs", side_effect=RunnerError("amazon quick: aws_chat_logs_" + suffix)) as logs, patch.object(quick.time, "sleep"):
                    with self.assertRaisesRegex(RunnerError, "aws_chat_logs_" + suffix):
                        runner.complete("one prompt")
                    self.assertEqual(logs.call_count, expected_reads)
                    with self.assertRaisesRegex(RunnerError, "halted"):
                        runner.complete("must not resend")
                self.assertEqual(state["sends"], ["one prompt"])
                self.assertEqual(runner.evidence_receipts(), ())

    def test_log_pagination_reads_all_pages_and_incomplete_search_fails(self):
        aws = quick.AwsCli(self.target)
        pages = [{"events": [], "nextToken": "two"}, {"events": [{"message": json.dumps(event())}]}]
        with patch.object(aws, "call", side_effect=pages) as call:
            self.assertEqual(aws.logs(100, 200), [event()])
            self.assertEqual(call.call_count, 2)
        with patch.object(aws, "call", return_value={"events": [], "nextToken": "loop"}), self.assertRaises(RunnerError):
            aws.logs(100, 200)

    def test_repeated_prompt_within_a_case_does_not_reuse_previous_message(self):
        runner, state = self.runner()
        previous = []
        original = runner.aws.logs
        def overlapping(start, end):
            current = original(start, end)
            # Delivery can contain earlier turns when timestamps coincide.
            results = previous[:] + current
            previous.extend(current)
            return results
        runner.aws.logs = overlapping
        self.assertEqual(runner.complete_conversation(("same", "same")), ("reply:same", "reply:same"))
        self.assertEqual(len(runner.evidence_receipts()), 2)

    def test_registry_revocation_between_turns_stops_before_another_send(self):
        runner = quick.AmazonQuickRunner(URL, TOKEN)
        self.addCleanup(runner.close)
        # Real registration gate; fake only the paid I/O providers.
        fake, state = self.runner()
        runner.aws, runner.browser_factory = fake.aws, fake.browser_factory
        original = runner.aws.logs
        def revoke(*args):
            result = original(*args)
            self.row["approved"] = False
            self.write_registry()
            return result
        runner.aws.logs = revoke
        with self.assertRaises(RunnerError):
            runner.complete_conversation(("first", "second"))
        self.assertEqual(state["sends"], ["first"])

    def test_readiness_configuration_mode_never_calls_aws_and_refuses_overwrite(self):
        from tools.amazon_quick_pilot import main
        receipt = Path(self.tmp.name) / "receipt.json"
        output = io.StringIO()
        with patch.dict(os.environ, {"AMAZON_QUICK_PILOT_TOKEN": TOKEN}), patch.object(quick.AwsCli, "call", side_effect=AssertionError("AWS must not run")), redirect_stdout(output):
            self.assertEqual(main(["--endpoint", URL, "--receipt", str(receipt)]), 0)
            self.assertEqual(main(["--endpoint", URL, "--receipt", str(receipt)]), 2)
        data = json.loads(receipt.read_text())
        self.assertEqual(data["status"], "configuration_validated_only")
        self.assertEqual(data["completed"], 0)
        self.assertNotIn(TOKEN, receipt.read_text() + output.getvalue())

    @unittest.skipIf(os.name == "nt", "Production handler imports require Linux fcntl; exercised in Linux CI")
    def test_production_connection_check_routes_through_quick_runner(self):
        from api.scan_handlers import connectivity_check_handler
        runner, state = self.runner()
        factory = Mock(return_value=runner)
        result = connectivity_check_handler({"agent_endpoint": {"url": URL, "transport": quick.TRANSPORT,
                                             "auth": {"type": "bearer"}}, "credential_secret": TOKEN}, runner_factory=factory)
        self.assertTrue(result[1]["success"])
        self.assertEqual(len(state["sends"]), 1)


if __name__ == "__main__":
    unittest.main()
