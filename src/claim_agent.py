"""
claim_agent.py
==============
Main orchestrator for the Insurance Claim Settlement Agent.
Coordinates OCR → Bill Parsing (Google Gemini AI) → Policy Matching → Rule Engine → Report.
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional, Union

from .bill_parser import BillParser
from .fraud_detector import FraudDetector, FraudReport
from .preauth_predictor import PreAuthPredictor, CoveragePrediction, format_prediction_report
from .policy_parser import PolicyParser
from .rule_engine import RuleEngine, ClaimDecision
from .ocr_extractor import OCRExtractor

logger = logging.getLogger(__name__)


class ClaimAgent:
    """
    End-to-end insurance claim settlement agent.

    Pipeline:
        Input (PDF/Image/JSON) → OCR → Bill Parser → Policy Parser
                              → Rule Engine → Decision Report
    """

    def __init__(self, policy_path: str):
        """
        Initialize the agent with a policy document.

        Args:
            policy_path: Path to the insurance policy JSON file.
        """
        self.policy = PolicyParser(policy_path)
        self.rule_engine = RuleEngine(self.policy)
        self._bill_parser = None   # lazy — only init when PDF/text processing is needed
        self.ocr = OCRExtractor()
        self.fraud_detector = FraudDetector()
        self._preauth_predictor = None   # lazy — only init when API key present
        logger.info(f"ClaimAgent initialized with policy: {self.policy.get_summary()['policy_name']}")

    @property
    def preauth_predictor(self) -> PreAuthPredictor:
        if self._preauth_predictor is None:
            self._preauth_predictor = PreAuthPredictor(self.policy, self.rule_engine)
        return self._preauth_predictor

    @property
    def bill_parser(self) -> BillParser:
        """Lazily init BillParser so GEMINI_API_KEY is only required for PDF/OCR input."""
        if self._bill_parser is None:
            self._bill_parser = BillParser()
        return self._bill_parser

    def process_pdf_bill(self, pdf_path: str) -> ClaimDecision:
        """Process a scanned or digital hospital bill PDF."""
        logger.info(f"Processing PDF bill: {pdf_path}")
        text = self.ocr.extract_from_pdf(pdf_path)
        bill = self.bill_parser.parse_from_text(text)
        return self.rule_engine.evaluate(bill)

    def process_json_bill(self, bill_data: dict) -> ClaimDecision:
        """Process a structured bill dictionary — no Gemini API key required."""
        # Use _normalize_bill directly: avoids API init for structured JSON input
        parser = BillParser.__new__(BillParser)
        bill = parser._normalize_bill(bill_data)
        return self.rule_engine.evaluate(bill)

    def process_json_file(self, json_path: str, bill_id: Optional[str] = None) -> Union[ClaimDecision, list]:
        """
        Process bills from a JSON test file.
        If bill_id given, process one. Otherwise, process all.
        """
        import json as _json
        with open(json_path) as f:
            data = _json.load(f)

        bills = data.get("test_cases", [data])

        if bill_id:
            for b in bills:
                if b.get("bill_id") == bill_id:
                    return self.process_json_bill(b)
            raise ValueError(f"Bill {bill_id} not found in {json_path}")

        return [self.process_json_bill(b) for b in bills]

    def process_json_bill_with_fraud(self, bill_data: dict) -> tuple:
        """Process bill and run fraud detection in parallel. Returns (ClaimDecision, FraudReport)."""
        parser = BillParser.__new__(BillParser)
        bill = parser._normalize_bill(bill_data)
        decision = self.rule_engine.evaluate(bill)
        fraud_report = self.fraud_detector.analyse(bill)
        return decision, fraud_report

    def process_pdf_bill_vision(self, pdf_path: str) -> ClaimDecision:
        """Process PDF using Gemini Vision (no OCR step — sends images directly to Gemini)."""
        logger.info(f"Vision pipeline: {pdf_path}")
        bill = self.bill_parser.parse_from_pdf_vision(pdf_path)
        return self.rule_engine.evaluate(bill)

    def process_image_bill_vision(self, image_path: str) -> ClaimDecision:
        """Process a bill photo using Gemini Vision — for WhatsApp submissions."""
        bill = self.bill_parser.parse_from_image_vision(image_path)
        return self.rule_engine.evaluate(bill)

    def predict_coverage(self, condition: str, policy_start: str, age: int) -> CoveragePrediction:
        """Proactive pre-admission coverage prediction."""
        return self.preauth_predictor.predict(condition, policy_start, age)

    def format_prediction_report(self, pred: CoveragePrediction) -> str:
        return format_prediction_report(pred)

    def format_report(self, decision: ClaimDecision) -> str:
        """
        Format a ClaimDecision into a human-readable text report.
        """
        lines = []
        sep = "=" * 70

        lines.append(sep)
        lines.append("        INSURANCE CLAIM SETTLEMENT REPORT")
        lines.append(sep)
        lines.append(f"  Bill ID       : {decision.bill_id}")
        lines.append(f"  Patient       : {decision.patient_name}")
        lines.append(f"  Hospital      : {decision.hospital}")
        lines.append(f"  Diagnosis     : {decision.diagnosis}")
        lines.append(f"  Generated At  : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append(sep)

        # Overall decision banner
        verdict_char = {"APPROVED": "✅", "REJECTED": "❌", "PARTIAL": "⚠️ "}.get(
            decision.overall_decision, "?"
        )
        lines.append(f"\n  OVERALL DECISION: {verdict_char}  {decision.overall_decision}\n")

        # Financial Summary
        lines.append("  FINANCIAL SUMMARY")
        lines.append("  " + "-" * 40)
        lines.append(f"  Total Billed      : ₹{decision.total_billed:>12,.2f}")
        lines.append(f"  Total Approved    : ₹{decision.total_approved:>12,.2f}")
        lines.append(f"  Total Rejected    : ₹{decision.total_rejected:>12,.2f}")
        if decision.copayment > 0:
            lines.append(f"  Co-payment (10%)  : ₹{decision.copayment:>12,.2f}")
        lines.append(f"  NET PAYABLE       : ₹{decision.net_payable:>12,.2f}")
        lines.append("")

        # Blocking rejections
        if decision.blocking_rejections:
            lines.append("  REJECTION REASONS")
            lines.append("  " + "-" * 40)
            for r in decision.blocking_rejections:
                lines.append(f"  • {r}")
            lines.append("")

        # Notes
        if decision.notes:
            lines.append("  NOTES & WARNINGS")
            lines.append("  " + "-" * 40)
            for note in decision.notes:
                lines.append(f"  {note}")
            lines.append("")

        # Line item breakdown
        lines.append("  LINE ITEM BREAKDOWN")
        lines.append("  " + "-" * 68)
        lines.append(f"  {'Description':<38} {'Billed':>10} {'Approved':>10} {'Status':<10}")
        lines.append("  " + "-" * 68)

        for ld in decision.line_decisions:
            icon = {"APPROVED": "✅", "REJECTED": "❌", "PARTIAL": "⚠️ "}.get(ld.decision, "?")
            desc = ld.description[:37]
            lines.append(
                f"  {desc:<38} ₹{ld.billed_amount:>9,.0f} ₹{ld.approved_amount:>9,.0f}  {icon} {ld.decision}"
            )
            if ld.decision != "APPROVED" and ld.reason:
                lines.append(f"    ↳ {ld.reason}")
            if ld.citation and ld.decision != "APPROVED":
                lines.append(f"    ↳ Citation: {ld.citation}")

        lines.append("  " + "-" * 68)

        # Citations used
        if decision.citations_used:
            lines.append("\n  POLICY CITATIONS REFERENCED")
            lines.append("  " + "-" * 40)
            for i, cite in enumerate(decision.citations_used, 1):
                lines.append(f"  [{i}] {cite}")

        lines.append("\n" + sep)
        lines.append("  This report was generated by the AI-powered Claim Settlement Agent.")
        lines.append("  For disputes, contact claims@securelife.in or call 1800-XXX-XXXX.")
        lines.append(sep + "\n")

        return "\n".join(lines)

    def export_json(self, decision: ClaimDecision) -> dict:
        """Export decision as a JSON-serializable dictionary."""
        return {
            "bill_id": decision.bill_id,
            "patient_name": decision.patient_name,
            "hospital": decision.hospital,
            "diagnosis": decision.diagnosis,
            "overall_decision": decision.overall_decision,
            "financial_summary": {
                "total_billed": decision.total_billed,
                "total_approved": decision.total_approved,
                "total_rejected": decision.total_rejected,
                "copayment": decision.copayment,
                "net_payable": decision.net_payable
            },
            "blocking_rejections": decision.blocking_rejections,
            "line_items": [
                {
                    "description": ld.description,
                    "billed_amount": ld.billed_amount,
                    "approved_amount": ld.approved_amount,
                    "rejected_amount": ld.rejected_amount,
                    "decision": ld.decision,
                    "reason": ld.reason,
                    "citation": ld.citation,
                    "citation_text": ld.citation_text
                }
                for ld in decision.line_decisions
            ],
            "citations_used": decision.citations_used,
            "notes": decision.notes,
            "generated_at": datetime.now().isoformat(),
            "claim_confidence_pct": decision.claim_confidence_pct,
            "requires_human_review": decision.requires_human_review,
        }
