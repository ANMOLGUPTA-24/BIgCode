# 🏥 Insurance Claim Settlement Agent
### The Big Code 2026 — Hackathon | Problem Statement #3

An AI-powered end-to-end insurance claim settlement engine. Uses **Google Gemini 2.0 Flash** for NLP bill parsing, **OCR** for scanned PDFs, and a **deterministic 13-rule engine** to automatically Approve, Reject, or Partially Approve medical claims — with a precise policy citation (exact page, section, paragraph) for every decision.

---

## 🚀 Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Set Gemini API key (free at aistudio.google.com/app/apikey)
#    Only required for PDF/OCR mode. JSON mode needs no key.
export GEMINI_API_KEY=your_key_here

# 3. Generate sample bill PDFs (for OCR demo)
python scripts/generate_sample_bills.py

# 4. Run end-to-end demo
python demo.py --mode json       # Rule engine only (no API key needed)
python demo.py --mode eval       # Accuracy metrics
python demo.py --mode pdf        # Full OCR + Gemini pipeline

# 5. Run the test suite
python tests/test_claim_agent.py

# 6. Launch Streamlit dashboard (for live pitch)
streamlit run streamlit_app.py

# 7. Start the REST API
uvicorn api:app --reload --port 8000
# Swagger: http://localhost:8000/docs
```

---

## 📊 Results — 100% Accuracy, 33 Tests Passing

| Bill | Scenario | Expected | Got | ✓ |
|---|---|---|---|---|
| BILL-2024-001 | Standard appendectomy | APPROVED | APPROVED | ✅ |
| BILL-2024-002 | Septoplasty + Rhinoplasty (mixed) | PARTIAL | PARTIAL | ✅ |
| BILL-2024-003 | Dental / Root Canal (excluded) | REJECTED | REJECTED | ✅ |
| BILL-2024-004 | Pneumonia (30-day waiting period) | REJECTED | REJECTED | ✅ |
| BILL-2024-005 | Total Knee Replacement, age 67 | PARTIAL | PARTIAL | ✅ |
| BILL-2024-006 | Hernia (24-month waiting period) | REJECTED | REJECTED | ✅ |

---

## 🏗️ Architecture

```
Hospital Bill (PDF / Scanned Image / JSON)
       │
       ▼
[ OCR Extractor ]   PyMuPDF (digital) → Tesseract fallback (scanned)
       │
       ▼
[ Bill Parser ]     Google Gemini 2.0 Flash — structured JSON extraction
       │                  ↑
       │         Insurance Policy JSON (O(1) hash-map clause index)
       ▼
[ Rule Engine ]     13 priority rules → per-line decisions + citations
       │
       ▼
[ ClaimDecision ]   APPROVED / REJECTED / PARTIAL + net payable + citations
```

---

## 📁 Project Structure

```
insurance-claim-agent/
├── src/
│   ├── claim_agent.py        # Main orchestrator
│   ├── bill_parser.py        # Gemini AI NLP extraction
│   ├── policy_parser.py      # O(1) indexed clause lookup
│   ├── rule_engine.py        # 13-rule deterministic engine
│   └── ocr_extractor.py      # PDF/image OCR
├── data/
│   ├── sample_policy.json                         # 7-section, 30-clause policy
│   ├── test_bills.json                            # 6 labelled test cases
│   ├── sample_bill_APPROVED_appendectomy.pdf
│   ├── sample_bill_REJECTED_dental.pdf
│   └── sample_bill_PARTIAL_knee_replacement.pdf
├── tests/
│   └── test_claim_agent.py   # 33 unit + integration tests
├── scripts/
│   └── generate_sample_bills.py
├── main.py                   # CLI
├── demo.py                   # End-to-end demo (3 modes)
├── api.py                    # FastAPI REST server
├── streamlit_app.py          # Streamlit dashboard
└── requirements.txt
```

---

## 🔬 Rule Engine — 13 Priority Rules

| # | Rule | Scope | Policy Clause |
|---|---|---|---|
| 1 | 30-day initial waiting period | Whole claim blocked | S4.P1 — Page 9 |
| 2 | 24-month specific disease wait | Whole claim blocked | S4.P2 — Page 9 |
| 3 | 36-month pre-existing disease | Whole claim blocked | S4.P3 — Page 9 |
| 4 | Categorical exclusions (dental, cosmetic…) | Whole claim blocked | S3.P2–P10 — Page 6 |
| 5 | Pre-authorization missing | Warning / 50% penalty | S5.P1 — Page 11 |
| 6 | CPT code exclusion list | Line item rejected | S3 — Page 6 |
| 7 | Keyword exclusion scan | Line item rejected | S3 — Page 6 |
| 8 | Room rent sub-limit (₹3K/₹6K per day) | Line item partial | S2.P2 — Page 3 |
| 9 | ICU daily limit (₹6K/day) | Line item partial | S2.P2 — Page 3 |
| 10 | Implant/prosthetics cap (₹50K/year) | Line item partial | S6.P6 — Page 13 |
| 11 | Physiotherapy session cap (₹500/session) | Line item partial | S6.P4 — Page 13 |
| 12 | Co-payment 10% for age ≥61 | Deducted from total | S7.P1 — Page 15 |
| 13 | Annual sum insured cap (₹5L) | Total capped | S1.P2 — Page 1 |

---

## 🌐 API Reference

Start server: `uvicorn api:app --reload --port 8000`

| Endpoint | Method | Description |
|---|---|---|
| `/health` | GET | Health check |
| `/policy/summary` | GET | Policy metadata + benefit limits |
| `/policy/clause/{id}` | GET | Lookup any clause e.g. `S3.P2` |
| `/claim/json` | POST | Submit structured JSON bill |
| `/claim/pdf` | POST | Upload scanned/digital PDF bill |
| `/demo/bills` | GET | All 6 demo test bills |
| `/demo/run/{bill_id}` | POST | Run a demo bill by ID |

---

## 🔒 Privacy & Security

- Patient PII processed in-memory only; never persisted
- Gemini API: stateless calls; no training data retention
- Compliant with India DPDP Act 2023 and IRDAI data guidelines

## 📜 References

- IRDAI Annual Report 2023–24 — irdai.gov.in
- National Health Authority PM-JAY data
- ICD-10-CM 2024 — WHO | CPT 2024 — AMA
- Google Gemini API — gemini-2.0-flash

---

*Built for The Big Code 2026 | Problem #3: Insurance Claim Settlement Agent*
