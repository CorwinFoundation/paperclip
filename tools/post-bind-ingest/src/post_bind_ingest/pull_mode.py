"""Vendor-local pull-mode orchestration for the existing Passive Monitor lane."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping, Sequence, Any


class PullModeError(ValueError):
    pass


@dataclass(frozen=True)
class PullPage:
    events: tuple[Mapping[str, Any], ...]
    next_cursor: str | None


class PullConsumer:
    """Pull bounded pages from an authenticated, tenant-bound local source."""

    def __init__(self, *, tenant_id: str, bearer_token: str, max_page_size: int = 100):
        if not tenant_id or not bearer_token or max_page_size < 1 or max_page_size > 1000:
            raise PullModeError("invalid pull consumer configuration")
        self.tenant_id, self._token, self.max_page_size = tenant_id, bearer_token, max_page_size
        self._seen: set[str] = set()

    def pull(self, source: Callable[..., Mapping[str, Any]], *, cursor: str | None = None) -> PullPage:
        raw = source(tenant_id=self.tenant_id, bearer_token=self._token, cursor=cursor, limit=self.max_page_size)
        if not isinstance(raw, Mapping) or set(raw) != {"tenant_id", "events", "next_cursor"}:
            raise PullModeError("malformed pull page")
        if raw["tenant_id"] != self.tenant_id:
            raise PullModeError("cross-tenant pull page rejected")
        events = raw["events"]
        if not isinstance(events, Sequence) or isinstance(events, (str, bytes)) or len(events) > self.max_page_size:
            raise PullModeError("pull page exceeds backpressure limit")
        accepted = []
        for event in events:
            if not isinstance(event, Mapping) or not isinstance(event.get("event_id"), str):
                raise PullModeError("malformed pull event")
            if event["event_id"] in self._seen:
                continue
            self._seen.add(event["event_id"])
            accepted.append(event)
        next_cursor = raw["next_cursor"]
        if next_cursor is not None and not isinstance(next_cursor, str):
            raise PullModeError("next_cursor must be a string or null")
        return PullPage(tuple(accepted), next_cursor)

