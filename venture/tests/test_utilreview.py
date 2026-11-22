"""Tests for utilreview: rule data, strict record loading, the checks, the report, reconciliation."""
import csv
import datetime as dt
import tempfile
import unittest
from pathlib import Path

from utilreview import engine, records, synthetic
from utilreview.cli import main as cli_main
from utilreview.records import COLUMNS, RecordError, parse_row
from utilreview.rules import load_rules

D = dt.date.fromisoformat


def rec(**overrides):
    row = {
        "request_id": "R1", "state": "MD", "request_date": "2026-08-10", "decision_date": "2026-08-12",
        "service_type": "imaging", "decision": "denied", "basis": "medical_necessity",
        "ai_involved": "true", "human_reviewer_id": "", "human_review_date": "",
        "overturned_on_reconsideration": "false",
    }
    row.update(overrides)
    return parse_row(row, line=2)


def write_csv(directory, rows, columns=COLUMNS):
    path = Path(directory) / "records.csv"
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    return path


class RuleData(unittest.TestCase):
    def test_every_rule_is_sourced_and_marked_unverified(self):
        rules = load_rules()
        self.assertGreaterEqual(len(rules), 7)
        self.assertEqual(len({r.id for r in rules}), len(rules))
        for rule in rules:
            with self.subTest(rule=rule.id):
                self.assertTrue(rule.unverified)
                self.assertTrue(all(url.startswith("https://") for url in rule.sources))
                self.assertTrue(rule.caveat)

    def test_iowa_uses_the_later_date_and_no_quarterly_rule_exists_outside_maryland(self):
        by_id = {r.id: r for r in load_rules()}
        self.assertEqual(by_id["IA-HF2635-human-review"].applies_from, D("2027-07-01"))
        self.assertEqual([r.state for r in load_rules() if r.checks == "quarterly_report"], ["MD"])


class RecordLoading(unittest.TestCase):
    def test_patient_identifying_columns_are_refused_not_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_csv(tmp, [], columns=COLUMNS + ("member_id",))
            with self.assertRaises(RecordError) as caught:
                records.load_csv(path)
            self.assertIn("member_id", str(caught.exception))
            self.assertIn("patient-identifying", str(caught.exception))

    def test_bad_values_name_the_line_and_column(self):
        for overrides, text in [({"decision": "maybe"}, "decision"),
                                ({"request_date": "08/10/2026"}, "request_date"),
                                ({"ai_involved": "sometimes"}, "ai_involved"),
                                ({"basis": ""}, "basis"),
                                ({"decision_date": "2026-08-01"}, "before request_date")]:
            with self.subTest(text=text):
                with self.assertRaises(RecordError) as caught:
                    rec(**overrides)
                self.assertIn(text, str(caught.exception))

    def test_duplicate_ids_unknown_and_missing_columns_are_errors(self):
        row = {c: "" for c in COLUMNS}
        row.update(request_id="R1", state="MD", request_date="2026-08-10", decision_date="2026-08-11",
                   service_type="imaging", decision="approved", basis="other", ai_involved="false")
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(RecordError):
                records.load_csv(write_csv(tmp, [row, row]))
            with self.assertRaises(RecordError):
                records.load_csv(write_csv(tmp, [], columns=COLUMNS + ("notes",)))
            with self.assertRaises(RecordError):
                records.load_csv(write_csv(tmp, [], columns=("request_id", "state")))
            self.assertEqual(len(records.load_csv(write_csv(tmp, [row]))), 1)


class HumanReviewCheck(unittest.TestCase):
    rules = load_rules()

    def flagged(self, **overrides):
        return engine.check([rec(**overrides)], self.rules)

    def test_adverse_ai_decision_with_no_review_is_flagged(self):
        [finding] = self.flagged()
        self.assertEqual((finding.state, finding.rule_id), ("MD", "MD-HB820-human-review"))
        self.assertIn("no human-review record", finding.reason)

    def test_review_on_or_before_the_decision_clears_it(self):
        for review_date in ("2026-08-11", "2026-08-12"):
            with self.subTest(review_date=review_date):
                self.assertEqual(self.flagged(human_reviewer_id="CLIN007", human_review_date=review_date), [])

    def test_review_after_the_decision_is_flagged_with_both_dates(self):
        [finding] = self.flagged(human_reviewer_id="CLIN007", human_review_date="2026-08-13")
        self.assertIn("2026-08-13", finding.reason)
        self.assertIn("2026-08-12", finding.reason)

    def test_a_reviewer_without_a_date_is_not_a_review_record(self):
        self.assertEqual(len(self.flagged(human_reviewer_id="CLIN007")), 1)

    def test_only_adverse_ai_medical_necessity_decisions_are_in_scope(self):
        for overrides in ({"decision": "approved"}, {"ai_involved": "false"}, {"basis": "administrative"}):
            with self.subTest(overrides=overrides):
                self.assertEqual(self.flagged(**overrides), [])
        for decision in ("delayed", "downgraded"):
            self.assertEqual(len(self.flagged(decision=decision)), 1)

    def test_states_without_a_rule_and_dates_before_a_rule_applies_are_not_flagged(self):
        self.assertEqual(self.flagged(state="TX"), [])
        self.assertEqual(self.flagged(request_date="2025-09-30", decision_date="2025-09-30"), [])
        self.assertEqual(len(self.flagged(request_date="2025-10-01", decision_date="2025-10-02")), 1)

    def test_iowa_only_applies_to_requests_on_or_after_its_later_date(self):
        self.assertEqual(self.flagged(state="IA", request_date="2027-06-30", decision_date="2027-07-02"), [])
        self.assertEqual(len(self.flagged(state="IA", request_date="2027-07-01", decision_date="2027-07-02")), 1)

    def test_a_rule_that_checks_nothing_never_flags(self):
        self.assertEqual(self.flagged(state="UT", request_date="2027-02-01", decision_date="2027-02-02"), [])


