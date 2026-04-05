"""
preauth_predictor.py
====================
Proactive coverage predictor — tells patients what will be covered BEFORE hospitalisation.

Pipeline:
  Patient describes condition in plain text (any language)
      ↓ Gemini maps it to likely procedures + CPT codes
      ↓ Rule engine evaluates each predicted procedure
      ↓ Returns coverage prediction + pre-admission checklist

This flips the system from reactive (post-billing) to proactive (pre-admission).
No other team will have this. This is the pitch-day demo moment.
"""

import json
import re
import os
import logging
from dataclasses import dataclass, field
from typing import Optional
from google import genai

logger = logging.getLogger(__name__)
GEMINI_MODEL = "gemini-2.0-flash"


PREDICTION_PROMPT = """You are an expert health insurance advisor in India.

A patient has described their medical condition. Predict:
1. The most likely procedures they will need
2. The ICD-10 diagnosis codes
3. Estimated cost ranges in INR for each procedure
4. Documents they should collect before hospitalisation

Patient description: "{condition}"
Policy start date: {policy_start}
Patient age: {age}

Return ONLY valid JSON (no markdown):
{{
  "predicted_diagnosis": "primary diagnosis in medical terms",
  "icd_codes": ["ICD-10 codes"],
  "predicted_procedures": [
    {{
      "name": "procedure name",
      "cpt_code": "CPT code or null",
      "estimated_cost_min": number,
      "estimated_cost_max": number,
      "is_elective": true or false,
      "requires_preauth": true or false
    }}
  ],
  "estimated_total_min": number,
  "estimated_total_max": number,
  "urgency": "emergency|elective|semi-urgent",
  "recommended_documents": ["list of documents to collect"],
  "plain_english_summary": "2-sentence plain language summary of what this condition is",
  "language_detected": "english|hindi|telugu|tamil|other"
}}"""


@dataclass
class ProcedurePrediction:
    name: str
    cpt_code: Optional[str]
    estimated_cost_min: float
    estimated_cost_max: float
    is_elective: bool
    requires_preauth: bool
    coverage_decision: str = "UNKNOWN"   # filled by rule engine
    approved_amount: float = 0.0
    rejected_reason: str = ""
    citation: str = ""


@dataclass
class CoveragePrediction:
    """Complete pre-admission coverage prediction."""
    condition_input: str
    predicted_diagnosis: str
    icd_codes: list[str]
    urgency: str
    procedures: list[ProcedurePrediction]
    estimated_total_min: float
    estimated_total_max: float
    estimated_covered_min: float
    estimated_covered_max: float
    estimated_out_of_pocket_min: float
    estimated_out_of_pocket_max: float
    copayment_applicable: bool
    copayment_pct: int
    recommended_documents: list[str]
    preauth_required: bool
    plain_english_summary: str
    checklist: list[str]
    warnings: list[str] = field(default_factory=list)
    language_detected: str = "english"


