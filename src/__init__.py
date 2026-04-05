"""
Insurance Claim Settlement Agent
=================================
AI-powered engine for automated claim processing with policy citations.
"""

from .claim_agent import ClaimAgent
from .bill_parser import BillParser
from .policy_parser import PolicyParser
from .rule_engine import RuleEngine, ClaimDecision, LineItemDecision
from .ocr_extractor import OCRExtractor

__all__ = [
    "ClaimAgent",
    "BillParser",
    "PolicyParser",
    "RuleEngine",
    "ClaimDecision",
    "LineItemDecision",
    "OCRExtractor"
]

from .fraud_detector import FraudDetector, FraudReport, FraudSignal
from .preauth_predictor import PreAuthPredictor, CoveragePrediction
