# Stage A synthetic purchase adapter

Run from the repository root:

```sh
python3 -m stage_a_demo --db /tmp/stage-a-demo.sqlite3
python3 -m unittest tests.test_stage_a_demo -v
```

Run the local consumer UI:

```sh
python3 -m stage_a_demo.ui --port 8788 --db /tmp/stage-a-demo-ui.sqlite3
```

Open `http://127.0.0.1:8788`. The five purchase and claim fixtures execute
through the human approval authority, synthetic merchant adapter, and durable
SQLite evidence store. The selector also exposes unsupported, disconnected,
expired approval, denied consent, and generic UI-only states. The UI is
local-only and synthetic.

Every emitted record is server-labeled `DEMO`. SQLite provides restart-safe local persistence. The reference adapter is synthetic and does not access payment, reimbursement, protection, policy, or public-sharing systems.

Seams:

- `SyntheticMerchantAdapter` is the boundary for a future supported purchase adapter.
- `EvidenceStore.packet` is the evidence-reader and claim-reconstruction boundary.
- `claims_review.source_links` separates mandate, human approval, agent assertion, and independent merchant evidence.
- `HumanApprovalIssuer` stays on the human side of the seam. The execution path receives only an `ApprovalVerifier`, which has no minting or approval capability.
- Consumer authorization is the immutable mandate plus approval token. Passive Monitor observation is not represented as authorization.

## Protection activation gate (BEAAA-35012)

`stage_a_demo/activation.py` holds the single activation check. Protection is
active only when all six records validate for the requested program version:
governing legal structure and jurisdiction, executed backing authority, funded
financial security, approved versioned terms, named human claims reviewer and
payment approver, and the approved consumer disclosure set. Missing, malformed,
expired, or wrong-program-version records are not active, and an error anywhere
in the check is not active.

Every surface that can state protection status - screen banner, receipt packet,
`/api/receipt` response, referral/invitation transition - reads from that check.
None of them takes a scan result, receipt, policy id, or subscription as input,
so none can infer coverage from one. The customer string when protection is not
active is defined once, in `activation.py`.

The free period starts only on an activation record: interest-list signup, a
failed connection, and a demo completion return no free period. The 30-day
default is an illustrative planning input, not an approved benefit.

Records are read from `$BACKBOND_ACTIVATION_RECORDS/<program_version>.json`.
No directory means not active, which is the demo's state.
