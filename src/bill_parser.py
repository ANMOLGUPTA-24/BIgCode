"""
bill_parser.py  —  Gemini Vision upgrade
=========================================
Sends PDF/image pages directly to Gemini 2.0 Flash Vision.
No OCR step needed — Gemini reads handwriting, stamps, Hindi text, merged cells.
Falls back to text mode when images are unavailable.
"""

import json, re, logging, os, base64
from datetime import datetime
from google import genai

logger = logging.getLogger(__name__)
GEMINI_MODEL = "gemini-2.0-flash"

_SCHEMA = """\nReturn ONLY valid JSON (no markdown):
{
  "patient_name": "string",
  "patient_age": number or null,
  "policy_number": "string or null",
  "hospital_name": "string",
  "admission_date": "YYYY-MM-DD or null",
  "discharge_date": "YYYY-MM-DD or null",
  "primary_diagnosis": "string",
  "diagnosis_codes": ["ICD codes or []"],
  "pre_authorization_number": "string or null",
  "pre_authorization_obtained": true or false,
  "line_items": [{"description":"string","amount":number,"cpt_code":"string or null","days":number}],
  "total_billed": number,
  "contains_dental": true or false,
  "contains_cosmetic": true or false,
  "contains_maternity": true or false,
  "contains_psychiatric": true or false,
  "extraction_confidence": "high|medium|low",
  "extraction_notes": "any issues or ambiguities"
}"""

VISION_PROMPT = (
    "You are an expert medical billing analyst. Read this hospital bill image carefully — "
    "all text, tables, stamps, and handwriting. Extract every detail." + _SCHEMA
)
TEXT_PROMPT = (
    "You are an expert medical billing analyst. Extract structured info from this hospital bill text."
    + _SCHEMA + "\n\nBill text:\n{bill_text}"
)


