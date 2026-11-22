"""Checks, the quarterly report, and reconciliation against what was filed.

The mechanism is deliberately mechanical: every adverse medical-necessity decision
either carries a human-review record dated no later than the decision, or it is
flagged; every reported count is recomputed from the records and compared. Nothing
here is a legal conclusion. A flag means "the record does not show what the rule
appears to require".
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from .records import Record


@dataclass(frozen=True)
class Finding:
    request_id: str
    state: str
    rule_id: str
    reason: str


def _review_gap(record: Record):
    """Why the record does not show human review, or None if it does."""
    if not record.human_reviewer_id or record.human_review_date is None:
        return "adverse AI-involved medical-necessity decision with no human-review record"
    if record.human_review_date > record.decision_date:
        return (f"human review recorded on {record.human_review_date}, "
                f"after the decision on {record.decision_date}")
    return None


def check(records, rules) -> list:
    """Flag adverse, AI-involved, medical-necessity decisions without timely human review."""
    review_rules = [r for r in rules if r.checks == "human_review"]
    findings = []
    for record in records:
        if not (record.adverse and record.ai_involved and record.basis == "medical_necessity"):
            continue
        gap = _review_gap(record)
        if gap is None:
            continue
        for rule in review_rules:
            if rule.state == record.state and rule.applies_from <= record.request_date:
                findings.append(Finding(record.request_id, record.state, rule.id, gap))
    return findings


def quarter_of(date) -> str:
    return f"{date.year}-Q{(date.month - 1) // 3 + 1}"


def quarterly_report(records, rules) -> list:
    """Counts a state quarterly adverse-decision report asks for, per state, quarter, service type."""
    start = {r.state: r.applies_from for r in rules if r.checks == "quarterly_report"}
    groups = defaultdict(lambda: {"requests_made": 0, "requests_granted": 0,
                                  "adverse_decisions": 0, "adverse_ai_involved": 0,
                                  "overturned_on_reconsideration": 0})
    for record in records:
        if record.state not in start or record.decision_date < start[record.state]:
            continue
        row = groups[(record.state, quarter_of(record.decision_date), record.service_type)]
        row["requests_made"] += 1
        row["requests_granted"] += record.decision == "approved"
        if record.adverse:
            row["adverse_decisions"] += 1
            row["adverse_ai_involved"] += record.ai_involved
            row["overturned_on_reconsideration"] += record.overturned
    return [{"state": s, "quarter": q, "service_type": t, **counts}
            for (s, q, t), counts in sorted(groups.items())]


COUNT_FIELDS = ("requests_made", "requests_granted", "adverse_decisions",
                "adverse_ai_involved", "overturned_on_reconsideration")


def reconcile(computed, filed) -> list:
    """Compare recomputed counts with what was filed. Returns human-readable differences."""
    key = lambda row: (row["state"], row["quarter"], row["service_type"])
    computed_by, filed_by = {key(r): r for r in computed}, {key(r): r for r in filed}
    problems = []
    for k in sorted(set(computed_by) | set(filed_by)):
        label = "/".join(k)
        if k not in filed_by:
            problems.append(f"{label}: records produce a row that was not filed")
        elif k not in computed_by:
            problems.append(f"{label}: filed row has no supporting records")
        else:
            for field in COUNT_FIELDS:
                if computed_by[k][field] != filed_by[k].get(field):
                    problems.append(f"{label}: {field} filed {filed_by[k].get(field)} "
                                    f"but records show {computed_by[k][field]}")
    return problems
