from pathlib import Path
import json
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from handoff_capsule_sweep import (
    actionable_status,
    discover_pairs,
    desired_route,
    find_capsule,
    marked_pair,
    product_pair,
    retained_blocker_ids,
    routed_status,
    verdict_from_comments,
)
from handoff_capsule import build_capsule


class FakeClient:
    company_id = "company"

    def __init__(self, issues, products=None):
        self.issues = issues
        self.products = products or {}

    def request(self, method, path, body=None):
        del body
        if method == "GET" and path.startswith("/api/companies/company/issues?"):
            return self.issues
        if method == "GET" and path.endswith("/work-products"):
            identifier = path.split("/")[3]
            return self.products.get(identifier, [])
        raise AssertionError((method, path))


class FakeDownloadClient:
    company_id = "company"

    def __init__(self, downloads):
        self.downloads = downloads

    def download(self, path, destination):
        shutil.copyfile(self.downloads[path], destination)


class HandoffCapsuleSweepTests(unittest.TestCase):
    def test_actionable_route_preserves_active_work(self) -> None:
        self.assertEqual(actionable_status("in_progress"), "in_progress")
        self.assertEqual(actionable_status("blocked"), "todo")

    def test_unrelated_blockers_survive_pair_routing(self) -> None:
        issue = {"blockedBy": [{"id": "qa"}, {"id": "helper"}]}
        self.assertEqual(retained_blocker_ids(issue, "qa"), ["helper"])
        self.assertEqual(routed_status("todo", ["helper"]), "blocked")
        self.assertEqual(routed_status("todo", []), "todo")

    def test_missing_capsule_routes_to_producer(self) -> None:
        self.assertEqual(desired_route(capsule_id=None, verdict="pending"), "producer_work")

    def test_valid_capsule_routes_to_qa(self) -> None:
        self.assertEqual(desired_route(capsule_id="a" * 64, verdict="pending"), "qa_review")

    def test_changes_requested_routes_back_to_producer(self) -> None:
        self.assertEqual(desired_route(capsule_id="a" * 64, verdict="changes_requested"), "producer_work")

    def test_approval_must_name_exact_capsule(self) -> None:
        capsule = "a" * 64
        comments = [{"createdAt": "2026-01-01T00:00:00Z", "body": "APPROVED some other object"}]
        self.assertEqual(verdict_from_comments(comments, capsule), "pending")
        comments.append({"createdAt": "2026-01-02T00:00:00Z", "body": f"APPROVED capsule {capsule}"})
        self.assertEqual(verdict_from_comments(comments, capsule), "approved")

    def test_code_approval_must_also_name_exact_candidate(self) -> None:
        capsule = "a" * 64
        candidate = "b" * 40
        comments = [{"createdAt": "2026-01-01T00:00:00Z", "body": f"APPROVED {capsule}"}]
        self.assertEqual(
            verdict_from_comments(comments, capsule, candidate_sha=candidate),
            "pending",
        )
        comments.append(
            {"createdAt": "2026-01-02T00:00:00Z", "body": f"APPROVED {capsule} {candidate}"}
        )
        self.assertEqual(
            verdict_from_comments(comments, capsule, candidate_sha=candidate),
            "approved",
        )

    def test_routing_language_is_not_a_verdict(self) -> None:
        capsule = "a" * 64
        candidate = "b" * 40
        comments = [
            {
                "authorAgentId": "cto",
                "createdAt": "2026-01-01T00:00:00Z",
                "body": f"QA must record APPROVED for {capsule} {candidate}",
            }
        ]
        self.assertEqual(
            verdict_from_comments(
                comments,
                capsule,
                candidate_sha=candidate,
                reviewer_agent_id="qa",
            ),
            "pending",
        )

    def test_verdict_must_come_from_assigned_reviewer(self) -> None:
        capsule = "a" * 64
        comments = [
            {
                "authorAgentId": "producer",
                "createdAt": "2026-01-01T00:00:00Z",
                "body": f"APPROVED {capsule}",
            },
            {
                "authorAgentId": "qa",
                "createdAt": "2026-01-02T00:00:00Z",
                "body": f"APPROVED {capsule}",
            },
        ]
        self.assertEqual(
            verdict_from_comments(comments, capsule, reviewer_agent_id="qa"),
            "approved",
        )

    def test_old_rejection_does_not_reject_new_capsule(self) -> None:
        comments = [{"createdAt": "2026-01-01T00:00:00Z", "body": "CHANGES REQUESTED"}]
        self.assertEqual(
            verdict_from_comments(comments, "a" * 64, since="2026-01-02T00:00:00Z"),
            "pending",
        )

    def test_marker_requires_explicit_producer(self) -> None:
        qa = {
            "identifier": "ALPHA7-124",
            "description": (
                "handoff_capsule_v1: required\n"
                "handoff_role: reviewer\n"
                "handoff_producer: ALPHA7-123"
            ),
        }
        self.assertEqual(marked_pair(qa), ("ALPHA7-123", "ALPHA7-124"))
        qa["description"] = "handoff_capsule_v1: required\nParent: ALPHA7-123"
        self.assertIsNone(marked_pair(qa))

    def test_marker_rejects_non_reviewer_helper(self) -> None:
        helper = {
            "identifier": "ALPHA7-124",
            "description": (
                "Publish the producer capsule.\n"
                "handoff_capsule_v1: required\n"
                "handoff_producer: ALPHA7-123"
            ),
        }
        self.assertIsNone(marked_pair(helper))

    def test_product_pair_is_fail_closed(self) -> None:
        product = {
            "title": "Handoff Capsule v1 index abc",
            "status": "ready_for_review",
            "metadata": {"attachmentId": "attachment-1"},
            "summary": (
                '{"schema":"paperclip.handoff-capsule-index/v1",'
                '"producer_issue":"ALPHA7-123","qa_issue":"ALPHA7-124",'
                '"index_attachment_id":"attachment-1"}'
            ),
        }
        self.assertEqual(product_pair(product, "ALPHA7-123"), ("ALPHA7-123", "ALPHA7-124"))
        self.assertIsNone(product_pair(product, "ALPHA7-999"))
        product["metadata"] = {"attachmentId": "different-attachment"}
        self.assertIsNone(product_pair(product, "ALPHA7-123"))

    def test_find_capsule_verifies_remote_index_and_part_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            artifact = root / "result.txt"
            artifact.write_text("durable result", encoding="utf-8")
            index_path = build_capsule(
                output_dir=root / "capsule",
                producer_issue="OPS-1",
                qa_issue="OPS-2",
                artifacts=[str(artifact)],
                created_at="2026-01-01T00:00:00Z",
            )
            index = json.loads(index_path.read_text(encoding="utf-8"))
            downloads = {"/api/attachments/index/content": root / "published.index.json"}
            for position, part in enumerate(index["parts"]):
                attachment_id = f"part-{position}"
                part["attachment_id"] = attachment_id
                part["content_path"] = f"/api/attachments/{attachment_id}/content"
                downloads[part["content_path"]] = index_path.parent / part["name"]
            downloads["/api/attachments/index/content"].write_text(
                json.dumps(index),
                encoding="utf-8",
            )
            product = {
                "title": f"Handoff Capsule v1 index {index['capsule_id'][:12]}",
                "status": "ready_for_review",
                "metadata": {"attachmentId": "index"},
                "summary": json.dumps(
                    {
                        "schema": "paperclip.handoff-capsule-index/v1",
                        "capsule_id": index["capsule_id"],
                        "producer_issue": "OPS-1",
                        "qa_issue": "OPS-2",
                        "index_attachment_id": "index",
                    }
                ),
            }
            client = FakeDownloadClient(downloads)
            capsule_id, found = find_capsule(client, [product], "OPS-1", "OPS-2")
            self.assertEqual(capsule_id, index["capsule_id"])
            self.assertIs(found, product)

            first_part = downloads["/api/attachments/part-0/content"]
            first_part.write_bytes(first_part.read_bytes() + b"tamper")
            self.assertEqual(find_capsule(client, [product], "OPS-1", "OPS-2"), (None, None))

    def test_discovery_combines_markers_and_capsules_without_guessing(self) -> None:
        issues = [
            {"identifier": "ALPHA7-123", "description": "implementation"},
            {
                "identifier": "ALPHA7-124",
                "description": (
                    "handoff_capsule_v1: required\n"
                    "handoff_role: reviewer\n"
                    "handoff_producer: ALPHA7-123"
                ),
            },
            {"identifier": "ALPHA7-125", "description": "implementation"},
            {"identifier": "ALPHA7-126", "description": "review for the producer"},
            {"identifier": "ALPHA7-999", "description": "unrelated review"},
        ]
        products = {
            "ALPHA7-125": [
                {
                    "title": "Handoff Capsule v1 index def",
                    "status": "ready_for_review",
                    "metadata": {"attachmentId": "attachment-2"},
                    "summary": (
                        '{"schema":"paperclip.handoff-capsule-index/v1",'
                        '"producer_issue":"ALPHA7-125","qa_issue":"ALPHA7-126",'
                        '"index_attachment_id":"attachment-2"}'
                    ),
                }
            ]
        }
        self.assertEqual(
            discover_pairs(FakeClient(issues, products)),
            [("ALPHA7-123", "ALPHA7-124"), ("ALPHA7-125", "ALPHA7-126")],
        )


if __name__ == "__main__":
    unittest.main()
