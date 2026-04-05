"""
api.py
======
FastAPI REST server for the Insurance Claim Settlement Agent.
Exposes endpoints for claim submission, policy info, and batch processing.

Run with:
    uvicorn api:app --reload --port 8000

Endpoints:
    POST /claim/json        — Process a structured JSON bill
    POST /claim/pdf         — Upload a scanned PDF bill
    GET  /policy/summary    — Policy metadata
    GET  /policy/clause/{id}— Get specific policy clause
    GET  /health            — Health check
    GET  /demo              — Serve the web demo UI
"""

import json
import os
import sys
import logging
from pathlib import Path
from datetime import datetime

from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field
from typing import Optional, List
import tempfile

sys.path.insert(0, str(Path(__file__).parent))
from src.claim_agent import ClaimAgent

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── Paths ─────────────────────────────────────────────────────────────────
BASE_DIR   = Path(__file__).parent
POLICY_PATH = BASE_DIR / "data" / "sample_policy.json"

# ── FastAPI App ────────────────────────────────────────────────────────────
app = FastAPI(
    title="Insurance Claim Settlement Agent",
    description=(
        "AI-powered insurance claim processing engine using Google Gemini. "
        "Automatically approves, rejects, or partially approves claims with "
        "precise policy citations (page + paragraph) for every decision."
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Singleton agent ────────────────────────────────────────────────────────
_agent: Optional[ClaimAgent] = None

def get_agent() -> ClaimAgent:
    global _agent
    if _agent is None:
        _agent = ClaimAgent(str(POLICY_PATH))
    return _agent


# ── Pydantic Schemas ───────────────────────────────────────────────────────

class PatientInfo(BaseModel):
    name: str
    age: Optional[int] = None
    policy_number: Optional[str] = None
    policy_start_date: Optional[str] = None  # YYYY-MM-DD

class BillLineItem(BaseModel):
    description: str
    amount: float
    cpt_code: Optional[str] = None
    days: Optional[int] = 1

class BillRequest(BaseModel):
    bill_id: Optional[str] = "BILL-LIVE"
    hospital: str
    patient: PatientInfo
    admission_date: str   # YYYY-MM-DD
    discharge_date: str   # YYYY-MM-DD
    diagnosis: str
    diagnosis_codes: Optional[List[str]] = []
    pre_authorization_obtained: Optional[bool] = False
    pre_auth_number: Optional[str] = None
    line_items: List[BillLineItem]

class LineDecisionOut(BaseModel):
    description: str
    billed_amount: float
    approved_amount: float
    rejected_amount: float
    decision: str
    reason: str
    citation: Optional[str] = None

class ClaimResponse(BaseModel):
    bill_id: str
    patient_name: str
    hospital: str
    diagnosis: str
    overall_decision: str
    total_billed: float
    total_approved: float
    total_rejected: float
    copayment: float
    net_payable: float
    line_items: List[LineDecisionOut]
    citations_used: List[str]
    notes: List[str]
    blocking_rejections: List[str]
    generated_at: str


# ── Endpoints ──────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    """Health check."""
    return {"status": "ok", "service": "Insurance Claim Settlement Agent", "version": "1.0.0"}


@app.get("/policy/summary")
def policy_summary():
    """Return policy metadata."""
    agent = get_agent()
    summary = agent.policy.get_summary()
    limits  = agent.policy.get_benefit_limits()
    return {
        "policy": summary,
        "benefit_limits": limits,
        "waiting_period_diseases": agent.policy.get_waiting_period_diseases(),
        "excluded_conditions": agent.policy.get_excluded_conditions()[:10],  # sample
    }


@app.get("/policy/clause/{para_id}")
def get_clause(para_id: str):
    """Return a specific policy clause by paragraph ID (e.g. S3.P2)."""
    agent = get_agent()
    clause = agent.policy.get_clause(para_id)
    if not clause:
        raise HTTPException(status_code=404, detail=f"Clause {para_id} not found")
    return {
        "para_id": clause.para_id,
        "section_id": clause.section_id,
        "section_title": clause.section_title,
        "page": clause.page,
        "text": clause.text,
        "citation": clause.citation()
    }


@app.post("/claim/json", response_model=ClaimResponse)
def process_json_claim(bill: BillRequest):
    """
    Submit a structured hospital bill for claim adjudication.
    Returns a full decision with per-line-item breakdown and policy citations.
    """
    agent = get_agent()

    # Convert Pydantic model → dict for the rule engine
    bill_dict = {
        "bill_id": bill.bill_id,
        "hospital": bill.hospital,
        "patient": {
            "name": bill.patient.name,
            "age": bill.patient.age,
            "policy_number": bill.patient.policy_number,
            "policy_start_date": bill.patient.policy_start_date,
        },
        "admission_date": bill.admission_date,
        "discharge_date": bill.discharge_date,
        "diagnosis": bill.diagnosis,
        "diagnosis_codes": bill.diagnosis_codes,
        "pre_authorization_obtained": bill.pre_authorization_obtained,
        "pre_auth_number": bill.pre_auth_number,
        "line_items": [
            {
                "description": li.description,
                "amount": li.amount,
                "cpt_code": li.cpt_code or "",
                "days": li.days or 1,
            }
            for li in bill.line_items
        ],
        "total_billed": sum(li.amount for li in bill.line_items),
    }

    decision = agent.process_json_bill(bill_dict)
    return _decision_to_response(decision)


@app.post("/claim/pdf", response_model=ClaimResponse)
async def process_pdf_claim(file: UploadFile = File(...)):
    """
    Upload a scanned or digital hospital bill PDF.
    The system extracts text via OCR, parses entities with Gemini AI,
    then runs the rule engine.
    """
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted.")

    agent = get_agent()

    # Save to temp file
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name

    try:
        decision = agent.process_pdf_bill(tmp_path)
    finally:
        os.unlink(tmp_path)

    return _decision_to_response(decision)


@app.get("/demo/bills")
def demo_bills():
    """Return the 6 built-in demo test bills for the web UI."""
    bills_path = BASE_DIR / "data" / "test_bills.json"
    with open(bills_path) as f:
        data = json.load(f)
    return data.get("test_cases", [])


@app.post("/demo/run/{bill_id}")
def demo_run(bill_id: str):
    """Run a specific demo bill by ID and return full decision."""
    agent  = get_agent()
    bills_path = BASE_DIR / "data" / "test_bills.json"
    with open(bills_path) as f:
        bills = json.load(f).get("test_cases", [])

    bill = next((b for b in bills if b["bill_id"] == bill_id), None)
    if not bill:
        raise HTTPException(status_code=404, detail=f"Demo bill {bill_id} not found")

    decision = agent.process_json_bill(bill)
    result = _decision_to_response(decision)
    # Also include the text report
    result_dict = result.dict()
    result_dict["text_report"] = agent.format_report(decision)
    return result_dict


# ── New endpoints ─────────────────────────────────────────────────────────────

class PredictRequest(BaseModel):
    condition: str = "I have knee pain and my doctor recommends surgery"
    policy_start_date: str = "2022-01-01"
    patient_age: int = 40
    policy_number: str = "POL-HEALTH-2024-001"


@app.post("/predict/coverage")
def predict_coverage(req: PredictRequest):
    """
    Proactive pre-admission coverage predictor.
    Describe your condition in plain text (any language) and get a full
    coverage prediction + pre-admission checklist BEFORE hospitalisation.
    """
    agent = get_agent()
    try:
        pred = agent.predict_coverage(
            req.condition, req.policy_start_date, req.patient_age
        )
        return {
            "predicted_diagnosis":      pred.predicted_diagnosis,
            "urgency":                  pred.urgency,
            "plain_english_summary":    pred.plain_english_summary,
            "language_detected":        pred.language_detected,
            "procedures": [
                {
                    "name":              p.name,
                    "estimated_cost":    f"₹{p.estimated_cost_min:,.0f} – ₹{p.estimated_cost_max:,.0f}",
                    "coverage_decision": p.coverage_decision,
                    "approved_amount":   p.approved_amount,
                    "issue":             p.rejected_reason,
                    "citation":          p.citation,
                }
                for p in pred.procedures
            ],
            "financial_estimate": {
                "total_cost":       f"₹{pred.estimated_total_min:,.0f} – ₹{pred.estimated_total_max:,.0f}",
                "covered":          f"₹{pred.estimated_covered_min:,.0f} – ₹{pred.estimated_covered_max:,.0f}",
                "out_of_pocket":    f"₹{pred.estimated_out_of_pocket_min:,.0f} – ₹{pred.estimated_out_of_pocket_max:,.0f}",
                "copayment":        f"{pred.copayment_pct}%" if pred.copayment_applicable else "none",
            },
            "preauth_required":     pred.preauth_required,
            "checklist":            pred.checklist,
            "warnings":             pred.warnings,
            "recommended_documents": pred.recommended_documents,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/claim/json/fraud", response_model=ClaimResponse)
def process_json_claim_with_fraud(bill: BillRequest):
    """Submit a bill and get claim decision PLUS fraud analysis in one call."""
    agent = get_agent()
    bill_dict = {
        "bill_id": bill.bill_id,
        "hospital": bill.hospital,
        "patient": {
            "name": bill.patient.name, "age": bill.patient.age,
            "policy_number": bill.patient.policy_number,
            "policy_start_date": bill.patient.policy_start_date,
        },
        "admission_date": bill.admission_date,
        "discharge_date": bill.discharge_date,
        "diagnosis": bill.diagnosis,
        "diagnosis_codes": bill.diagnosis_codes or [],
        "pre_authorization_obtained": bill.pre_authorization_obtained or False,
        "pre_auth_number": bill.pre_auth_number,
        "line_items": [
            {"description": li.description, "amount": li.amount,
             "cpt_code": li.cpt_code or "", "days": li.days or 1}
            for li in bill.line_items
        ],
        "total_billed": sum(li.amount for li in bill.line_items),
    }
    decision, fraud_report = agent.process_json_bill_with_fraud(bill_dict)
    response = _decision_to_response(decision).dict()
    response["fraud_analysis"] = {
        "risk_level":   fraud_report.risk_level,
        "risk_score":   fraud_report.risk_score,
        "summary":      fraud_report.summary,
        "recommendation": fraud_report.recommendation,
        "signals": [
            {"type": s.signal_type, "severity": s.severity,
             "description": s.description, "evidence": s.evidence}
            for s in fraud_report.signals
        ],
    }
    response["confidence"] = {
        "claim_confidence_pct": decision.claim_confidence_pct,
        "requires_human_review": decision.requires_human_review,
    }
    return response


@app.post("/demo/run/fraud/{bill_id}")
def demo_run_with_fraud(bill_id: str):
    """Run a demo bill and include fraud analysis + confidence score."""
    agent = get_agent()
    bills_path = BASE_DIR / "data" / "test_bills.json"
    with open(bills_path) as f:
        bills = json.load(f).get("test_cases", [])

    bill = next((b for b in bills if b["bill_id"] == bill_id), None)
    if not bill:
        raise HTTPException(status_code=404, detail=f"Demo bill {bill_id} not found")

    decision, fraud = agent.process_json_bill_with_fraud(bill)
    result = _decision_to_response(decision).dict()
    result["fraud_analysis"] = {
        "risk_level":    fraud.risk_level,
        "risk_score":    fraud.risk_score,
        "summary":       fraud.summary,
        "signals_count": fraud.signal_count,
        "signals": [
            {"type": s.signal_type, "severity": s.severity, "description": s.description}
            for s in fraud.signals
        ],
    }
    result["confidence"] = {
        "claim_confidence_pct":  decision.claim_confidence_pct,
        "requires_human_review": decision.requires_human_review,
    }
    result["text_report"] = agent.format_report(decision)
    return result


# ── Helper ─────────────────────────────────────────────────────────────────

def _decision_to_response(decision) -> ClaimResponse:
    return ClaimResponse(
        bill_id=decision.bill_id,
        patient_name=decision.patient_name,
        hospital=decision.hospital,
        diagnosis=decision.diagnosis,
        overall_decision=decision.overall_decision,
        total_billed=decision.total_billed,
        total_approved=decision.total_approved,
        total_rejected=decision.total_rejected,
        copayment=decision.copayment,
        net_payable=decision.net_payable,
        line_items=[
            LineDecisionOut(
                description=ld.description,
                billed_amount=ld.billed_amount,
                approved_amount=ld.approved_amount,
                rejected_amount=ld.rejected_amount,
                decision=ld.decision,
                reason=ld.reason,
                citation=ld.citation,
            )
            for ld in decision.line_decisions
        ],
        citations_used=decision.citations_used,
        notes=decision.notes,
        blocking_rejections=decision.blocking_rejections,
        generated_at=datetime.now().isoformat(),
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True)
