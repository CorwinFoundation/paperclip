import base64
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import pytest

spec = importlib.util.spec_from_file_location('signed_provenance', Path(__file__).parents[1] / 'scripts/verify_signed_provenance.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def fixture():
    inputs = dict(image='ghcr.io/corwinfoundation/are-scanner@sha256:' + 'a' * 64, source='b' * 40, workflow_sha='c' * 40, workflow_ref='repo/workflow@refs/heads/master', run_url='https://github.com/repo/actions/runs/123', sbom=b'{"bomFormat":"CycloneDX"}')
    predicate = {'buildDefinition': {'buildType': 'https://backbond.ai/build/scanner-release-evidence@v1', 'externalParameters': dict(sourceSha=inputs['source'], imageDigest=inputs['image'], sbomSha256=hashlib.sha256(inputs['sbom']).hexdigest()), 'internalParameters': dict(workflowSha=inputs['workflow_sha'], workflowRef=inputs['workflow_ref']), 'resolvedDependencies': [{'uri': 'git+https://github.com/CorwinFoundation/are-scanner', 'digest': {'gitCommit': inputs['source']}}]}, 'runDetails': {'builder': {'id': inputs['workflow_ref']}, 'metadata': {'invocationID': inputs['run_url']}}}
    statement = {'predicateType': 'https://slsa.dev/provenance/v1', 'predicate': predicate, 'subject': [{'name': inputs['image'].split('@')[0], 'digest': {'sha256': 'a' * 64}}]}
    return inputs, predicate, statement


def bundle(statement):
    return {'dsseEnvelope': {'payload': base64.b64encode(json.dumps(statement).encode()).decode()}}


def test_actual_v1_claims_survive():
    inputs, predicate, statement = fixture()
    assert module.verify(bundle(statement), predicate, **inputs) == statement


@pytest.mark.parametrize('defect', ['empty_v02', 'empty_v1', 'dropped_field', 'subject', 'source', 'image', 'sbom', 'workflow_sha', 'workflow_ref', 'run_url'])
def test_signed_claim_loss_or_wrong_build_fails(defect):
    inputs, predicate, statement = fixture()
    expected = copy.deepcopy(predicate)
    if defect == 'empty_v02':
        statement.update(predicateType='https://slsa.dev/provenance/v0.2', predicate={})
    elif defect == 'empty_v1':
        statement['predicate'] = {}
    elif defect == 'dropped_field':
        del statement['predicate']['buildDefinition']['resolvedDependencies']
    elif defect == 'subject':
        statement['subject'][0]['digest']['sha256'] = 'd' * 64
    else:
        inputs[defect] = b'changed SBOM' if defect == 'sbom' else 'wrong-build-identity'
    with pytest.raises((ValueError, KeyError)):
        module.verify(bundle(statement), expected, **inputs)


def test_cosign_311_slsaprovenance1_recorded_output():
    # Decoded output of cosign 3.1.1 attest-blob --type slsaprovenance1,
    # using a disposable local key and no transparency-log upload.
    inputs, predicate, expected_statement = fixture()
    statement = json.loads((Path(__file__).parent / 'fixtures/cosign-3.1.1-slsaprovenance1-statement.json').read_text())
    assert statement['predicate']['runDetails']['metadata'] == {'invocationId': inputs['run_url']}
    # attest-blob names its local file; image attestation names the repository.
    # Replace only this subject wrapper; retain the actual emitted predicate.
    statement['subject'] = expected_statement['subject']
    assert module.verify(bundle(statement), predicate, **inputs) == statement


def test_ambiguous_invocation_keys_fail():
    inputs, predicate, statement = fixture()
    metadata = statement['predicate']['runDetails']['metadata']
    metadata['invocationId'] = metadata['invocationID']
    with pytest.raises((ValueError, KeyError)):
        module.verify(bundle(statement), predicate, **inputs)
