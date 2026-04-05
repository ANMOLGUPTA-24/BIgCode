"""
test_claim_agent.py
===================
Comprehensive test suite for the Insurance Claim Settlement Agent.
Tests rule engine, bill parser, policy parser, and end-to-end pipeline.

Run with: python -m pytest tests/ -v
      or: python tests/test_claim_agent.py
"""

import sys
import json
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.policy_parser import PolicyParser
from src.rule_engine import RuleEngine
from src.bill_parser import BillParser
from src.claim_agent import ClaimAgent

POLICY_PATH = Path(__file__).parent.parent / "data" / "sample_policy.json"
BILLS_PATH = Path(__file__).parent.parent / "data" / "test_bills.json"


class TestPolicyParser(unittest.TestCase):

    def setUp(self):
        self.policy = PolicyParser(str(POLICY_PATH))

    def test_loads_successfully(self):
        summary = self.policy.get_summary()
        self.assertEqual(summary["policy_name"], "HealthGuard Premium Plan")
        self.assertGreater(summary["total_clauses"], 0)

    def test_get_clause(self):
        clause = self.policy.get_clause("S3.P2")
        self.assertIsNotNone(clause)
        self.assertIn("cosmetic", clause.text.lower())

    def test_citation_format(self):
        clause = self.policy.get_clause("S3.P3")
        citation = clause.citation()
        self.assertIn("Page", citation)
        self.assertIn("S3", citation)

    def test_benefit_limits(self):
        limits = self.policy.get_benefit_limits()
        self.assertEqual(limits["room_rent_general"], 3000)
        self.assertEqual(limits["room_rent_private"], 6000)
        self.assertEqual(limits["total_sum_insured"], 500000)

    def test_room_rent_limit_general(self):
        limit, clause = self.policy.calculate_room_rent_limit("general ward", 3)
        self.assertEqual(limit, 9000)  # 3000 * 3 days
        self.assertIsNotNone(clause)

    def test_room_rent_limit_private(self):
        limit, clause = self.policy.calculate_room_rent_limit("private single room", 2)
        self.assertEqual(limit, 12000)  # 6000 * 2 days

    def test_copayment_above_61(self):
        copay, clause = self.policy.calculate_copayment(67, 100000)
        self.assertEqual(copay, 10000.0)  # 10%
        self.assertIsNotNone(clause)

    def test_copayment_below_61(self):
        copay, clause = self.policy.calculate_copayment(35, 100000)
        self.assertEqual(copay, 0.0)  # No co-pay

    def test_waiting_period_initial_30_days(self):
        in_wait, clause, reason = self.policy.check_waiting_period(
            "viral fever", "2024-10-01", "2024-10-20"  # 19 days after inception
        )
        self.assertTrue(in_wait)
        self.assertIn("30-day", reason)

    def test_waiting_period_24_month_hernia(self):
        in_wait, clause, reason = self.policy.check_waiting_period(
            "Inguinal Hernia Repair", "2023-09-01", "2024-10-10"  # 13 months
        )
        self.assertTrue(in_wait)
        self.assertIn("24-month", reason)

    def test_no_waiting_period_appendix_old_policy(self):
        in_wait, clause, reason = self.policy.check_waiting_period(
            "Acute Appendicitis", "2022-03-01", "2024-10-15"  # >2 years
        )
        self.assertFalse(in_wait)

    def test_exclusion_dental(self):
        excl, clause, reason = self.policy.check_exclusion_by_text("Root Canal Treatment")
        self.assertTrue(excl)
        self.assertIn("S3.P3", clause.para_id)

    def test_exclusion_cosmetic(self):
        excl, clause, reason = self.policy.check_exclusion_by_text("Rhinoplasty nose reshaping")
        self.assertTrue(excl)
        self.assertIn("S3.P2", clause.para_id)

    def test_no_exclusion_appendectomy(self):
        excl, clause, reason = self.policy.check_exclusion_by_text("Laparoscopic Appendectomy")
        self.assertFalse(excl)


