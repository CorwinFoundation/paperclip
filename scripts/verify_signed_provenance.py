#!/usr/bin/env python3
"""Check signed claims AFTER cosign verifies the bundle's signature and identity.

cosign 3.1.1 slsaprovenance selects v0.2, not v1. Compare the actual DSSE
payload to the independently generated predicate and current build inputs.
"""
import argparse
import base64
import copy
import hashlib
import json
from pathlib import Path


def canonical_predicate(predicate):
    # cosign 2.4.1 emits invocationID; 3.1.1 emits invocationId.
    # Normalize only this known schema spelling, never other claims.
    result = copy.deepcopy(predicate)
    metadata = result['runDetails']['metadata']
    if 'invocationID' in metadata and 'invocationId' in metadata:
        raise ValueError('ambiguous invocation identity')
    if 'invocationId' in metadata:
        metadata['invocationID'] = metadata.pop('invocationId')
    return result


def verify(bundle, expected, *, image, source, workflow_sha, workflow_ref, run_url, sbom):
    statement = json.loads(base64.b64decode(bundle["dsseEnvelope"]["payload"], validate=True))
    if statement.get("predicateType") != "https://slsa.dev/provenance/v1":
        raise ValueError("signed predicate must use SLSA v1")
    repository, digest = image.split("@sha256:")
    if statement.get("subject") != [{"name": repository, "digest": {"sha256": digest}}]:
        raise ValueError("signed image subject differs from current image")
    predicate = statement.get("predicate")
    if not predicate or canonical_predicate(predicate) != canonical_predicate(expected):
        raise ValueError("signed predicate is empty or differs from generated predicate")
    predicate = canonical_predicate(predicate)
    definition = predicate["buildDefinition"]
    if definition["buildType"] != "https://backbond.ai/build/scanner-release-evidence@v1":
        raise ValueError("unexpected build type")
    if definition["externalParameters"] != dict(sourceSha=source, imageDigest=image, sbomSha256=hashlib.sha256(sbom).hexdigest()):
        raise ValueError("source/image/SBOM binding mismatch")
    if definition["internalParameters"] != dict(workflowSha=workflow_sha, workflowRef=workflow_ref):
        raise ValueError("workflow binding mismatch")
    if definition["resolvedDependencies"] != [{"uri": "git+https://github.com/CorwinFoundation/are-scanner", "digest": {"gitCommit": source}}]:
        raise ValueError("source dependency mismatch")
    details = predicate["runDetails"]
    if details["builder"]["id"] != workflow_ref or details["metadata"]["invocationID"] != run_url:
        raise ValueError("builder/run identity mismatch")
    return statement


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("bundle", "predicate", "sbom", "image", "source", "workflow-sha", "workflow-ref", "run-url"):
        parser.add_argument("--" + name, required=True)
    args = vars(parser.parse_args())
    bundle = json.loads(Path(args.pop("bundle")).read_text())
    expected = json.loads(Path(args.pop("predicate")).read_text())
    args["sbom"] = Path(args["sbom"]).read_bytes()
    print(json.dumps(verify(bundle, expected, **args), sort_keys=True))


if __name__ == "__main__":
    main()
