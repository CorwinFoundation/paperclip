"""BEAAA-42596: fail-closed report revision gates. Intake never sends report mail.

Immutable report revisions and single-use BAC Lead decisions. The final Starter report and
its send to info@backbond.ai belong to the customer-operations Operations workflow
(operations_client.py); this module has no send outbox, no mail provider and no receipts.
A Lead-approved revision only opens the BAC report-token access gate (released()).

Revision content is Analyst-authored: {'report_input': the frozen Starter input from
report_tool.freeze_starter_input (reached only through the BAC case resolver), 'input_sha256',
'authored': {'summary', 'recommendations'}}. The artifact is that narrative rendered by the
released plain-text template (release_message, RENDERER_VERSION), never measurement JSON.

Each revision freezes its rendered artifact (subject+body bytes, artifact_sha256,
renderer_version); the Lead approves revision_sha256, which covers content and artifact.

Chain: Analyst authors -> an independent reviewer (neither author nor Lead) approves the exact
revision_sha256 -> the BAC Lead approves the same digest. Every decision nonce is single-use.

Public projection: public_lifecycle() supplies report_status/delivery_status/updated_at of
the starter_lifecycle@1 object on scan status, derived only from durable revision records
(scans the Operations workflow has acknowledged read from Operations instead, see app.py):
  report_status:   awaiting_results | drafting | review | changes_requested |
                   awaiting_ceo_approval | approved | unavailable
  (review = awaiting the independent reviewer; awaiting_ceo_approval only after a valid review)
  delivery_status: not_ready | pending | sending | provider_accepted | failed | unknown | unavailable
Intake never sends, so its own records only ever yield not_ready or unavailable delivery.
`provider_accepted` means the mail provider accepted the request, never inbox delivery.
No actor ids, idempotency keys, message ids, recipients or evidence URIs are exposed.
"""
import hashlib
import json
import re
import sqlite3
import threading
import time
from datetime import datetime, timezone

RENDERER_VERSION = 'starter_report_text@2'  # was 'release_message@1' (canonical JSON dump)
REPORT_STATUSES = ('awaiting_results', 'drafting', 'review', 'changes_requested', 'awaiting_ceo_approval',
                   'approved', 'unavailable')
DELIVERY_STATUSES = ('not_ready', 'pending', 'sending', 'provider_accepted', 'failed', 'unknown', 'unavailable')
PUBLIC_RELEASE_FIELDS = {'report_status': REPORT_STATUSES, 'delivery_status': DELIVERY_STATUSES}
UNAVAILABLE = {'report_status': 'unavailable', 'delivery_status': 'unavailable', 'updated_at': None}


class ReleaseError(ValueError):
    pass


class RevisionTampered(ReleaseError):
    pass


class ApprovalRefused(ReleaseError):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def canonical(content: dict) -> str:
    return json.dumps(content, sort_keys=True, separators=(',', ':'))


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def artifact_digest(subject: str, body: str) -> str:
    return hashlib.sha256(subject.encode() + b'\x00' + body.encode()).hexdigest()


def revision_digest(content_sha256: str, artifact_sha256: str, renderer_version: str,
                    author_actor: str | None = None) -> str:
    """What the Lead approves: content, the exact rendered artifact bytes and the Analyst author.

    Author-less (legacy) revisions keep the pre-author digest so they still read, but they
    can never be approved (see approve/approval_valid)."""
    base = f'{content_sha256}|{artifact_sha256}|{renderer_version}'
    return sha256(base + f'|author:{author_actor}' if author_actor else base)


def _add_columns(db, table, columns):
    have = {r[1] for r in db.execute(f'PRAGMA table_info({table})')}
    for name, decl in columns:
        if name not in have:  # migration-safe for databases created before the column existed
            db.execute(f'ALTER TABLE {table} ADD COLUMN {name} {decl}')


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


