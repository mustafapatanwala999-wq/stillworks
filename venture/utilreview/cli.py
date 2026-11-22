"""Command line: python -m utilreview {demo,check,report,reconcile}."""
from __future__ import annotations

import argparse
import json
import sys

from . import engine, records, synthetic
from .rules import load_rules

NOTICE = ("NOTE: rules are unverified summaries from secondary sources, not legal advice. "
          "Findings show where the records do not show what a rule appears to require.")


def _print_findings(findings, rules):
    by_rule = {r.id: r for r in rules}
    print(f"{len(findings)} flagged decision(s)")
    for f in findings[:25]:
        print(f"  {f.request_id}  {f.state}  {f.rule_id}: {f.reason}")
    if len(findings) > 25:
        print(f"  ... and {len(findings) - 25} more")
    for rule_id in sorted({f.rule_id for f in findings}):
        rule = by_rule[rule_id]
        print(f"\n{rule_id}: {rule.summary}\n  caveat: {rule.caveat}")


def _print_report(rows):
    print("state  quarter   service_type        made  granted  adverse  adverse_ai  overturned")
    for r in rows:
        print(f"{r['state']:<6} {r['quarter']:<9} {r['service_type']:<19} {r['requests_made']:>4}  "
              f"{r['requests_granted']:>7}  {r['adverse_decisions']:>7}  {r['adverse_ai_involved']:>10}  "
              f"{r['overturned_on_reconsideration']:>10}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="utilreview", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    demo = sub.add_parser("demo", help="run on synthetic records")
    demo.add_argument("--n", type=int, default=400)
    demo.add_argument("--seed", type=int, default=7)
    for name, help_text in (("check", "flag decisions missing a human-review record"),
                            ("report", "compute the quarterly counts"),
                            ("reconcile", "compare counts with a filed report (JSON list of rows)")):
        cmd = sub.add_parser(name, help=help_text)
        cmd.add_argument("records_csv")
        if name == "reconcile":
            cmd.add_argument("filed_json")
    args = parser.parse_args(argv)

    rules = load_rules()
    try:
        recs = (synthetic.generate(args.n, args.seed) if args.command == "demo"
                else records.load_csv(args.records_csv))
    except records.RecordError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(NOTICE + "\n")
    if args.command in ("demo", "check"):
        _print_findings(engine.check(recs, rules), rules)
    if args.command == "demo":
        print()
    if args.command in ("demo", "report"):
        _print_report(engine.quarterly_report(recs, rules))
    if args.command == "reconcile":
        with open(args.filed_json, encoding="utf-8") as handle:
            filed = json.load(handle)
        problems = engine.reconcile(engine.quarterly_report(recs, rules), filed)
        print(f"{len(problems)} difference(s) between the filing and the records")
        for problem in problems:
            print(f"  {problem}")
        return 1 if problems else 0
    return 0
