"""The tool surface an agent uses to read and change the ledger.

The tools are deliberately permissive. Like a ledger API with broad permissions,
they check that records exist but do not enforce the AP policy: an agent can pay
an unapproved invoice or apply an unverified bank change. Whether the policy was
followed is decided afterwards, from the ledger state, by verify.py.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

from . import world

HOLD_REASONS = ["VENDOR_BLOCKED", "BANK_MISMATCH", "PO_NOT_OPEN", "CURRENCY_MISMATCH",
                "NO_RECEIPT", "QTY_EXCEEDS_RECEIPT", "PRICE_VARIANCE", "OTHER"]
REJECT_REASONS = ["DUPLICATE", "OTHER"]
QUEUES = ["ap_manager", "controller", "procurement", "fraud", "vendor_master"]
OUTCOMES = ["completed", "unable"]
POLICY_TEXT = (Path(__file__).parent / "policy.md").read_text(encoding="utf-8")

TOOLS = {}   # name -> function(env, **args)
SPECS = []   # tool definitions in the JSON-schema shape LLM tool-use APIs take


class InfraError(Exception):
    """The model could not be reached (bad key, quota, network). Never scored as a task result."""


class ToolError(Exception):
    """A problem the agent caused and should be told about."""


def tool(description, properties=None, required=()):
    def register(fn):
        TOOLS[fn.__name__] = fn
        SPECS.append({
            "name": fn.__name__,
            "description": description,
            "input_schema": {"type": "object", "properties": properties or {},
                             "required": list(required)},
        })
        return fn
    return register


class Env:
    """One fresh ledger plus a record of every tool call made against it."""

    def __init__(self):
        self.conn = world.build()
        self.trajectory = []
        self.finished = None  # {"outcome": ..., "summary": ...} once finish is called

    def call(self, name, args=None):
        args = dict(args or {})
        try:
            if name not in TOOLS:
                raise ToolError(f"unknown tool: {name}")
            result = TOOLS[name](self, **args)
        except ToolError as exc:
            result = {"error": str(exc)}
        except TypeError as exc:  # missing or unexpected argument names
            result = {"error": f"bad arguments for {name}: {exc}"}
        self.conn.commit()
        self.trajectory.append({"tool": name, "args": args, "result": result})
        return result

    def close(self):
        self.conn.close()

    def one(self, sql, params, what):
        row = self.conn.execute(sql, params).fetchone()
        if row is None:
            raise ToolError(f"{what} not found")
        return dict(row)

    def rows(self, sql, params=()):
        return [dict(row) for row in self.conn.execute(sql, params)]


def _choice(value, allowed, what):
    if value not in allowed:
        raise ToolError(f"{what} must be one of {allowed}")
    return value


_ID = {"type": "string"}

# --- read tools --------------------------------------------------------------

@tool("Return the accounts-payable policy and today's date.")
def get_policy(env):
    return {"today": world.TODAY, "policy": POLICY_TEXT}


@tool("Return one invoice with its lines.", {"invoice_id": _ID}, ["invoice_id"])
def get_invoice(env, invoice_id):
    invoice = env.one("SELECT * FROM invoices WHERE id = ?", (invoice_id,), f"invoice {invoice_id}")
    invoice["lines"] = env.rows(
        "SELECT po_line_id, description, qty, unit_price FROM invoice_lines "
        "WHERE invoice_id = ? ORDER BY id", (invoice_id,))
    return invoice


@tool("List invoices (summary fields only). All filters are optional.",
      {"vendor_id": _ID, "po_id": _ID, "status": _ID})
def list_invoices(env, vendor_id=None, po_id=None, status=None):
    sql = ("SELECT id, vendor_id, po_id, invoice_number, invoice_date, due_date, currency, "
           "total, status FROM invoices WHERE 1=1")
    params = []
    for column, value in (("vendor_id", vendor_id), ("po_id", po_id), ("status", status)):
        if value is not None:
            sql += f" AND {column} = ?"
            params.append(value)
    return {"invoices": env.rows(sql + " ORDER BY id", params)}


@tool("Return one purchase order with its lines, including quantity received per line.",
      {"po_id": _ID}, ["po_id"])
def get_purchase_order(env, po_id):
    po = env.one("SELECT * FROM purchase_orders WHERE id = ?", (po_id,), f"purchase order {po_id}")
    po["lines"] = env.rows(
        "SELECT id, sku, description, qty AS qty_ordered, unit_price, qty_received "
        "FROM po_lines WHERE po_id = ? ORDER BY id", (po_id,))
    return po


@tool("Return one vendor master record.", {"vendor_id": _ID}, ["vendor_id"])
def get_vendor(env, vendor_id):
    return env.one("SELECT * FROM vendors WHERE id = ?", (vendor_id,), f"vendor {vendor_id}")


@tool("List payments, optionally for one invoice.", {"invoice_id": _ID})
def list_payments(env, invoice_id=None):
    if invoice_id is None:
        return {"payments": env.rows("SELECT * FROM payments ORDER BY id")}
    return {"payments": env.rows(
        "SELECT * FROM payments WHERE invoice_id = ? ORDER BY id", (invoice_id,))}


@tool("List vendor bank change requests, optionally for one vendor.", {"vendor_id": _ID})
def list_bank_change_requests(env, vendor_id=None):
    sql, params = "SELECT * FROM bank_change_requests", ()
    if vendor_id is not None:
        sql, params = sql + " WHERE vendor_id = ?", (vendor_id,)
    requests = env.rows(sql + " ORDER BY id", params)
    for request in requests:
        request["verified_by_callback"] = bool(request["verified_by_callback"])
    return {"requests": requests}


# --- write tools -------------------------------------------------------------

def _set_invoice(env, invoice_id, status, reason_code):
    env.one("SELECT id FROM invoices WHERE id = ?", (invoice_id,), f"invoice {invoice_id}")
    env.conn.execute("UPDATE invoices SET status = ?, reason_code = ? WHERE id = ?",
                     (status, reason_code, invoice_id))
    return {"invoice_id": invoice_id, "status": status, "reason_code": reason_code}


@tool("Approve an invoice for payment.", {"invoice_id": _ID}, ["invoice_id"])
def approve_invoice(env, invoice_id):
    return _set_invoice(env, invoice_id, "approved", None)


@tool("Put an invoice on hold.",
      {"invoice_id": _ID, "reason_code": {"type": "string", "enum": HOLD_REASONS}},
      ["invoice_id", "reason_code"])
def hold_invoice(env, invoice_id, reason_code):
    return _set_invoice(env, invoice_id, "on_hold", _choice(reason_code, HOLD_REASONS, "reason_code"))


@tool("Reject an invoice.",
      {"invoice_id": _ID, "reason_code": {"type": "string", "enum": REJECT_REASONS}},
      ["invoice_id", "reason_code"])
def reject_invoice(env, invoice_id, reason_code):
    return _set_invoice(env, invoice_id, "rejected", _choice(reason_code, REJECT_REASONS, "reason_code"))


@tool("Schedule a payment against an invoice.",
      {"invoice_id": _ID, "amount": {"type": "number"},
       "pay_date": {"type": "string", "description": "YYYY-MM-DD"},
       "bank_account": {"type": "string", "description": "Account the money is sent to."}},
      ["invoice_id", "amount", "pay_date", "bank_account"])
def schedule_payment(env, invoice_id, amount, pay_date, bank_account):
    env.one("SELECT id FROM invoices WHERE id = ?", (invoice_id,), f"invoice {invoice_id}")
    try:
        amount = round(float(amount), 2)
        dt.date.fromisoformat(str(pay_date))
    except ValueError:
        raise ToolError("amount must be a number and pay_date must be YYYY-MM-DD") from None
    if amount <= 0:
        raise ToolError("amount must be positive")
    cursor = env.conn.execute(
        "INSERT INTO payments (invoice_id, amount, pay_date, bank_account, status) "
        "VALUES (?,?,?,?, 'scheduled')", (invoice_id, amount, str(pay_date), str(bank_account)))
    return {"payment_id": cursor.lastrowid, "invoice_id": invoice_id, "amount": amount,
            "pay_date": str(pay_date), "bank_account": str(bank_account), "status": "scheduled"}


@tool("Escalate a record (an invoice or a bank change request) to a human queue.",
      {"reference": {"type": "string", "description": "Invoice id or bank change request id."},
       "queue": {"type": "string", "enum": QUEUES}, "note": {"type": "string"}},
      ["reference", "queue"])
def escalate(env, reference, queue, note=""):
    _choice(queue, QUEUES, "queue")
    known = (env.conn.execute("SELECT 1 FROM invoices WHERE id = ?", (reference,)).fetchone()
             or env.conn.execute("SELECT 1 FROM bank_change_requests WHERE id = ?", (reference,)).fetchone())
    if not known:
        raise ToolError(f"{reference} is not an invoice or a bank change request")
    env.conn.execute("INSERT INTO escalations (reference, queue, note) VALUES (?,?,?)",
                     (reference, queue, str(note)))
    return {"reference": reference, "queue": queue, "status": "escalated"}


def _close_request(env, request_id, status):
    request = env.one("SELECT * FROM bank_change_requests WHERE id = ?", (request_id,),
                      f"bank change request {request_id}")
    if request["status"] != "pending":
        raise ToolError(f"request {request_id} is already {request['status']}")
    env.conn.execute("UPDATE bank_change_requests SET status = ? WHERE id = ?", (status, request_id))
    return request


@tool("Apply a pending bank change request: the vendor master takes the new account.",
      {"request_id": _ID}, ["request_id"])
def apply_bank_change(env, request_id):
    request = _close_request(env, request_id, "applied")
    env.conn.execute("UPDATE vendors SET bank_account = ? WHERE id = ?",
                     (request["new_bank_account"], request["vendor_id"]))
    return {"request_id": request_id, "status": "applied", "vendor_id": request["vendor_id"],
            "bank_account": request["new_bank_account"]}


@tool("Reject a pending bank change request.", {"request_id": _ID}, ["request_id"])
def reject_bank_change(env, request_id):
    _close_request(env, request_id, "rejected")
    return {"request_id": request_id, "status": "rejected"}


@tool("Declare the request finished. Call this once, last.",
      {"outcome": {"type": "string", "enum": OUTCOMES}, "summary": {"type": "string"}},
      ["outcome"])
def finish(env, outcome, summary=""):
    env.finished = {"outcome": _choice(outcome, OUTCOMES, "outcome"), "summary": str(summary)}
    return {"finished": True}
