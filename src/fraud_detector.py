"""
fraud_detector.py
=================
Parallel fraud signal analysis that runs alongside the rule engine.
Detects: upcoding, duplicate billing, impossible procedure combos,
statistical amount outliers, and phantom charges.

Returns a FraudReport with risk level (LOW / MEDIUM / HIGH) and
specific evidence for each signal detected.

Insurance fraud costs Indian insurers ₹30,000+ crore annually.
This module catches it with evidence — not just flags it.
"""

import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


# ── Statistical benchmarks (INR) — based on IRDAI / NHA data ─────────────────
# City-tier median prices per day / per procedure
AMOUNT_BENCHMARKS = {
    "room_general_per_day":  {"low": 800,   "median": 2000,  "high": 4000},
    "room_private_per_day":  {"low": 2000,  "median": 5000,  "high": 10000},
    "icu_per_day":           {"low": 3000,  "median": 6000,  "high": 15000},
    "surgeon_appendix":      {"low": 15000, "median": 30000, "high": 60000},
    "surgeon_hernia":        {"low": 20000, "median": 40000, "high": 80000},
    "surgeon_knee":          {"low": 50000, "median": 85000, "high": 150000},
    "anesthesia":            {"low": 5000,  "median": 10000, "high": 25000},
    "ot_charges":            {"low": 8000,  "median": 18000, "high": 40000},
    "lab_basic":             {"low": 500,   "median": 2000,  "high": 5000},
    "consultation":          {"low": 300,   "median": 800,   "high": 2500},
    "physiotherapy_session": {"low": 200,   "median": 500,   "high": 1500},
    "xray":                  {"low": 200,   "median": 600,   "high": 1500},
    "ct_scan":               {"low": 2000,  "median": 4000,  "high": 8000},
    "mri":                   {"low": 4000,  "median": 7000,  "high": 15000},
    "ambulance":             {"low": 500,   "median": 1500,  "high": 3000},
}

# Impossible procedure combinations — cannot happen in one admission
IMPOSSIBLE_COMBOS = [
    (["appendectomy", "appendix"], ["dental", "tooth", "root canal"], "Appendectomy and dental procedure cannot occur in same admission"),
    (["knee replacement", "tkr", "arthroplasty"], ["dental", "ent", "nasal"], "Joint replacement and ENT/dental in same admission is suspicious"),
    (["cataract"], ["hernia", "appendix", "gallbladder"], "Cataract surgery and major abdominal surgery in same admission"),
    (["maternity", "delivery", "cesarean"], ["appendectomy", "hernia", "knee"], "Maternity and unrelated major surgery in same admission"),
]

# CPT ↔ diagnosis consistency checks
CPT_DIAGNOSIS_MAP = {
    "44950": ["appendicitis", "appendix", "K35", "K37"],
    "27447": ["knee", "osteoarthritis", "M17", "arthroplasty"],
    "49650": ["hernia", "inguinal", "K40"],
    "30520": ["nasal", "septum", "deviated", "J34"],
    "D3330": ["dental", "root canal", "caries", "tooth"],
    "66984": ["cataract", "lens", "eye", "H26"],
}


@dataclass
class FraudSignal:
    """A single detected fraud signal with evidence."""
    signal_type: str       # "duplicate" | "outlier" | "impossible_combo" | "upcoding" | "cpt_mismatch"
    severity: str          # "low" | "medium" | "high"
    description: str
    evidence: str
    affected_items: list[str] = field(default_factory=list)
    expected_range: str = ""
    actual_amount: float = 0.0


@dataclass
class FraudReport:
    """Complete fraud analysis for a claim."""
    bill_id: str
    risk_level: str              # "LOW" | "MEDIUM" | "HIGH"
    risk_score: int              # 0-100 (100 = definitely fraudulent)
    signals: list[FraudSignal]   = field(default_factory=list)
    recommendation: str          = ""
    summary: str                 = ""

    @property
    def is_suspicious(self) -> bool:
        return self.risk_score >= 40

    @property
    def high_signals(self) -> list:
        return [s for s in self.signals if s.severity == "high"]

    @property
    def signal_count(self) -> int:
        return len(self.signals)


