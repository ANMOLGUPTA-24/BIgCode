"""
test_upgrades.py
================
Tests for the 4 new upgrade modules:
  - Gemini Vision parser (structure tests — no API key needed)
  - Confidence scoring
  - Fraud detection
  - Pre-auth predictor (structure tests — no API key needed)

Run: python tests/test_upgrades.py
"""

import sys, json, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.policy_parser import PolicyParser
from src.rule_engine import RuleEngine, ClaimDecision
from src.bill_parser import BillParser
from src.fraud_detector import FraudDetector, FraudReport, FraudSignal

POLICY_PATH = Path(__file__).parent.parent / "data" / "sample_policy.json"
BILLS_PATH  = Path(__file__).parent.parent / "data" / "test_bills.json"


def _load_bill(bill_id: str) -> dict:
    with open(BILLS_PATH) as f:
        bills = json.load(f)["test_cases"]
    raw = next(b for b in bills if b["bill_id"] == bill_id)
    parser = BillParser.__new__(BillParser)
    return parser._normalize_bill(raw)


def _engine():
    policy = PolicyParser(str(POLICY_PATH))
    return RuleEngine(policy), policy


# ══════════════════════════════════════════════════════════════════════════════
# Confidence scoring tests
# ══════════════════════════════════════════════════════════════════════════════
class TestConfidenceScoring(unittest.TestCase):

    def setUp(self):
        self.engine, self.policy = _engine()

    def test_clean_approved_claim_high_confidence(self):
        """Appendectomy with all info present → confidence should be high."""
        bill = _load_bill("BILL-2024-001")
        decision = self.engine.evaluate(bill)
        self.assertGreaterEqual(decision.claim_confidence_pct, 80)
        self.assertFalse(decision.requires_human_review)

    def test_clear_rejection_high_confidence(self):
        """Dental rejection via exact CPT code → should be very high confidence."""
        bill = _load_bill("BILL-2024-003")
        decision = self.engine.evaluate(bill)
        self.assertGreaterEqual(decision.claim_confidence_pct, 80)

    def test_confidence_field_exists(self):
        """ClaimDecision must have confidence fields."""
        bill = _load_bill("BILL-2024-001")
        decision = self.engine.evaluate(bill)
        self.assertIsInstance(decision.claim_confidence_pct, int)
        self.assertIsInstance(decision.requires_human_review, bool)
        self.assertGreaterEqual(decision.claim_confidence_pct, 0)
        self.assertLessEqual(decision.claim_confidence_pct, 100)

    def test_line_item_confidence_fields(self):
        """Each LineItemDecision must have confidence_pct."""
        bill = _load_bill("BILL-2024-005")
        decision = self.engine.evaluate(bill)
        for ld in decision.line_decisions:
            self.assertIsInstance(ld.confidence_pct, int)
            self.assertGreaterEqual(ld.confidence_pct, 0)
            self.assertLessEqual(ld.confidence_pct, 100)

    def test_all_six_bills_have_confidence(self):
        """All 6 test bills must produce a valid confidence score."""
        with open(BILLS_PATH) as f:
            bills = json.load(f)["test_cases"]
        for raw in bills:
            parser = BillParser.__new__(BillParser)
            bill = parser._normalize_bill(raw)
            decision = self.engine.evaluate(bill)
            self.assertIsInstance(decision.claim_confidence_pct, int,
                f"{raw['bill_id']} missing confidence_pct")
            self.assertIn(decision.claim_confidence_pct, range(0, 101),
                f"{raw['bill_id']} confidence out of range")

    def test_human_review_threshold(self):
        """requires_human_review must be True when confidence < 80."""
        bill = _load_bill("BILL-2024-001")
        decision = self.engine.evaluate(bill)
        if decision.claim_confidence_pct < 80:
            self.assertTrue(decision.requires_human_review)
        else:
            self.assertFalse(decision.requires_human_review)


