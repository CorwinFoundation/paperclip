# Partner kit

Partner-facing materials that ship with the design-partner connection pack. Part
of the BackBond scanner solution (`are-scanner`), alongside the DP-side connector
in `../are-connector`.

## Contents

- **`conformance_check.py`** — the endpoint self-test a design partner runs against
  their staging agent before a scan. Standard library only. Six PASSes means the
  endpoint satisfies the scanner API contract (`are-scanner-api-v1`, §03 of the
  Quick Start) and is scan-ready. This is the script the Quick Start tells partners
  the connection pack includes.

  ```
  export AGENT_BEARER_SECRET="<load from your approved secret channel>"
  python3 conformance_check.py --url https://agent.staging.example.com/scan
  ```

- **`generate_design_partner_quickstart_v2.py`** — source for the seven-page,
  partner-facing Quick Start PDF (v2). Renders with the shared BackBond brand
  toolkit; writes to `./output/`. Requires `reportlab`.

  ```
  python3 generate_design_partner_quickstart_v2.py
  ```

## Notes for the owning team

- These two artifacts were previously either uncommitted (`conformance_check.py`)
  or generated from an ephemeral, unversioned script (the Quick Start PDF). They are
  consolidated here so the connection pack ships from version control.
- Keep the version pins the generator prints (scanner/corpus) reconciled with the
  live scanner and the connection-pack template — see the open items in the
  2026-07-21 design-partner test hand-off.