class PreAuthPredictor:
    """
    Predicts insurance coverage for a described medical condition
    before the patient is hospitalised.
    """

    def __init__(self, policy_parser, rule_engine):
        api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not api_key:
            raise EnvironmentError("Set GEMINI_API_KEY to use pre-auth predictor.")
        self.client    = genai.Client(api_key=api_key)
        self.policy    = policy_parser
        self.engine    = rule_engine

    def predict(
        self,
        condition_description: str,
        policy_start_date: str,
        patient_age: int,
        policy_number: str = "POL-HEALTH-2024-001"
    ) -> CoveragePrediction:
        """
        Main entry point.

        Args:
            condition_description: Free-text patient description in any language.
                                   e.g. "I have knee pain and my doctor says I need surgery"
                                        "मुझे घुटने में दर्द है और डॉक्टर ने ऑपरेशन बताया है"
            policy_start_date: "YYYY-MM-DD"
            patient_age: int
            policy_number: str

        Returns:
            CoveragePrediction with full breakdown and checklist.
        """
        logger.info(f"Predicting coverage for: '{condition_description[:60]}...'")

        # Step 1: Gemini maps condition → procedures + cost estimates
        gemini_resp = self._gemini_predict(condition_description, policy_start_date, patient_age)

        # Step 2: Build a synthetic bill from the predicted procedures
        synthetic_bill = self._build_synthetic_bill(
            gemini_resp, policy_start_date, patient_age, policy_number
        )

        # Step 3: Run rule engine on the synthetic bill
        decision = self.engine.evaluate(synthetic_bill)

        # Step 4: Map rule engine output back to procedure predictions
        procedures = self._map_decisions(gemini_resp, decision)

        # Step 5: Calculate financial estimates
        covered_min, covered_max, oop_min, oop_max = self._estimate_financials(
            procedures, decision, patient_age
        )

        # Step 6: Build checklist
        checklist = self._build_checklist(gemini_resp, decision, patient_age)
        warnings  = self._build_warnings(decision, gemini_resp, policy_start_date)

        return CoveragePrediction(
            condition_input       = condition_description,
            predicted_diagnosis   = gemini_resp.get("predicted_diagnosis", ""),
            icd_codes             = gemini_resp.get("icd_codes", []),
            urgency               = gemini_resp.get("urgency", "elective"),
            procedures            = procedures,
            estimated_total_min   = float(gemini_resp.get("estimated_total_min", 0)),
            estimated_total_max   = float(gemini_resp.get("estimated_total_max", 0)),
            estimated_covered_min = covered_min,
            estimated_covered_max = covered_max,
            estimated_out_of_pocket_min = oop_min,
            estimated_out_of_pocket_max = oop_max,
            copayment_applicable  = patient_age >= 61,
            copayment_pct         = 10 if patient_age >= 61 else 0,
            recommended_documents = gemini_resp.get("recommended_documents", []),
            preauth_required      = any(p.requires_preauth for p in procedures),
            plain_english_summary = gemini_resp.get("plain_english_summary", ""),
            checklist             = checklist,
            warnings              = warnings,
            language_detected     = gemini_resp.get("language_detected", "english"),
        )

    # ── Internal ──────────────────────────────────────────────────────────────

    def _gemini_predict(self, condition: str, policy_start: str, age: int) -> dict:
        prompt = PREDICTION_PROMPT.format(
            condition=condition,
            policy_start=policy_start,
            age=age
        )
        resp = self.client.models.generate_content(
            model=GEMINI_MODEL, contents=prompt
        )
        raw = re.sub(r"```(?:json)?\n?", "", resp.text.strip()).strip().rstrip("`")
        return json.loads(raw)

    def _build_synthetic_bill(self, gemini_resp: dict, policy_start: str, age: int, policy_number: str) -> dict:
        """Build a fake bill from Gemini's predictions to run through the rule engine."""
        procs = gemini_resp.get("predicted_procedures", [])
        # Use the midpoint of the estimate for each procedure
        line_items = []
        for p in procs:
            mid = (float(p.get("estimated_cost_min", 0)) + float(p.get("estimated_cost_max", 0))) / 2
            line_items.append({
                "description": p.get("name", "Procedure"),
                "amount":      mid,
                "cpt_code":    p.get("cpt_code", ""),
                "days":        1,
            })

        return {
            "bill_id":  "PREDICT-001",
            "hospital": "Predicted",
            "patient": {
                "name":              "Patient",
                "age":               age,
                "policy_number":     policy_number,
                "policy_start_date": policy_start,
            },
            "admission_date":             "2025-01-01",  # future date
            "discharge_date":             "2025-01-03",
            "diagnosis":                  gemini_resp.get("predicted_diagnosis", ""),
            "diagnosis_codes":            gemini_resp.get("icd_codes", []),
            "pre_authorization_obtained": False,
            "line_items":                 line_items,
            "total_billed":               sum(i["amount"] for i in line_items),
            "flags": {
                "contains_dental":    False,
                "contains_cosmetic":  False,
                "contains_maternity": False,
                "contains_psychiatric": False,
                "contains_dental_code": False,
                "low_confidence":     False,
            }
        }

    def _map_decisions(self, gemini_resp: dict, decision) -> list[ProcedurePrediction]:
        procs = gemini_resp.get("predicted_procedures", [])
        result = []
        for i, p in enumerate(procs):
            ld = decision.line_decisions[i] if i < len(decision.line_decisions) else None
            pp = ProcedurePrediction(
                name               = p.get("name", ""),
                cpt_code           = p.get("cpt_code"),
                estimated_cost_min = float(p.get("estimated_cost_min", 0)),
                estimated_cost_max = float(p.get("estimated_cost_max", 0)),
                is_elective        = p.get("is_elective", True),
                requires_preauth   = p.get("requires_preauth", False),
            )
            if ld:
                pp.coverage_decision = ld.decision
                pp.approved_amount   = ld.approved_amount
                pp.rejected_reason   = ld.reason if ld.decision != "APPROVED" else ""
                pp.citation          = ld.citation or ""
            result.append(pp)
        return result

    def _estimate_financials(self, procedures, decision, age):
        total_min = sum(p.estimated_cost_min for p in procedures)
        total_max = sum(p.estimated_cost_max for p in procedures)

        # Rough coverage ratio based on rule engine result
        if decision.total_billed > 0:
            ratio = decision.total_approved / decision.total_billed
        else:
            ratio = 0.8

        covered_min = total_min * ratio
        covered_max = total_max * ratio

        # Co-payment
        copay = 0.10 if age >= 61 else 0
        oop_min = total_min - covered_min * (1 - copay)
        oop_max = total_max - covered_max * (1 - copay)

        return (
            round(covered_min), round(covered_max),
            round(max(0, oop_min)), round(max(0, oop_max))
        )

    def _build_checklist(self, gemini_resp: dict, decision, age: int) -> list[str]:
        checklist = []
        urgency = gemini_resp.get("urgency", "elective")

        # Pre-auth
        if any(p.get("requires_preauth") for p in gemini_resp.get("predicted_procedures", [])):
            checklist.append("Obtain pre-authorisation from insurer BEFORE admission (call 1800-XXX-XXXX or use insurer portal)")

        # Documents
        checklist.append("Carry original policy document and a photo ID to the hospital")
        checklist.append("Get a referral letter from your treating physician stating diagnosis and recommended procedure")

        for doc in gemini_resp.get("recommended_documents", [])[:4]:
            checklist.append(f"Collect: {doc}")

        # Age-specific
        if age >= 61:
            checklist.append("Note: 10% co-payment applies as you are above 61 years. Arrange ₹{} as your share.".format(
                round(decision.total_billed * 0.10 * 0.5)  # rough estimate
            ))

        # Emergency vs elective
        if urgency == "emergency":
            checklist.append("For emergencies: inform insurer within 24 hours of admission")
        else:
            checklist.append("For elective procedures: inform insurer at least 3 days before planned admission")

        # Network hospital
        checklist.append("Verify your hospital is on the insurer's network for cashless treatment")

        return checklist

    def _build_warnings(self, decision, gemini_resp: dict, policy_start: str) -> list[str]:
        warnings = []
        if decision.blocking_rejections:
            warnings.extend(decision.blocking_rejections)
        if decision.total_rejected > 0:
            warnings.append(
                f"Based on the predicted procedures, approximately ₹{decision.total_rejected:,.0f} "
                "may not be covered — please arrange funds for this amount."
            )
        if decision.notes:
            warnings.extend([n for n in decision.notes if "co-payment" in n.lower() or "waiting" in n.lower()])
        return warnings


