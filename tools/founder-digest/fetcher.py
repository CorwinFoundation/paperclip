"""Pull live state from the Paperclip API for the daily Founder digest.

All API queries are deterministic given (as_of, since, paperclip-state):
sort keys are explicit, client-side filters are pure functions, no random
selection. The fetcher does NOT mutate state and does NOT call render.
"""
from __future__ import annotations

import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

API_TIMEOUT_S = 30
ACTIVE_STATUSES = ("in_progress", "in_review", "blocked", "backlog")
PRIORITY_WEIGHT = {"critical": 4, "high": 3, "standard": 2, "medium": 2, "low": 1}


@dataclass
class IssueLite:
    id: str
    identifier: str
    title: str
    status: str
    priority: str
    assignee_agent_id: str | None
    assignee_user_id: str | None
    created_at: datetime
    last_activity_at: datetime
    completed_at: datetime | None
    started_at: datetime | None
    parent_id: str | None
    description: str
    blocker_attention: dict[str, Any]

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> "IssueLite":
        return cls(
            id=raw["id"],
            identifier=raw["identifier"],
            title=raw.get("title") or "",
            status=raw.get("status") or "",
            priority=raw.get("priority") or "standard",
            assignee_agent_id=raw.get("assigneeAgentId"),
            assignee_user_id=raw.get("assigneeUserId"),
            created_at=_iso(raw.get("createdAt")),
            last_activity_at=_iso(raw.get("lastActivityAt") or raw.get("updatedAt")),
            completed_at=_iso_optional(raw.get("completedAt")),
            started_at=_iso_optional(raw.get("startedAt")),
            parent_id=raw.get("parentId"),
            description=raw.get("description") or "",
            blocker_attention=raw.get("blockerAttention") or {},
        )


@dataclass
class Interaction:
    id: str
    issue_id: str
    issue_identifier: str
    issue_title: str
    kind: str
    status: str
    created_at: datetime
    prompt: str
    assignee_agent_id: str | None
    assignee_user_id: str | None


@dataclass
class AgentLite:
    id: str
    name: str
    role: str

    @property
    def label(self) -> str:
        return f"{self.name} ({self.role})" if self.role and self.role != "general" else self.name


@dataclass
class DigestState:
    as_of: datetime
    since: datetime
    api_base: str
    approval_interactions: list[Interaction] = field(default_factory=list)
    approval_in_review: list[IssueLite] = field(default_factory=list)
    blocked_top10: list[tuple[IssueLite, str]] = field(default_factory=list)
    yesterday_done: list[IssueLite] = field(default_factory=list)
    yesterday_started: list[IssueLite] = field(default_factory=list)
    founder_ask: IssueLite | None = None
    founder_ask_reason: str = ""
    agents_by_id: dict[str, AgentLite] = field(default_factory=dict)


def _iso(value: str | None) -> datetime:
    if not value:
        return datetime(1970, 1, 1, tzinfo=timezone.utc)
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _iso_optional(value: str | None) -> datetime | None:
    if not value:
        return None
    return _iso(value)


def _request(url: str, api_key: str) -> Any:
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=API_TIMEOUT_S) as resp:
            return _json_load(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:400]
        raise RuntimeError(f"GET {url} -> {e.code}: {body}") from e


def _json_load(blob: bytes) -> Any:
    import json
    return json.loads(blob.decode("utf-8"))


