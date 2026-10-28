"""Narrow report-mail policy over the maintained Scanner edge identity resolver."""
from __future__ import annotations

import math
import time

REPORT_MAIL_SCOPE = 'POST /scan/v1/reports/mail'


def authorize_report_mail(gateway, authorization: str, *, tenant_id: str,
                          key_id: str, now: float | None = None):
    """Require the configured BAC principal and an explicit, finite-lived grant.

    The general gateway permits legacy unscoped records on established routes.
    This new capability deliberately requires an explicit report-mail scope.
    Never return or log the bearer; callers audit the returned non-secret key ID.
    """
    if not tenant_id or not key_id or not isinstance(authorization, str):
        raise PermissionError('report_mail_unauthorized')
    prefix, separator, token = authorization.partition(' ')
    if prefix != 'Bearer' or not separator or not token or any(c.isspace() for c in token):
        raise PermissionError('report_mail_unauthorized')
    record = gateway.resolve_record(token)
    current = time.time() if now is None else now
    if (record is None or record.tenant_id != tenant_id or record.key_id != key_id
            or record.status != 'active' or not math.isfinite(record.expires_at)
            or record.expires_at <= current or record.scopes is None
            or REPORT_MAIL_SCOPE not in record.scopes):
        raise PermissionError('report_mail_unauthorized')
    return record
