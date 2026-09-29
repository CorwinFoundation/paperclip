# Stage B inbound DEMO deployment

This is a separate service on the existing BackBond development host. It does
not modify the scanner container or its release configuration.

- Public receiver: `https://scanner.backbond.ai/stage-b-demo/mailgun/mime`
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
