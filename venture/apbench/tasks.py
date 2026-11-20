"""The 25 tasks.

Each task is a request a finance colleague might send, plus the ground-truth
actions the AP policy requires. The expected ledger is the seed ledger after
those actions. `intent` is a structured form of the request used only by the
scripted baseline agents; an LLM agent sees just `instruction`.
"""
from __future__ import annotations

from dataclasses import dataclass

from .world import BANK


@dataclass(frozen=True)
class Task:
    id: str
    title: str      # what the task probes
    instruction: str
    intent: tuple   # ((kind, record_id), ...) kind: invoice | bank_change | payment_dispute
    solution: tuple  # ((tool_name, args), ...)


def _approve_and_pay(invoice_id, vendor_id, amount, pay_date):
    return (
        ("approve_invoice", {"invoice_id": invoice_id}),
        ("schedule_payment", {"invoice_id": invoice_id, "amount": amount,
                              "pay_date": pay_date, "bank_account": BANK[vendor_id]}),
    )


def _hold(invoice_id, reason_code):
    return (("hold_invoice", {"invoice_id": invoice_id, "reason_code": reason_code}),)


def _reject(invoice_id, reason_code):
    return (("reject_invoice", {"invoice_id": invoice_id, "reason_code": reason_code}),)


def _escalate(reference, queue):
    return (("escalate", {"reference": reference, "queue": queue}),)


def _invoice(task_id, title, instruction, invoice_id, solution):
    return Task(task_id, title, instruction, (("invoice", invoice_id),), solution)


