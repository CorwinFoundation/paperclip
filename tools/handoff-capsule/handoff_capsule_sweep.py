#!/usr/bin/env python3
"""Repair producer/reviewer handoff state without creating dependency cycles.

Discovery is intentionally fail closed.  A pair is managed only when explicitly
passed with --pair, when the QA description contains `handoff_capsule_v1:
required`, or when the producer already has a Handoff Capsule v1 work product.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Iterable

sys.path.insert(0, str(Path(__file__).resolve().parent))

from handoff_capsule import (
    DEFAULT_API,
    INDEX_SCHEMA,
    LEGACY_INDEX_SCHEMA,
    CapsuleError,
    PaperclipClient,
    fetch_index,
    verify_index,
)


ISSUE_IDENTIFIER = r"[A-Z][A-Z0-9]*-\d+"
PAIR_PATTERN = re.compile(rf"\b({ISSUE_IDENTIFIER})\b", re.IGNORECASE)
CAPSULE_TITLE = "Handoff Capsule v1 index "
CAPSULE_SCHEMAS = frozenset({INDEX_SCHEMA, LEGACY_INDEX_SCHEMA})
REQUIRED_MARKER = re.compile(
    r"(?im)^\s*handoff_capsule_v1\s*:\s*required\s*$"
)
PRODUCER_MARKER = re.compile(
    rf"(?im)^\s*handoff_producer\s*:\s*({ISSUE_IDENTIFIER})\s*$"
)
REVIEWER_ROLE_MARKER = re.compile(
    r"(?im)^\s*handoff_role\s*:\s*reviewer\s*$"
)
APPROVAL_VERDICT = re.compile(
    r"(?im)^\s*(?:\*\*)?(?:QA\s+VERDICT\s*:\s*)?APPROVED(?:\*\*)?(?:\s|$)"
)
REJECTION_VERDICT = re.compile(
    r"(?im)^\s*(?:\*\*)?(?:CHANGES REQUESTED|QA\s+VERDICT\s*:\s*BLOCKED)(?:\*\*)?(?:\s|$)"
)


def verdict_from_comments(
    comments: list[dict[str, object]],
    capsule_id: str | None,
    *,
    candidate_sha: str | None = None,
    reviewer_agent_id: str | None = None,
    since: str | None = None,
) -> str:
    for comment in sorted(comments, key=lambda value: str(value.get("createdAt") or ""), reverse=True):
        if since and str(comment.get("createdAt") or "") < since:
            continue
        body = str(comment.get("body") or "")
        if reviewer_agent_id and str(comment.get("authorAgentId") or "") != reviewer_agent_id:
            continue
        if REJECTION_VERDICT.search(body):
            return "changes_requested"
        if (
            APPROVAL_VERDICT.search(body)
            and capsule_id
            and capsule_id in body
            and (not candidate_sha or candidate_sha in body)
        ):
            return "approved"
    return "pending"


def desired_route(*, capsule_id: str | None, verdict: str) -> str:
    if not capsule_id:
        return "producer_work"
    if verdict == "changes_requested":
        return "producer_work"
    if verdict == "approved":
        return "approved"
    return "qa_review"


def actionable_status(current: object) -> str:
    return "in_progress" if current == "in_progress" else "todo"


def retained_blocker_ids(current: dict[str, object], counterpart_id: str) -> list[str]:
    """Preserve unrelated dependency edges while removing only the reciprocal edge."""

    return sorted(
        str(value.get("id"))
        for value in current.get("blockedBy", [])
        if isinstance(value, dict)
        and value.get("id")
        and str(value.get("id")) != counterpart_id
    )


def routed_status(preferred: str, blockers: list[str]) -> str:
    return "blocked" if blockers else preferred


def find_capsule(
    client: PaperclipClient,
    work_products: list[dict[str, object]],
    expected_producer: str,
    expected_reviewer: str,
) -> tuple[str | None, dict[str, object] | None, str | None]:
    """Return the newest created capsule whose complete bytes verify.

    Paperclip may refresh ``updatedAt`` on every work product when a sibling is
    published.  Creation time is therefore the only stable ordering signal for
    replacement capsules; using ``updatedAt`` can resurrect a rejected capsule.
    Candidate identity also comes from the verified attachment bytes, never
    from the denormalized work-product summary.
    """

    for product in sorted(
        work_products,
        key=lambda value: str(value.get("createdAt") or ""),
        reverse=True,
    ):
        if product_pair(product, expected_producer) != (
            expected_producer.upper(),
            expected_reviewer.upper(),
        ):
            continue
        summary = product.get("summary")
        assert isinstance(summary, str)
        parsed = json.loads(summary)
        capsule_id = str(parsed.get("capsule_id") or "")
        attachment_id = str(parsed.get("index_attachment_id") or "")
        if not re.fullmatch(r"[0-9a-f]{64}", capsule_id) or not attachment_id:
            continue
        try:
            with tempfile.TemporaryDirectory(prefix="handoff-sweep-") as raw:
                index_path = fetch_index(client, attachment_id, Path(raw))
                verified = verify_index(index_path)
        except (CapsuleError, OSError, ValueError, json.JSONDecodeError):
            continue
        if (
            verified.get("capsule_id") == capsule_id
            and str(verified.get("producer_issue") or "").upper() == expected_producer.upper()
            and str(verified.get("qa_issue") or "").upper() == expected_reviewer.upper()
        ):
            candidate = str(verified.get("candidate_sha") or "").lower()
            candidate_sha = (
                candidate
                if re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", candidate)
                else None
            )
            return capsule_id, product, candidate_sha
    return None, None, None


def issue(client: PaperclipClient, identifier: str) -> dict[str, object]:
    value = client.request("GET", f"/api/issues/{identifier}")
    if not isinstance(value, dict):
        raise CapsuleError(f"issue not found: {identifier}")
    return value


def comments(client: PaperclipClient, identifier: str) -> list[dict[str, object]]:
    value = client.request("GET", f"/api/issues/{identifier}/comments")
    if isinstance(value, list):
        return [entry for entry in value if isinstance(entry, dict)]
    if isinstance(value, dict) and isinstance(value.get("items"), list):
        return [entry for entry in value["items"] if isinstance(entry, dict)]
    return []


def work_products(client: PaperclipClient, identifier: str) -> list[dict[str, object]]:
    value = client.request("GET", f"/api/issues/{identifier}/work-products")
    return [entry for entry in value if isinstance(entry, dict)] if isinstance(value, list) else []


def active_issues(client: PaperclipClient) -> list[dict[str, object]]:
    value = client.request(
        "GET",
        (
            f"/api/companies/{client.company_id}/issues"
            "?status=todo,in_progress,in_review,blocked&limit=500"
        ),
    )
    if not isinstance(value, list):
        raise CapsuleError("active issue discovery did not return a list")
    return [entry for entry in value if isinstance(entry, dict)]


def marked_pair(value: dict[str, object]) -> tuple[str, str] | None:
    description = str(value.get("description") or "")
    qa = str(value.get("identifier") or "").upper()
    if (
        not REQUIRED_MARKER.search(description)
        or not REVIEWER_ROLE_MARKER.search(description)
        or not PAIR_PATTERN.fullmatch(qa)
    ):
        return None
    match = PRODUCER_MARKER.search(description)
    if not match:
        return None
    producer = match.group(1).upper()
    if producer == qa:
        return None
    return producer, qa


def product_pair(product: dict[str, object], expected_producer: str) -> tuple[str, str] | None:
    if (
        not str(product.get("title") or "").startswith(CAPSULE_TITLE)
        or product.get("status") != "ready_for_review"
    ):
        return None
    summary = product.get("summary")
    if not isinstance(summary, str):
        return None
    try:
        parsed = json.loads(summary)
    except json.JSONDecodeError:
        return None
    producer = str(parsed.get("producer_issue") or "").upper()
    qa = str(parsed.get("qa_issue") or "").upper()
    attachment_id = str(parsed.get("index_attachment_id") or "")
    metadata = product.get("metadata")
    if (
        parsed.get("schema") not in CAPSULE_SCHEMAS
        or not isinstance(metadata, dict)
        or not attachment_id
        or str(metadata.get("attachmentId") or "") != attachment_id
        or producer != expected_producer.upper()
        or not PAIR_PATTERN.fullmatch(producer)
        or not PAIR_PATTERN.fullmatch(qa)
        or producer == qa
    ):
        return None
    return producer, qa


def discover_pairs(client: PaperclipClient) -> list[tuple[str, str]]:
    """Find only explicitly marked or attachment-backed active handoffs.

    We intentionally do not infer a pair from titles, arbitrary issue links, parent
    relationships, or the word "QA".  That keeps a company-wide sweep from
    rewriting unrelated historical workflows.
    """

    issues = active_issues(client)
    active_identifiers = {
        str(value.get("identifier") or "").upper()
        for value in issues
        if PAIR_PATTERN.fullmatch(str(value.get("identifier") or ""))
    }
    discovered: set[tuple[str, str]] = set()
    for value in issues:
        pair = marked_pair(value)
        if pair and pair[0] in active_identifiers:
            discovered.add(pair)
    for value in issues:
        producer = str(value.get("identifier") or "").upper()
        if not PAIR_PATTERN.fullmatch(producer):
            continue
        for product in work_products(client, producer):
            pair = product_pair(product, producer)
            if pair and pair[1] in active_identifiers:
                discovered.add(pair)
    return sorted(discovered)


def wake(client: PaperclipClient, target: dict[str, object], reason: str, key: str) -> object | None:
    agent_id = str(target.get("assigneeAgentId") or "")
    if not agent_id:
        return None
    return client.request(
        "POST",
        f"/api/agents/{agent_id}/wakeup",
        {
            "source": "automation",
            "triggerDetail": "system",
            "reason": reason,
            "payload": {"issueId": target["id"], "issueIdentifier": target["identifier"]},
            "idempotencyKey": key,
        },
    )


def patch_if_needed(
    client: PaperclipClient,
    current: dict[str, object],
    *,
    status: str,
    blockers: list[str],
    marker: str,
    message: str,
    dry_run: bool,
) -> bool:
    current_blockers = sorted(str(value.get("id")) for value in current.get("blockedBy", []) if isinstance(value, dict))
    wanted = sorted(blockers)
    # Paperclip returns newest-first today, but the contract does not require an
    # ordering. Search the complete fetched thread so idempotency survives either
    # direction and long-running issues.
    already_marked = any(
        marker in str(value.get("body") or "")
        for value in comments(client, str(current["identifier"]))
    )
    if current.get("status") == status and current_blockers == wanted and already_marked:
        return False
    if not dry_run:
        client.request(
            "PATCH",
            f"/api/issues/{current['id']}",
            {"status": status, "blockedByIssueIds": blockers, "comment": f"{message}\n\n`{marker}`"},
        )
    return True


def route_pair(client: PaperclipClient, producer_ident: str, qa_ident: str, *, dry_run: bool = False) -> dict[str, object]:
    producer = issue(client, producer_ident)
    qa = issue(client, qa_ident)
    capsule_id, product, candidate_sha = find_capsule(
        client,
        work_products(client, producer_ident),
        producer_ident,
        qa_ident,
    )
    published_at = str((product or {}).get("createdAt") or (product or {}).get("updatedAt") or "") or None
    reviewer_agent_id = str(qa.get("assigneeAgentId") or "")
    verdict = (
        verdict_from_comments(
            comments(client, qa_ident),
            capsule_id,
            candidate_sha=candidate_sha,
            reviewer_agent_id=reviewer_agent_id,
            since=published_at,
        )
        if reviewer_agent_id
        else "pending"
    )
    route = desired_route(capsule_id=capsule_id, verdict=verdict)
    route_token = capsule_id or "missing"
    marker = f"HANDOFF-CAPSULE-ROUTE:{route}:{route_token}"
    changed: list[str] = []
    wake_target: dict[str, object] | None = None
    producer_id = str(producer["id"])
    qa_id = str(qa["id"])
    producer_other_blockers = retained_blocker_ids(producer, qa_id)
    qa_other_blockers = retained_blocker_ids(qa, producer_id)
    if route == "producer_work":
        if patch_if_needed(
            client,
            producer,
            status=routed_status(actionable_status(producer.get("status")), producer_other_blockers),
            blockers=producer_other_blockers,
            marker=marker,
            message=(
                "Handoff Capsule recovery: producer is actionable. Publish a durable capsule before requesting QA."
                if not capsule_id
                else "Handoff Capsule recovery: QA requested changes; producer is actionable again."
            ),
            dry_run=dry_run,
        ):
            changed.append(producer_ident)
        if patch_if_needed(
            client,
            qa,
            status="blocked",
            blockers=sorted(set(qa_other_blockers + [producer_id])),
            marker=marker,
            message="QA is waiting on producer bytes. This is the only dependency direction; no reciprocal blocker is allowed.",
            dry_run=dry_run,
        ):
            changed.append(qa_ident)
        wake_target = producer
    elif route == "qa_review":
        if patch_if_needed(
            client,
            producer,
            status=routed_status("in_review", producer_other_blockers),
            blockers=producer_other_blockers,
            marker=marker,
            message=f"Capsule `{capsule_id}` is durable and awaiting independent QA.",
            dry_run=dry_run,
        ):
            changed.append(producer_ident)
        if patch_if_needed(
            client,
            qa,
            status=routed_status(actionable_status(qa.get("status")), qa_other_blockers),
            blockers=qa_other_blockers,
            marker=marker,
            message=f"Capsule `{capsule_id}` is ready. Fetch and verify the attachment-backed index before testing.",
            dry_run=dry_run,
        ):
            changed.append(qa_ident)
        wake_target = qa
    else:
        if patch_if_needed(
            client,
            producer,
            status=routed_status("in_review", producer_other_blockers),
            blockers=producer_other_blockers,
            marker=marker,
            message=f"Independent QA approved capsule `{capsule_id}`. Parent owner now owns the next governed action.",
            dry_run=dry_run,
        ):
            changed.append(producer_ident)
        if patch_if_needed(
            client,
            qa,
            status=routed_status("done", qa_other_blockers),
            blockers=qa_other_blockers,
            marker=marker,
            message=f"Recorded exact-capsule approval for `{capsule_id}`.",
            dry_run=dry_run,
        ):
            changed.append(qa_ident)
        parent_id = str(producer.get("parentId") or "")
        wake_target = issue(client, parent_id) if parent_id else None
    wake_result = None
    if changed and wake_target and not dry_run:
        wake_revision = hashlib.sha256(
            f"{producer.get('updatedAt')}:{qa.get('updatedAt')}".encode("utf-8")
        ).hexdigest()[:16]
        wake_result = wake(
            client,
            wake_target,
            f"Handoff Capsule route is {route} for {producer_ident} -> {qa_ident}",
            f"handoff:{producer_ident}:{qa_ident}:{route}:{route_token}:{wake_revision}",
        )
    return {
        "producer": producer_ident,
        "qa": qa_ident,
        "capsule_id": capsule_id,
        "candidate_sha": candidate_sha,
        "work_product_id": product.get("id") if product else None,
        "verdict": verdict,
        "route": route,
        "changed": changed,
        "wake": wake_result,
        "dry_run": dry_run,
    }


def explicit_pairs(values: Iterable[str]) -> list[tuple[str, str]]:
    pairs = []
    for value in values:
        if ":" not in value:
            raise CapsuleError(f"pair must be PRODUCER:QA: {value}")
        producer, qa = value.split(":", 1)
        if not PAIR_PATTERN.fullmatch(producer) or not PAIR_PATTERN.fullmatch(qa):
            raise CapsuleError(f"invalid issue pair: {value}")
        pairs.append((producer.upper(), qa.upper()))
    return pairs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pair", action="append", default=[])
    parser.add_argument(
        "--discover",
        action="store_true",
        help="route all active explicitly marked or attachment-backed Handoff Capsule v1 pairs",
    )
    parser.add_argument("--api-url", default=os.environ.get("PAPERCLIP_API_URL", DEFAULT_API))
    parser.add_argument("--company-id", default=os.environ.get("PAPERCLIP_COMPANY_ID"))
    parser.add_argument("--api-key", default=os.environ.get("PAPERCLIP_API_KEY", ""))
    parser.add_argument("--run-id", default=os.environ.get("PAPERCLIP_RUN_ID", ""))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        company_id = str(args.company_id or "").strip()
        if not company_id:
            raise CapsuleError("--company-id or PAPERCLIP_COMPANY_ID is required")
        client = PaperclipClient(args.api_url, company_id, args.api_key, args.run_id)
        pairs = set(explicit_pairs(args.pair))
        if args.discover:
            pairs.update(discover_pairs(client))
        if not pairs:
            raise CapsuleError("no handoff pairs supplied or discovered")
        results = [
            route_pair(client, producer, qa, dry_run=args.dry_run)
            for producer, qa in sorted(pairs)
        ]
    except (CapsuleError, OSError, ValueError) as exc:
        print(f"handoff-capsule-sweep: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(results, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