class FraudDetector:
    """
    Runs parallel fraud analysis on a parsed bill.
    Completely independent of the rule engine — can be run alongside it.
    """

    def analyse(self, bill: dict) -> FraudReport:
        """
        Run all fraud checks and return a FraudReport.
        Call this in parallel with rule_engine.evaluate() for zero added latency.
        """
        signals: list[FraudSignal] = []
        items = bill.get("line_items", [])
        dx = (bill.get("diagnosis", "") or "").lower()
        hospital = (bill.get("hospital", "") or "").lower()

        # Run all checks
        signals.extend(self._check_duplicates(items))
        signals.extend(self._check_amount_outliers(items, bill))
        signals.extend(self._check_impossible_combos(items, dx))
        signals.extend(self._check_cpt_diagnosis_mismatch(items, dx))
        signals.extend(self._check_round_number_inflation(items))
        signals.extend(self._check_phantom_icu(bill, items))

        # Score and level
        score = self._calculate_risk_score(signals)
        level = "HIGH" if score >= 70 else "MEDIUM" if score >= 40 else "LOW"

        rec = self._recommendation(level, signals)
        summary = self._summary(signals, score)

        report = FraudReport(
            bill_id=bill.get("bill_id", "UNKNOWN"),
            risk_level=level,
            risk_score=score,
            signals=signals,
            recommendation=rec,
            summary=summary,
        )
        logger.info(f"Fraud analysis: {bill.get('bill_id')} → {level} (score {score}, {len(signals)} signals)")
        return report

    # ── Checks ────────────────────────────────────────────────────────────────

    def _check_duplicates(self, items: list) -> list[FraudSignal]:
        """Detect identical or near-identical line items billed multiple times."""
        signals = []
        seen = {}
        for item in items:
            desc = item["description"].lower().strip()
            # Normalise - remove numbers and parentheses for fuzzy match
            key = "".join(c for c in desc if c.isalpha() or c == " ").strip()
            if key in seen:
                signals.append(FraudSignal(
                    signal_type="duplicate",
                    severity="high",
                    description="Duplicate line item detected",
                    evidence=f"'{item['description']}' appears to be billed more than once",
                    affected_items=[item["description"], seen[key]["description"]],
                    actual_amount=item["amount"] + seen[key]["amount"]
                ))
            else:
                seen[key] = item
        return signals

    def _check_amount_outliers(self, items: list, bill: dict) -> list[FraudSignal]:
        """Flag items priced significantly above benchmark ranges."""
        signals = []
        for item in items:
            desc = item["description"].lower()
            amt = item["amount"]
            days = item.get("days", 1)
            per_unit = amt / max(days, 1)

            benchmark = None
            bench_key = None

            if "room" in desc and ("general" in desc or "ward" in desc):
                benchmark = AMOUNT_BENCHMARKS["room_general_per_day"]
                bench_key = "room (general), per night"
                check_amt = per_unit
            elif "room" in desc and any(w in desc for w in ["private", "single", "deluxe"]):
                benchmark = AMOUNT_BENCHMARKS["room_private_per_day"]
                bench_key = "room (private), per night"
                check_amt = per_unit
            elif "icu" in desc:
                benchmark = AMOUNT_BENCHMARKS["icu_per_day"]
                bench_key = "ICU, per day"
                check_amt = per_unit
            elif "anesthes" in desc or "anaesthe" in desc:
                benchmark = AMOUNT_BENCHMARKS["anesthesia"]
                bench_key = "anesthesiologist fees"
                check_amt = amt
            elif "consultation" in desc:
                benchmark = AMOUNT_BENCHMARKS["consultation"]
                bench_key = "physician consultation"
                check_amt = amt
            elif "ct scan" in desc or "ct-scan" in desc:
                benchmark = AMOUNT_BENCHMARKS["ct_scan"]
                bench_key = "CT scan"
                check_amt = amt
            elif "mri" in desc:
                benchmark = AMOUNT_BENCHMARKS["mri"]
                bench_key = "MRI scan"
                check_amt = amt
            elif "x-ray" in desc or "xray" in desc:
                benchmark = AMOUNT_BENCHMARKS["xray"]
                bench_key = "X-ray"
                check_amt = amt
            elif "ambulance" in desc:
                benchmark = AMOUNT_BENCHMARKS["ambulance"]
                bench_key = "ambulance"
                check_amt = amt

            if benchmark and check_amt > benchmark["high"] * 2:
                signals.append(FraudSignal(
                    signal_type="outlier",
                    severity="high",
                    description=f"Amount is {check_amt/benchmark['median']:.1f}× the national median",
                    evidence=f"'{item['description']}': billed ₹{check_amt:,.0f} vs expected range ₹{benchmark['low']:,}–₹{benchmark['high']:,}",
                    affected_items=[item["description"]],
                    expected_range=f"₹{benchmark['low']:,} – ₹{benchmark['high']:,}",
                    actual_amount=check_amt
                ))
            elif benchmark and check_amt > benchmark["high"] * 1.3:
                signals.append(FraudSignal(
                    signal_type="outlier",
                    severity="medium",
                    description="Amount is above the high benchmark for this service",
                    evidence=f"'{item['description']}': billed ₹{check_amt:,.0f} vs high benchmark ₹{benchmark['high']:,}",
                    affected_items=[item["description"]],
                    expected_range=f"₹{benchmark['low']:,} – ₹{benchmark['high']:,}",
                    actual_amount=check_amt
                ))
        return signals

    def _check_impossible_combos(self, items: list, diagnosis: str) -> list[FraudSignal]:
        """Flag procedures that cannot logically occur in the same hospitalisation."""
        signals = []
        all_text = diagnosis + " " + " ".join(i["description"].lower() for i in items)

        for group_a, group_b, reason in IMPOSSIBLE_COMBOS:
            has_a = any(kw in all_text for kw in group_a)
            has_b = any(kw in all_text for kw in group_b)
            if has_a and has_b:
                signals.append(FraudSignal(
                    signal_type="impossible_combo",
                    severity="high",
                    description="Impossible procedure combination detected",
                    evidence=reason,
                    affected_items=[g for g in group_a + group_b if g in all_text]
                ))
        return signals

    def _check_cpt_diagnosis_mismatch(self, items: list, diagnosis: str) -> list[FraudSignal]:
        """Check that CPT codes are consistent with the stated diagnosis."""
        signals = []
        for item in items:
            cpt = str(item.get("cpt_code", "")).strip().upper()
            if not cpt or cpt in ["PHARMACY", "FACILITY", "NURSING", "DIAGNOSTICS"]:
                continue
            if cpt in CPT_DIAGNOSIS_MAP:
                expected_dx_keywords = [k.lower() for k in CPT_DIAGNOSIS_MAP[cpt]]
                matches = any(kw in diagnosis for kw in expected_dx_keywords)
                if not matches:
                    signals.append(FraudSignal(
                        signal_type="cpt_mismatch",
                        severity="medium",
                        description=f"CPT code {cpt} does not match the stated diagnosis",
                        evidence=(
                            f"'{item['description']}' (CPT {cpt}) typically corresponds to "
                            f"{' / '.join(CPT_DIAGNOSIS_MAP[cpt][:3])}, "
                            f"but diagnosis is '{diagnosis[:60]}'"
                        ),
                        affected_items=[item["description"]]
                    ))
        return signals

    def _check_round_number_inflation(self, items: list) -> list[FraudSignal]:
        """Flag suspiciously round numbers that suggest estimated rather than actual costs."""
        signals = []
        suspicious = []
        for item in items:
            amt = item["amount"]
            # Amounts that are exact multiples of 5000 or 10000 above ₹10,000
            if amt >= 10000 and amt % 5000 == 0:
                suspicious.append(item)

        if len(suspicious) >= 3:
            signals.append(FraudSignal(
                signal_type="round_number_inflation",
                severity="medium",
                description=f"{len(suspicious)} line items have suspiciously round amounts",
                evidence=(
                    "Multiple charges are exact multiples of ₹5,000 — suggests estimated "
                    "rather than itemised billing: " +
                    ", ".join(f"{i['description']} (₹{i['amount']:,.0f})" for i in suspicious[:3])
                ),
                affected_items=[i["description"] for i in suspicious]
            ))
        return signals

    def _check_phantom_icu(self, bill: dict, items: list) -> list[FraudSignal]:
        """Detect ICU charges without a corresponding ICU diagnosis code or note."""
        icu_items = [i for i in items if "icu" in i["description"].lower()]
        if not icu_items:
            return []

        dx = (bill.get("diagnosis", "") or "").lower()
        dx_codes = " ".join(bill.get("diagnosis_codes", []))

        icu_dx_indicators = ["icu", "intensive", "critical", "ventilat", "septic", "shock",
                             "cardiac arrest", "post-operative monitoring"]
        has_icu_dx = any(ind in dx for ind in icu_dx_indicators)
        has_icu_code = any(c.startswith(("J96", "R65", "I46", "J18.0")) for c in bill.get("diagnosis_codes", []))

        if not has_icu_dx and not has_icu_code:
            icu_total = sum(i["amount"] for i in icu_items)
            return [FraudSignal(
                signal_type="phantom_charge",
                severity="medium",
                description="ICU charges present but no ICU diagnosis or indication",
                evidence=(
                    f"ICU billed ₹{icu_total:,.0f} but diagnosis '{dx[:60]}' "
                    "does not indicate intensive care was required"
                ),
                affected_items=[i["description"] for i in icu_items],
                actual_amount=icu_total
            )]
        return []

    # ── Scoring ───────────────────────────────────────────────────────────────

    def _calculate_risk_score(self, signals: list[FraudSignal]) -> int:
        score = 0
        weights = {"high": 35, "medium": 20, "low": 10}
        for s in signals:
            score += weights.get(s.severity, 10)
        return min(100, score)

    def _recommendation(self, level: str, signals: list[FraudSignal]) -> str:
        if level == "HIGH":
            return (
                "REFER TO FRAUD INVESTIGATION UNIT. Do not settle claim until "
                "investigator review is complete. Request original bills, doctor certificates, "
                "and hospital records."
            )
        elif level == "MEDIUM":
            return (
                "ESCALATE TO SENIOR ADJUDICATOR. Request clarification documents from "
                "hospital before settlement. Consider field verification."
            )
        return "Proceed with normal adjudication. No significant fraud indicators detected."

    def _summary(self, signals: list[FraudSignal], score: int) -> str:
        if not signals:
            return "No fraud signals detected. Claim appears genuine."
        high = sum(1 for s in signals if s.severity == "high")
        med  = sum(1 for s in signals if s.severity == "medium")
        return (
            f"{len(signals)} fraud signal(s) detected (risk score {score}/100): "
            f"{high} high-severity, {med} medium-severity. "
            + signals[0].description + "."
        )
