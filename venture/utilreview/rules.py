"""Load the rule data."""
from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from pathlib import Path

RULES_FILE = Path(__file__).with_name("rules.json")
CHECKS = {"human_review", "quarterly_report", "none"}


@dataclass(frozen=True)
class Rule:
    id: str
    state: str
    checks: str
    applies_from: dt.date
    summary: str
    citation: str
    sources: tuple
    unverified: bool
    caveat: str


def load_rules(path: Path = RULES_FILE) -> list:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))["rules"]
    rules = []
    for item in raw:
        if item["checks"] not in CHECKS:
            raise ValueError(f"{item['id']}: unknown check type {item['checks']!r}")
        if not item["sources"]:
            raise ValueError(f"{item['id']}: a rule must cite at least one source")
        rules.append(Rule(
            id=item["id"], state=item["state"], checks=item["checks"],
            applies_from=dt.date.fromisoformat(item["applies_from"]),
            summary=item["summary"], citation=item["citation"],
            sources=tuple(item["sources"]), unverified=bool(item["unverified"]),
            caveat=item["caveat"]))
    return rules
