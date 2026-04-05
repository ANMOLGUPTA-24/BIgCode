"""
rule_engine.py
==============
Deterministic rule-based engine that evaluates each bill line item against
the insurance policy, producing an itemized decision with precise citations.
"""

import logging
from dataclasses import dataclass, field
from typing import Optional
from .policy_parser import PolicyParser, PolicyClause

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------ #
#  Data Structures                                                     #
# ------------------------------------------------------------------ #

@dataclass
class LineItemDecision:
    """Decision for a single bill line item."""
    description: str
    billed_amount: float
    decision: str           # "APPROVED" | "REJECTED" | "PARTIAL"
    approved_amount: float
    rejected_amount: float
    reason: str
    citation: Optional[str] = None
    citation_text: Optional[str] = None
    confidence_pct: int = 100       # 0-100
    confidence_reason: str = ""     # why confidence is below 100


@dataclass
class ClaimDecision:
    """Final claim decision with all line item results and overall verdict."""
    bill_id: str
    patient_name: str
    hospital: str
    diagnosis: str
    total_billed: float
    total_approved: float
    total_rejected: float
    copayment: float
    net_payable: float
    overall_decision: str   # "APPROVED" | "REJECTED" | "PARTIAL"
    line_decisions: list[LineItemDecision] = field(default_factory=list)
    blocking_rejections: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    citations_used: list[str] = field(default_factory=list)
    claim_confidence_pct: int = 100       # 0-100 confidence in this decision
    requires_human_review: bool = False   # True when confidence < 80


# ------------------------------------------------------------------ #
#  Rule Engine                                                         #
# ------------------------------------------------------------------ #