class PaperclipClient:
    def __init__(self, base: str, api_key: str, company_id: str) -> None:
        self.base = base.rstrip("/")
        self.api_key = api_key
        self.company_id = company_id

    def list_issues(self, status: Iterable[str] | None = None, limit: int = 200,
                    offset: int = 0) -> list[dict[str, Any]]:
        params: dict[str, str] = {"limit": str(limit), "offset": str(offset)}
        if status:
            params["status"] = ",".join(status)
        url = f"{self.base}/api/companies/{self.company_id}/issues?{urllib.parse.urlencode(params)}"
        return _request(url, self.api_key)

    def list_issues_all(self, status: Iterable[str]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        offset = 0
        page = 200
        while True:
            batch = self.list_issues(status=status, limit=page, offset=offset)
            out.extend(batch)
            if len(batch) < page:
                break
            offset += page
        return out

    def issue_interactions(self, issue_id: str) -> list[dict[str, Any]]:
        url = f"{self.base}/api/issues/{issue_id}/interactions"
        return _request(url, self.api_key)

    def issue_comments(self, issue_id: str) -> list[dict[str, Any]]:
        url = f"{self.base}/api/issues/{issue_id}/comments"
        return _request(url, self.api_key)

    def list_agents(self) -> list[dict[str, Any]]:
        url = f"{self.base}/api/companies/{self.company_id}/agents"
        return _request(url, self.api_key)


def _priority_sort_key(issue: IssueLite) -> tuple[int, datetime, str]:
    return (
        -PRIORITY_WEIGHT.get(issue.priority, 0),
        issue.created_at,
        issue.identifier,
    )


def _blocker_age_days(issue: IssueLite, as_of: datetime) -> float:
    return max(0.0, (as_of - issue.last_activity_at).total_seconds() / 86400.0)


def _blocker_score(issue: IssueLite, as_of: datetime) -> tuple[float, str]:
    priority_weight = PRIORITY_WEIGHT.get(issue.priority, 1)
    age = _blocker_age_days(issue, as_of)
    return (priority_weight * age, issue.identifier)


def _extract_blocker_cause(client: PaperclipClient, issue: IssueLite) -> str:
    """Pull the most recent blocker cause sentence from the issue comments.

    Strategy: scan comments newest-first; the first sentence that mentions
    "blocked", "blocker", "waiting on", or "stuck" wins. Falls back to the
    first 200 chars of the most recent comment when no signal found.
    """
    try:
        comments = client.issue_comments(issue.id)
    except RuntimeError:
        return "Cause not parsable from comments."
    if not comments:
        return _fallback_cause_from_description(issue.description)
    comments_sorted = sorted(
        comments,
        key=lambda c: c.get("createdAt") or "",
        reverse=True,
    )
    signal_re = re.compile(
        r"(?i)(?:^|[^a-z])(blocked|blocker|waiting on|waiting for|stuck|needs?|pending)\b"
    )
    for c in comments_sorted:
        body = (c.get("body") or "").strip()
        if not body:
            continue
        body = re.sub(r"^---.*?---", "", body, count=1, flags=re.DOTALL).strip()
        sentences = re.split(r"(?<=[.!?])\s+", body)
        for s in sentences:
            if signal_re.search(s):
                cleaned = re.sub(r"\s+", " ", s).strip().rstrip(".") + "."
                if 12 <= len(cleaned) <= 220:
                    return cleaned
    return _fallback_cause_from_description(issue.description)


def _fallback_cause_from_description(desc: str) -> str:
    if not desc:
        return "Cause not parsable from thread."
    first_block = desc.strip().split("\n\n", 1)[0]
    first_block = re.sub(r"^#+\s*", "", first_block.strip())
    if len(first_block) > 200:
        first_block = first_block[:197].rstrip() + "..."
    return first_block + ("." if not first_block.endswith(".") else "")


def fetch(client: PaperclipClient, as_of: datetime, since: datetime,
          founder_role_keywords: tuple[str, ...] = ("Founders", "Founder")) -> DigestState:
    """Pull all four sections of the digest in a deterministic order."""
    state = DigestState(as_of=as_of, since=since, api_base=client.base)

    agents_raw = client.list_agents()
    state.agents_by_id = {
        a["id"]: AgentLite(id=a["id"], name=a.get("name") or "", role=a.get("role") or "general")
        for a in agents_raw
    }

    active = client.list_issues_all(ACTIVE_STATUSES)
    active.sort(key=lambda r: r.get("identifier") or "")
    active_issues = [IssueLite.from_api(r) for r in active]

    state.approval_in_review = sorted(
        (i for i in active_issues if i.status == "in_review"),
        key=_priority_sort_key,
    )

    interactions: list[Interaction] = []
    for issue in active_issues:
        try:
            raw_list = client.issue_interactions(issue.id)
        except RuntimeError:
            continue
        for ix in raw_list:
            if ix.get("status") != "pending":
                continue
            kind = ix.get("kind", "")
            if kind not in {"request_confirmation", "ask_user_questions"}:
                continue
            payload = ix.get("payload") or {}
            prompt = (payload.get("prompt") or payload.get("question") or "").strip()
            interactions.append(Interaction(
                id=ix["id"],
                issue_id=issue.id,
                issue_identifier=issue.identifier,
                issue_title=issue.title,
                kind=kind,
                status=ix["status"],
                created_at=_iso(ix.get("createdAt")),
                prompt=prompt,
                assignee_agent_id=issue.assignee_agent_id,
                assignee_user_id=issue.assignee_user_id,
            ))
    interactions.sort(key=lambda x: (x.created_at, x.issue_identifier, x.id))
    state.approval_interactions = interactions

    blocked = [i for i in active_issues if i.status == "blocked"]
    blocked.sort(key=lambda i: (-_blocker_score(i, as_of)[0], i.identifier))
    state.blocked_top10 = []
    for issue in blocked[:10]:
        cause = _extract_blocker_cause(client, issue)
        state.blocked_top10.append((issue, cause))

    done_all = client.list_issues_all(("done",))
    done_issues = [IssueLite.from_api(r) for r in done_all]
    state.yesterday_done = sorted(
        (
            i for i in done_issues
            if i.completed_at and since <= i.completed_at < as_of
        ),
        key=lambda i: (i.completed_at or as_of, i.identifier),
    )

    state.yesterday_started = sorted(
        (
            i for i in active_issues
            if i.started_at and since <= i.started_at < as_of
        ),
        key=lambda i: (i.started_at or as_of, i.identifier),
    )

    state.founder_ask, state.founder_ask_reason = _select_founder_ask(
        active_issues, state.agents_by_id, as_of, founder_role_keywords,
    )

    return state


def _issue_to_dict(i: IssueLite) -> dict[str, Any]:
    return {
        "id": i.id, "identifier": i.identifier, "title": i.title,
        "status": i.status, "priority": i.priority,
        "assignee_agent_id": i.assignee_agent_id,
        "assignee_user_id": i.assignee_user_id,
        "created_at": i.created_at.isoformat(),
        "last_activity_at": i.last_activity_at.isoformat(),
        "completed_at": i.completed_at.isoformat() if i.completed_at else None,
        "started_at": i.started_at.isoformat() if i.started_at else None,
        "parent_id": i.parent_id, "description": i.description,
        "blocker_attention": i.blocker_attention,
    }


def _issue_from_dict(d: dict[str, Any]) -> IssueLite:
    return IssueLite(
        id=d["id"], identifier=d["identifier"], title=d["title"],
        status=d["status"], priority=d["priority"],
        assignee_agent_id=d.get("assignee_agent_id"),
        assignee_user_id=d.get("assignee_user_id"),
        created_at=_iso(d["created_at"]),
        last_activity_at=_iso(d["last_activity_at"]),
        completed_at=_iso_optional(d.get("completed_at")),
        started_at=_iso_optional(d.get("started_at")),
        parent_id=d.get("parent_id"), description=d.get("description") or "",
        blocker_attention=d.get("blocker_attention") or {},
    )


def save_snapshot(state: DigestState, path) -> None:
    """Persist the fetched state to disk for replay (determinism tests)."""
    import json
    from pathlib import Path as _P
    payload = {
        "schema_version": 1,
        "as_of": state.as_of.isoformat(),
        "since": state.since.isoformat(),
        "agents": [
            {"id": a.id, "name": a.name, "role": a.role}
            for a in sorted(state.agents_by_id.values(), key=lambda x: x.id)
        ],
        "approval_interactions": [
            {
                "id": ix.id, "issue_id": ix.issue_id,
                "issue_identifier": ix.issue_identifier,
                "issue_title": ix.issue_title, "kind": ix.kind,
                "status": ix.status, "created_at": ix.created_at.isoformat(),
                "prompt": ix.prompt,
                "assignee_agent_id": ix.assignee_agent_id,
                "assignee_user_id": ix.assignee_user_id,
            } for ix in state.approval_interactions
        ],
        "approval_in_review": [_issue_to_dict(i) for i in state.approval_in_review],
        "blocked_top10": [
            {"issue": _issue_to_dict(i), "cause": c}
            for i, c in state.blocked_top10
        ],
        "yesterday_done": [_issue_to_dict(i) for i in state.yesterday_done],
        "yesterday_started": [_issue_to_dict(i) for i in state.yesterday_started],
        "founder_ask": _issue_to_dict(state.founder_ask) if state.founder_ask else None,
        "founder_ask_reason": state.founder_ask_reason,
        "api_base": state.api_base,
    }
    _P(path).write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def load_snapshot(path, as_of: datetime, since: datetime) -> DigestState:
    """Reconstruct a DigestState from a snapshot file."""
    import json
    from pathlib import Path as _P
    data = json.loads(_P(path).read_text(encoding="utf-8"))
    state = DigestState(as_of=as_of, since=since, api_base=data.get("api_base", ""))
    state.agents_by_id = {
        a["id"]: AgentLite(id=a["id"], name=a["name"], role=a["role"])
        for a in data.get("agents", [])
    }
    state.approval_interactions = [
        Interaction(
            id=ix["id"], issue_id=ix["issue_id"],
            issue_identifier=ix["issue_identifier"],
            issue_title=ix["issue_title"], kind=ix["kind"],
            status=ix["status"], created_at=_iso(ix["created_at"]),
            prompt=ix["prompt"],
            assignee_agent_id=ix.get("assignee_agent_id"),
            assignee_user_id=ix.get("assignee_user_id"),
        ) for ix in data.get("approval_interactions", [])
    ]
    state.approval_in_review = [_issue_from_dict(d) for d in data.get("approval_in_review", [])]
    state.blocked_top10 = [
        (_issue_from_dict(b["issue"]), b["cause"]) for b in data.get("blocked_top10", [])
    ]
    state.yesterday_done = [_issue_from_dict(d) for d in data.get("yesterday_done", [])]
    state.yesterday_started = [_issue_from_dict(d) for d in data.get("yesterday_started", [])]
    state.founder_ask = _issue_from_dict(data["founder_ask"]) if data.get("founder_ask") else None
    state.founder_ask_reason = data.get("founder_ask_reason", "")
    return state


def _select_founder_ask(
    active: list[IssueLite],
    agents_by_id: dict[str, AgentLite],
    as_of: datetime,
    keywords: tuple[str, ...],
) -> tuple[IssueLite | None, str]:
    """Deterministic single-Founder-ask selector.

    Prefers items already escalated (title or description mentions
    ESCALATION / Founders), then critical/high priority in_review items
    routed away from agent assignees (i.e., needing a board decision).
    Tie-breaks are: priority desc, age desc, identifier asc.
    """
    if not active:
        return None, "No active items."

    escalation_re = re.compile(r"(?i)\bescalat(?:e|ion|ed)\b|\bfounder", re.IGNORECASE)
    candidates: list[tuple[tuple[int, float, str], IssueLite, str]] = []

    for issue in active:
        is_escalation = bool(escalation_re.search(issue.title)) or bool(
            escalation_re.search(issue.description[:1500])
        )
        is_in_review = issue.status == "in_review"
        is_board_routed = (issue.assignee_user_id is not None) or (
            issue.assignee_agent_id is None and is_in_review
        )

        score_bucket = 0
        reason_parts: list[str] = []
        if is_escalation:
            score_bucket += 100
            reason_parts.append("already escalated")
        if is_in_review and is_board_routed:
            score_bucket += 50
            reason_parts.append("waiting on a board decision")
        if issue.priority == "critical":
            score_bucket += 20
        elif issue.priority == "high":
            score_bucket += 10

        if score_bucket == 0:
            continue

        age = _blocker_age_days(issue, as_of)
        sort_key = (-score_bucket, -age, issue.identifier)
        reason = ", ".join(reason_parts) if reason_parts else "highest open priority"
        candidates.append((sort_key, issue, reason))

    if not candidates:
        critical_open = [i for i in active if i.priority in ("critical", "high")]
        if not critical_open:
            return None, "No item meets the founder-ask selector threshold today."
        critical_open.sort(key=_priority_sort_key)
        chosen = critical_open[0]
        return chosen, "highest open priority"

    candidates.sort(key=lambda x: x[0])
    _, chosen, reason = candidates[0]
    return chosen, reason
