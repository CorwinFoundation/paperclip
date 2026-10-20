"""BEAAA-42596 / BEAAA-42618: report consumer of the BAC gateway's native Lead decision read
(BAC_GATEWAY.md, GET /api/issues/<case>/starter-report-approval). Fixture responses only."""
import sys
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).parents[1] / 'server'))
from bac_intake import BACIntake, BACUnconfirmed
from report_release import ApprovalRefused, ReportRelease, sync_native_decisions
from test_report_release import ANALYST, CONTENT, LEAD, REVIEWER, nonce

SCAN, REQUEST, CASE = 's1', 'r1', 'case-1'


class Resp:
    def __init__(self, status, body):
        self.status_code, self.body = status, body
    def json(self):
        return self.body


def intake(tmp_path, responses, bound=True):
    calls = []
    def get(route, **kw):
        calls.append((route, kw))
        status, body = responses.pop(0)
        return Resp(status, body)
    bac = BACIntake(str(tmp_path / 'intake.sqlite'), base_url='https://bac.invalid', company_id='bac',
                    owner_id='owner', token='injected', post=None, get=get)
    if bound:
        bac.db.execute('INSERT INTO bac_intake VALUES (?,?,?,?)', (REQUEST, '{}', 'acknowledged', CASE))
        bac.db.execute('INSERT INTO bac_scan_bindings VALUES (?,?)', (SCAN, REQUEST))
    return bac, calls


def decision(digest, comment='cmt-1', value='approve', **over):
    return dict(dict(type='report_approval', request_id=REQUEST, case_id=CASE, scan_id=SCAN,
                     revision_sha256=digest, decision=value, id=comment, authorAgentId=LEAD), **over)


def reviewed_revision(r, content=CONTENT, i=0):
    rev = r.create_revision(SCAN, content, author_actor=ANALYST)
    r.review(rev['revision_id'], rev['revision_sha256'], REVIEWER, nonce(7000 + i))
    return rev


# --- gateway read (intake side) ---------------------------------------------------------------

def test_read_exact_route_and_single_bound_decision(tmp_path):
    d = 'a' * 64
    bac, calls = intake(tmp_path, [(200, [decision(d)])])
    assert bac.report_decision(SCAN, d) == decision(d)
    route, kw = calls[0]
    assert route == f'https://bac.invalid/api/issues/{CASE}/starter-report-approval?scan_id={SCAN}&revision_sha256={d}'
    assert kw['headers'] == {'Authorization': 'Bearer injected'} and kw['follow_redirects'] is False


def test_read_empty_is_none(tmp_path):
    bac, _ = intake(tmp_path, [(200, [])])
    assert bac.report_decision(SCAN, 'a' * 64) is None


@pytest.mark.parametrize('response', [
    (403, {'error': 'BAC operation unconfirmed or denied'}),         # gateway: duplicate/conflict
    (200, [decision('a' * 64), decision('a' * 64, comment='cmt-2')]),  # never pick a winner
    (200, [decision('a' * 64, value='reject'), decision('a' * 64, comment='cmt-2')]),
    (200, [decision('b' * 64)]),                                      # stale digest
    (200, [decision('a' * 64, scan_id='s2')]),
    (200, [decision('a' * 64, case_id='case-2')]),
    (200, [decision('a' * 64, request_id='r2')]),
    (200, [decision('a' * 64, value='approved')]),
    (200, [dict(decision('a' * 64), body_author='lead-agent')]),      # extra field
    (200, [{k: v for k, v in decision('a' * 64).items() if k != 'authorAgentId'}]),
    (200, [decision('a' * 64, id='../x')]),
    (200, {'id': 'cmt-1'}),
    (500, []),
])
def test_read_fails_closed(tmp_path, response):
    bac, _ = intake(tmp_path, [response])
    with pytest.raises(BACUnconfirmed):
        bac.report_decision(SCAN, 'a' * 64)


def test_unbound_scan_never_reads(tmp_path):
    bac, calls = intake(tmp_path, [(200, [decision('a' * 64)])], bound=False)
    with pytest.raises(BACUnconfirmed):
        bac.report_decision(SCAN, 'a' * 64)
    assert calls == []


# --- consumption (release side) ----------------------------------------------------------------

def rel(tmp_path):
    return ReportRelease(str(tmp_path / 'release.sqlite'), lead_actor_id=LEAD)


def reader(*answers):
    seen = []
    def read(scan_id, revision_id, digest):
        seen.append((scan_id, revision_id, digest))
        answer = answers[min(len(seen), len(answers)) - 1]
        if isinstance(answer, BaseException):
            raise answer
        return answer(digest) if callable(answer) else answer
    return read, seen