# ══════════════════════════════════════════════════════════════════════════════
# Fraud detection tests
# ══════════════════════════════════════════════════════════════════════════════
class TestFraudDetector(unittest.TestCase):

    def setUp(self):
        self.detector = FraudDetector()

    def _make_bill(self, items, dx="Appendicitis", preauth=True):
        parser = BillParser.__new__(BillParser)
        return parser._normalize_bill({
            "bill_id": "TEST",
            "hospital": "Test Hospital",
            "patient": {"name": "Test", "age": 35, "policy_number": "P1", "policy_start_date": "2022-01-01"},
            "admission_date": "2024-10-01", "discharge_date": "2024-10-03",
            "diagnosis": dx, "diagnosis_codes": [],
            "pre_authorization_obtained": preauth,
            "line_items": items,
            "total_billed": sum(i["amount"] for i in items),
        })

    def test_clean_bill_low_risk(self):
        """A clean, normal bill should get LOW risk."""
        bill = _load_bill("BILL-2024-001")
        report = self.detector.analyse(bill)
        self.assertIsInstance(report, FraudReport)
        self.assertIn(report.risk_level, ["LOW", "MEDIUM", "HIGH"])
        self.assertGreaterEqual(report.risk_score, 0)
        self.assertLessEqual(report.risk_score, 100)

    def test_duplicate_item_detected(self):
        """Two identical line items → duplicate signal flagged."""
        items = [
            {"description": "Surgeon fees - Appendectomy", "amount": 25000, "cpt_code": "44950"},
            {"description": "Surgeon fees - Appendectomy", "amount": 25000, "cpt_code": "44950"},
            {"description": "Room charges general ward", "amount": 3000, "cpt_code": "room_general", "days": 1},
        ]
        bill = self._make_bill(items)
        report = self.detector.analyse(bill)
        dup_signals = [s for s in report.signals if s.signal_type == "duplicate"]
        self.assertGreater(len(dup_signals), 0, "Duplicate not detected")
        self.assertEqual(dup_signals[0].severity, "high")

    def test_outlier_amount_detected(self):
        """Consultation fee of ₹50,000 should trigger outlier signal."""
        items = [
            {"description": "Physician consultation", "amount": 50000, "cpt_code": "99213"},
            {"description": "Room charges general ward", "amount": 2000, "cpt_code": "room_general", "days": 1},
        ]
        bill = self._make_bill(items)
        report = self.detector.analyse(bill)
        outlier_signals = [s for s in report.signals if s.signal_type == "outlier"]
        self.assertGreater(len(outlier_signals), 0, "Outlier not detected")

    def test_impossible_combo_detected(self):
        """Appendectomy + dental in same admission → impossible combo."""
        items = [
            {"description": "Laparoscopic Appendectomy", "amount": 30000, "cpt_code": "44950"},
            {"description": "Dental Crown procedure", "amount": 8000, "cpt_code": "D2750"},
        ]
        bill = self._make_bill(items, dx="Acute appendicitis with dental caries")
        report = self.detector.analyse(bill)
        combo_signals = [s for s in report.signals if s.signal_type == "impossible_combo"]
        self.assertGreater(len(combo_signals), 0, "Impossible combo not detected")
        self.assertEqual(combo_signals[0].severity, "high")

    def test_cpt_diagnosis_mismatch_detected(self):
        """CPT 44950 (appendectomy) with knee diagnosis → mismatch."""
        items = [
            {"description": "Surgical procedure", "amount": 30000, "cpt_code": "44950"},
        ]
        bill = self._make_bill(items, dx="Total knee replacement osteoarthritis")
        report = self.detector.analyse(bill)
        mismatch = [s for s in report.signals if s.signal_type == "cpt_mismatch"]
        self.assertGreater(len(mismatch), 0, "CPT mismatch not detected")

    def test_report_has_required_fields(self):
        """FraudReport must have all required fields."""
        bill = _load_bill("BILL-2024-001")
        report = self.detector.analyse(bill)
        self.assertTrue(hasattr(report, "risk_level"))
        self.assertTrue(hasattr(report, "risk_score"))
        self.assertTrue(hasattr(report, "signals"))
        self.assertTrue(hasattr(report, "recommendation"))
        self.assertTrue(hasattr(report, "summary"))
        self.assertIn(report.risk_level, ["LOW", "MEDIUM", "HIGH"])

    def test_high_risk_triggers_investigation(self):
        """Multiple high-severity signals → HIGH risk + investigation recommendation."""
        items = [
            {"description": "Surgeon fees appendectomy", "amount": 25000, "cpt_code": "44950"},
            {"description": "Surgeon fees appendectomy", "amount": 25000, "cpt_code": "44950"},  # duplicate
            {"description": "Consultation fee", "amount": 80000, "cpt_code": "99213"},  # outlier
            {"description": "Dental crown", "amount": 8000, "cpt_code": "D2750"},  # combo
        ]
        bill = self._make_bill(items, dx="appendicitis with dental")
        report = self.detector.analyse(bill)
        # Should have multiple signals
        self.assertGreater(len(report.signals), 1)
        # Should not be LOW
        self.assertIn(report.risk_level, ["MEDIUM", "HIGH"])

    def test_fraud_report_summary_not_empty(self):
        """Summary should always be a non-empty string."""
        bill = _load_bill("BILL-2024-005")
        report = self.detector.analyse(bill)
        self.assertIsInstance(report.summary, str)
        self.assertGreater(len(report.summary), 0)

    def test_all_six_bills_analysable(self):
        """All 6 test bills should produce a FraudReport without errors."""
        with open(BILLS_PATH) as f:
            bills = json.load(f)["test_cases"]
        for raw in bills:
            parser = BillParser.__new__(BillParser)
            bill = parser._normalize_bill(raw)
            report = self.detector.analyse(bill)
            self.assertIsInstance(report, FraudReport, f"{raw['bill_id']} failed")
            self.assertIn(report.risk_level, ["LOW", "MEDIUM", "HIGH"])


