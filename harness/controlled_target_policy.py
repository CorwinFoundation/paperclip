"""Explicit service-owned policy loading; no environment or request defaults.

A missing binding remains closed. The digest must come from service configuration,
not from the request or the policy itself. Loading never enables a permit.
"""
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import stat

from harness.controlled_permit_loader import AdmissionClosed
from harness.controlled_target_transport import ControlledTargetTransport, FixedRequestPrice


def load_fixed_handler_transport(*, path, expected_sha256, payload):
    """Load an exact fixed-handler ceiling including all downstream work.

    Generic hostname policies and token-priced upstream handlers are unsupported.
    A policy is an operator-provided fact, not inferred from endpoint reachability.
    """
    fd = None
    try:
        if (not isinstance(expected_sha256, str)
                or not re.fullmatch('[0-9a-f]{64}', expected_sha256)):
            raise AdmissionClosed('Service-owned policy digest required')
        path = Path(path)
        if not path.is_absolute():
            raise AdmissionClosed('Absolute policy path required')
        parent = path.parent.lstat()
        if (not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.geteuid()
                or parent.st_mode & 0o077):
            raise AdmissionClosed('Private service-owned policy directory required')
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or info.st_mode & 0o077 or info.st_size > 4096):
            raise AdmissionClosed('Private bounded service-owned policy required')
        raw = os.read(fd, 4097)
        if len(raw) > 4096 or not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), expected_sha256):
            raise AdmissionClosed('Fixed-handler policy digest mismatch')
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise AdmissionClosed('Duplicate policy field')
                result[key] = value
            return result
        policy = json.loads(raw, object_pairs_hook=unique)
        if (not isinstance(policy, dict) or set(policy) != {
                'version', 'kind', 'target_sha256', 'upper_microusd'}
                or type(policy['version']) is not int or policy['version'] != 1
                or policy['kind'] != 'exact-fixed-handler-complete-request'):
            raise AdmissionClosed('Exact complete-request fixed-handler policy required')
        return ControlledTargetTransport(payload=payload, price=FixedRequestPrice(
            policy['target_sha256'], policy['upper_microusd']))
    except (OSError, ValueError, TypeError) as exc:
        raise AdmissionClosed('Fixed-handler policy unavailable') from exc
    finally:
        if fd is not None:
            os.close(fd)


def load_bound_target_transport(config, *, permit, secret, endpoint, payload=None):
    """Bind explicit service configuration to the validated permit and vault secret.

    The complete original payload stays service-owned; it is never reconstructed
    from normalized request fields or accepted from endpoint configuration.
    """
    from harness.controlled_permit_loader import BoundScannerPermit, fingerprint
    if (type(permit) is not BoundScannerPermit or not isinstance(config, dict)
            or set(config) != {'path', 'expected_sha256'}):
        raise AdmissionClosed('Service-owned controlled target configuration required')
    if (not isinstance(payload, dict)
            or payload.get('credential_secret') != secret
            or not isinstance(payload.get('agent_endpoint'), dict)
            or payload['agent_endpoint'].get('url') != endpoint
            or fingerprint(payload) != permit.admission.binding['target']):
        raise AdmissionClosed('Controlled service payload mismatch')
    return load_fixed_handler_transport(path=config['path'],
        expected_sha256=config['expected_sha256'], payload=payload)


def startup_target_config(endpoint_config):
    """Read protected service configuration only for an API-validated descriptor.

    This loads no permit and never arms state. Ordinary scans ignore the setting.
    Only non-secret policy references are read. Execution context stays in RAM.
    """
    if not isinstance(endpoint_config, dict) or '_controlled_permit' not in endpoint_config:
        return None
    value = os.environ.get('ARE_CONTROLLED_TARGET_CONFIG')
    fd = None
    try:
        if not value or not Path(value).is_absolute():
            raise AdmissionClosed('Controlled target startup configuration required')
        path = Path(value)
        parent = path.parent.lstat()
        if (not stat.S_ISDIR(parent.st_mode) or stat.S_ISLNK(parent.st_mode)
                or parent.st_uid != os.geteuid() or parent.st_mode & 0o077):
            raise AdmissionClosed('Private service configuration directory required')
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or info.st_mode & 0o077 or info.st_size > 65536):
            raise AdmissionClosed('Private bounded startup configuration required')
        raw = os.read(fd, 65537)
        def unique(pairs):
            result = {}
            for key, item in pairs:
                if key in result:
                    raise AdmissionClosed('Duplicate startup configuration field')
                result[key] = item
            return result
        config = json.loads(raw, object_pairs_hook=unique)
        if (not isinstance(config, dict)
                or set(config) != {'path', 'expected_sha256'}):
            raise AdmissionClosed('Invalid controlled startup configuration')
        return config
    except (OSError, ValueError, TypeError) as exc:
        raise AdmissionClosed('Controlled startup configuration unavailable') from exc
    finally:
        if fd is not None:
            os.close(fd)
