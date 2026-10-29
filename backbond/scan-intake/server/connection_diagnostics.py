"""Public response sanitization shipped with the standalone intake service."""
import html
import re


def safe_excerpt(text, secrets=()):
    # Redact before truncation so the boundary cannot expose a credential prefix.
    for secret in sorted((s for s in secrets if isinstance(s, str) and s), key=len, reverse=True):
        text = text.replace(secret, '[REDACTED]')
    text = re.sub(r'(?i)(bearer\s+)[^\s"<>]+', r'\1[REDACTED]', text)
    text = re.sub(r'(?im)(authorization\s*[:=]\s*)[^\r\n]+', r'\1[REDACTED]', text)
    return html.escape(text[:200], quote=True)