class RuleEngine:
    """
    Evaluates insurance claims against policy rules.
    Applies rules in priority order:
      1. Waiting period checks (claim-level blockers)
      2. Exclusion checks (claim-level blockers)
      3. Pre-authorization checks
      4. Line item limits and sub-limits
      5. Co-payment calculation
    """

    def __init__(self, policy: PolicyParser):
        self.policy = policy

    def evaluate(self, bill: dict) -> ClaimDecision:
        """
        Main entry point. Evaluate a parsed bill against the policy.
        Returns a ClaimDecision with full itemized breakdown.
        """
        logger.info(f"Evaluating claim {bill.get('bill_id', 'UNKNOWN')}")

        patient = bill.get("patient", {})
        flags = bill.get("flags", {})

        decision = ClaimDecision(
            bill_id=bill.get("bill_id", "UNKNOWN"),
            patient_name=patient.get("name", ""),
            hospital=bill.get("hospital", ""),
            diagnosis=bill.get("diagnosis", ""),
            total_billed=bill.get("total_billed", 0),
            total_approved=0,
            total_rejected=0,
            copayment=0,
            net_payable=0,
            overall_decision="APPROVED"
        )

        # ---- Step 1: Claim-Level Blockers --------------------------------
        blocking = self._check_claim_level_blockers(bill, flags)
        if blocking:
            # Entire claim is rejected
            decision.blocking_rejections = [r["reason"] for r in blocking]
            decision.citations_used = [r["citation"] for r in blocking if r.get("citation")]
            for r in blocking:
                decision.notes.append(f"⛔ {r['reason']}")
                if r.get("citation"):
                    decision.notes.append(f"   ↳ Citation: {r['citation']}")
                if r.get("citation_text"):
                    decision.notes.append(f"   ↳ Policy text: \"{r['citation_text'][:150]}...\"")

            # All line items rejected
            for item in bill.get("line_items", []):
                decision.line_decisions.append(LineItemDecision(
                    description=item["description"],
                    billed_amount=item["amount"],
                    decision="REJECTED",
                    approved_amount=0,
                    rejected_amount=item["amount"],
                    reason=blocking[0]["reason"],
                    citation=blocking[0].get("citation"),
                    citation_text=blocking[0].get("citation_text")
                ))

            decision.total_rejected = decision.total_billed
            decision.overall_decision = "REJECTED"
            return decision

        # ---- Step 2: Pre-Authorization Check ----------------------------
        preauth_warnings = self._check_preauthorization(bill)
        for w in preauth_warnings:
            decision.notes.append(f"⚠️  {w}")

        # ---- Step 3: Line Item Evaluation --------------------------------
        for item in bill.get("line_items", []):
            item_decision = self._evaluate_line_item(item, bill, flags)
            decision.line_decisions.append(item_decision)
            decision.total_approved += item_decision.approved_amount
            decision.total_rejected += item_decision.rejected_amount
            if item_decision.citation:
                if item_decision.citation not in decision.citations_used:
                    decision.citations_used.append(item_decision.citation)

        # ---- Step 4: Co-payment ----------------------------------------
        age = patient.get("age")
        if age:
            copay, copay_clause = self.policy.calculate_copayment(age, decision.total_approved)
            if copay > 0:
                decision.copayment = round(copay, 2)
                decision.notes.append(
                    f"ℹ️  10% co-payment applied for patient aged {age} years "
                    f"(≥61 years). Co-pay: ₹{copay:,.2f}"
                )
                if copay_clause:
                    decision.notes.append(f"   ↳ Citation: {copay_clause.citation()}")
                    if copay_clause.citation() not in decision.citations_used:
                        decision.citations_used.append(copay_clause.citation())

        # ---- Step 5: Sum Insured Check ----------------------------------
        sum_insured = self.policy.get_sum_insured()
        if decision.total_approved > sum_insured:
            excess = decision.total_approved - sum_insured
            decision.notes.append(
                f"⚠️  Approved amount ₹{decision.total_approved:,.0f} exceeds "
                f"sum insured ₹{sum_insured:,.0f}. Capped to sum insured."
            )
            decision.total_approved = sum_insured
            decision.total_rejected += excess
            si_clause = self.policy.get_clause("S1.P2")
            if si_clause:
                decision.notes.append(f"   ↳ Citation: {si_clause.citation()}")

        # ---- Step 6: Final Net Payable ----------------------------------
        decision.net_payable = round(
            max(0, decision.total_approved - decision.copayment), 2
        )
        decision.total_approved = round(decision.total_approved, 2)
        decision.total_rejected = round(decision.total_rejected, 2)

        # ---- Step 7: Determine Overall Decision -------------------------
        if decision.total_approved == 0:
            decision.overall_decision = "REJECTED"
        elif decision.total_rejected > 0:
            decision.overall_decision = "PARTIAL"
        else:
            decision.overall_decision = "APPROVED"

        # ---- Step 8: Confidence Scoring ---------------------------------
        decision.claim_confidence_pct, decision.requires_human_review = (
            self._calculate_claim_confidence(decision, bill)
        )

        return decision

    # ------------------------------------------------------------------ #
    #  Internal Rule Methods                                               #
    # ------------------------------------------------------------------ #

    def _calculate_claim_confidence(self, decision, bill: dict) -> tuple[int, bool]:
        """
        Calculate overall claim confidence score (0-100).

        Deductions:
          - Low Gemini extraction confidence:  -25
          - Borderline amounts (within 5% of a limit): -10 each
          - Keyword-based (not exact CPT) rejection: -10 per item
          - Pre-auth missing on high-value claim: -15
          - Mixed decision (some approved, some rejected): -5
          - Partial items present: -5

        < 80 → flagged for human review.
        """
        score = 100
        reasons = []

        # Gemini extraction quality
        if bill.get("flags", {}).get("low_confidence"):
            score -= 25
            reasons.append("Low Gemini extraction confidence on bill input")

        # Borderline amounts — within 5% of a policy limit
        limits = self.policy.get_benefit_limits()
        for ld in decision.line_decisions:
            for limit_key, limit_val in limits.items():
                if isinstance(limit_val, (int, float)) and limit_val > 0:
                    if 0 < ld.billed_amount <= limit_val * 1.05 and ld.billed_amount >= limit_val * 0.95:
                        score -= 10
                        reasons.append(f"'{ld.description}' amount is within 5% of limit {limit_key}")
                        break

        # Keyword-based rejections (less certain than CPT code matches)
        keyword_rejects = [ld for ld in decision.line_decisions
                          if ld.decision == "REJECTED" and ld.citation and
                          "S3" in ld.citation and ld.billed_amount > 0]
        if keyword_rejects:
            score -= min(10 * len(keyword_rejects), 20)
            reasons.append(f"{len(keyword_rejects)} rejection(s) based on keyword match (not exact CPT code)")

        # Missing pre-auth on large claim
        if (not bill.get("pre_authorization_obtained") and
                decision.total_billed > 50000):
            score -= 15
            reasons.append("Pre-authorization not obtained for claim > ₹50,000")

        # Partial decisions introduce uncertainty
        partials = [ld for ld in decision.line_decisions if ld.decision == "PARTIAL"]
        if partials:
            score -= 5

        score = max(0, min(100, score))

        # Update individual line item confidences
        for ld in decision.line_decisions:
            if ld.decision == "APPROVED":
                ld.confidence_pct = min(score + 5, 100)
            elif ld.decision == "REJECTED" and ld.citation:
                # Check if it's an exact CPT match (high confidence) or keyword match (lower)
                cpt = ld.description.lower()
                ld.confidence_pct = 98 if any(c in ld.citation for c in ["S4","S3"]) else 88
            elif ld.decision == "PARTIAL":
                ld.confidence_pct = 92
            if ld.confidence_pct < 85:
                ld.confidence_reason = "; ".join(reasons[:2])

        requires_review = score < 80
        return score, requires_review

    def _check_claim_level_blockers(self, bill: dict, flags: dict) -> list[dict]:
        """Check rules that reject the ENTIRE claim."""
        blockers = []

        # 1. Waiting period
        policy_start = bill.get("patient", {}).get("policy_start_date")
        admission_date = bill.get("admission_date")
        diagnosis = bill.get("diagnosis", "")

        if policy_start and admission_date:
            in_waiting, clause, reason = self.policy.check_waiting_period(
                diagnosis, policy_start, admission_date
            )
            if in_waiting:
                blockers.append({
                    "reason": reason,
                    "citation": clause.citation() if clause else None,
                    "citation_text": clause.text if clause else None
                })
                return blockers  # No point checking further

        # 2. Dental exclusion
        if flags.get("contains_dental") or flags.get("contains_dental_code"):
            clause = self.policy.get_clause("S3.P3")
            blockers.append({
                "reason": "Claim involves dental treatment, which is excluded under this policy.",
                "citation": clause.citation() if clause else None,
                "citation_text": clause.text if clause else None
            })
            return blockers

        # 3. Maternity exclusion
        if flags.get("contains_maternity"):
            clause = self.policy.get_clause("S3.P4")
            blockers.append({
                "reason": "Claim involves maternity/pregnancy-related treatment, which is excluded.",
                "citation": clause.citation() if clause else None,
                "citation_text": clause.text if clause else None
            })
            return blockers

        # 4. Psychiatric exclusion
        if flags.get("contains_psychiatric"):
            clause = self.policy.get_clause("S3.P5")
            blockers.append({
                "reason": "Claim involves psychiatric/mental health treatment, which is excluded.",
                "citation": clause.citation() if clause else None,
                "citation_text": clause.text if clause else None
            })

        return blockers

    def _check_preauthorization(self, bill: dict) -> list[str]:
        """Check pre-authorization compliance. Returns warning strings."""
        warnings = []
        preauth_obtained = bill.get("pre_authorization_obtained", False)
        total = bill.get("total_billed", 0)

        for item in bill.get("line_items", []):
            code = item.get("cpt_code", "")
            required, clause = self.policy.is_preauth_required(code, item.get("amount", 0))
            if required and not preauth_obtained:
                msg = (f"Pre-authorization required but not obtained for "
                       f"'{item['description']}' (CPT: {code}).")
                if clause:
                    msg += f" | {clause.citation()}"
                warnings.append(msg)

        if total > 50000 and not preauth_obtained:
            clause = self.policy.get_clause("S5.P1")
            msg = f"Total claim ₹{total:,.0f} exceeds ₹50,000 — pre-authorization mandatory."
            if clause:
                msg += f" | {clause.citation()}"
            warnings.append(msg)

        return list(dict.fromkeys(warnings))  # deduplicate

    def _evaluate_line_item(self, item: dict, bill: dict, flags: dict) -> LineItemDecision:
        """Evaluate a single bill line item against policy rules."""
        desc = item.get("description", "")
        amount = float(item.get("amount", 0))
        category = item.get("category", "other")
        cpt_code = item.get("cpt_code", "")
        days = item.get("days", 1)

        # --- Check CPT code exclusions ---
        excluded, ex_clause = self.policy.is_procedure_excluded(cpt_code)
        if excluded:
            return LineItemDecision(
                description=desc, billed_amount=amount,
                decision="REJECTED", approved_amount=0, rejected_amount=amount,
                reason=f"Procedure code {cpt_code} is explicitly excluded.",
                citation=ex_clause.citation() if ex_clause else None,
                citation_text=ex_clause.text if ex_clause else None
            )

        # --- Check text-based exclusions ---
        is_excluded, excl_clause, excl_reason = self.policy.check_exclusion_by_text(desc)
        if is_excluded:
            return LineItemDecision(
                description=desc, billed_amount=amount,
                decision="REJECTED", approved_amount=0, rejected_amount=amount,
                reason=excl_reason,
                citation=excl_clause.citation() if excl_clause else None,
                citation_text=excl_clause.text if excl_clause else None
            )

        # --- Room charges sub-limit ---
        if category == "room":
            room_type = desc.lower()
            limit, limit_clause = self.policy.calculate_room_rent_limit(room_type, days)
            if amount > limit:
                excess = amount - limit
                note = (f"Room rent ₹{amount:,.0f} exceeds policy limit "
                        f"₹{limit:,.0f} ({days} day(s) × ₹{limit // max(days,1):,.0f}/day). "
                        f"Excess ₹{excess:,.0f} not covered.")
                return LineItemDecision(
                    description=desc, billed_amount=amount,
                    decision="PARTIAL", approved_amount=limit, rejected_amount=excess,
                    reason=note,
                    citation=limit_clause.citation() if limit_clause else None,
                    citation_text=limit_clause.text if limit_clause else None
                )

        # --- ICU sub-limit ---
        if category == "icu":
            icu_limit, icu_clause = self.policy.calculate_icu_limit(days)
            if amount > icu_limit:
                excess = amount - icu_limit
                note = (f"ICU charges ₹{amount:,.0f} exceed policy limit "
                        f"₹{icu_limit:,.0f} ({days} day(s) × ₹{icu_limit // max(days,1):,.0f}/day).")
                return LineItemDecision(
                    description=desc, billed_amount=amount,
                    decision="PARTIAL", approved_amount=icu_limit, rejected_amount=excess,
                    reason=note,
                    citation=icu_clause.citation() if icu_clause else None,
                    citation_text=icu_clause.text if icu_clause else None
                )

        # --- Physiotherapy sub-limit ---
        if category == "physiotherapy":
            limits = self.policy.get_benefit_limits()
            per_session = limits.get("physiotherapy_per_session", 500)
            sessions = days  # "days" field reused for sessions
            limit_total = per_session * sessions
            if amount > limit_total:
                excess = amount - limit_total
                clause = self.policy.get_clause("S6.P4")
                return LineItemDecision(
                    description=desc, billed_amount=amount,
                    decision="PARTIAL", approved_amount=limit_total, rejected_amount=excess,
                    reason=f"Physiotherapy capped at ₹{per_session}/session × {sessions} sessions = ₹{limit_total:,.0f}.",
                    citation=clause.citation() if clause else None,
                    citation_text=clause.text if clause else None
                )

        # --- Implant sub-limit ---
        if category == "implant":
            limits = self.policy.get_benefit_limits()
            implant_limit = limits.get("prosthetics_implants", 50000)
            if amount > implant_limit:
                excess = amount - implant_limit
                clause = self.policy.get_clause("S6.P6")
                return LineItemDecision(
                    description=desc, billed_amount=amount,
                    decision="PARTIAL", approved_amount=implant_limit, rejected_amount=excess,
                    reason=f"Implant/prosthetics sub-limit ₹{implant_limit:,.0f} applies.",
                    citation=clause.citation() if clause else None,
                    citation_text=clause.text if clause else None
                )

        # --- Ambulance sub-limit ---
        if category == "ambulance":
            limits = self.policy.get_benefit_limits()
            amb_limit = limits.get("ambulance", 2000)
            if amount > amb_limit:
                excess = amount - amb_limit
                clause = self.policy.get_clause("S2.P5")
                return LineItemDecision(
                    description=desc, billed_amount=amount,
                    decision="PARTIAL", approved_amount=amb_limit, rejected_amount=excess,
                    reason=f"Ambulance charges capped at ₹{amb_limit:,.0f}.",
                    citation=clause.citation() if clause else None,
                    citation_text=clause.text if clause else None
                )

        # --- Cosmetic partial check (for mixed bills) ---
        if flags.get("contains_cosmetic") and any(
            kw in desc.lower() for kw in ["rhinoplasty", "liposuction", "cosmetic", "face lift", "hair transplant"]
        ):
            clause = self.policy.get_clause("S3.P2")
            return LineItemDecision(
                description=desc, billed_amount=amount,
                decision="REJECTED", approved_amount=0, rejected_amount=amount,
                reason="Cosmetic/aesthetic procedure explicitly excluded.",
                citation=clause.citation() if clause else None,
                citation_text=clause.text if clause else None
            )

        # --- Default: Approved ---
        coverage_clause = self.policy.get_clause("S2.P1")
        return LineItemDecision(
            description=desc, billed_amount=amount,
            decision="APPROVED", approved_amount=amount, rejected_amount=0,
            reason="Covered under in-patient hospitalization benefits.",
            citation=coverage_clause.citation() if coverage_clause else None
        )
