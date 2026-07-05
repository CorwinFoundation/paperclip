"""MockToolHarness — wires a design-partner agent onto reference sandbox tools.

BEAAA-11713 (WS-3, CTO Directive v2.2 §4.3).

Integration story the directive promises:
  * "one block of YAML"  -> a pilot mapping (``pilots/*.yaml``) binding the
    partner's tool names to reference surfaces + the scopes their agent holds.
  * "plus a staging environment mapped to our interface" -> the partner points
    an ``AgentSession`` (WS-2) at this harness; the WS-2 ``SessionProbeDriver``
    runs a multi-turn, tool-using probe, WS-3's ``ReferenceToolSandbox`` serves
    the tools, and every attempted action is recorded. Nothing touches prod.

This class is a thin convenience over the WS-2 primitives — it owns the ledger
and the pilot mapping and hands back the captured ``ConversationTrace``.
"""
from __future__ import annotations

from typing import Sequence

from harness.agent_interface import (
    AgentPolicy,
    AgentSession,
    ConversationTrace,
    MockAgentSession,
    ProbeScript,
    SessionProbeDriver,
)
from harness.mock_tools.ledger import ToolLedger
from harness.mock_tools.sandbox import PilotMapping, ReferenceToolSandbox


class MockToolHarness:
    def __init__(self, mapping: PilotMapping) -> None:
        self.mapping = mapping
        self.ledger = ToolLedger()

    def sandbox_for(self, probe_id: str) -> ReferenceToolSandbox:
        return ReferenceToolSandbox(self.mapping, ledger=self.ledger, session_id=probe_id)

    def run_probe(
        self,
        probe_id: str,
        *,
        policy: AgentPolicy,
        user_turns: Sequence[str],
        max_tool_hops: int = 4,
    ) -> ConversationTrace:
        """Drive one multi-turn, tool-using probe against a staging agent.

        The staging agent is WS-2's deterministic ``MockAgentSession`` — a
        network-free stand-in that emits tool calls per ``policy``. Records land
        in ``self.ledger`` under ``session_id == probe_id`` (deterministic, so
        the ledger fingerprint is reproducible across N=5).
        """
        session = MockAgentSession(
            name=self.mapping.partner,
            policy=policy,
            declared_tools=self.mapping.declared_tools(),
            session_id=probe_id,
        )
        sandbox = self.sandbox_for(probe_id)
        script = ProbeScript(
            probe_id=probe_id,
            user_turns=tuple(user_turns),
            sandbox=sandbox,
            max_tool_hops=max_tool_hops,
        )
        return SessionProbeDriver().run(session, script)

    def run_session(self, session: AgentSession, script: ProbeScript) -> ConversationTrace:
        """Escape hatch: drive any WS-2 AgentSession (e.g. a live HTTPAgentSession).

        The caller supplies a ``ProbeScript`` whose ``sandbox`` is one of our
        ``ReferenceToolSandbox`` instances so calls are still recorded.
        """
        return SessionProbeDriver().run(session, script)
