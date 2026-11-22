"""Decision records: metadata only, strictly validated.

A record describes one utilization-review request and its outcome. It carries an
opaque request id, never anything that identifies a patient. A file with a
patient-identifying column name is refused outright rather than ignored.
"""
from __future__ import annotations

import csv
import datetime as dt
from dataclasses import dataclass
from typing import Optional

COLUMNS = (
    "request_id", "state", "request_date", "decision_date", "service_type", "decision",
    "basis", "ai_involved", "human_reviewer_id", "human_review_date",
    "overturned_on_reconsideration",
)
DECISIONS = {"approved", "denied", "delayed", "downgraded"}
ADVERSE = {"denied", "delayed", "downgraded"}
BASES = {"medical_necessity", "administrative", "other"}
# Column names that suggest patient-identifying data. Refused, never read.
PHI_LIKE = {
    "patient", "patient_name", "name", "first_name", "last_name", "dob", "date_of_birth",
    "ssn", "member_id", "mrn", "medical_record_number", "address", "phone", "email",
    "diagnosis", "icd10", "policy_number",
}
REQUIRED = {"request_id", "state", "request_date", "decision_date", "service_type",
            "decision", "basis", "ai_involved"}


class RecordError(ValueError):
    """The input file is not acceptable."""


@dataclass(frozen=True)
class Record:
    request_id: str
    state: str
    request_date: dt.date
    decision_date: dt.date
    service_type: str
    decision: str
    basis: str
    ai_involved: bool
    human_reviewer_id: Optional[str]
    human_review_date: Optional[dt.date]
    overturned: bool

    @property
    def adverse(self) -> bool:
        return self.decision in ADVERSE


def _bool(value, where):
    text = (value or "").strip().lower()
    if text in ("true", "yes", "1"):
        return True
    if text in ("false", "no", "0", ""):
        return False
    raise RecordError(f"{where}: {value!r} is not true/false")


def _date(value, where):
    try:
        return dt.date.fromisoformat((value or "").strip())
    except ValueError:
        raise RecordError(f"{where}: {value!r} is not a YYYY-MM-DD date") from None


def parse_row(row: dict, line: int) -> Record:
    def at(column):
        return f"line {line}, {column}"

    for column in REQUIRED:
        if not (row.get(column) or "").strip():
            raise RecordError(f"{at(column)}: required value is missing")
    decision, basis = row["decision"].strip(), row["basis"].strip()
    if decision not in DECISIONS:
        raise RecordError(f"{at('decision')}: {decision!r} not in {sorted(DECISIONS)}")
    if basis not in BASES:
        raise RecordError(f"{at('basis')}: {basis!r} not in {sorted(BASES)}")
    request_date = _date(row["request_date"], at("request_date"))
    decision_date = _date(row["decision_date"], at("decision_date"))
    if decision_date < request_date:
        raise RecordError(f"line {line}: decision_date is before request_date")
    review_date = (row.get("human_review_date") or "").strip()
    return Record(
        request_id=row["request_id"].strip(), state=row["state"].strip().upper(),
        request_date=request_date, decision_date=decision_date,
        service_type=row["service_type"].strip(), decision=decision, basis=basis,
        ai_involved=_bool(row["ai_involved"], at("ai_involved")),
        human_reviewer_id=(row.get("human_reviewer_id") or "").strip() or None,
        human_review_date=_date(review_date, at("human_review_date")) if review_date else None,
        overturned=_bool(row.get("overturned_on_reconsideration"),
                         at("overturned_on_reconsideration")))


def load_csv(path) -> list:
    with open(path, newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        headers = [h.strip().lower() for h in (reader.fieldnames or [])]
        phi = sorted(set(headers) & PHI_LIKE)
        if phi:
            raise RecordError(
                f"refusing to read {path}: columns {phi} look patient-identifying. "
                "This tool takes decision metadata only.")
        unknown = sorted(set(headers) - set(COLUMNS))
        if unknown:
            raise RecordError(f"unknown columns {unknown}; expected {list(COLUMNS)}")
        missing = sorted(REQUIRED - set(headers))
        if missing:
            raise RecordError(f"missing required columns {missing}")
        rows = ({k.strip().lower(): v for k, v in row.items()} for row in reader)
        records = [parse_row(row, line) for line, row in enumerate(rows, start=2)]
    ids = [r.request_id for r in records]
    if len(ids) != len(set(ids)):
        raise RecordError("duplicate request_id values")
    return records
