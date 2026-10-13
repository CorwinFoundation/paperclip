# Founder CI spending policy

Ari Katz's October 13, 2026 instruction is binding on every human and agent working in this repository, including CEO, CTO, engineering and QA. Company record: [BEAAA-40719](https://paperclip-release.backbond.ai/BEAAA/issues/BEAAA-40719).

- Eligible heavy tests and version-control preflight checks queue on the isolated `backbond-ci` VM. Busy, offline, stalled or backlogged capacity does not authorize automatic GitHub-hosted fallback.
- Do not re-enable paid fallback, return migrated jobs to hosted runners, disable local-CI opt-ins, or expand paid execution without Ari's explicit approval recorded for the proposed change before implementation and merge. CEO/CTO approval, green tests, urgency and approval of unrelated product work are not substitutes.
- Request approval with the specific PR/commit, reason, expected extra cost and duration. No approval means retain queue-only routing and report or recover the runner within existing authority.
- Do not weaken the CI cost-policy regression checks to bypass this rule. Existing retained hosted control, native integration, PR unit-test and image-build jobs are the baseline, not permission to expand paid usage.

See [the operating policy](docs/founder-ci-cost-policy.md). This specific Founder spending restriction overrides generic instructions to fix a blocker without asking the Founder when that fix would purchase hosted execution.
