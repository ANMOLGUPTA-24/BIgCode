"""
generate_sample_bills.py
========================
Generates realistic hospital bill PDFs to demonstrate the full OCR pipeline.
Run: python scripts/generate_sample_bills.py
"""
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import cm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Table, TableStyle,
    Spacer, HRFlowable
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_RIGHT, TA_LEFT

W, H = A4
styles = getSampleStyleSheet()

NAVY  = colors.HexColor("#1A3A5C")
LGREY = colors.HexColor("#F5F5F5")
MGREY = colors.HexColor("#CCCCCC")

def make_style(name, **kw):
    kw.setdefault("fontName", "Helvetica")
    kw.setdefault("fontSize", 10)
    kw.setdefault("leading", 14)
    return ParagraphStyle(name, **kw)

S_HOSP   = make_style("hosp",   fontName="Helvetica-Bold", fontSize=16, textColor=NAVY, alignment=TA_CENTER)
S_HEAD   = make_style("head",   fontName="Helvetica-Bold", fontSize=11, textColor=NAVY, alignment=TA_CENTER)
S_LABEL  = make_style("label",  fontName="Helvetica-Bold", fontSize=9,  textColor=colors.HexColor("#555555"))
S_VALUE  = make_style("value",  fontSize=9)
S_TITLE  = make_style("title",  fontName="Helvetica-Bold", fontSize=10, textColor=NAVY)
S_SMALL  = make_style("small",  fontSize=8,  textColor=colors.HexColor("#777777"), alignment=TA_CENTER)
S_FOOTER = make_style("footer", fontSize=7.5, textColor=colors.HexColor("#999999"), alignment=TA_CENTER)

def P(text, style=None): return Paragraph(text, style or S_VALUE)

def info_table(rows):
    data = [[P(k, S_LABEL), P(v)] for k, v in rows]
    t = Table(data, colWidths=[4*cm, 9*cm])
    t.setStyle(TableStyle([
        ("FONTSIZE",      (0,0), (-1,-1), 9),
        ("TOPPADDING",    (0,0), (-1,-1), 4),
        ("BOTTOMPADDING", (0,0), (-1,-1), 4),
        ("VALIGN",        (0,0), (-1,-1), "TOP"),
    ]))
    return t

def bill_table(items, total):
    header = [P("<b>S.No</b>", S_LABEL), P("<b>Description of Services</b>", S_LABEL),
              P("<b>CPT Code</b>", S_LABEL), P("<b>Amount (INR)</b>", S_LABEL)]
    rows = [header]
    for i, item in enumerate(items, 1):
        rows.append([
            P(str(i)),
            P(item["description"]),
            P(item.get("cpt_code", "-")),
            P(f"Rs. {item['amount']:,.2f}", make_style("ra", alignment=TA_RIGHT, fontSize=9))
        ])
    rows.append(["", "", P("<b>TOTAL</b>", make_style("tot", fontName="Helvetica-Bold", fontSize=9, alignment=TA_RIGHT)),
                 P(f"<b>Rs. {total:,.2f}</b>", make_style("totv", fontName="Helvetica-Bold", fontSize=9, alignment=TA_RIGHT, textColor=NAVY))])

    t = Table(rows, colWidths=[1.2*cm, 10*cm, 2.5*cm, 3.3*cm])
    t.setStyle(TableStyle([
        ("BACKGROUND",    (0,0), (-1,0), NAVY),
        ("TEXTCOLOR",     (0,0), (-1,0), colors.white),
        ("FONTNAME",      (0,0), (-1,0), "Helvetica-Bold"),
        ("FONTSIZE",      (0,0), (-1,-1), 9),
        ("ROWBACKGROUNDS",(0,1), (-1,-2), [colors.white, LGREY]),
        ("GRID",          (0,0), (-1,-1), 0.3, MGREY),
        ("TOPPADDING",    (0,0), (-1,-1), 5),
        ("BOTTOMPADDING", (0,0), (-1,-1), 5),
        ("LEFTPADDING",   (0,0), (-1,-1), 6),
        ("RIGHTPADDING",  (0,0), (-1,-1), 6),
        ("BACKGROUND",    (0,-1),(-1,-1), colors.HexColor("#EBF5FB")),
        ("LINEABOVE",     (0,-1),(-1,-1), 1, NAVY),
    ]))
    return t

