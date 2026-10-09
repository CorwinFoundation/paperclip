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
