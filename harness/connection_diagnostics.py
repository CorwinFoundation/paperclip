"""Safe public diagnostics for a single connection probe (BEAAA-43924)."""
import errno
import html
import re
import socket
import ssl
import httpx


def safe_excerpt(text, secrets=()):
    # Redact the entire bounded response before cutting through a credential.
    for secret in sorted((s for s in secrets if isinstance(s, str) and s), key=len, reverse=True):
        text = text.replace(secret, '[REDACTED]')
    text = re.sub(r'(?i)(bearer\s+)[^\s"<>]+', r'\1[REDACTED]', text)
    text = re.sub(r'(?im)(authorization\s*[:=]\s*)[^\r\n]+', r'\1[REDACTED]', text)
    return html.escape(text[:200], quote=True)


def failure_details(exc, runner, elapsed_ms, secrets=()):
    status = getattr(exc, 'status_code', None) or getattr(runner, '_connection_status', None)
    result = {'failed_stage': 'response_parse' if status else 'transport', 'elapsed_ms': elapsed_ms}
    if status:
        result['target_status'] = status
        result['failed_stage'] = 'auth' if status in (401, 403) else ('reachability' if status >= 300 else 'response_parse')
    current, seen = exc, set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        kind = None
        if isinstance(current, socket.gaierror): kind = 'dns'
        elif isinstance(current, ssl.SSLError): kind = 'tls'
        elif isinstance(current, (httpx.TimeoutException, TimeoutError)): kind = 'timeout'
        elif isinstance(current, ConnectionRefusedError) or getattr(current, 'errno', None) == errno.ECONNREFUSED: kind = 'connection_refused'
        elif isinstance(current, ConnectionResetError) or getattr(current, 'errno', None) == errno.ECONNRESET: kind = 'connection_reset'
        if kind:
            result['error_class'] = kind
            result['failed_stage'] = 'reachability' if kind == 'dns' else 'transport'
            break
        current = current.__cause__ or current.__context__
    raw = getattr(exc, 'body', '') or getattr(runner, '_connection_body', '')
    if raw:
        result['response_excerpt'] = safe_excerpt(raw, secrets)
    return result
