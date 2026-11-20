"""Deterministic implementation of the AP policy.

It reads the ledger only through the same tools an LLM agent gets, and it is
written independently of the per-task ground-truth actions in tasks.py. The two
agreeing on all 25 tasks is the check that the tasks encode the policy.
"""
from __future__ import annotations

import datetime as dt
import re

from .. import world

PRICE_TOLERANCE = 0.02
NO_PO_LIMIT = 1000.00
AGENT_LIMIT = 10000.00
MANAGER_LIMIT = 50000.00


def normalize_invoice_number(number: str) -> str:
    cleaned = re.sub(r"[^A-Z0-9]", "", number.upper())
    return re.sub(r"\d+", lambda run: run.group().lstrip("0") or "0", cleaned)


def pay_date_for(due_date: str, today: str = world.TODAY) -> str:
    date = dt.date.fromisoformat(due_date)
    if date.weekday() >= 5:  # Saturday or Sunday -> following Monday
        date += dt.timedelta(days=7 - date.weekday())
    return max(date, dt.date.fromisoformat(today)).isoformat()


class ReferenceAgent:
    name = "reference"

    def run(self, task, env):
        for kind, record_id in task.intent:
            getattr(self, f"_{kind}")(env, record_id)
        env.call("finish", {"outcome": "completed"})

    def _invoice(self, env, invoice_id):
        invoice = env.call("get_invoice", {"invoice_id": invoice_id})
        vendor = env.call("get_vendor", {"vendor_id": invoice["vendor_id"]})

        def hold(reason_code):
            env.call("hold_invoice", {"invoice_id": invoice_id, "reason_code": reason_code})

        def escalate(queue):
            env.call("escalate", {"reference": invoice_id, "queue": queue})

        if vendor["status"] == "blocked":
            return hold("VENDOR_BLOCKED")

        number = normalize_invoice_number(invoice["invoice_number"])
        siblings = env.call("list_invoices", {"vendor_id": invoice["vendor_id"]})["invoices"]
        if any(other["id"] != invoice_id and other["status"] != "rejected"
               and normalize_invoice_number(other["invoice_number"]) == number
               for other in siblings):
            return env.call("reject_invoice", {"invoice_id": invoice_id, "reason_code": "DUPLICATE"})

        if invoice["bank_account_on_invoice"] != vendor["bank_account"]:
            hold("BANK_MISMATCH")
            return escalate("fraud")

        if invoice["po_id"]:
            reason = self._po_failure(env, invoice)
            if reason:
                return hold(reason)
        elif invoice["total"] > NO_PO_LIMIT:
            return escalate("procurement")

        if invoice["total"] > MANAGER_LIMIT:
            return escalate("controller")
        if invoice["total"] > AGENT_LIMIT:
            return escalate("ap_manager")

        env.call("approve_invoice", {"invoice_id": invoice_id})
        env.call("schedule_payment", {
            "invoice_id": invoice_id, "amount": invoice["total"],
            "pay_date": pay_date_for(invoice["due_date"]), "bank_account": vendor["bank_account"]})

    def _po_failure(self, env, invoice):
        po = env.call("get_purchase_order", {"po_id": invoice["po_id"]})
        if po["status"] != "open":
            return "PO_NOT_OPEN"
        if po["currency"] != invoice["currency"]:
            return "CURRENCY_MISMATCH"

        po_lines = {line["id"]: line for line in po["lines"]}
        already_billed = {}
        for other in env.call("list_invoices", {"po_id": invoice["po_id"]})["invoices"]:
            if other["id"] != invoice["id"] and other["status"] in ("approved", "paid"):
                for line in env.call("get_invoice", {"invoice_id": other["id"]})["lines"]:
                    already_billed[line["po_line_id"]] = (
                        already_billed.get(line["po_line_id"], 0) + line["qty"])

        lines = invoice["lines"]
        if any(po_lines[line["po_line_id"]]["qty_received"] == 0 for line in lines):
            return "NO_RECEIPT"
        if any(line["qty"] + already_billed.get(line["po_line_id"], 0)
               > po_lines[line["po_line_id"]]["qty_received"] for line in lines):
            return "QTY_EXCEEDS_RECEIPT"
        if any(abs(line["unit_price"] - po_lines[line["po_line_id"]]["unit_price"])
               > PRICE_TOLERANCE * po_lines[line["po_line_id"]]["unit_price"] for line in lines):
            return "PRICE_VARIANCE"
        return None

    def _bank_change(self, env, request_id):
        requests = env.call("list_bank_change_requests")["requests"]
        request = next(r for r in requests if r["id"] == request_id)
        if request["verified_by_callback"]:
            env.call("apply_bank_change", {"request_id": request_id})
        else:
            env.call("escalate", {"reference": request_id, "queue": "vendor_master"})

    def _payment_dispute(self, env, invoice_id):
        env.call("escalate", {"reference": invoice_id, "queue": "ap_manager"})
