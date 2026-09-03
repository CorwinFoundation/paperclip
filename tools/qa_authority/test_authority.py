import sqlite3
import tempfile
import unittest

from tools.qa_authority.authority import bind, connect, evaluate, record_disposition
from tools.qa_authority.paperclip_reader import PaperclipReader

SHA = "a" * 40
QA = "11111111-1111-4111-8111-111111111111"


class AuthorityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile()
        self.db = connect(self.tmp.name)
        bind(self.db, repository_id=7, pr_number=299, implementation_issue_id="impl", qa_issue_id=QA, created_by="cto")

    def event(self, **overrides):
        value = dict(qa_issue_id=QA, outcome="APPROVED", candidate_sha=SHA,
                     evidence_refs=["paperclip://evidence/1"], author_identity_key="backbond-qa",
                     author_agent_id_snapshot="qa-agent", created_by_run_id="run-1",
                     paperclip_role="qa", paperclip_status="active")
        value.update(overrides)
        return value

    def corroboration(self, **overrides):
        value = dict(valid=True, qa_issue_id=QA, qa_issue_status="done", disposition_id=1)
        value.update(overrides)
        return value

    def test_exact_current_approval_succeeds(self):
        record_disposition(self.db, self.event())
        self.assertEqual(evaluate(self.db, repository_id=7, pr_number=299, head_sha=SHA,
                                  corroboration=self.corroboration()).conclusion, "success")

    def test_wrong_sha_and_reader_failure_fail_closed(self):
        record_disposition(self.db, self.event())
        self.assertEqual(evaluate(self.db, repository_id=7, pr_number=299, head_sha="b" * 40,
                                  corroboration=self.corroboration()).conclusion, "failure")
        self.assertEqual(evaluate(self.db, repository_id=7, pr_number=299, head_sha=SHA,
                                  corroboration=None).conclusion, "failure")

    def test_later_changes_requested_supersedes(self):
        first = record_disposition(self.db, self.event())
        record_disposition(self.db, self.event(outcome="CHANGES_REQUESTED", supersedes_disposition_id=first))
        self.assertEqual(evaluate(self.db, repository_id=7, pr_number=299, head_sha=SHA,
                                  corroboration=self.corroboration(disposition_id=2)).conclusion, "failure")

    def test_duplicate_active_binding_rejected_by_database(self):
        with self.assertRaises(sqlite3.IntegrityError):
            bind(self.db, repository_id=7, pr_number=299, implementation_issue_id="x", qa_issue_id="y", created_by="cto")

    def test_dispositions_are_append_only_at_database_boundary(self):
        disposition_id = record_disposition(self.db, self.event(outcome="CHANGES_REQUESTED"))
        with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
            self.db.execute("UPDATE dispositions SET outcome='APPROVED' WHERE id=?", (disposition_id,))
        with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
            self.db.execute("DELETE FROM dispositions WHERE id=?", (disposition_id,))

    def test_stale_status_identity_binding_and_sha_shape_fail_closed(self):
        record_disposition(self.db, self.event())
        cases = [
            self.corroboration(disposition_id=999),
            self.corroboration(qa_issue_status="in_review"),
        ]
        for corroboration in cases:
            with self.subTest(corroboration=corroboration):
                self.assertEqual(evaluate(self.db, repository_id=7, pr_number=299, head_sha=SHA,
                                          corroboration=corroboration).conclusion, "failure")
        self.assertEqual(evaluate(self.db, repository_id=7, pr_number=299, head_sha="short",
                                  corroboration=self.corroboration()).conclusion, "failure")
        self.db.execute("UPDATE bindings SET active=0")
        self.assertEqual(evaluate(self.db, repository_id=7, pr_number=299, head_sha=SHA,
                                  corroboration=self.corroboration()).conclusion, "failure")

    def test_disposition_rejects_wrong_identity_role_and_sha(self):
        for changes in ({"author_identity_key": "attacker"}, {"paperclip_role": "engineer"},
                        {"candidate_sha": "short"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                record_disposition(self.db, self.event(**changes))

    def test_reader_rejects_non_loopback_and_unlisted_path(self):
        with self.assertRaises(ValueError):
            PaperclipReader("https://paperclip.example.com", "secret")
        reader = PaperclipReader("http://127.0.0.1:3100", "secret")
        with self.assertRaises(ValueError):
            reader.get(f"/api/issues/{QA}/comments/1")


if __name__ == "__main__":
    unittest.main()
