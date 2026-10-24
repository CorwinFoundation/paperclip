"""Bounded timing for the legacy anonymous Pollinations free endpoint."""
from urllib.parse import urlsplit

REQUEST_INTERVAL_SECONDS = 31.0
REQUEST_TIMEOUT_SECONDS = 90.0
MAX_RATE_LIMIT_ATTEMPTS = 3
MAX_RETRY_AFTER_SECONDS = 60.0


def row_wait_budget():
    """Cover initial pacing, every request, and both bounded retry waits."""
    return (REQUEST_INTERVAL_SECONDS + MAX_RATE_LIMIT_ATTEMPTS * REQUEST_TIMEOUT_SECONDS
            + (MAX_RATE_LIMIT_ATTEMPTS - 1) * MAX_RETRY_AFTER_SECONDS)


def needs_free_tier_pacing(endpoint, config):
    url = urlsplit(endpoint)
    config = config or {}
    return (
        url.scheme == 'https' and url.hostname == 'text.pollinations.ai'
        and url.path == '/openai' and not url.query and not url.fragment
        and not url.username and not url.password and url.port in (None, 443)
        and config.get('transport') in {'json_map', 'openai_chat'}
        and (config.get('auth') or {}).get('type') == 'none'
    )


def starter_wait_budget(scan_request):
    """Add waiting time only; retain the ordinary probe, token and cost caps."""
    if (scan_request is not None and scan_request.pack_id == 'starter-100@4'
            and needs_free_tier_pacing(scan_request.agent_endpoint, scan_request.endpoint_config)):
        return 100 * REQUEST_INTERVAL_SECONDS
    return 0.0
