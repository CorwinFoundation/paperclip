"""Compatibility seam for legacy best-effort Scanner webhook delivery."""
from __future__ import annotations

from typing import Any, Callable


def fire_webhook(
    *,
    webhook_cfg: dict[str, str],
    payload: dict[str, Any],
    resolver: Callable[[str], str | bytes | None] | None,
    http_poster: Callable[[str, dict, bytes], None],
) -> bool:
    """Send a signed webhook best-effort; never alter the committed response."""
    from api.ci_scan_ext import WebhookSecretResolutionError, build_signed_delivery

    if resolver is None:
        return False
    try:
        headers, body_bytes = build_signed_delivery(payload, webhook_cfg["secret_ref"], resolver)
        http_poster(webhook_cfg["url"], headers, body_bytes)
        return True
    except WebhookSecretResolutionError:
        return False
    except Exception:
        return False
