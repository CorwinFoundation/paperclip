---
owner: CTO
artifact_type: audit
status: review
updated_at: 2026-08-09
source_refs:
  - BEAAA-21591
  - origin/master@691d7cf72019d63d38978d93749c2ca3a96bbc15
claim_or_decision: Remove only exact same-SHA duplicate branch names under Founder authorization and preserve every removed name as an archive tag.
verification: Remote tag SHAs were checked before deletion; a post-fetch scan found no duplicate remote branch heads and 205 retained branches.
approvals:
  - Founder directive BEAAA-21591 pre-authorized exact same-SHA duplicate removal.
version: 1.0
---

# Branch dedupe execution report

## Result

Four duplicate branch names were archive-tagged at their original immutable heads and then deleted. The previously known `agent/beaaa-21043-register` group had already been archive-tagged and removed before this pass, so no action was repeated.

|Kept branch|Removed duplicate|Head SHA|Archive tag|
|---|---|---|---|
|agent/beaaa-15758-wave0-gate|beaaa-16605-wave0-gate-candidate|57324e8eaeaa1da2c7d112d2b6c58e94978d252d|archive/beaaa-16605-wave0-gate-candidate|
|agent/beaaa-19316-internal-canary-rotation|agent/beaaa-19570-immutable-replacement|67755e0724f722acef189cfb9ef3e331f8f41dbc|archive/agent/beaaa-19570-immutable-replacement|
|beaaa-16713-approval-provenance|beaaa-16713-approval-provenance-candidate|afecd4859890ac3fd6472f24a0527bb578c72f95|archive/beaaa-16713-approval-provenance-candidate|
|beaaa-18825-evidence-receipt-projection|beaaa-18825-rebase-6000dd7|c6fc74fc906e95e582a9082d2039e2776b34035a|archive/beaaa-18825-rebase-6000dd7|

## Controls and rollback

Master and all open pull-request heads were excluded. Each archive tag was verified at the branch head before its duplicate name was deleted. A removed name can be restored with `git push origin refs/tags/archive/<branch>:refs/heads/<branch>`.

## CTO self-audit

This audit creates a retrievable evidence trail for the Founder and repository maintainers. It does not alter BackBond Scanner scoring, corpus, models, seeds, API fields, customer data handling, or release identity. The Supreme Law, glossary, Carrier Rule, and current protection-path status are unaffected. No LLM judge or score appears here. The next action is Founder review of the separate equivalence manifest; no content-equivalent deletion is authorized by this report.
