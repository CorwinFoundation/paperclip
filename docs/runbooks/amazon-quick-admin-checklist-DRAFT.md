# Quick Path C: customer administrator checklist

BackBond draft | 25 October 2026 | Synthetic configuration copy

- [ ] **Owners and scope:** name the customer, AllCloud lead, AWS/Quick admin
  and security/privacy owner. Approve the separate sandbox account, actual
  region, native registered users, test window, cost/call limits and synthetic-only
  scope. The report will not attest to production permissions or SSO/device policy.
- [ ] **Reviewed export:** obtain DescribeAgent in the customer's environment;
  remove real personal information, credentials and secrets from free text before
  sharing. Approve the exact export hash and permitted redactions. Recreate
  instructions and relevant settings; replace every real space with synthetic
  sources and leave real action connectors disabled. Record model/configuration
  differences and attest to the resulting copy.
- [ ] **Three users:** create dedicated chat-capable licensed identities with
  Viewer access to the same sandbox agent. HR/finance roles: employee, Sales
  manager, HR. Healthcare roles: ward clinician, operations, HR/payroll. No humans
  share these identities during the pilot.
- [ ] **Synthetic sources:** install only the exported sources and verify hashes,
  indexed resource IDs and source versions. Use structured datasets/topics for
  roster CSVs. Apply the installation manifest's source/space/knowledge-base
  grants and row rules. Healthcare: clinician sees only its ward's patient rows;
  operations and HR/payroll see no patient rows; HR/payroll alone sees staff pay.
  Keep scanner code, case profile and expected answers out of the indexes.
- [ ] **Scoped access:** deploy the customer role/logging template using the exact
  per-client BackBond execution-role ARN, ExternalId, three user ARNs, HTTPS origin
  and UTC cutoff. Runtime access is embedding plus read-only pilot-log queries.
- [ ] **Log isolation:** configure delivery before testing. Account-wide direct
  delivery is suitable only for a dedicated synthetic sandbox. Otherwise use
  the optional three-user/one-agent subscription and Lambda forwarder. Verify
  `user_arn` from actual CHAT_LOGS, original message preservation, and separate
  source/destination groups. Approve the deployed configuration and unfiltered,
  untruncated three-user audit; BackBond must not read a shared group.
- [ ] **Handling and qualification:** approve location, named readers, 30-day
  retention, deletion owner/deadline and unexpected-real-data procedure. External
  judging is off unless separately approved. Supply setup screenshots and source
  evidence; BackBond completes bound readiness/preflight and bounded qualification.
- [ ] **Closeout:** review proposed fixes, scope any retest, remove test access,
  users and sources, verify worker shutdown, and obtain version-deletion proof
  at the agreed retention deadline.

Timing is confirmed after intake and sandbox checks. This draft checklist has not
yet been exercised end-to-end for v1.2. Use the full Path C admin runbook for
commands, configuration differences and release evidence.