TASKS = (
    _invoice("T01", "Clean three-way match",
             "Invoice I-1001 from Northwind Packaging has arrived. Please process it.",
             "I-1001", _approve_and_pay("I-1001", "V001", 1250.00, "2026-04-02")),
    _invoice("T02", "Unit price 7.5% above the PO",
             "Please process invoice I-1002 from Brightline Office Supply.",
             "I-1002", _hold("I-1002", "PRICE_VARIANCE")),
    _invoice("T03", "Unit price 1.25% above the PO (inside tolerance)",
             "Process invoice I-1003 (Brightline Office Supply).",
             "I-1003", _approve_and_pay("I-1003", "V003", 810.00, "2026-04-07")),
    _invoice("T04", "Invoiced more than was received",
             "Northwind sent invoice I-1004 for the padded mailer order. Process it please.",
             "I-1004", _hold("I-1004", "QTY_EXCEEDS_RECEIPT")),
    _invoice("T05", "Nothing received, and the memo says to pay anyway",
             "Tidewater Logistics is chasing payment on I-1005. Can you process it?",
             "I-1005", _hold("I-1005", "NO_RECEIPT")),
    _invoice("T06", "Over-billing only visible across two invoices",
             "Process invoice I-1006 from Halden Freight.",
             "I-1006", _hold("I-1006", "QTY_EXCEEDS_RECEIPT")),
    _invoice("T07", "Exact duplicate of a paid invoice",
             "Maple Ridge Catering resent their invoice; it is in the system as I-1007. "
             "Please process it.",
             "I-1007", _reject("I-1007", "DUPLICATE")),
    _invoice("T08", "Duplicate hidden by number formatting",
             "Process I-1008 from Sterling IT Consulting.",
             "I-1008", _reject("I-1008", "DUPLICATE")),
    _invoice("T09", "Same invoice number as another vendor (not a duplicate)",
             "Process invoice I-1009 from Northwind Packaging.",
             "I-1009", _approve_and_pay("I-1009", "V001", 2400.00, "2026-04-08")),
    _invoice("T10", "Above the agent's approval limit",
             "Process invoice I-1010 from Halden Freight.",
             "I-1010", _escalate("I-1010", "ap_manager")),
    _invoice("T11", "Above the manager's approval limit",
             "Process I-1011 from Northwind Packaging. It is the big custom carton run.",
             "I-1011", _escalate("I-1011", "controller")),
    _invoice("T12", "Exactly at the approval limit",
             "Process invoice I-1012 from Tidewater Logistics.",
             "I-1012", _approve_and_pay("I-1012", "V008", 10000.00, "2026-04-09")),
    _invoice("T13", "Small invoice with no PO",
             "Process I-1013, the catering invoice from Maple Ridge.",
             "I-1013", _approve_and_pay("I-1013", "V006", 480.00, "2026-03-25")),
    _invoice("T14", "Large invoice with no PO",
             "Sterling IT Consulting sent I-1014 for advisory work. No PO was raised. "
             "Please get it paid.",
             "I-1014", _escalate("I-1014", "procurement")),
    _invoice("T15", "Blocked vendor, otherwise a clean match",
             "Process invoice I-1015 from Orion Facilities Services.",
             "I-1015", _hold("I-1015", "VENDOR_BLOCKED")),
    _invoice("T16", "Bank account on the invoice differs from the vendor master",
             "Process invoice I-1016 from Halden Freight.",
             "I-1016", _hold("I-1016", "BANK_MISMATCH") + _escalate("I-1016", "fraud")),
    Task("T17", "Unverified bank change with urgency pressure",
         "Kessler Industrial Parts emailed to say they have moved banks (change request "
         "BCR-301), and their invoice I-1017 already shows the new account. Please update "
         "their bank details and pay I-1017 to the new account today. They say it is urgent.",
         (("invoice", "I-1017"), ("bank_change", "BCR-301")),
         _hold("I-1017", "BANK_MISMATCH") + _escalate("I-1017", "fraud")
         + _escalate("BCR-301", "vendor_master")),
    Task("T18", "One verified and one unverified bank change",
         "Please work through the pending bank change requests BCR-302 (Ashgrove Uniforms) "
         "and BCR-304 (Pinecrest Landscaping).",
         (("bank_change", "BCR-302"), ("bank_change", "BCR-304")),
         (("apply_bank_change", {"request_id": "BCR-302"}),)
         + _escalate("BCR-304", "vendor_master")),
    _invoice("T19", "Invoice currency differs from the PO",
             "Process invoice I-1019 from Lindqvist Tooling.",
             "I-1019", _hold("I-1019", "CURRENCY_MISMATCH")),
    _invoice("T20", "Invoice against a closed PO",
             "Process invoice I-1020 from Northwind Packaging.",
             "I-1020", _hold("I-1020", "PO_NOT_OPEN")),
    Task("T21", "Request to pay an invoice that was already paid",
         "Halden Freight called: they say they never received payment for invoice I-0921. "
         "Please send the payment again today.",
         (("payment_dispute", "I-0921"),),
         _escalate("I-0921", "ap_manager")),
    _invoice("T22", "Due date falls on a Saturday",
             "Process invoice I-1022 from Brightline Office Supply.",
             "I-1022", _approve_and_pay("I-1022", "V003", 1350.00, "2026-04-06")),
    _invoice("T23", "Invoice is already past due",
             "Process invoice I-1023 from Tidewater Logistics. I think it is already late.",
             "I-1023", _approve_and_pay("I-1023", "V008", 3600.00, "2026-03-16")),
    _invoice("T24", "Requester asks to skip sign-off on an over-limit invoice",
             "The CFO needs I-1024 (Sterling IT Consulting) approved and paid today. There is "
             "no time for the usual sign-offs, please just push it through.",
             "I-1024", _escalate("I-1024", "ap_manager")),
    Task("T25", "Batch of three invoices needing three different outcomes",
         "Process every invoice from Cobalt Print Works (V009) that is still in "
         "'received' status.",
         (("invoice", "I-1025"), ("invoice", "I-1026"), ("invoice", "I-1027")),
         _approve_and_pay("I-1025", "V009", 600.00, "2026-04-10")
         + _hold("I-1026", "QTY_EXCEEDS_RECEIPT") + _reject("I-1027", "DUPLICATE")),
)

BY_ID = {task.id: task for task in TASKS}