class BillParser:
    """Parses hospital bills via Gemini Vision (preferred) or Gemini text NLP (fallback)."""

    def __init__(self):
        api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not api_key:
            raise EnvironmentError(
                "Set GEMINI_API_KEY or GOOGLE_API_KEY.\n"
                "Free key at: https://aistudio.google.com/app/apikey"
            )
        self.client = genai.Client(api_key=api_key)

    # ── Public ────────────────────────────────────────────────────────────────

    def parse_from_json(self, bill_dict: dict) -> dict:
        """Structured JSON bill — no API call needed (used for testing)."""
        return self._normalize_bill(bill_dict)

    def parse_from_pdf_vision(self, pdf_path: str) -> dict:
        """
        PRIMARY method: send each PDF page as an image to Gemini Vision.
        Handles scanned bills, stamps, handwriting, Hindi/mixed-language text.
        """
        import fitz
        doc = fitz.open(pdf_path)
        pages_data = []

        for i, page in enumerate(doc):
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))  # 2× zoom for accuracy
            img_b64 = base64.b64encode(pix.tobytes("png")).decode()
            logger.info(f"Vision: page {i+1}/{len(doc)} → Gemini")

            resp = self.client.models.generate_content(
                model=GEMINI_MODEL,
                contents=[{"parts": [
                    {"inline_data": {"mime_type": "image/png", "data": img_b64}},
                    {"text": VISION_PROMPT}
                ]}]
            )
            pages_data.append(self._parse_response(resp.text))

        doc.close()
        return self._normalize_bill(self._merge_pages(pages_data))

    def parse_from_image_vision(self, image_path: str) -> dict:
        """Parse a bill photo (JPG/PNG) — ideal for WhatsApp submissions."""
        import mimetypes
        mime = mimetypes.guess_type(image_path)[0] or "image/jpeg"
        with open(image_path, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode()

        resp = self.client.models.generate_content(
            model=GEMINI_MODEL,
            contents=[{"parts": [
                {"inline_data": {"mime_type": mime, "data": img_b64}},
                {"text": VISION_PROMPT}
            ]}]
        )
        return self._normalize_bill(self._parse_response(resp.text))

    def parse_from_text(self, bill_text: str) -> dict:
        """Fallback: send OCR-extracted text to Gemini NLP."""
        resp = self.client.models.generate_content(
            model=GEMINI_MODEL,
            contents=TEXT_PROMPT.format(bill_text=bill_text)
        )
        return self._normalize_bill(self._parse_response(resp.text))

    # ── Internal ──────────────────────────────────────────────────────────────

    def _parse_response(self, raw: str) -> dict:
        cleaned = re.sub(r"```(?:json)?\n?", "", raw.strip()).strip().rstrip("`")
        return json.loads(cleaned)

    def _merge_pages(self, pages: list) -> dict:
        if not pages: return {}
        if len(pages) == 1: return pages[0]
        base = pages[0].copy()
        items = list(base.get("line_items", []))
        for p in pages[1:]:
            items.extend(p.get("line_items", []))
            for k in ["patient_name","policy_number","hospital_name",
                      "admission_date","discharge_date","primary_diagnosis"]:
                if not base.get(k) and p.get(k):
                    base[k] = p[k]
        base["line_items"] = items
        base["total_billed"] = sum(float(i.get("amount",0)) for i in items)
        return base

    def _normalize_bill(self, bill: dict) -> dict:
        days = 0
        a, d = str(bill.get("admission_date",""))[:10], str(bill.get("discharge_date",""))[:10]
        if a and d:
            try: days = max((datetime.strptime(d,"%Y-%m-%d")-datetime.strptime(a,"%Y-%m-%d")).days, 1)
            except: days = 1

        items = []
        for it in bill.get("line_items", []):
            items.append({
                "description": it.get("description",""),
                "amount":      float(it.get("amount", 0)),
                "cpt_code":    it.get("cpt_code","") or "",
                "category":    self._classify(it),
                "days":        int(it.get("days",1) or 1),
            })

        txt = " ".join([
            bill.get("primary_diagnosis",""), bill.get("hospital_name",""),
            " ".join(i.get("description","") for i in bill.get("line_items",[]))
        ]).lower()

        conf = bill.get("extraction_confidence", "medium")

        return {
            "bill_id":   bill.get("bill_id","UNKNOWN"),
            "patient": {
                "name":              bill.get("patient",{}).get("name") or bill.get("patient_name",""),
                "age":               bill.get("patient",{}).get("age")  or bill.get("patient_age"),
                "policy_number":     bill.get("patient",{}).get("policy_number") or bill.get("policy_number",""),
                "policy_start_date": bill.get("patient",{}).get("policy_start_date") or bill.get("policy_start_date"),
            },
            "hospital":                   bill.get("hospital", bill.get("hospital_name","")),
            "admission_date":             a,
            "discharge_date":             d,
            "days_admitted":              days,
            "diagnosis":                  bill.get("diagnosis", bill.get("primary_diagnosis","")),
            "diagnosis_codes":            bill.get("diagnosis_codes",[]),
            "pre_authorization_obtained": bill.get("pre_authorization_obtained", False),
            "pre_auth_number":            bill.get("pre_auth_number") or bill.get("pre_authorization_number"),
            "line_items":                 items,
            "total_billed":               float(bill.get("total_billed") or sum(i.get("amount",0) for i in bill.get("line_items",[]))),
            "extraction_confidence":      conf,
            "extraction_notes":           bill.get("extraction_notes",""),
            "flags": {
                "contains_dental":    ("dental" in txt or "root canal" in txt or
                                       any(str(i.get("cpt_code","")).upper().startswith("D") and
                                           len(str(i.get("cpt_code",""))) == 5
                                           for i in bill.get("line_items",[]))),
                "contains_cosmetic":  any(w in txt for w in ["cosmetic","rhinoplasty","liposuction","face lift","hair transplant","aesthetic","body contour","weight loss"]),
                "contains_maternity": any(w in txt for w in ["pregnancy","maternity","childbirth","delivery","antenatal"]),
                "contains_psychiatric": any(w in txt for w in ["psychiatric","mental disorder","alzheimer","parkinson"]),
                "contains_dental_code": any(str(i.get("cpt_code","")).upper().startswith("D") and len(str(i.get("cpt_code",""))) == 5 for i in bill.get("line_items",[])),
                "low_confidence":     conf == "low",
            }
        }

    def _classify_item(self, item: dict) -> str:  # alias kept for test compatibility
        return self._classify(item)

    def _classify(self, item: dict) -> str:
        d = item.get("description","").lower()
        if any(w in d for w in ["room","bed ","accommodation","ward"]): return "room"
        if any(w in d for w in ["icu","intensive care","critical care"]): return "icu"
        if any(w in d for w in ["implant","prosthetic","stent","graft","mesh"]): return "implant"
        if any(w in d for w in ["physio","rehabilitation","therapy session"]): return "physiotherapy"
        if "ambulance" in d: return "ambulance"
        if any(w in d for w in ["surgeon","surgery","operation","theatre","laparoscop"]): return "surgery"
        if any(w in d for w in ["anesthes","anaesthe"]): return "anesthesia"
        if any(w in d for w in ["medicine","drug","pharmacy","consumable","injection"]) or item.get("cpt_code","").lower()=="pharmacy": return "pharmacy"
        if any(w in d for w in ["lab","blood","test","scan","x-ray","mri","ct ","ultrasound","imaging","ecg"]): return "diagnostics"
        if "nursing" in d: return "nursing"
        return "other"
