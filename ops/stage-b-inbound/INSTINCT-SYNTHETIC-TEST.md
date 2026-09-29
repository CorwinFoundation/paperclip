# Instinct synthetic correlation test

This test stays inside the Stage B DEMO lane. It does not establish a partnership,
customer onboarding, agent identity, credentials, or API access.

1. Founder verifies the already connected Instinct account and sending channel
   for `katzzzari@gmail.com`. No separate email OAuth step is used.
2. Observe one message at the Mailgun route and record its actual envelope/MIME
   sender. Do not change the enrolled sender or broaden an allowlist from a guessed
   address.
3. Stop if the observed sender is not the already enrolled synthetic address.
4. Send one check and its simulated forwarded confirmation with the same explicit
   line: `Check ID: BB-INSTINCT-20260929-01`. Both messages carry identical
   supported structured fields. The second message is labelled `Simulated
   confirmation: yes`; it is not described as independent merchant evidence.
5. Confirm exactly one projected receipt matches that key. Do not send additional
   messages, rotate credentials, or infer any capability beyond this observation.

The existing two user-zero receipts and `/root/backbond-stage-b-user-zero.json`
remain untouched.