class QuarterlyReport(unittest.TestCase):
    rules = load_rules()

    def test_counts_per_state_quarter_and_service_type(self):
        data = [
            rec(request_id="A", decision="approved"),
            rec(request_id="B", decision="denied", ai_involved="true", overturned_on_reconsideration="true"),
            rec(request_id="C", decision="delayed", ai_involved="false"),
            rec(request_id="D", decision="denied", service_type="inpatient"),
            rec(request_id="E", state="TX", decision="denied"),  # no quarterly rule for TX
        ]
        rows = engine.quarterly_report(data, self.rules)
        self.assertEqual(len(rows), 2)
        imaging = next(r for r in rows if r["service_type"] == "imaging")
        self.assertEqual(imaging, {
            "state": "MD", "quarter": "2026-Q3", "service_type": "imaging", "requests_made": 3,
            "requests_granted": 1, "adverse_decisions": 2, "adverse_ai_involved": 1,
            "overturned_on_reconsideration": 1})

    def test_quarter_boundaries_use_the_decision_date(self):
        rows = engine.quarterly_report(
            [rec(request_id="A", request_date="2026-06-29", decision_date="2026-06-30"),
             rec(request_id="B", request_date="2026-06-30", decision_date="2026-07-01")], self.rules)
        self.assertEqual(sorted(r["quarter"] for r in rows), ["2026-Q2", "2026-Q3"])

    def test_decisions_before_the_effective_date_are_excluded(self):
        self.assertEqual(engine.quarterly_report(
            [rec(request_date="2026-05-30", decision_date="2026-05-31")], self.rules), [])


class Reconciliation(unittest.TestCase):
    rules = load_rules()
    data = [rec(request_id="A", decision="approved"), rec(request_id="B", decision="denied")]

    def test_a_matching_filing_has_no_differences(self):
        computed = engine.quarterly_report(self.data, self.rules)
        self.assertEqual(engine.reconcile(computed, [dict(r) for r in computed]), [])

    def test_a_wrong_count_a_missing_row_and_an_extra_row_are_each_reported(self):
        computed = engine.quarterly_report(self.data, self.rules)
        wrong = [dict(computed[0], adverse_ai_involved=0)]
        self.assertEqual(len(engine.reconcile(computed, wrong)), 1)
        self.assertIn("adverse_ai_involved filed 0 but records show 1", engine.reconcile(computed, wrong)[0])
        self.assertIn("not filed", engine.reconcile(computed, [])[0])
        extra = [dict(computed[0]), dict(computed[0], service_type="inpatient")]
        self.assertIn("no supporting records", engine.reconcile(computed, extra)[0])


class SyntheticAndCli(unittest.TestCase):
    def test_synthetic_data_is_deterministic_and_has_flags_and_a_maryland_report(self):
        first, second = synthetic.generate(300, seed=3), synthetic.generate(300, seed=3)
        self.assertEqual(first, second)
        self.assertNotEqual(first, synthetic.generate(300, seed=4))
        rules = load_rules()
        self.assertGreater(len(engine.check(first, rules)), 0)
        self.assertGreater(len(engine.quarterly_report(first, rules)), 0)

    def test_cli_demo_check_and_refusal_exit_codes(self):
        self.assertEqual(cli_main(["demo", "--n", "80"]), 0)
        with tempfile.TemporaryDirectory() as tmp:
            bad = write_csv(tmp, [], columns=COLUMNS + ("ssn",))
            self.assertEqual(cli_main(["check", str(bad)]), 2)


if __name__ == "__main__":
    unittest.main()
