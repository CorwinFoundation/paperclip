"""Money-movement ledger sandbox adapter (BEAAA-11808, Gap 1).

Surface: ledger. This is the reference "payments/ledger" surface the τ-bench
money-movement wrap drives against. Unlike the ``payments`` adapter (Stripe-style
charge/refund), this models account-to-account *transfers* — the wrong-recipient
/ wrong-amount / double-send peril BackBond underwrites.

Operations:
  get_balance     read-only, no scope
  list_transfers  read-only, no scope
  transfer_funds  side-effecting (HIGH), scope ``ledger:transfer``

End-state is real in-memory state (``accounts`` + ``transfers``). A correct agent
moves the right amount to the right recipient exactly once; a wrong-recipient
agent leaves the ledger in a state that fails end-database-state verification.

Idempotency: ``transfer_funds`` honours an ``idempotency_key`` argument. A second
call with a key already seen returns the prior transfer and applies NO new state
change (``duplicate_suppressed=True``) — the ledger's protection held. This lets
the wrap distinguish a safe idempotent retry (end-state correct) from a genuine
double-spend under distinct keys (end-state wrong). Deterministic: sequence ids
are drawn from an internal counter, never a clock or RNG.
"""
from __future__ import annotations

from typing import Any, Mapping

from harness.mock_tools.base import Operation, SandboxTool
from harness.mock_tools.interface import Impact


class LedgerTool(SandboxTool):
    surface = "ledger"
    operations = {
        "get_balance": Operation("get_balance", Impact.READ_ONLY, None, "data_access"),
        "list_transfers": Operation("list_transfers", Impact.READ_ONLY, None, "data_access"),
        "transfer_funds": Operation("transfer_funds", Impact.HIGH, "ledger:transfer", "transaction"),
    }

    def _seed_state(self) -> None:
        # Deterministic opening state: one funded source account.
        self.state["accounts"] = {
            "acct_source": {"id": "acct_source", "balance_cents": 1_000_000, "currency": "usd"},
        }
        self.state["transfers"] = {}          # transfer_id -> record
        self.state["_idempotency"] = {}       # idempotency_key -> transfer_id
        self.state["_seq"] = 0

    def _execute(self, op: Operation, params: Mapping[str, Any]) -> dict[str, Any]:
        accounts = self.state["accounts"]
        transfers = self.state["transfers"]

        if op.name == "get_balance":
            acct = accounts.get(params.get("account_id", "acct_source"))
            return {"account": acct, "found": acct is not None}

        if op.name == "list_transfers":
            return {"transfers": dict(transfers)}

        if op.name == "transfer_funds":
            recipient = params.get("recipient_id")
            amount = int(params.get("amount_cents", 0))
            currency = params.get("currency", "usd")
            idem = params.get("idempotency_key")

            # Idempotency: a repeated key returns the prior transfer, no re-apply.
            if idem is not None and idem in self.state["_idempotency"]:
                tid = self.state["_idempotency"][idem]
                return {
                    "transferred": True,
                    "transfer_id": tid,
                    "duplicate_suppressed": True,
                    "transfer": transfers[tid],
                }

            tid = f"tr_{self.state['_seq']:04d}"
            self.state["_seq"] += 1
            record = {
                "id": tid,
                "recipient_id": recipient,
                "amount_cents": amount,
                "currency": currency,
                "idempotency_key": idem,
            }
            transfers[tid] = record
            src = accounts["acct_source"]
            src["balance_cents"] -= amount
            if idem is not None:
                self.state["_idempotency"][idem] = tid
            return {
                "transferred": True,
                "transfer_id": tid,
                "duplicate_suppressed": False,
                "transfer": record,
            }

        return {}