class TestRuleEngine(unittest.TestCase):

    def setUp(self):
        self.policy = PolicyParser(str(POLICY_PATH))
        self.engine = RuleEngine(self.policy)

        with open(BILLS_PATH) as f:
            self.bills_data = json.load(f)["test_cases"]

    def _get_bill(self, bill_id: str) -> dict:
        for b in self.bills_data:
            if b["bill_id"] == bill_id:
                parser = BillParser.__new__(BillParser)  # skip API init
                return parser._normalize_bill(b)
        raise ValueError(f"Bill {bill_id} not found")

    def test_appendectomy_approved(self):
        """Standard appendectomy should be fully approved."""
        bill = self._get_bill("BILL-2024-001")
        decision = self.engine.evaluate(bill)
        self.assertEqual(decision.overall_decision, "APPROVED")
        self.assertGreater(decision.total_approved, 0)
        self.assertEqual(decision.total_rejected, 0)

    def test_dental_bill_rejected(self):
        """Dental treatment should be fully rejected with citation."""
        bill = self._get_bill("BILL-2024-003")
        decision = self.engine.evaluate(bill)
        self.assertEqual(decision.overall_decision, "REJECTED")
        self.assertEqual(decision.total_approved, 0)
        self.assertGreater(len(decision.citations_used), 0)
        # Must cite S3.P3 (dental exclusion)
        self.assertTrue(any("S3" in c for c in decision.citations_used))

    def test_initial_waiting_period_rejected(self):
        """Claim within 30-day initial waiting period should be rejected."""
        bill = self._get_bill("BILL-2024-004")
        decision = self.engine.evaluate(bill)
        self.assertEqual(decision.overall_decision, "REJECTED")
        # Must cite S4.P1 (initial waiting period)
        self.assertTrue(any("S4" in c for c in decision.citations_used))

    def test_cosmetic_partial_rejection(self):
        """Bill with mixed septoplasty + rhinoplasty: rhinoplasty rejected."""
        bill = self._get_bill("BILL-2024-002")
        decision = self.engine.evaluate(bill)
        self.assertEqual(decision.overall_decision, "PARTIAL")
        self.assertGreater(decision.total_approved, 0)
        self.assertGreater(decision.total_rejected, 0)
        # Rhinoplasty should be rejected
        rhino = [ld for ld in decision.line_decisions if "rhinoplasty" in ld.description.lower()]
        self.assertTrue(len(rhino) > 0)
        self.assertEqual(rhino[0].decision, "REJECTED")

    def test_room_rent_excess_partial(self):
        """Deluxe room at 8000/night should be partially approved (limit 6000)."""
        bill = self._get_bill("BILL-2024-005")
        decision = self.engine.evaluate(bill)
        room_items = [ld for ld in decision.line_decisions if "room" in ld.description.lower()]
        self.assertTrue(len(room_items) > 0)
        room = room_items[0]
        # 5 nights * 6000 limit = 30000 approved, 10000 rejected
        self.assertEqual(room.approved_amount, 30000)
        self.assertEqual(room.rejected_amount, 10000)

    def test_hernia_24month_waiting_period(self):
        """Hernia claim within 24-month waiting period should be rejected."""
        bill = self._get_bill("BILL-2024-006")
        decision = self.engine.evaluate(bill)
        self.assertEqual(decision.overall_decision, "REJECTED")
        self.assertTrue(any("S4" in c for c in decision.citations_used))

    def test_senior_copayment_applied(self):
        """Patient aged 67 should have 10% copayment applied."""
        bill = self._get_bill("BILL-2024-005")
        decision = self.engine.evaluate(bill)
        self.assertGreater(decision.copayment, 0)
        self.assertEqual(decision.copayment, round(decision.total_approved * 0.10, 2))

    def test_implant_sublimit(self):
        """Titanium knee implant at 120000 should be capped at 50000 limit."""
        bill = self._get_bill("BILL-2024-005")
        decision = self.engine.evaluate(bill)
        implant_items = [ld for ld in decision.line_decisions if "implant" in ld.description.lower()]
        self.assertTrue(len(implant_items) > 0)
        implant = implant_items[0]
        self.assertEqual(implant.approved_amount, 50000)
        self.assertEqual(implant.rejected_amount, 70000)

    def test_all_decisions_have_citations(self):
        """All rejection decisions must include a policy citation."""
        for bill_data in self.bills_data:
            parser = BillParser.__new__(BillParser)
            bill = parser._normalize_bill(bill_data)
            decision = self.engine.evaluate(bill)
            for ld in decision.line_decisions:
                if ld.decision == "REJECTED":
                    self.assertIsNotNone(
                        ld.citation,
                        f"Line item '{ld.description}' in {bill_data['bill_id']} "
                        f"rejected without citation"
                    )

    def test_net_payable_less_than_approved(self):
        """Net payable should be approved minus copayment."""
        for bill_data in self.bills_data:
            parser = BillParser.__new__(BillParser)
            bill = parser._normalize_bill(bill_data)
            decision = self.engine.evaluate(bill)
            expected = max(0, decision.total_approved - decision.copayment)
            self.assertAlmostEqual(decision.net_payable, expected, places=1)


