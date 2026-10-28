"""Supplied artifacts use the existing intake recipient challenges and sessions."""
import base64
import hashlib
import json

from qa_service import IntakeStore
from report_mail_outbox import ReportMailOutbox


class MailRecipients(IntakeStore):
    def __init__(self, path):
        super().__init__(path)
        self.db.execute('''CREATE TABLE IF NOT EXISTS mailed_report_recipients(
            token TEXT PRIMARY KEY, report_id TEXT NOT NULL, email TEXT NOT NULL)''')

    def _report_recipient(self, token):
        return self.db.execute('SELECT email FROM mailed_report_recipients WHERE token=?',
                               (token,)).fetchone()


class ReportMailAccess(ReportMailOutbox):
    def __init__(self, path):
        super().__init__(path)
        self.recipients = MailRecipients(path)

    def issue(self, ident, recipient, *, request_key=None, now=None):
        if not isinstance(recipient, str):
            return None
        with self.connect() as db:
            row = db.execute('SELECT binding FROM bac_report_mail WHERE request_id=?', (ident,)).fetchone()
        if not row:
            return None
        binding = json.loads(row['binding'])
        if recipient not in binding['to'] + binding['cc']:
            return None
        token = hashlib.sha256((ident + '\0' + recipient).encode()).hexdigest()
        with self.recipients._lock:
            self.recipients.db.execute('INSERT OR IGNORE INTO mailed_report_recipients VALUES(?,?,?)',
                                       (token, ident, recipient))
            issued = self.recipients.issue_report_code(token, request_key=request_key, now=now)
        return tuple(issued[1].split('.', 1)) if issued else None

    def verify(self, ident, challenge, code, *, now=None):
        with self.recipients._lock:
            row = self.recipients.db.execute('''SELECT r.token FROM mailed_report_recipients r
                JOIN report_verifications v ON v.report_token=r.token
                WHERE r.report_id=? AND v.id=?''', (ident, challenge)).fetchone()
            if not row:
                return None
            return self.recipients.verify_report_code(row['token'], challenge + '.' + code, now=now)

    def artifact(self, ident, session, kind, *, now=None):
        if not session or kind not in {'html', 'pdf'}:
            return None
        with self.recipients._lock:
            row = self.recipients.db.execute('''SELECT r.token FROM mailed_report_recipients r
                JOIN report_sessions s ON s.report_token=r.token
                WHERE r.report_id=? AND s.session_hash=?''',
                (ident, hashlib.sha256(session.encode()).hexdigest())).fetchone()
            if not row or not self.recipients.authorize_report(row['token'], session, now=now):
                return None
        with self.connect() as db:
            row = db.execute('SELECT binding FROM bac_report_mail WHERE request_id=?', (ident,)).fetchone()
        return base64.b64decode(json.loads(row['binding'])['payload'][kind + '_base64'], validate=True)