def format_prediction_report(pred: CoveragePrediction) -> str:
    """Format a CoveragePrediction as a human-readable report."""
    lines = []
    sep = "=" * 65

    lines.append(sep)
    lines.append("   PRE-ADMISSION COVERAGE PREDICTION")
    lines.append("   Insurance Claim Settlement Agent — Powered by Gemini AI")
    lines.append(sep)
    lines.append(f"\n  Condition:  {pred.condition_input[:60]}")
    lines.append(f"  Diagnosis:  {pred.predicted_diagnosis}")
    lines.append(f"  Urgency:    {pred.urgency.upper()}")
    if pred.icd_codes:
        lines.append(f"  ICD Codes:  {', '.join(pred.icd_codes)}")
    lines.append("")
    lines.append(f"  {pred.plain_english_summary}")
    lines.append("")

    lines.append("  PREDICTED PROCEDURES & COVERAGE")
    lines.append("  " + "-" * 61)
    for p in pred.procedures:
        icon = {"APPROVED": "✅", "REJECTED": "❌", "PARTIAL": "⚠️ "}.get(p.coverage_decision, "?")
        lines.append(f"  {icon} {p.name}")
        lines.append(f"      Estimated cost: ₹{p.estimated_cost_min:,.0f} – ₹{p.estimated_cost_max:,.0f}")
        if p.coverage_decision == "APPROVED":
            lines.append(f"      Expected coverage: ₹{p.approved_amount:,.0f} ✓")
        elif p.coverage_decision in ("REJECTED", "PARTIAL"):
            lines.append(f"      Coverage issue: {p.rejected_reason}")
            if p.citation:
                lines.append(f"      Citation: {p.citation}")
    lines.append("")

    lines.append("  FINANCIAL ESTIMATE")
    lines.append("  " + "-" * 40)
    lines.append(f"  Total estimated cost : ₹{pred.estimated_total_min:,.0f} – ₹{pred.estimated_total_max:,.0f}")
    lines.append(f"  Expected covered     : ₹{pred.estimated_covered_min:,.0f} – ₹{pred.estimated_covered_max:,.0f}")
    if pred.copayment_applicable:
        lines.append(f"  Co-payment ({pred.copayment_pct}%)      : applicable (age ≥61)")
    lines.append(f"  Est. out-of-pocket   : ₹{pred.estimated_out_of_pocket_min:,.0f} – ₹{pred.estimated_out_of_pocket_max:,.0f}")
    lines.append("")

    if pred.warnings:
        lines.append("  ⚠️  WARNINGS")
        lines.append("  " + "-" * 40)
        for w in pred.warnings:
            lines.append(f"  • {w}")
        lines.append("")

    lines.append("  YOUR PRE-ADMISSION CHECKLIST")
    lines.append("  " + "-" * 40)
    for i, item in enumerate(pred.checklist, 1):
        lines.append(f"  {i}. {item}")

    lines.append("\n" + sep)
    lines.append("  This is a prediction only. Actual coverage depends on")
    lines.append("  final diagnosis, treatment received, and policy terms.")
    lines.append(sep + "\n")

    return "\n".join(lines)
