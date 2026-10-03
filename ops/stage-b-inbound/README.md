# Stage B inbound DEMO deployment

This is a separate service on the existing BackBond development host. It does
not modify the scanner container or its release configuration.

- Public receiver: `https://scanner.backbond.ai/stage-b-demo/mailgun/mime`
- History receiver: `https://scanner.backbond.ai/stage-b-demo/history/mailgun/mime`
- Private history and support page: `https://scanner.backbond.ai/stage-b-demo/history`
- Public health: `https://scanner.backbond.ai/stage-b-demo/healthz`
- Private receipts: `https://scanner.backbond.ai/stage-b-demo/receipts?email=<enrolled-email>`
- Loopback service: `127.0.0.1:8788`
- Mailgun-only secret: `/etc/backbond/stage-b-inbound.env`, root-owned mode `0600`
- Receipt-only config: `/etc/backbond/stage-b-receipts.env`, root-owned mode `0600`
- Receipt credentials: `/var/lib/backbond-stage-b/receipt-logins.json`, service-user-owned mode `0600`
- Receipt store: `/var/lib/backbond-stage-b/inbound.sqlite3`, service-user-owned
- Unit: `backbond-stage-b-inbound.service`

The required secret is the Mailgun **HTTP Webhook Signing Key**, not an API key,
SMTP password, or domain-management credential. From a trusted checkout, run:

Outbound answers use `BACKBOND_MAILGUN_MESSAGES_URL`,
`BACKBOND_MAILGUN_API_KEY`, and `BACKBOND_MAILGUN_FROM`. Without all three, the
authenticated decision is persisted and reported as retryable but unsent. A
Mailgun response counts only as provider acceptance when its JSON body includes
a non-empty `id`; HTTP 200 alone never marks an answer sent or acknowledged.

```sh
sudo ops/stage-b-inbound/install.sh
```

The installer prompts with terminal echo disabled, writes separate restricted
receiver and receipt-service files, installs the separate services and nginx
location, then explicitly restarts both units so changed Python is loaded. It
prints the deployed commit and both main PIDs after health checks. It inserts only into the unique
`scanner.backbond.ai` TLS block, backs up the vhost, and rolls back if nginx,
service health, or the existing release-identity/SLA routes change. Re-running
it restores the prior application tree and unit files before restarting. Re-running
it preserves both secrets, receipt credentials, and the receipt database. Each update explicitly
restarts both isolated services and prints the installed runtime hash after its
health checks. The receipt service never receives the Mailgun signing key.
After public health returns 200,
configure the prepared route:

```text
match_recipient("^check@inbound\\.backbond\\.ai$")
forward("https://scanner.backbond.ai/stage-b-demo/mailgun/mime")
stop()
```

The Instinct history route is separate from the check receipt route. Add a
Mailgun route for `history+<one-time-token>@inbound.backbond.ai`:

```text
match_recipient("^history\\+[^@]+@inbound\\.backbond\\.ai$")
forward("https://scanner.backbond.ai/stage-b-demo/history/mailgun/mime")
stop()
```

The same signed Mailgun webhook authenticates transport. It does not establish
that Instinct authored a report. Each imported value starts agent-reported.
The one-time address is bound to the authenticated BackBond account, its
selected lookback, and its purpose: initial onboarding or a later support
update. The private page shows the received report, separate user corrections,
confirmation, and deletion. The continuing check and confirmation receipts
remain on `/stage-b-demo/receipts`; no BackBond approval answer is collected on
that page.

The installer generates `BB_HISTORY_ENCRYPTION_KEY_HEX` once and creates a
separate history database. `BB_HISTORY_EXTERNAL_ENABLED=0` keeps real-user
intake off. `BB_HISTORY_SYNTHETIC_ENABLED=1` permits only labeled synthetic
fixtures for acceptance testing. External intake must remain off until the
first jurisdiction, notice, permitted data origin, retention and deletion
settings have been reviewed and configured. A successful scanner image deploy
does not update these separately managed Stage B services; rerun this installer
from the approved checkout and verify its runtime receipt.

Mailgun webhook authentication proves that Mailgun delivered the POST. It does
not prove that an arbitrary `From` address belongs to the sender. The receiver
therefore continues to require a separately confirmed enrollment row before it
stores any message. Do not enable the provider route until the user-zero sender
has completed that confirmation.

For the sole synthetic user-zero, the operator begins enrollment and sends the
displayed challenge through the controlled confirmation step. After that user
returns the challenge, the operator confirms it:

```sh
sudo -u backbond-stage-b /usr/bin/python3 -m stage_b_pilot.enrollment \
  --db /var/lib/backbond-stage-b/inbound.sqlite3 begin --email user-zero@example.test
sudo -u backbond-stage-b /usr/bin/python3 -m stage_b_pilot.enrollment \
  --db /var/lib/backbond-stage-b/inbound.sqlite3 confirm \
  --email user-zero@example.test --token '<returned challenge>'
```

The same token is the synthetic user's bearer for private receipt retrieval.
This proves completion of the controlled enrollment challenge, not the origin
of every later MIME `From`; results must describe the address as enrolled, not
cryptographically verified.

Bind the already-enrolled human to the registered agent address without
resetting the enrollment or changing its token. The command prompts for the
existing enrollment token with terminal echo disabled:

```sh
sudo -u backbond-stage-b /usr/bin/python3 -m stage_b_pilot.enrollment \
  --db /var/lib/backbond-stage-b/inbound.sqlite3 set-agent-recipient \
  --email katzzzari@gmail.com --agent-email ia5p01@mail.instinct.com
```

The answer endpoint ignores any recipient supplied by a browser form or inbound
message. It reloads the registered agent address and immutable check fields from
the receipt database before constructing the relay.
