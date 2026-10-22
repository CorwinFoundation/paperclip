"""Service-only fixed-price REST transport for a controlled permit.

No policy is loaded from HTTP, endpoint_config, or the environment here. The
service must establish a complete per-request price ceiling (including tools
and downstream work) for the exact target before constructing this adapter.
Token-priced or otherwise unbounded targets have no supported policy yet.
"""
from dataclasses import dataclass
import json

from harness.controlled_permit_loader import AdmissionClosed, execute_preflight, fingerprint
from harness.customer_runner import _CustomerAgentHttpRunner


@dataclass(frozen=True)
class FixedRequestPrice:
    target_sha256: str
    upper_microusd: int

    def validate(self, payload):
        if (self.target_sha256 != fingerprint(payload)
                or type(self.upper_microusd) is not int
                or not 0 <= self.upper_microusd <= 10_000_000):
            raise AdmissionClosed('Complete exact-target fixed request price required')


class ControlledTargetTransport:
    """One ledger operation wraps one base REST runner request, without retries.

    This adapter deliberately excludes subclasses, conversation/tool adapters,
    and alternate protocols. It constructs its
    own base runner from the same full payload used for permit validation.
    """
    def __init__(self, *, payload, price):
        if type(price) is not FixedRequestPrice:
            raise AdmissionClosed('Service-owned fixed request price required')
        price.validate(payload)
        endpoint = payload.get('agent_endpoint')
        if (not isinstance(endpoint, dict) or not {'url'} <= set(endpoint)
                or endpoint.get('transport', 'rest') != 'rest'
                or set(endpoint) - {'url', 'transport', 'auth'}
                or not isinstance(endpoint['url'], str)
                or not isinstance(payload.get('credential_secret'), str)):
            raise AdmissionClosed('Only bounded REST prompt transport supported')
        self._price = price
        self._runner = _CustomerAgentHttpRunner(endpoint['url'], payload['credential_secret'], dict(endpoint))
        self._target_sha256 = price.target_sha256

    def _operation(self, prompt):
        if not isinstance(prompt, str) or len(prompt.encode('utf-8')) > 65_536:
            raise AdmissionClosed('Controlled target input exceeds bound')
        return lambda: _CustomerAgentHttpRunner.complete(self._runner, prompt)

    def complete(self, permit, *, test_id, phase, prompt):
        if permit.admission.binding['target'] != self._target_sha256:
            raise AdmissionClosed('Controlled target mismatch')
        return permit.call(test_id=test_id, phase=phase,
                           upper_microusd=self._price.upper_microusd,
                           operation=self._operation(prompt))

    def complete_turn(self, permit, *, test_id, turn_index, messages):
        if (type(turn_index) is not int or turn_index < 0
                or not isinstance(messages, list) or not messages
                or any(not isinstance(m, dict) or set(m) != {'role', 'content'}
                       or m['role'] not in {'system', 'user', 'assistant'}
                       or not isinstance(m['content'], str) for m in messages)
                or len(json.dumps(messages).encode('utf-8')) > 65_536):
            raise AdmissionClosed('Bounded controlled conversation required')
        # The base runner performs exactly one request; no conversation adapter
        # or tool dispatcher is invoked. Always clear the temporary payload.
        self._runner._messages_payload = {'messages': [dict(m) for m in messages]}
        try:
            return self.complete(permit, test_id=test_id,
                                 phase=f'target-turn-{turn_index}', prompt='')
        finally:
            self._runner._messages_payload = None

    def preflight(self, opaque_id, *, tenant_id, payload, prompt):
        self._price.validate(payload)
        return execute_preflight(opaque_id, tenant_id=tenant_id, payload=payload,
                                 upper_microusd=self._price.upper_microusd,
                                 operation=self._operation(prompt))