def test_empty_consumes_nothing(tmp_path):
    r = rel(tmp_path)
    rev = reviewed_revision(r)
    read, seen = reader(None)
    assert sync_native_decisions(r, read) == []
    assert seen == [(SCAN, rev['revision_id'], rev['revision_sha256'])]
    assert not r.released(SCAN)


def test_approve_releases_exact_revision(tmp_path):
    r = rel(tmp_path)
    rev = reviewed_revision(r)
    read, _ = reader(lambda d: decision(d))
    assert sync_native_decisions(r, read) == [
        {'revision_id': rev['revision_id'], 'revision_sha256': rev['revision_sha256'], 'decision': 'approved'}]
    assert r.released(SCAN)
    assert r.awaiting_lead() == []  # decided: no further polling


def test_reject_records_rejection_and_never_releases(tmp_path):
    r = rel(tmp_path)
    reviewed_revision(r)
    read, _ = reader(lambda d: decision(d, value='reject'))
    assert sync_native_decisions(r, read)[0]['decision'] == 'rejected'
    assert not r.released(SCAN)


def test_conflict_or_unconfirmed_read_changes_nothing(tmp_path):
    r = rel(tmp_path)
    reviewed_revision(r)
    read, _ = reader(BACUnconfirmed('decision unconfirmed'))
    assert sync_native_decisions(r, read) == []
    assert not r.released(SCAN)


def test_stale_decision_for_superseded_revision_refused(tmp_path):
    r = rel(tmp_path)
    old = reviewed_revision(r)
    new = reviewed_revision(r, dict(CONTENT, authored={'summary': 'edited', 'recommendations': []}), i=1)
    with pytest.raises(ApprovalRefused) as exc:
        r.consume_native_decision(old['revision_id'], decision(old['revision_sha256']))
    assert exc.value.reason == 'stale_revision'
    # the worker only asks about the current digest; an answer for the old digest is ignored
    read, seen = reader(decision(old['revision_sha256']))
    assert sync_native_decisions(r, read) == []
    assert seen == [(SCAN, new['revision_id'], new['revision_sha256'])]
    assert not r.released(SCAN)


def test_edit_after_native_approval_voids_it(tmp_path):
    r = rel(tmp_path)
    reviewed_revision(r)
    read, _ = reader(lambda d: decision(d))
    sync_native_decisions(r, read)
    assert r.released(SCAN)
    reviewed_revision(r, dict(CONTENT, authored={'summary': 'edited', 'recommendations': []}), i=1)
    assert not r.released(SCAN)


def test_replayed_comment_is_idempotent_for_same_revision_refused_for_another(tmp_path):
    r = rel(tmp_path)
    rev = reviewed_revision(r)
    first = r.consume_native_decision(rev['revision_id'], decision(rev['revision_sha256']))
    again = r.consume_native_decision(rev['revision_id'], decision(rev['revision_sha256']))
    assert first['decision'] == again['decision'] == 'approved' and again['replayed'] is True
    new = reviewed_revision(r, dict(CONTENT, authored={'summary': 'edited', 'recommendations': []}), i=1)
    with pytest.raises(ApprovalRefused) as exc:  # same native comment cannot approve the edit
        r.consume_native_decision(new['revision_id'], decision(new['revision_sha256']))
    assert exc.value.reason == 'nonce_replayed'
    assert not r.released(SCAN)


def test_restart_does_not_reapply_consumed_comment(tmp_path):
    r = rel(tmp_path)
    rev = reviewed_revision(r)
    r.consume_native_decision(rev['revision_id'], decision(rev['revision_sha256'], value='reject'))
    r2 = rel(tmp_path)
    assert r2.consume_native_decision(rev['revision_id'],
                                      decision(rev['revision_sha256'], value='reject'))['replayed'] is True
    assert not r2.released(SCAN)


def test_non_lead_author_refused(tmp_path):
    r = rel(tmp_path)
    rev = reviewed_revision(r)
    read, _ = reader(lambda d: decision(d, authorAgentId='someone-else'))
    assert sync_native_decisions(r, read) == [
        {'revision_id': rev['revision_id'], 'decision': 'refused', 'reason': 'wrong_actor'}]
    assert not r.released(SCAN)


def test_unreviewed_revision_is_not_polled(tmp_path):
    r = rel(tmp_path)
    r.create_revision(SCAN, CONTENT, author_actor=ANALYST)
    read, seen = reader(lambda d: decision(d))
    assert sync_native_decisions(r, read) == [] and seen == []


def test_round_robin_polling_does_not_starve(tmp_path):
    r = rel(tmp_path)
    reviewed_revision(r)
    other = r.create_revision('s2', CONTENT, author_actor=ANALYST)
    r.review(other['revision_id'], other['revision_sha256'], REVIEWER, nonce(7100))
    read, seen = reader(None)
    sync_native_decisions(r, read, now=1)
    sync_native_decisions(r, read, now=2)
    assert [s[0] for s in seen] == [SCAN, 's2']
