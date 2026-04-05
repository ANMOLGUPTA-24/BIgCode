"""
policy_parser.py
================
Loads, indexes, and provides query interface for insurance policy documents.
Supports JSON-structured policies and raw PDF text extraction.
"""

import json
import logging
import re
from datetime import datetime
from typing import Optional
from pathlib import Path

logger = logging.getLogger(__name__)


class PolicyClause:
    """Represents a single policy clause with its location metadata."""

    def __init__(self, section_id: str, para_id: str, page: int,
                 section_title: str, text: str):
        self.section_id = section_id
        self.para_id = para_id
        self.page = page
        self.section_title = section_title
        self.text = text

    def citation(self) -> str:
        """Return a formatted citation string."""
        return (f"Page {self.page}, Section {self.section_id} "
                f"({self.section_title}), Paragraph {self.para_id}")

    def __repr__(self):
        return f"PolicyClause({self.para_id}: {self.text[:60]}...)"


class PolicyParser:
    """
    Parses and indexes an insurance policy document.
    Provides fast lookups for rules, exclusions, waiting periods, and benefit limits.
    """

    def __init__(self, policy_path: str):
        self.policy_path = policy_path
        self.policy_data = {}
        self.clauses = []          # All clauses indexed for search
        self.clause_map = {}       # para_id -> PolicyClause
        self._load()

    def _load(self):
        """Load policy from JSON file."""
        with open(self.policy_path) as f:
            self.policy_data = json.load(f)

        # Build clause index
        for section in self.policy_data.get("sections", []):
            for para in section.get("paragraphs", []):
                clause = PolicyClause(
                    section_id=section["section_id"],
                    para_id=para["para_id"],
                    page=section["page"],
                    section_title=section["title"],
                    text=para["text"]
                )
                self.clauses.append(clause)
                self.clause_map[para["para_id"]] = clause

        logger.info(f"Loaded policy '{self.policy_data.get('policy_name')}' "
                    f"with {len(self.clauses)} clauses")

    # ------------------------------------------------------------------ #
    #  Query Methods                                                       #
    # ------------------------------------------------------------------ #

    def get_clause(self, para_id: str) -> Optional[PolicyClause]:
        return self.clause_map.get(para_id)

    def get_benefit_limits(self) -> dict:
        return self.policy_data.get("benefit_limits", {})

    def get_sum_insured(self) -> float:
        return float(self.policy_data.get("benefit_limits", {}).get("total_sum_insured", 500000))

    def get_excluded_conditions(self) -> list:
        return self.policy_data.get("excluded_conditions", [])

    def get_waiting_period_diseases(self) -> list:
        return self.policy_data.get("waiting_period_diseases", [])

    def get_covered_procedure_codes(self) -> list:
        return self.policy_data.get("covered_procedure_codes", [])

    def get_excluded_procedure_codes(self) -> list:
        return self.policy_data.get("excluded_procedure_codes", [])

    def get_preauth_required_procedures(self) -> list:
        return self.policy_data.get("preauth_required_procedures", [])

    def is_procedure_excluded(self, cpt_code: str) -> tuple[bool, Optional[PolicyClause]]:
        """Check if a CPT/procedure code is excluded. Returns (is_excluded, citation_clause)."""
        excluded = self.get_excluded_procedure_codes()
        if cpt_code and cpt_code.upper() in [c.upper() for c in excluded]:
            return True, self.get_clause("S3.P3")  # General exclusions clause
        return False, None

    def is_preauth_required(self, cpt_code: str, amount: float = 0) -> tuple[bool, Optional[PolicyClause]]:
        """Check if pre-authorization is required."""
        required = self.get_preauth_required_procedures()
        if cpt_code and cpt_code.upper() in [c.upper() for c in required]:
            return True, self.get_clause("S5.P1")
        if amount > 50000:
            return True, self.get_clause("S5.P1")
        return False, None

    def check_waiting_period(self, diagnosis: str, policy_start_date: str,
                              admission_date: str) -> tuple[bool, Optional[PolicyClause], str]:
        """
        Check if a claim falls within a waiting period.
        Returns (is_in_waiting_period, citation_clause, reason).
        """
        try:
            start = datetime.strptime(str(policy_start_date)[:10], "%Y-%m-%d")
            admission = datetime.strptime(str(admission_date)[:10], "%Y-%m-%d")
            months_since_start = (admission - start).days / 30.44
        except Exception as e:
            logger.warning(f"Date parse error: {e}")
            return False, None, ""

        diagnosis_lower = diagnosis.lower()

        # 30-day initial waiting period
        if months_since_start < 1:
            return (True, self.get_clause("S4.P1"),
                    f"Claim falls within 30-day initial waiting period "
                    f"({(admission - start).days} days since policy inception).")

        # 24-month specific disease waiting period
        if months_since_start < 24:
            waiting_diseases = self.get_waiting_period_diseases()
            for disease in waiting_diseases:
                if disease.lower() in diagnosis_lower:
                    return (True, self.get_clause("S4.P2"),
                            f"'{disease.title()}' is subject to a 24-month waiting period. "
                            f"Policy active for only {months_since_start:.1f} months.")

        # 36-month pre-existing disease waiting period (simplified check)
        if months_since_start < 36:
            pre_existing_keywords = ["chronic", "pre-existing", "known case of", "hypertension", "diabetes", "thyroid"]
            for kw in pre_existing_keywords:
                if kw in diagnosis_lower:
                    return (True, self.get_clause("S4.P3"),
                            f"Pre-existing disease waiting period applies (36 months). "
                            f"Policy active for only {months_since_start:.1f} months.")

        return False, None, ""

    def check_exclusion_by_text(self, text: str) -> tuple[bool, Optional[PolicyClause], str]:
        """Check if the described service/diagnosis matches any exclusion keywords."""
        text_lower = text.lower()
        excluded_conditions = self.get_excluded_conditions()

        exclusion_to_clause = {
            "cosmetic": "S3.P2", "aesthetic": "S3.P2", "rhinoplasty": "S3.P2",
            "liposuction": "S3.P2", "face lift": "S3.P2", "hair transplant": "S3.P2",
            "obesity": "S3.P2", "weight loss": "S3.P2",
            "dental": "S3.P3", "orthodontic": "S3.P3", "root canal": "S3.P3",
            "tooth": "S3.P3", "teeth": "S3.P3", "periodontal": "S3.P3",
            "pregnancy": "S3.P4", "childbirth": "S3.P4", "maternity": "S3.P4",
            "abortion": "S3.P4",
            "mental disorder": "S3.P5", "psychiatric": "S3.P5",
            "alzheimer": "S3.P5", "parkinson": "S3.P5",
            "aids": "S3.P9", "hiv": "S3.P9",
            "alcohol": "S3.P10", "drug abuse": "S3.P10", "self-inflicted": "S3.P10",
            "experimental": "S3.P7", "unproven": "S3.P7",
            "vitamins": "S3.P8", "supplements": "S3.P8",
            "hazardous sport": "S3.P6"
        }

        for keyword, clause_id in exclusion_to_clause.items():
            if keyword in text_lower:
                clause = self.get_clause(clause_id)
                return (True, clause,
                        f"Service/diagnosis contains excluded keyword: '{keyword}'")

        return False, None, ""

    def calculate_room_rent_limit(self, room_type: str, days: int) -> tuple[float, Optional[PolicyClause]]:
        """Calculate allowable room rent and return limit clause."""
        limits = self.get_benefit_limits()
        if "private" in room_type.lower() or "single" in room_type.lower() or "deluxe" in room_type.lower():
            daily_limit = limits.get("room_rent_private", 6000)
        else:
            daily_limit = limits.get("room_rent_general", 3000)

        total_limit = daily_limit * days
        clause = self.get_clause("S2.P2")
        return total_limit, clause

    def calculate_icu_limit(self, days: int) -> tuple[float, Optional[PolicyClause]]:
        """Calculate allowable ICU charges."""
        limits = self.get_benefit_limits()
        daily = limits.get("icu_daily", 6000)
        return daily * days, self.get_clause("S2.P2")

    def calculate_copayment(self, age: int, admissible_amount: float) -> tuple[float, Optional[PolicyClause]]:
        """Calculate co-payment if applicable."""
        if age and age >= 61:
            copay = admissible_amount * 0.10
            return copay, self.get_clause("S7.P1")
        return 0.0, None

    def get_summary(self) -> dict:
        """Return a summary of the policy for display."""
        limits = self.get_benefit_limits()
        return {
            "policy_name": self.policy_data.get("policy_name"),
            "policy_id": self.policy_data.get("policy_id"),
            "insurer": self.policy_data.get("insurer"),
            "sum_insured": limits.get("total_sum_insured"),
            "total_sections": len(self.policy_data.get("sections", [])),
            "total_clauses": len(self.clauses),
            "total_exclusions": len(self.get_excluded_conditions())
        }