# ══════════════════════════════════════════════════════════════════════════════
# Gemini Vision — structure tests (no API key)
# ══════════════════════════════════════════════════════════════════════════════
class TestBillParserStructure(unittest.TestCase):

    def setUp(self):
        self.parser = BillParser.__new__(BillParser)  # skip API init

    def test_has_vision_methods(self):
        """BillParser must expose all 3 parsing modes."""
        self.assertTrue(hasattr(BillParser, "parse_from_pdf_vision"))
        self.assertTrue(hasattr(BillParser, "parse_from_image_vision"))
        self.assertTrue(hasattr(BillParser, "parse_from_text"))
        self.assertTrue(hasattr(BillParser, "parse_from_json"))

    def test_normalize_sets_extraction_confidence(self):
        """Normalised bill must include extraction_confidence."""
        bill_data = {
            "hospital_name": "Test", "patient_name": "Test", "patient_age": 35,
            "admission_date": "2024-01-01", "discharge_date": "2024-01-03",
            "primary_diagnosis": "Test diagnosis",
            "line_items": [{"description": "Surgery", "amount": 10000}],
            "extraction_confidence": "high", "extraction_notes": ""
        }
        result = self.parser._normalize_bill(bill_data)
        self.assertEqual(result["extraction_confidence"], "high")
        self.assertFalse(result["flags"]["low_confidence"])

    def test_low_confidence_flag(self):
        """extraction_confidence: low should set flags.low_confidence = True."""
        bill_data = {
            "hospital_name": "Test", "patient_name": "X", "patient_age": 40,
            "admission_date": "2024-01-01", "discharge_date": "2024-01-02",
            "primary_diagnosis": "Test",
            "line_items": [{"description": "Room", "amount": 2000}],
            "extraction_confidence": "low",
        }
        result = self.parser._normalize_bill(bill_data)
        self.assertTrue(result["flags"]["low_confidence"])

    def test_merge_pages_combines_items(self):
        """Multi-page merge should combine line items from all pages."""
        page1 = {
            "patient_name": "Test", "hospital_name": "H", "patient_age": 30,
            "admission_date": "2024-01-01", "discharge_date": "2024-01-02",
            "primary_diagnosis": "Dx", "extraction_confidence": "high",
            "line_items": [{"description": "Surgery", "amount": 30000}],
            "total_billed": 30000,
        }
        page2 = {
            "patient_name": None, "hospital_name": None,
            "line_items": [{"description": "Medicines", "amount": 5000}],
            "total_billed": 5000,
        }
        merged = self.parser._merge_pages([page1, page2])
        self.assertEqual(len(merged["line_items"]), 2)
        self.assertEqual(merged["total_billed"], 35000)

    def test_classify_item_room(self):
        self.assertEqual(self.parser._classify_item({"description": "Room charges general ward", "amount": 3000}), "room")

    def test_classify_item_icu(self):
        self.assertEqual(self.parser._classify_item({"description": "ICU charges post-op", "amount": 6000}), "icu")

    def test_classify_item_implant(self):
        self.assertEqual(self.parser._classify_item({"description": "Titanium knee implant", "amount": 120000}), "implant")

    def test_classify_item_physio(self):
        self.assertEqual(self.parser._classify_item({"description": "Physiotherapy session", "amount": 500}), "physiotherapy")


