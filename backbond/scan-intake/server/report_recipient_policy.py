"""Founder routing only; requester routing requires a future Founder enablement."""
import os
import re


def founder_recipient():
    value = os.environ.get('QA_REPORT_MAIL_FOUNDERS', 'info@backbond.ai').strip()
    if not re.fullmatch(r'[^\s@<>,]+@[^\s@<>,]+\.[^\s@<>,]+', value):
        raise ValueError('exactly_one_founder_recipient_required')
    return value


RECIPIENT = founder_recipient()