class ReportRelease:
    def __init__(self, path: str, *, lead_actor_id=''):
        self._lock = threading.RLock()
        self._lead = lead_actor_id if callable(lead_actor_id) else (lambda: lead_actor_id)
        self.db = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
          PRAGMA journal_mode=WAL;
          CREATE TABLE IF NOT EXISTS release_revisions(revision_id TEXT PRIMARY KEY, scan_id TEXT NOT NULL,
            seq INTEGER NOT NULL, content_json TEXT NOT NULL, content_sha256 TEXT NOT NULL,
            created_at TEXT NOT NULL, UNIQUE(scan_id, seq));
          CREATE TRIGGER IF NOT EXISTS release_revisions_immutable BEFORE UPDATE ON release_revisions
            BEGIN SELECT RAISE(ABORT, 'revisions are immutable'); END;
          CREATE TABLE IF NOT EXISTS release_current(scan_id TEXT PRIMARY KEY, revision_id TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS release_decisions(nonce TEXT PRIMARY KEY, revision_id TEXT NOT NULL,
            content_sha256 TEXT NOT NULL, actor_id TEXT NOT NULL, decision TEXT NOT NULL,
            outcome TEXT NOT NULL, reason TEXT, decided_at TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS release_decision_polls(revision_id TEXT PRIMARY KEY, checked_at REAL NOT NULL);
        ''')
        # Legacy revisions without a frozen artifact read as tampered: fail closed, never re-render.
        _add_columns(self.db, 'release_revisions', [('subject', 'TEXT'), ('body', 'TEXT'),
                     ('artifact_sha256', 'TEXT'), ('renderer_version', 'TEXT'), ('revision_sha256', 'TEXT')])
        # REPORT-AUTHOR-SEPARATION: the authenticated Analyst author. Pre-existing rows stay NULL
        # and fail closed (never approvable; a new authored revision is required).
        _add_columns(self.db, 'release_revisions', [('author_actor', 'TEXT')])
        # Pre-existing decisions were all Lead decisions. stage: lead | review.
        _add_columns(self.db, 'release_decisions', [('stage', "TEXT NOT NULL DEFAULT 'lead'")])

    def create_revision(self, scan_id: str, content: dict, *, author_actor: str) -> dict:
        """author_actor is the server-authenticated Analyst; never taken from a request body."""
        if not isinstance(author_actor, str) or not author_actor:
            raise ReleaseError('author_required')
        body = canonical(content)
        digest = sha256(body)
        with self._lock:
            self.db.execute('BEGIN IMMEDIATE')
            try:
                seq = self.db.execute('SELECT COALESCE(MAX(seq),0)+1 FROM release_revisions WHERE scan_id=?',
                                      (scan_id,)).fetchone()[0]
                revision_id = 'rev_' + sha256(f'{scan_id}|{digest}|{seq}')[:32]
                # Render once, here; the approved artifact is these frozen bytes, never a later template.
                subject, text = release_message({'revision_id': revision_id, 'content_sha256': digest,
                                                 'content': content})
                artifact = artifact_digest(subject, text)
                approval = revision_digest(digest, artifact, RENDERER_VERSION, author_actor)
                self.db.execute('INSERT INTO release_revisions(revision_id,scan_id,seq,content_json,content_sha256,'
                                'created_at,subject,body,artifact_sha256,renderer_version,revision_sha256,'
                                'author_actor) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                                (revision_id, scan_id, seq, body, digest, _now_iso(), subject, text, artifact,
                                 RENDERER_VERSION, approval, author_actor))
                # Superseding: every approval of an older revision is void from here on.
                self.db.execute('INSERT OR REPLACE INTO release_current VALUES(?,?)', (scan_id, revision_id))
                self.db.execute('COMMIT')
            except Exception:
                self.db.execute('ROLLBACK')
                raise
        return {'revision_id': revision_id, 'scan_id': scan_id, 'seq': seq, 'content_sha256': digest,
                'artifact_sha256': artifact, 'renderer_version': RENDERER_VERSION, 'revision_sha256': approval}

    def read_revision(self, revision_id: str) -> dict | None:
        with self._lock:
            row = self.db.execute('SELECT r.*, c.revision_id AS current FROM release_revisions r '
                                  'LEFT JOIN release_current c ON c.scan_id=r.scan_id WHERE r.revision_id=?',
                                  (revision_id,)).fetchone()
        if not row:
            return None
        if (sha256(row['content_json']) != row['content_sha256'] or row['subject'] is None or row['body'] is None
                or artifact_digest(row['subject'], row['body']) != row['artifact_sha256']
                or revision_digest(row['content_sha256'], row['artifact_sha256'], row['renderer_version'] or '',
                                   row['author_actor']) != row['revision_sha256']):
            raise RevisionTampered('stored revision digest mismatch')
        return {'revision_id': row['revision_id'], 'scan_id': row['scan_id'], 'seq': row['seq'],
                'content': json.loads(row['content_json']), 'content_sha256': row['content_sha256'],
                'subject': row['subject'], 'body': row['body'], 'artifact_sha256': row['artifact_sha256'],
                'renderer_version': row['renderer_version'], 'revision_sha256': row['revision_sha256'],
                'created_at': row['created_at'], 'author_actor': row['author_actor'],
                'current': row['current'] == revision_id}

    def approve(self, revision_id: str, revision_sha256: str, actor_id: str, nonce: str,
                decision: str = 'approve', *, role: str = 'lead') -> dict:
        """revision_sha256 covers content and frozen artifact bytes; release_decisions keeps it
        in its legacy `content_sha256` column."""
        content_sha256 = revision_sha256
        if decision not in ('approve', 'reject'):
            raise ApprovalRefused('invalid_decision')
        if not isinstance(nonce, str) or not 16 <= len(nonce) <= 128:
            raise ApprovalRefused('invalid_nonce')
        with self._lock:
            if self.db.execute('SELECT 1 FROM release_decisions WHERE nonce=?', (nonce,)).fetchone():
                raise ApprovalRefused('nonce_replayed')
            lead, reason = self._lead(), None
            try:
                revision = self.read_revision(revision_id)
            except RevisionTampered:
                revision, reason = None, 'revision_tampered'
            if not lead:
                reason = 'lead_unconfigured'
            elif role != 'lead':
                reason = 'wrong_role'
            elif actor_id != lead:
                reason = 'wrong_actor'
            elif reason:
                pass
            elif revision is None:
                reason = 'unknown_revision'
            elif not revision['author_actor']:
                reason = 'author_missing'  # legacy revision: needs a new Analyst-authored revision
            elif revision['author_actor'] == actor_id:
                reason = 'self_approval_refused'
            elif not revision['current']:
                reason = 'stale_revision'
            elif content_sha256 != revision['revision_sha256']:
                reason = 'digest_mismatch'
            elif decision == 'approve' and not self.review_valid(revision_id):
                reason = 'review_required'
            outcome = 'refused' if reason else ('approved' if decision == 'approve' else 'rejected')
            self._record(nonce, revision_id, content_sha256, actor_id, decision, outcome, reason, 'lead')
        if reason:
            raise ApprovalRefused(reason)
        return {'revision_id': revision_id, 'revision_sha256': content_sha256, 'decision': outcome}

    def _record(self, nonce, revision_id, digest, actor_id, decision, outcome, reason, stage):
        # The nonce is consumed by every attempt, refused ones included, across all stages.
        try:
            self.db.execute('INSERT INTO release_decisions(nonce,revision_id,content_sha256,actor_id,decision,'
                            'outcome,reason,decided_at,stage) VALUES(?,?,?,?,?,?,?,?,?)',
                            (nonce, revision_id, str(digest), str(actor_id), decision, outcome, reason,
                             _now_iso(), stage))
        except sqlite3.IntegrityError:  # another process consumed the nonce first
            raise ApprovalRefused('nonce_replayed')

    def consume_native_decision(self, revision_id: str, decision: dict) -> dict:
        """Apply one gateway-verified native Lead decision (BAC_GATEWAY.md) to this exact revision.

        The native comment id is the replay identity: once consumed, that comment is never applied
        again, to this or any other revision (re-reading the same comment for the same revision is
        an idempotent no-op). Actor, current-revision, digest, review and author-separation checks
        are the ordinary approve() gates; the gateway's verified authorAgentId is the actor."""
        nonce = 'native-comment:' + decision['id']
        with self._lock:
            row = self.db.execute('SELECT revision_id,content_sha256,outcome FROM release_decisions WHERE nonce=?',
                                  (nonce,)).fetchone()
        if row:
            if (row['revision_id'] == revision_id and row['content_sha256'] == decision['revision_sha256']
                    and row['outcome'] in ('approved', 'rejected')):
                return {'revision_id': revision_id, 'revision_sha256': row['content_sha256'],
                        'decision': row['outcome'], 'replayed': True}
            raise ApprovalRefused('nonce_replayed')
        return self.approve(revision_id, decision['revision_sha256'], decision['authorAgentId'], nonce,
                            decision['decision'])

    def awaiting_lead(self, limit: int = 10, now: float | None = None) -> list[dict]:
        """Current, independently reviewed revisions with no Lead decision yet, least recently
        polled first (so one undecided report cannot starve the rest)."""
        now = time.time() if now is None else now
        with self._lock:
            rows = self.db.execute('SELECT c.scan_id, c.revision_id FROM release_current c LEFT JOIN '
                                   'release_decision_polls p ON p.revision_id=c.revision_id '
                                   'ORDER BY COALESCE(p.checked_at,0), c.scan_id').fetchall()
        out = []
        for scan_id, revision_id in rows:
            if len(out) >= max(0, limit):
                break
            if self._last(revision_id, 'lead', ('approved', 'rejected')) or not self.review_valid(revision_id):
                continue
            try:
                revision = self.read_revision(revision_id)
            except RevisionTampered:
                continue
            if revision and revision['current']:
                out.append({'scan_id': scan_id, 'revision_id': revision_id,
                            'revision_sha256': revision['revision_sha256']})
        with self._lock:
            for item in out:
                self.db.execute('INSERT OR REPLACE INTO release_decision_polls VALUES(?,?)',
                                (item['revision_id'], now))
        return out

    def review(self, revision_id: str, revision_sha256: str, actor_id: str, nonce: str,
               decision: str = 'approve', *, role: str = 'reviewer') -> dict:
        """Independent reviewer step: neither the author nor the Lead, bound to revision_sha256."""
        if decision not in ('approve', 'request_changes'):
            raise ApprovalRefused('invalid_decision')
        if not isinstance(nonce, str) or not 16 <= len(nonce) <= 128:
            raise ApprovalRefused('invalid_nonce')
        with self._lock:
            if self.db.execute('SELECT 1 FROM release_decisions WHERE nonce=?', (nonce,)).fetchone():
                raise ApprovalRefused('nonce_replayed')
            lead, reason = self._lead(), None
            try:
                revision = self.read_revision(revision_id)
            except RevisionTampered:
                revision, reason = None, 'revision_tampered'
            if role != 'reviewer':
                reason = 'wrong_role'
            elif not isinstance(actor_id, str) or not actor_id:
                reason = 'wrong_actor'
            elif reason:
                pass
            elif revision is None:
                reason = 'unknown_revision'
            elif not revision['author_actor']:
                reason = 'author_missing'
            elif revision['author_actor'] == actor_id:
                reason = 'self_review_refused'
            elif lead and actor_id == lead:
                reason = 'reviewer_is_lead'
            elif not revision['current']:
                reason = 'stale_revision'
            elif revision_sha256 != revision['revision_sha256']:
                reason = 'digest_mismatch'
            outcome = 'refused' if reason else ('approved' if decision == 'approve' else 'changes_requested')
            self._record(nonce, revision_id, revision_sha256, actor_id, decision, outcome, reason, 'review')
        if reason:
            raise ApprovalRefused(reason)
        return {'revision_id': revision_id, 'revision_sha256': revision_sha256, 'decision': outcome}

    def _last(self, revision_id, stage, outcomes):
        with self._lock:
            return self.db.execute(
                f"SELECT * FROM release_decisions WHERE revision_id=? AND stage=? AND outcome IN "
                f"({','.join('?' * len(outcomes))}) ORDER BY rowid DESC LIMIT 1",
                (revision_id, stage, *outcomes)).fetchone()

    def review_valid(self, revision_id: str) -> bool:
        try:
            revision = self.read_revision(revision_id)
        except RevisionTampered:
            return False
        lead = self._lead()
        if not revision or not revision['current'] or not lead or not revision['author_actor']:
            return False
        row = self._last(revision_id, 'review', ('approved', 'changes_requested'))
        return bool(row and row['outcome'] == 'approved' and row['actor_id'] not in (lead, revision['author_actor'])
                    and row['content_sha256'] == revision['revision_sha256'])

    def approval_valid(self, revision_id: str) -> bool:
        try:
            revision = self.read_revision(revision_id)
        except RevisionTampered:
            return False
        lead = self._lead()
        if not revision or not revision['current'] or not lead or not revision['author_actor']:
            return False
        row = self._last(revision_id, 'lead', ('approved', 'rejected'))
        return bool(row and row['outcome'] == 'approved' and row['actor_id'] == lead and self.review_valid(revision_id)
                    and row['actor_id'] != revision['author_actor']
                    and row['content_sha256'] == revision['revision_sha256'])

    def released(self, scan_id: str) -> bool:
        with self._lock:
            row = self.db.execute('SELECT revision_id FROM release_current WHERE scan_id=?', (scan_id,)).fetchone()
        return bool(row and self.approval_valid(row[0]))

    def public_lifecycle(self, scan_id: str, *, scan_failed: bool = False) -> dict:
        """starter_lifecycle@1 report/delivery fields from durable revision records only.

        Never manufactures a phase: no draft table exists, so a missing revision is
        awaiting_results (or unavailable when the scan failed); anything unreadable is unavailable.
        Intake never sends report mail, so an approved revision's delivery stays not_ready here.
        """
        with self._lock:
            row = self.db.execute('SELECT revision_id FROM release_current WHERE scan_id=?', (scan_id,)).fetchone()
        if not row:
            return dict(UNAVAILABLE) if scan_failed else \
                {'report_status': 'awaiting_results', 'delivery_status': 'not_ready', 'updated_at': None}
        revision_id = row[0]
        try:
            revision = self.read_revision(revision_id)
        except RevisionTampered:
            return dict(UNAVAILABLE)
        if not revision or not revision['author_actor']:
            return dict(UNAVAILABLE)  # legacy author-less revision: cannot be approved, needs a new one
        review = self._last(revision_id, 'review', ('approved', 'changes_requested'))
        decision = self._last(revision_id, 'lead', ('approved', 'rejected'))
        if decision and decision['outcome'] == 'rejected':
            return {'report_status': 'changes_requested', 'delivery_status': 'not_ready',
                    'updated_at': decision['decided_at']}
        if review and review['outcome'] == 'changes_requested':
            return {'report_status': 'changes_requested', 'delivery_status': 'not_ready',
                    'updated_at': review['decided_at']}
        if not review:
            return {'report_status': 'review', 'delivery_status': 'not_ready', 'updated_at': revision['created_at']}
        if not self.review_valid(revision_id):
            return dict(UNAVAILABLE)
        if not decision:
            return {'report_status': 'awaiting_ceo_approval', 'delivery_status': 'not_ready',
                    'updated_at': review['decided_at']}
        if not self.approval_valid(revision_id):
            return dict(UNAVAILABLE)
        return {'report_status': 'approved', 'delivery_status': 'not_ready', 'updated_at': decision['decided_at']}


def release_message(revision: dict) -> tuple[str, str]:
    """Released plain-text Starter report template (RENDERER_VERSION starter_report_text@2).

    Rendered once at revision creation from the Analyst-authored content and the frozen
    report input; approval binds the stored bytes, which are never re-rendered. Deterministic:
    the same revision always yields the same subject and body."""
    content = revision['content']
    frozen, authored = content['report_input'], content['authored']
    counts = frozen['verdict_counts']
    recommendations = authored.get('recommendations') or []
    subject = f"BackBond Starter report \u2014 case {frozen['case_id']} (revision {revision['revision_id']})"
    lines = [
        'BackBond Starter report',
        f"Revision: {revision['revision_id']}",
        f"Revision content sha256: {revision['content_sha256']}",
        f"Report input sha256: {content['input_sha256']}",
        '',
        'Summary',
        authored['summary'],
        '',
        'Recommendations',
        *([f'- {item}' for item in recommendations] or ['None recorded.']),
        '',
        'Results',
        f"Pack: {frozen['pack_version']}",
        f"Outcome: {frozen['outcome']}",
        f"Pass: {counts['pass']}",
        f"Fail: {counts['fail']}",
        f"Could not run: {counts['could_not_run']}",
        '',
        'Scope',
        'This is a single Starter scan. No comparison and no retest apply to a Starter scan.',
        '',
        'Internal release to info@backbond.ai after BAC review and CEO approval. '
        'Provider acceptance is not confirmation of inbox delivery.',
    ]
    return subject, '\n'.join(lines) + '\n'


def sync_native_decisions(release: ReportRelease, read_decision, limit: int = 1, now=None) -> list[dict]:
    """Worker step: for current reviewed revisions, read the native Lead decision bound to that
    exact digest (read_decision(scan_id, revision_id, revision_sha256) -> None | decision, raising
    when unconfirmed) and consume it. Empty, unconfirmed or conflicting reads change nothing."""
    results = []
    for item in release.awaiting_lead(limit, now):
        try:
            decision = read_decision(item['scan_id'], item['revision_id'], item['revision_sha256'])
        except Exception:
            continue
        if decision is None:
            continue
        if decision.get('revision_sha256') != item['revision_sha256'] or decision.get('scan_id') != item['scan_id']:
            continue
        try:
            results.append(release.consume_native_decision(item['revision_id'], decision))
        except ApprovalRefused as exc:
            results.append({'revision_id': item['revision_id'], 'decision': 'refused', 'reason': exc.reason})
    return results