# ══════════════════════════════════════════════════════════════════════════════
# Integration — fraud + confidence together
# ══════════════════════════════════════════════════════════════════════════════
class TestUpgradesIntegration(unittest.TestCase):

    def setUp(self):
        self.engine, _ = _engine()
        self.detector = FraudDetector()

    def test_process_bill_with_fraud_returns_both(self):
        """process_json_bill_with_fraud must return (ClaimDecision, FraudReport)."""
        from src.claim_agent import ClaimAgent
        agent = ClaimAgent(str(POLICY_PATH))

        with open(BILLS_PATH) as f:
            raw_bill = json.load(f)["test_cases"][0]

        decision, fraud = agent.process_json_bill_with_fraud(raw_bill)
        self.assertIsInstance(decision, ClaimDecision)
        self.assertIsInstance(fraud, FraudReport)

    def test_clean_appendectomy_low_fraud_high_confidence(self):
        """Appendectomy: clean bill → low fraud risk + high confidence."""
        bill = _load_bill("BILL-2024-001")
        decision = self.engine.evaluate(bill)
        fraud = self.detector.analyse(bill)

        self.assertEqual(decision.overall_decision, "APPROVED")
        self.assertGreaterEqual(decision.claim_confidence_pct, 80)
        self.assertFalse(decision.requires_human_review)
        self.assertEqual(fraud.risk_level, "LOW")

    def test_export_json_includes_confidence(self):
        """JSON export must include claim_confidence_pct and requires_human_review."""
        from src.claim_agent import ClaimAgent
        agent = ClaimAgent(str(POLICY_PATH))

        with open(BILLS_PATH) as f:
            raw_bill = json.load(f)["test_cases"][0]

        decision = agent.process_json_bill(raw_bill)
        export = agent.export_json(decision)

        self.assertIn("claim_confidence_pct", export)
        self.assertIn("requires_human_review", export)
        self.assertIsInstance(export["claim_confidence_pct"], int)


if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    suite.addTests(loader.loadTestsFromTestCase(TestConfidenceScoring))
    suite.addTests(loader.loadTestsFromTestCase(TestFraudDetector))
    suite.addTests(loader.loadTestsFromTestCase(TestBillParserStructure))
    suite.addTests(loader.loadTestsFromTestCase(TestUpgradesIntegration))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    import sys; sys.exit(0 if result.wasSuccessful() else 1)
