# Amazon Quick Suite connector pilot

Internal implementation and access handoff | 25 October 2026

The `amazon_quick` transport connects the scanner to an existing Quick chat agent through AWS's registered-user embedding API and the pinned Quick Chat SDK. It submits the original test prompt and accepts an answer only from a matching AWS-delivered `CHAT_LOGS` record. There are no private console endpoints, guessed UI selectors, synthetic answer fallbacks or automatic target retries.

The implementation is available for customer qualification. Offline tests do not establish compatibility with a particular customer account. The live readiness command below must pass before an adversarial pilot. Deployment, access configuration and live qualification are still required. Initial scope is conversational behavior; native tool invocation evidence and a full underwriting assessment are outside this transport. With incomplete coverage, the overall BackBond Score is N/A.

## Customer access request

Send the following technical request to the customer's Quick/AWS administrator. This is a draft for the pilot owner to review; it has not been sent.

| Required item | Exact request | Access boundary |
| --- | --- | --- |
| Target identity | AWS account ID, AWS Region, Quick subscription/account type, namespace, agent ID/ARN and agent link. Confirm this is a Quick chat agent. | One agent and one region per registered connection. Automate jobs are a different target type. |
| Dedicated test user | A licensed Quick user with chat enabled. Share the target agent with **Viewer** permission. Share the required sandbox spaces, documents, datasets and action integrations separately. Supply the user's exact Quick ARN. | Reserve this identity exclusively for the pilot window. No simultaneous employee chat or other scan using it. Agent Owner permission is unnecessary for the runtime. |
| Embedding permission | A temporary AWS role/session allowing `quicksight:GenerateEmbedUrlForRegisteredUser` for that exact user ARN and the agreed pilot HTTPS origin. Use the [policy template](amazon-quick-pilot-runtime-policy.example.json), replacing every placeholder. | No `RegisterUser`, `CreateAgent`, `UpdateAgent`, wildcard-user embedding or AWS administrator access is required by the connector. The administrator creates and shares the user/resources. |
| Chat evidence | Enable regional Quick `CHAT_LOGS` delivery before testing. Provide a CloudWatch log-group name containing the pilot user's chat records and grant the runtime role `logs:FilterLogEvents` on that group only. | Prefer a sandbox account or a customer-controlled filtered pilot destination. Reading a shared group grants visibility to all records in it; a query filter is not an IAM boundary. The connector does not request `logs:Unmask`. |
| Transcript fidelity | Confirm the pilot destination preserves the original synthetic test input and answer text, identifiers and event time. | Masking, truncation or transformations can invalidate disclosure tests. Do not run those tests against altered evidence. Use synthetic data rather than broad access to real conversations. |
| Pilot harness origin | Agree one HTTPS URL such as `https://pilot.customer.example/quick/index.html`. Permit this origin for embedded Quick chat and host the generated static files there. | The page contains no customer credentials or permanent embed tokens. Generated URLs are supplied to the browser in memory. |
| Network path | Allow the pilot worker to reach the harness host, the generated regional Quick embed host, the reviewed Quick asset/service hosts, regional QuickSight and CloudWatch Logs APIs, and the credential provider/STS route required by the temporary role. | Give the operator an exact hostname list. Browser requests are restricted to that list and public resolved addresses. Configure the worker/network firewall explicitly; existing single-endpoint HTTP rules do not cover this path. |
| Test scope and processing | Confirm sandbox knowledge, authorized test families, pilot window, call limit, evidence retention and permitted evaluator location/provider. | Initial tools must be read-only or sandboxed. Native business-action outcomes need additional execution receipts. The current starter judge uses an external provider, so AWS-only evaluation needs separate configuration and validation. |

For permission-boundary testing, provide a second dedicated test identity with deliberately restricted access to the same seeded resources. Each identity gets its own registered connection and temporary permissions. For configuration review, the customer can provide an export of agent instructions, attached resources and connector permissions; `quicksight:DescribeAgent` and `quicksight:DescribeAgentPermissions` are optional read permissions and are not used by this runtime.

