"""Bind exact registry manifest bytes to a docker-save archive (BEAAA-43364)."""
import argparse
import hashlib
import json
import re
import tarfile
from pathlib import Path


def sha(raw):
    return 'sha256:' + hashlib.sha256(raw).hexdigest()


def verify(manifest, archive, source, signed_ref, config, expected_source, expected_subject):
    if not re.fullmatch(r'[0-9a-f]{40}', source) or source != expected_source:
        raise ValueError('source mismatch')
    subject = signed_ref.rsplit('@', 1)[-1]
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', subject) or subject != expected_subject:
        raise ValueError('subject mismatch')
    if sha(manifest) != subject:
        raise ValueError('registry manifest hash mismatch')
    document = json.loads(manifest)
    if (document.get('schemaVersion') != 2 or document.get('mediaType') not in {
            'application/vnd.docker.distribution.manifest.v2+json',
            'application/vnd.oci.image.manifest.v1+json'} or 'manifests' in document):
        raise ValueError('unsupported registry manifest media shape')
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', config):
        raise ValueError('invalid config digest')
    if document['config']['digest'] != config:
        raise ValueError('registry config mismatch')
    with tarfile.open(archive, 'r:*') as saved:
        def metadata(name):
            matches = [member for member in saved.getmembers() if member.name == name]
            if len(matches) != 1 or not matches[0].isfile() or matches[0].size > 4 * 1024 * 1024:
                raise ValueError('invalid archive metadata member')
            return saved.extractfile(matches[0]).read()

        entries = json.loads(metadata('manifest.json'))
        if len(entries) != 1:
            raise ValueError('ambiguous archive')
        config_name = entries[0]['Config']
        digest = config.removeprefix('sha256:')
        if config_name not in {digest + '.json', 'blobs/sha256/' + digest}:
            raise ValueError('archive config member not allowlisted')
        config_bytes = metadata(config_name)
        if sha(config_bytes) != config:
            raise ValueError('tar config mismatch')
        if json.loads(config_bytes).get('config', {}).get('Labels', {}).get('org.opencontainers.image.revision') != source:
            raise ValueError('archive source mismatch')
    with open(archive, 'rb') as stream:
        tar_hash = 'sha256:' + hashlib.file_digest(stream, 'sha256').hexdigest()
    return dict(source=source, signed_ref=signed_ref, config=config,
                tarSHA=tar_hash, manifestSHA=subject)


def main():
    p = argparse.ArgumentParser()
    for name in ('manifest', 'archive', 'source', 'signed-ref', 'config', 'expected-source', 'expected-subject', 'receipt'):
        p.add_argument('--' + name, required=True)
    a = p.parse_args()
    result = verify(Path(a.manifest).read_bytes(), a.archive, a.source, a.signed_ref,
                    a.config, a.expected_source, a.expected_subject)
    Path(a.receipt).write_text(json.dumps(result, sort_keys=True) + '\n')


if __name__ == '__main__':
    main()
