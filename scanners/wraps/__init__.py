"""Upstream-eval-framework wraps for the customer-true Track B walker.

Each module in this package wraps ONE pinned upstream eval framework
(Inspect, PyRIT, Garak, HolisticBias, BBQ, ...) and exposes a tiny
``probe_specs()`` accessor consumed by
:mod:`scanners.customer_agent_walker`. v0.3 ships ONE wrap
(``inspect_section1_v0``) per BEAAA-5414 Q-B2; remaining families queue
as separate rule-execution wrap-tickets.

The wrap modules do NOT vendor the upstream framework. Each module
records the pinned upstream version in a module-level constant for the
follow-on wrap-ticket to integrate the real upstream runner. v0.3
exercises the IPC + walker round-trip via deterministic prompt/response
shapes; real upstream invocation is a separate gate.
"""