def build_bill(output_path, hospital, address, bill_no, date, patient, policy_no,
               policy_start, admission, discharge, diagnosis, icd_codes,
               preauth, preauth_no, items, total):
    doc = SimpleDocTemplate(output_path, pagesize=A4,
        leftMargin=1.8*cm, rightMargin=1.8*cm,
        topMargin=1.5*cm, bottomMargin=1.5*cm)
    story = []

    # Header
    story.append(P(hospital, S_HOSP))
    story.append(P(address, S_SMALL))
    story.append(Spacer(1, 4))
    story.append(HRFlowable(width="100%", thickness=2, color=NAVY))
    story.append(Spacer(1, 4))
    story.append(P("HOSPITAL BILL / TAX INVOICE", S_HEAD))
    story.append(HRFlowable(width="100%", thickness=0.5, color=MGREY))
    story.append(Spacer(1, 8))

    # Bill meta + patient info side by side
    meta_data = [
        [P("Bill No:", S_LABEL),       P(bill_no),
         P("Admission Date:", S_LABEL), P(admission)],
        [P("Bill Date:", S_LABEL),     P(date),
         P("Discharge Date:", S_LABEL), P(discharge)],
        [P("Patient Name:", S_LABEL),  P(f"<b>{patient}</b>"),
         P("Diagnosis:", S_LABEL),      P(diagnosis)],
        [P("Policy No:", S_LABEL),     P(policy_no),
         P("ICD Codes:", S_LABEL),      P(", ".join(icd_codes))],
        [P("Policy Start:", S_LABEL),  P(policy_start),
         P("Pre-Auth No:", S_LABEL),    P(preauth_no if preauth else "Not Obtained")],
    ]
    meta = Table(meta_data, colWidths=[3.5*cm, 6*cm, 3.5*cm, 4*cm])
    meta.setStyle(TableStyle([
        ("FONTSIZE",      (0,0),(-1,-1), 9),
        ("TOPPADDING",    (0,0),(-1,-1), 4),
        ("BOTTOMPADDING", (0,0),(-1,-1), 4),
        ("BACKGROUND",    (0,0),(-1,-1), LGREY),
        ("GRID",          (0,0),(-1,-1), 0.3, MGREY),
        ("LEFTPADDING",   (0,0),(-1,-1), 6),
    ]))
    story.append(meta)
    story.append(Spacer(1, 10))

    # Bill items
    story.append(P("ITEMISED BILL DETAILS", S_TITLE))
    story.append(Spacer(1, 4))
    story.append(bill_table(items, total))
    story.append(Spacer(1, 12))

    # Declaration
    decl_data = [[
        P("Declaration: This bill is true and correct to the best of our knowledge. "
          "All charges are as per the approved tariff.", S_SMALL),
        P(f"For {hospital}", make_style("sig", fontName="Helvetica-Bold", fontSize=9, alignment=TA_RIGHT))
    ]]
    decl = Table(decl_data, colWidths=[11*cm, 6*cm])
    decl.setStyle(TableStyle([("VALIGN",(0,0),(-1,-1),"BOTTOM"),("TOPPADDING",(0,0),(-1,-1),20)]))
    story.append(decl)
    story.append(Spacer(1, 6))
    story.append(HRFlowable(width="100%", thickness=0.5, color=MGREY))
    story.append(P("This is a computer-generated bill and does not require a signature. "
                   "For queries: billing@hospital.in | 1800-XXX-XXXX", S_FOOTER))

    doc.build(story)
    print(f"Generated: {output_path}")


# ── BILL 1: Appendectomy — should be APPROVED ─────────────────────────────
build_bill(
    output_path="data/sample_bill_APPROVED_appendectomy.pdf",
    hospital="Apollo Hospitals",
    address="Jubilee Hills, Hyderabad — 500033 | GSTIN: 36AABCA1234A1Z5",
    bill_no="APL-HYD-2024-10872",
    date="18-Oct-2024",
    patient="Rahul Sharma (Age: 35 Years)",
    policy_no="POL-HEALTH-2024-001",
    policy_start="01-Mar-2022",
    admission="15-Oct-2024",
    discharge="18-Oct-2024",
    diagnosis="Acute Appendicitis — Laparoscopic Appendectomy",
    icd_codes=["K35.80"],
    preauth=True,
    preauth_no="PA-2024-78231",
    items=[
        {"description": "Room Charges — General Ward (3 nights @ Rs. 2,400/night)", "amount": 7200,  "cpt_code": "99220"},
        {"description": "Surgeon Fees — Laparoscopic Appendectomy",                  "amount": 25000, "cpt_code": "44950"},
        {"description": "Anesthesiologist Fees",                                      "amount": 8000,  "cpt_code": "00840"},
        {"description": "Operation Theatre Charges",                                  "amount": 15000, "cpt_code": "00100"},
        {"description": "Post-operative Medicines and Consumables",                   "amount": 5500,  "cpt_code": "99232"},
        {"description": "Lab Tests — CBC, LFT, RFT, Coagulation Profile",            "amount": 2800,  "cpt_code": "80048"},
        {"description": "Ultrasound Abdomen (Pre-operative)",                         "amount": 1800,  "cpt_code": "76700"},
        {"description": "Nursing Charges",                                            "amount": 3000,  "cpt_code": "99232"},
    ],
    total=68300
)

