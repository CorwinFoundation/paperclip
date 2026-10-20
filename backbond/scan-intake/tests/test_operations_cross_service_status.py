import sys, json, os
from pathlib import Path
import pytest
operations_root = os.environ.get("OPERATIONS_TEST_ROOT")
if not operations_root:
    pytest.skip("set OPERATIONS_TEST_ROOT to the composed Operations checkout", allow_module_level=True)
sys.path[:0] = [str(Path(operations_root) / 'src'), str(Path(operations_root) / 'tests'), str(Path(__file__).parents[1] / 'server')]
from test_starter_reports import ops, auth, approved_revision, ANALYST, reviewed, lead_approved, FakeProvider, REVIEWER
from operations_client import OperationsOutbox, OperationsClient

class Response:
    status_code = 200
    def __init__(self, value): self.value = value
    def json(self): return self.value

@pytest.mark.parametrize('approve', [False, True])
def test_actual_operations_revision_status_survives_intake_restart(ops, auth, tmp_path, approve):
    revision = approved_revision(ops, auth)
    ops.release_starter_report(auth, revision['revision_id'], released_by='fixture', provider=FakeProvider('accepted'))
    path = tmp_path / 'intake.sqlite'
    outbox = OperationsOutbox(path)
    value = ops.get_starter_status(auth, 'bac-scan-1')
    outbox.db.execute("INSERT INTO operations_starter_outbox (scan_id,request_id,case_id,evidence_sha256,payload,state,operations_starter_id) VALUES (?,?,?,?,?,'acknowledged',?)", ('bac-scan-1','r','c','a'*64,json.dumps({'bac_scan_id':'bac-scan-1'}),value['result_id']))
    def forbidden(*a, **kw): raise AssertionError('no POST/send')
    client = OperationsClient('https://fixture.invalid','fixture',forbidden,lambda *a, **kw: Response(ops.get_starter_status(auth,'bac-scan-1')))
    def check(report, delivery, tick):
        nonlocal outbox
        outbox = OperationsOutbox(path)
        assert outbox.poll_status(client,now=tick) == ('bac-scan-1','recorded')
        assert outbox.public_lifecycle('bac-scan-1')['report_status'] == report
        assert outbox.public_lifecycle('bac-scan-1')['delivery_status'] == delivery
        assert outbox.flush(client, now=tick) == []
    check('approved','provider_accepted',1)
    edited = ops.create_starter_revision(auth,revision['input_id'],ANALYST,{'summary':'v2'},supersedes_revision_id=revision['revision_id'])
    check('review','not_ready',2)
    ops.review_starter_revision(auth, edited['revision_id'], reviewer_id=REVIEWER, reviewer_role='assurance_release_reviewer', approve=approve, revision_sha256=edited['artifact_sha256'])
    if approve:
        lead_approved(ops, auth, edited)
        check('approved', 'pending', 3)
    else:
        check('changes_requested','not_ready',3)
