"""Synthetic decision records for demos and tests. Not real data and not realistic rates."""
from __future__ import annotations

import datetime as dt
import random

from .records import COLUMNS, parse_row

STATES = ("MD", "IA", "WA", "TX")
SERVICES = ("imaging", "inpatient", "outpatient_surgery", "specialty_drug")


def generate(n: int = 400, seed: int = 7) -> list:
    """Deterministic list of Record objects. About one in eight adverse AI decisions lacks review."""
    rng = random.Random(seed)
    first_day = dt.date(2026, 4, 1)
    records = []
    for i in range(n):
        request_date = first_day + dt.timedelta(days=rng.randrange(0, 230))
        decision_date = request_date + dt.timedelta(days=rng.randrange(0, 6))
        decision = rng.choices(["approved", "denied", "delayed", "downgraded"], [70, 18, 7, 5])[0]
        ai = rng.random() < 0.55
        reviewed = rng.random() > 0.125
        review_date = decision_date - dt.timedelta(days=rng.randrange(0, 2))
        late = reviewed and rng.random() < 0.05
        row = {
            "request_id": f"R{i:05d}",
            "state": rng.choice(STATES),
            "request_date": request_date.isoformat(),
            "decision_date": decision_date.isoformat(),
            "service_type": rng.choice(SERVICES),
            "decision": decision,
            "basis": rng.choices(["medical_necessity", "administrative"], [85, 15])[0],
            "ai_involved": str(ai).lower(),
            "human_reviewer_id": f"CLIN{rng.randrange(1, 40):03d}" if reviewed else "",
            "human_review_date": ((decision_date + dt.timedelta(days=1)) if late else review_date
                                  ).isoformat() if reviewed else "",
            "overturned_on_reconsideration": str(decision != "approved" and rng.random() < 0.2).lower(),
        }
        assert set(row) == set(COLUMNS)
        records.append(parse_row(row, line=i + 2))
    return records