# ── BILL 2: Dental — should be REJECTED ───────────────────────────────────
build_bill(
    output_path="data/sample_bill_REJECTED_dental.pdf",
    hospital="Max Dental and Healthcare",
    address="Connaught Place, New Delhi — 110001 | GSTIN: 07AABCM5678B1Z2",
    bill_no="MAX-DEL-2024-03341",
    date="05-Sep-2024",
    patient="Arjun Patel (Age: 42 Years)",
    policy_no="POL-HEALTH-2024-001",
    policy_start="01-Jun-2023",
    admission="05-Sep-2024",
    discharge="05-Sep-2024",
    diagnosis="Dental Caries with Root Canal Treatment — Mandibular Molar",
    icd_codes=["K02.9", "K04.0"],
    preauth=False,
    preauth_no=None,
    items=[
        {"description": "Consultation Fee — Dental Surgeon",                  "amount": 500,   "cpt_code": "D0140"},
        {"description": "Dental X-Ray — Periapical and Panoramic (OPG)",      "amount": 1500,  "cpt_code": "D0330"},
        {"description": "Root Canal Treatment — Mandibular First Molar",      "amount": 12000, "cpt_code": "D3330"},
        {"description": "Dental Crown — Porcelain Fused to Metal (PFM)",      "amount": 8000,  "cpt_code": "D2750"},
    ],
    total=22000
)

# ── BILL 3: TKR — should be PARTIAL (room excess + implant cap + physio + copay)
build_bill(
    output_path="data/sample_bill_PARTIAL_knee_replacement.pdf",
    hospital="Manipal Hospitals",
    address="Kharadi, Pune — 411014 | GSTIN: 27AABCM9012C1Z8",
    bill_no="MAN-PUN-2024-07651",
    date="17-Aug-2024",
    patient="Vikram Singh (Age: 67 Years)",
    policy_no="POL-HEALTH-2024-001",
    policy_start="01-May-2021",
    admission="12-Aug-2024",
    discharge="17-Aug-2024",
    diagnosis="Knee Osteoarthritis — Total Knee Replacement (Right)",
    icd_codes=["M17.11", "Z96.641"],
    preauth=True,
    preauth_no="PA-2024-54321",
    items=[
        {"description": "Room Charges — Deluxe Private Room (5 nights @ Rs. 8,000/night)", "amount": 40000,  "cpt_code": "99221"},
        {"description": "Orthopedic Surgeon Fees — Total Knee Replacement",                 "amount": 85000,  "cpt_code": "27447"},
        {"description": "Knee Implant — Cruciate-Retaining Titanium System",                "amount": 120000, "cpt_code": "27447"},
        {"description": "Anesthesiologist Fees",                                             "amount": 15000,  "cpt_code": "01400"},
        {"description": "Operation Theatre Charges",                                         "amount": 25000,  "cpt_code": "00400"},
        {"description": "ICU Charges (1 day post-operative)",                               "amount": 7500,   "cpt_code": "99291"},
        {"description": "Physiotherapy Sessions (5 sessions)",                              "amount": 4000,   "cpt_code": "97110"},
        {"description": "Medicines and Consumables",                                         "amount": 12000,  "cpt_code": "99232"},
        {"description": "Lab Tests and Pre-operative Imaging",                               "amount": 8500,   "cpt_code": "80048"},
    ],
    total=317000
)

print("\nAll 3 sample bill PDFs generated in data/")
print("Run with: python main.py --pdf data/sample_bill_APPROVED_appendectomy.pdf")
