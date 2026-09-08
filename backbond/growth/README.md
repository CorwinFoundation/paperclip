# backbond/growth

Home for BackBond growth and distribution working files that need to be readable and writable by agents on the VM.

Created under BEAAA-30615 so that BEAAA-29869 (Agent Scan v1.2 WO-4, Friday numbers) has a real place to commit a script and append a weekly row. It sits beside `backbond/architecture/` and `backbond/operations/` in the same repository, so the paths WO-4 already names resolve from the repository root with no rewrite:

- `backbond/growth/scripts/` — scripts that produce or check growth numbers.
- `backbond/growth/agent-scan-scoreboard.md` — the weekly Agent Scan scoreboard.

## Working here

Ordinary repository rules apply. Branch, commit, open a PR, merge when CI is green.

Nothing in this directory is external copy. A file here that later becomes customer-facing goes through the normal external-release path before it is sent or published.

## What does not live here

The Agent Scan launch pack at `C:/Users/katza/Cluade/IFA/backbond/growth/agent-scan-launch-2026-10-06/` is on the Founder workstation and has not been read from the VM. BEAAA-30615 created the destination only. Moving that pack across is a separate handoff and is not implied by this directory existing.