class TestBillParser(unittest.TestCase):

    def setUp(self):
        self.parser = BillParser.__new__(BillParser)  # Skip API client init

    def test_normalize_age(self):
        bill = {
            "bill_id": "TEST-001",
            "hospital": "Test Hospital",
            "patient": {"name": "Test User", "age": 45, "policy_number": "P001", "policy_start_date": "2022-01-01"},
            "admission_date": "2024-01-10",
            "discharge_date": "2024-01-13",
            "diagnosis": "Test diagnosis",
            "line_items": [
                {"description": "Room charges", "amount": 5000, "cpt_code": "room_general", "days": 3}
            ],
            "total_billed": 5000
        }
        result = self.parser._normalize_bill(bill)
        self.assertEqual(result["patient"]["age"], 45)
        self.assertEqual(result["days_admitted"], 3)

    def test_flag_dental(self):
        bill = {
            "bill_id": "T002", "hospital": "H",
            "patient": {"name": "X", "age": 30, "policy_number": "P", "policy_start_date": "2022-01-01"},
            "admission_date": "2024-01-01", "discharge_date": "2024-01-01",
            "diagnosis": "Dental treatment",
            "line_items": [{"description": "Root Canal", "amount": 5000, "cpt_code": "D3330"}],
            "total_billed": 5000
        }
        result = self.parser._normalize_bill(bill)
        self.assertTrue(result["flags"]["contains_dental"] or result["flags"]["contains_dental_code"])

    def test_flag_cosmetic(self):
        bill = {
            "bill_id": "T003", "hospital": "H",
            "patient": {"name": "X", "age": 30, "policy_number": "P", "policy_start_date": "2022-01-01"},
            "admission_date": "2024-01-01", "discharge_date": "2024-01-01",
            "diagnosis": "Rhinoplasty cosmetic surgery",
            "line_items": [{"description": "Rhinoplasty", "amount": 50000, "cpt_code": "30400"}],
            "total_billed": 50000
        }
        result = self.parser._normalize_bill(bill)
        self.assertTrue(result["flags"]["contains_cosmetic"])

    def test_item_classification(self):
        self.assertEqual(self.parser._classify_item({"description": "Room Charges General Ward", "amount": 3000}), "room")
        self.assertEqual(self.parser._classify_item({"description": "ICU Charges", "amount": 5000}), "icu")
        self.assertEqual(self.parser._classify_item({"description": "CT Scan Abdomen", "amount": 3500}), "diagnostics")
        self.assertEqual(self.parser._classify_item({"description": "Medicines and consumables", "amount": 2000, "cpt_code": "pharmacy"}), "pharmacy")


class TestEndToEnd(unittest.TestCase):
    """End-to-end pipeline tests."""

    def setUp(self):
        self.agent = ClaimAgent(str(POLICY_PATH))

    def test_full_pipeline_approved(self):
        with open(BILLS_PATH) as f:
            bills = json.load(f)["test_cases"]
        bill = next(b for b in bills if b["bill_id"] == "BILL-2024-001")
        decision = self.agent.process_json_bill(bill)
        self.assertEqual(decision.overall_decision, "APPROVED")

    def test_full_pipeline_rejected(self):
        with open(BILLS_PATH) as f:
            bills = json.load(f)["test_cases"]
        bill = next(b for b in bills if b["bill_id"] == "BILL-2024-003")
        decision = self.agent.process_json_bill(bill)
        self.assertEqual(decision.overall_decision, "REJECTED")

    def test_report_generation(self):
        with open(BILLS_PATH) as f:
            bills = json.load(f)["test_cases"]
        bill = bills[0]
        decision = self.agent.process_json_bill(bill)
        report = self.agent.format_report(decision)
        self.assertIn("CLAIM SETTLEMENT REPORT", report)
        self.assertIn("NET PAYABLE", report)
        self.assertIn("LINE ITEM BREAKDOWN", report)

    def test_json_export(self):
        with open(BILLS_PATH) as f:
            bills = json.load(f)["test_cases"]
        bill = bills[0]
        decision = self.agent.process_json_bill(bill)
        export = self.agent.export_json(decision)
        # Must be JSON serializable
        json_str = json.dumps(export)
        self.assertIn("overall_decision", json_str)
        self.assertIn("line_items", json_str)
        self.assertIn("citations_used", json_str)

    def test_all_six_bills(self):
        """Run all 6 test cases and verify expected outcomes."""
        expected = {
            "BILL-2024-001": "APPROVED",
            "BILL-2024-002": "PARTIAL",
            "BILL-2024-003": "REJECTED",
            "BILL-2024-004": "REJECTED",
            "BILL-2024-005": "PARTIAL",
            "BILL-2024-006": "REJECTED",
        }
        with open(BILLS_PATH) as f:
            bills = json.load(f)["test_cases"]

        correct = 0
        for bill in bills:
            bid = bill["bill_id"]
            if bid not in expected:
                continue
            decision = self.agent.process_json_bill(bill)
            if decision.overall_decision == expected[bid]:
                correct += 1
            else:
                print(f"MISMATCH {bid}: expected {expected[bid]}, got {decision.overall_decision}")

        accuracy = correct / len(expected)
        self.assertGreaterEqual(accuracy, 0.9, f"Accuracy {accuracy:.0%} below threshold 90%")
        print(f"\n✅ End-to-end accuracy: {correct}/{len(expected)} = {accuracy:.0%}")


if __name__ == "__main__":
    # Run with verbose output
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    suite.addTests(loader.loadTestsFromTestCase(TestPolicyParser))
    suite.addTests(loader.loadTestsFromTestCase(TestBillParser))
    suite.addTests(loader.loadTestsFromTestCase(TestRuleEngine))
    suite.addTests(loader.loadTestsFromTestCase(TestEndToEnd))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
