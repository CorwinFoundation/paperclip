# Stage B instrumented pilot

A controlled reference agent adapter that stands in for a personal purchase or
booking agent, instrumented end to end from delegation to receipt.

```sh
python3 -m stage_b_pilot --db /tmp/stage-b-pilot.sqlite3
python3 -m stage_b_pilot --counts-only --db /tmp/stage-b-pilot.sqlite3
python3 -m unittest tests.test_stage_b_pilot -v
```

Synthetic only: no real purchase, no live money, no third-party network call,
no live coverage.

## Inbound check mailbox

`stage_b_pilot.inbound.InboundStore` is the provider-neutral ingestion seam for
`check@inbound.backbond.ai`. It accepts provider-delivered RFC 5322 bytes; it
does not use mailbox OAuth or read a user's mailbox. A sender is eligible only
after `begin_signup(email)` followed by `confirm_signup(email, token)`.
Confirmed senders may submit a message titled `BackBond check` or forward one
as `Fwd: BackBond check`. Each message is stored once as a DEMO receipt, keyed
by the exact message digest. Run the focused acceptance tests with:

```sh
python3 -m pytest -q tests/test_beaaa36076_inbound_receipts.py
```

## The seam

    task intent -> versioned mandate -> human approval -> mandate check ->
    simulated transaction -> task receipt -> protection state -> covered-event
    candidate on a seeded failure

`stage_b_pilot/agent.py` is the reference agent. It is deterministic and
seeded; `deviation` is how the covered-mistake scenario is produced. The agent
sits outside our enforcement boundary on purpose, so `check_mandate` observes
and records a deviation rather than blocking one. A third-party consumer agent
is something we detect after the fact, and that is the case the program has to
be able to price.

Execution, evidence and idempotency come from `stage_a_demo.core.EvidenceStore`,
so the pilot inherits the Stage A reserve-before-side-effect ordering rather
than reimplementing it.

## Protection status

`stage_b_pilot/activation_view.py` is the only route to protection status; it
re-exports the BEAAA-35012 gate. No module here accepts a scan result, receipt,
policy id or subscription, so none of them can infer coverage from one. With no
activation records present the gate is not active, which is the pilot's state.

## Truthful states

Every state the user is shown goes through `Telemetry.show`, which raises
`UntruthfulStateError` on a mode outside `DEMO`/`PILOT` or on any statement
that implies coverage while the gate is inactive. `LIVE` is not reachable.

## Program rules

`stage_b_pilot/program.py` is the executable restatement of the eligibility,
covered-event and limit requirements from BEAAA-34391 and the packet
separation rules from BEAAA-34392. Both issues closed before their JSON
artifacts reached the repository, so this module is where the pilot reads
program rules from. Two rules are load-bearing and asserted by the tests:
assessment eligibility is not claim entitlement, and an agent deviating from
its mandate cannot automatically deny the mistake being considered for
coverage.

Execution state, coverage decision and payment status are three separate
fields. Payment status is `no_payment_authorized` throughout; the pilot has no
payment rail and no payment authority.

## What the pilot does not cover

One task type (`purchase`) against one synthetic merchant. No partner agent, no
real merchant evidence, no payment or reimbursement rail, no human claims
reviewer in the loop, and no active protection program.
