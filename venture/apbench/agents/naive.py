"""An obedient agent with no controls: it does what the requester asks.

It exists to show that the verifier separates policy-following behaviour from
plausible-looking behaviour. It should pass only the tasks where the request
and the policy happen to agree.
"""
from __future__ import annotations

from .. import world


class NaiveAgent:
    name = "naive"

    def run(self, task, env):
        for kind, record_id in task.intent:
            if kind == "invoice":
                invoice = env.call("get_invoice", {"invoice_id": record_id})
                env.call("approve_invoice", {"invoice_id": record_id})
                env.call("schedule_payment", {
                    "invoice_id": record_id, "amount": invoice["total"],
                    "pay_date": invoice["due_date"],
                    "bank_account": invoice["bank_account_on_invoice"]})
            elif kind == "bank_change":
                env.call("apply_bank_change", {"request_id": record_id})
            elif kind == "payment_dispute":
                invoice = env.call("get_invoice", {"invoice_id": record_id})
                env.call("schedule_payment", {
                    "invoice_id": record_id, "amount": invoice["total"],
                    "pay_date": world.TODAY,
                    "bank_account": invoice["bank_account_on_invoice"]})
        env.call("finish", {"outcome": "completed"})
