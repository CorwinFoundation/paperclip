# Automatic Findings reports

Completed portal scans use the existing Findings Report Writer and independent
Findings Report Reviewer. The CEO coordinates their native tasks. A durable
gateway workflow binds the intake request, complete saved exchanges, evidence
archive, role identities, two review rounds and exact PDF/HTML hashes. Only a
closed independent SHIP review with passing mechanical checks releases files.
Routine FIX requests route automatically to the same Writer. Incomplete evidence
or unresolved review failures remain held; there is no legacy report fallback.

The existing owner-authenticated Scanner result route supplies `selection`
from its saved accepted request and pre-dispatch form. It exports expected
family counts, selected case IDs and integrity hashes; credentials, scenario
payloads and the private selection seed stay on Scanner. No SSH identity,
new privileged service or broader API grant is needed. Intake checks tenant
ownership and binds the record to the saved results and corpus pins before
packaging it for the agents. Missing capture evidence is recorded and retried.

For a workflow created before selection capture, an append-only supplement
preserves the original request, archive and verdict. A service-created native
evidence-recovery child wakes the CEO. Writer and Reviewer must attest the new
archive revision independently; old approvals cannot release it. Supplement
retries are idempotent and do not create another production Writer.

Intake sends the exact reviewed pair to the original request email through the
existing mail service. The request and recipient are immutable. A persisted send
intent and deterministic provider idempotency key cover retries and restarts.
Unknown sends retry with identical bytes inside the provider window; expiry
requires reconciliation. Provider acceptance is reported as acceptance, not
proof that a recipient opened the email.

Enable `BAC_FINDINGS_ENABLED=1` on the restricted gateway before enabling
`QA_FINDINGS_REPORTS=1` at intake. Set `QA_FINDINGS_REPORTS_SINCE` to the cutover
timestamp. Existing gateway and mail credentials remain protected on their
current servers; no native agent key is exposed to intake. Historical customers
are excluded. `QA_FINDINGS_REPLAY_SCAN_IDS` permits an explicitly chosen retained
internal scan for acceptance testing, without making a new Scanner request.
Remove that replay setting after verification. Intake and gateway databases need
private backups before deployment. Roll back code and mode together; never
silently downgrade an in-flight Findings report to the retired chain.
