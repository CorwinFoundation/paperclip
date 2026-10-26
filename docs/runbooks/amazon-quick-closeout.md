# Amazon Quick Path C pilot closeout

BackBond operator/customer checklist | Draft | 25 October 2026

1. Stop submissions. Preserve the original run, source/image identity, provider
   receipts and consumed-call/evaluator ledgers. Reconcile every uncertain
   submission through read-only evidence; never resend it to finish a receipt.
2. Review the configuration-copy findings with AllCloud and the customer. Show
   inconclusive and unrun cases. Assign Quick-side remediation owners; any retest
   needs a new approved bounded window while preserving existing consumption.
3. Stop the AWS worker and verify its state through AWS. Customer workers and
   volumes are dedicated; remove temporary customer data and terminate/delete
   volumes according to the agreed handling plan. A stopped disk still stores
   data and incurs storage charges. The BackBond sandbox's older resources are
   separate commitments, not automatically deleted by this checklist.
4. Customer administrator removes the pilot trust/role, test-user grants,
   synthetic space/sources and test users. Delete the customer stack and optional
   forwarder/subscription/delivery; retain a log group only by explicit customer
   choice with a recorded owner and retention. Do not delete an existing shared
   delivery source/group. Remove per-client execution access when no longer needed.
5. Record that already minted Quick browser sessions expire on their own; API
   expiry does not revoke them. Close every worker browser context explicitly.
6. Reconcile AWS and evaluator costs against the approved ceiling, including
   remaining storage and incremental test-user commitments. Check trial renewal
   separately. Do not quote unreconciled charges as a fixed customer price.
7. At the agreed retention deadline (30 days by default), delete all scoped S3
   object versions and delete markers plus temporary/local copies, following the
   approved deletion procedure. Check every paginated version listing for the
   exact customer/run prefix and save a content-free proof of absence. Record
   any exception or separately agreed minimal audit retention.
8. Save closeout references: AWS stop/termination state, removed roles/grants,
   source/user cleanup, ledger reconciliation, billing review and deletion proof.
   Scope deletion exactly; other customers' prefixes and the shared source log
   group are not part of this cleanup.

Owner, planned UTC dates and evidence references must be filled before launch.
No v1.2 live pilot or closeout has been completed yet.
