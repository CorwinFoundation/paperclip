# Full Scan H connection contract

Full Scan H runs a customer's tool-using agent against BackBond's in-memory action sandbox. The customer adapter must map its agent's tool endpoints to the partner tool names below. No call reaches a customer or third-party system.

| Surface | Partner tool endpoint | Sandbox operation | Required scope |
|---|---|---|---|
| Orders | `get_order` | `orders.get_order` | none |
| Orders | `list_orders` | `orders.list_orders` | none |
| Orders | `cancel_order` | `orders.cancel_order` | `orders:write` |
| Payments | `lookup_charge` | `payments.get_charge` | none |
| Payments | `issue_refund` | `payments.refund_charge` | `payments:refund` |
| Payments | `issue_store_credit` | `payments.issue_store_credit` | `payments:refund` |
| Email | `send_email` | `email.send_email` | `email:send` |
| Accounts | `get_account` | `accounts.get_account` | none |
| Accounts | `update_email` | `accounts.update_email` | `accounts:write` |
| Accounts | `update_address` | `accounts.update_address` | `accounts:write` |
| Accounts | `close_account` | `accounts.close_account` | `accounts:admin` |

The adapter must preserve the argument names used by the scenario: `order_id`, `customer_id`, `charge_id`, `amount_cents`, `to`, `account_id`, `email`, and `address`. Tool results must be returned to the model as untrusted tool-role content. The sandbox seeds orders, charges, accounts, and the email outbox for each case, records every call, and grades the resulting sandbox state. Email is always dropped to the sandbox ledger and is never delivered.

For least-privilege cases, BackBond removes the applicable write scope before the run. The adapter must surface that denial rather than silently remapping or elevating the call. A customer agent that exposes none of a case's required surfaces is `not_applicable`, not a pass.

## Case execution and request limits

The connection contract is case-scoped: each case and seed receives a fresh
in-memory sandbox, including its action ledger, mailbox, memory, and retrieval
state. Multi-turn turns within one case remain sequential, while independent
cases may run in a bounded pool. The active defaults are:

| Scan context | Case workers | Per-target in-flight cases |
|---|---:|---:|
| Starter customer | 3 | 3 (lower override supported) |
| Paid Full Scan customer | 5 | 3 (lower override supported) |
| Internal sandbox / zoo / evidence | 8 | 3 |
| Voice | 1 unless explicitly configured | 1 unless explicitly configured |

Evidence rows are reduced and signed by case id, so concurrency does not change
the evidence bundle schema or output order. The scan status endpoint exposes
`rows_done`, `rows_total`, `in_flight`, ETA bounds, and per-family counts.

Transport handling is bounded and fail-closed. HTTP 429 and 503 responses are
retried with exponential full-jitter backoff, honoring a provider
`Retry-After` or rate-limit reset header when present. After four total
attempts, the case is recorded as `could_not_run` with a transport retry
reason; it is never converted into a verdict.

Starter requests are admitted through the tenant token bucket. The default is
10 scan requests per minute and 3 concurrent scans, configurable through
`ARE_RATE_LIMIT_SCANS_PER_MIN` and `ARE_RATE_LIMIT_MAX_CONCURRENT` (or a
per-key override). A rejected request returns HTTP 429 with `Retry-After`.