The customer administrator owns log setup, including `quicksight:AllowVendedLogDeliveryForResource` and the destination-specific CloudWatch delivery/KMS policies. These setup privileges are separate from the scanner role. [AWS log-delivery instructions](https://docs.aws.amazon.com/quick/latest/userguide/monitoring-cloudwatch-logs.html).

**Identity Center limitation:** this first transport implements `GenerateEmbedUrlForRegisteredUser` with an explicit Quick user ARN. Confirm that the customer's account can generate a `QuickChat` embedding for the dedicated identity. An SSO login or ordinary AWS permission set alone does not establish this. Accounts requiring `GenerateEmbedUrlForRegisteredUserWithIdentity` need separate qualification of identity-enhanced sessions and Quick Chat support; this connector does not silently substitute another identity. [AWS registered-user API](https://docs.aws.amazon.com/quicksight/latest/APIReference/API_GenerateEmbedUrlForRegisteredUser.html), [identity-enhanced API](https://docs.aws.amazon.com/quicksight/latest/APIReference/API_GenerateEmbedUrlForRegisteredUserWithIdentity.html).

## Operator setup

Use a dedicated Linux pilot worker with Python 3.11+, the scanner's locked runtime dependencies/Chromium, and AWS CLI v2 supporting `QuickChat` in `generate-embed-url-for-registered-user`. AWS CLI is an additional pilot-worker prerequisite; the standard scanner Docker image does not install it. Provide short-lived role credentials via the worker's normal AWS credential chain or named profile. Configure role trust for the exact execution principal; cross-account access should use the agreed external ID and temporary `sts:AssumeRole` session. Do not send AWS access keys or browser cookies in the intake form.

Build the static host from `tools/amazon-quick-pilot`:

```sh
npm ci --ignore-scripts
npm run build
npm test
```

Publish `index.html`, `pilot.js`, `amazon-quicksight-embedding-sdk-2.12.1.min.js` and `SDK-LICENSE.txt` together at the agreed HTTPS location. Do not publish `node_modules`, the target registry or AWS credentials. Serve the host without third-party analytics, with `Cache-Control: no-store` for the HTML, `Referrer-Policy: no-referrer` and `X-Content-Type-Options: nosniff`. Its CSP must permit same-origin scripts and the reviewed Quick frame destinations. Do not configure a proxy or access logger to capture embed-token URLs or chat bodies.

SDK artifact SHA-256: `efa46791b6dffbf66ffb70643a713b991200d840e14572af1d01e7786382c531`. The build verifies this checksum; the HTML uses the corresponding subresource integrity value. The host fixes the selected agent and requests private mode. New browser contexts and a durable conversation ledger enforce independent cases; private mode alone is not treated as proof of isolation.

Copy `config/amazon-quick-targets.example.json` outside the checkout into an operator-controlled location. Replace the placeholders, assign a pilot ID and expiry, and set `approved` to true only after the customer scope and evaluator boundary have been reviewed. Generate a random token of at least 32 characters, store only its SHA-256 in the registry, and deliver the token through the agreed secret channel. The intake bearer token authorizes this registered pilot; it is not an AWS credential and never enters the browser or an AWS call.

Set these variables for the worker/service account:

```sh
ARE_AMAZON_QUICK_ENABLED=1
ARE_AMAZON_QUICK_TARGETS_FILE=/etc/backbond/quick-targets.json
ARE_AMAZON_QUICK_STATE_DIR=/var/lib/backbond/quick-pilot
```

Create the state directory in advance with access restricted to that service account. Protect the registry and AWS profile likewise. The SQLite ledger persists call consumption, halted pilots, exclusive test-identity leases and hashed conversation/message identities. Preserve it across process restarts and deployments. Use one worker/state directory per identity; separate machines with independent state cannot enforce mutual exclusion. A pilot ID must not be changed to bypass a consumed budget or halted run.

The browser hostname list is exact and contains no wildcard. Begin with the harness and expected regional embed host; qualify required AWS asset hosts without submitting adversarial tests and then register the reviewed names. Requests to missing hosts, private-address resolutions, plaintext destinations, unexpected redirects or an unrecognized embed experience fail. AWS CLI calls use region-bound service operations and the operator credential chain. Maintain the worker's network firewall independently; do not disable the scanner's global egress controls.

Keep the worker clock synchronized. Evidence matching requires account, user, agent, exact prompt and a bounded event-time window; it rejects ambiguous matches and conflicts. Within a multi-turn case the conversation must remain the same. Across independent cases it must be new. The dedicated-user requirement is necessary because the SDK does not return a chat message ID to correlate directly.

## Qualification before scanning

The connector uses logs as its answer source. Log delivery must complete inside the configured per-case deadline: 120 seconds by default, configurable from 10 to 300. If real delivery takes longer, the pilot is blocked pending a different evidence path. No runtime compatibility or log-latency claim has been established without a customer trial.

The harness accepts the SDK's `EXPERIENCE_INITIALIZED` or `CONTENT_LOADED` message to establish its channel. A BackBond sandbox check on 25 October 2026 observed initialization without `CONTENT_LOADED`; requiring only the latter prevented mounting an otherwise rendered chat. Initialization is not an agent answer. Every submitted turn still requires the matching `CHAT_LOGS` record, and unknown submissions halt the pilot without replay.

The first BackBond synthetic readiness run on that date completed nine exact reply/evidence checks, then stopped with the earlier generic `aws_operation_failed` error. Read-only reconciliation found ten successful exact replies in ten distinct AWS conversations, including all nine receipt replies. That locates the failed operation in final evidence retrieval; its underlying AWS error remains unknown. The original receipt remains incomplete. Subsequent code distinguishes fixed `embed` and `chat_logs` phases and bounded error categories without recording CLI stderr, credentials or URLs. A transient or unclassified failed log command can retry at most twice, with backoff inside the existing case deadline; prompts and embedding requests are never retried. Denied access, invalid evidence and exhausted read retries still halt persistently. Successful receipts include the log-read retry count.

A separately authorized fresh run of runtime commit `a22e9c9749773986183e48852ee0248d0da58b1e` completed all **ten** exact marker/evidence checks and returned `transport_verified` on 25 October 2026. It preserved the original receipt and all 16 previously consumed calls, recorded operator reconciliation, and allowed only ten additional calls. AWS SSM command `d5c4c3d1-1240-4fd9-93b5-66fcd311409d` exited successfully. This establishes transport operation in BackBond's isolated IAM/Quick-user sandbox, not customer-account compatibility, adversarial coverage or a BackBond Score. Each customer must still qualify its own identity, embedding and log-delivery path. Preserve existing evidence before authorizing recovery; never replay an uncertain attempt or reset consumed calls.

First validate local registration. This command performs no AWS or browser calls:

```sh
python -m tools.amazon_quick_pilot --endpoint https://pilot.customer.example/quick/index.html --receipt /secure/quick-config.json
```

Supply the pilot token through the `AMAZON_QUICK_PILOT_TOKEN` environment variable or the secret source used by the service. Then run ten distinct benign prompts explicitly:

```sh
python -m tools.amazon_quick_pilot --live --endpoint https://pilot.customer.example/quick/index.html --receipt /secure/quick-live-readiness.json
```

The receipt path must be new. The command writes status and hashes incrementally, never a transcript, credential or embed URL. Each benign prompt requests an exact marker; a mismatch stops qualification. These calls consume the durable target-call budget, as do intake connection checks. The example limit of 20 is for initial transport qualification, not a 100-case scan. Account for all readiness, connection-check and multi-turn sends before setting a larger customer-approved limit.

Every failure after a reserved submission halts that pilot ID persistently. Inspect customer evidence and reconcile the uncertain attempt before authorizing recovery. Do not retry an unknown submission. Lease expiry after a crashed process does not clear a halt or restore a consumed call. The connector never grades SDK acknowledgements, absent answers, blocked requests or partial conversations as passes.

After qualification, use the normal intake connection check, consent, proof, quota and scan submission path. Select **Amazon Quick Suite (pilot)**, enter the registered harness URL and use the pilot token as Bearer authentication. Agent instructions, model selection and request templates cannot be overridden through this connection type. The connection-check HTTP path allows up to 320 seconds; deploy ingress/proxy timeouts that cover it. Existing scan deadlines, corpus, judges, admission and scoring policy remain in force. Measure throughput before choosing a full pack.

`starter_result_rows` carries only successful correlated replies for grading and bounded redacted report rows. Failed cases have no fabricated reply. `transport_evidence` contains hashed conversation/message/answer identities and provider timestamps. The standalone readiness receipt is evidence of transport qualification, not a BackBond Score.

## Rollback and known limits

Disable `ARE_AMAZON_QUICK_ENABLED`, revoke the pilot token/registration and expire the temporary AWS role credentials. Stop any active worker before revoking in-flight sessions. Preserve the ledger and approved evidence for reconciliation. No CI routing changes are part of this connector; eligible heavy tests and preflight checks queue on the existing isolated VM under the Founder cost policy.

There is no Automate Job API adapter, native Quick tool-call interception, business-action attestation or Identity Center trusted-propagation implementation in this change. The pilot needs a sandbox and customer log access. Data location and evaluator policy must be resolved before real customer content is used.

Dependency note: `npm audit` reports the upstream SDK's UUID dependency under [GHSA-w5hq-g745-h8pq](https://github.com/advisories/GHSA-w5hq-g745-h8pq). The advisory concerns the v3/v5/v6 buffer APIs. The inspected, checksum-pinned browser artifact's source map contains UUID rng/stringify/native/v4 modules only; affected APIs are absent. The build checks that module boundary. This is a scoped reachability assessment, not a clean dependency audit; reassess any SDK artifact update. Do not use `npm audit fix --force` to downgrade away from Quick Chat support.
