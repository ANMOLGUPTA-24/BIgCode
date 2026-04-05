"""
ocr_extractor.py
================
Extracts text from scanned hospital bill PDFs and images.
Uses PyMuPDF for digital PDFs, pytesseract for scanned images.
"""

import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class OCRExtractor:
    """
    Extracts text from:
    - Digital PDFs (using PyMuPDF for direct text extraction)
    - Scanned PDFs (renders each page to image, then runs OCR)
    - Image files (JPG, PNG — runs OCR directly)
    """

    def __init__(self):
        self._check_dependencies()

    def _check_dependencies(self):
        """Check which OCR backends are available."""
        self.has_fitz = False
        self.has_tesseract = False

        try:
            import fitz  # PyMuPDF
            self.has_fitz = True
            logger.info("PyMuPDF available for PDF text extraction.")
        except ImportError:
            logger.warning("PyMuPDF not available. Install with: pip install PyMuPDF")

        try:
            import pytesseract
            pytesseract.get_tesseract_version()
            self.has_tesseract = True
            logger.info("Tesseract OCR available for scanned images.")
        except Exception:
            logger.warning("Tesseract not available. Scanned PDF OCR will be limited.")

    def extract_from_pdf(self, pdf_path: str) -> str:
        """
        Extract text from a PDF file.
        Tries direct text extraction first; falls back to OCR if needed.
        """
        if not self.has_fitz:
            raise RuntimeError("PyMuPDF required for PDF extraction. pip install PyMuPDF")

        import fitz

        doc = fitz.open(pdf_path)
        full_text = []

        for page_num, page in enumerate(doc, start=1):
            text = page.get_text("text")

            if len(text.strip()) < 50 and self.has_tesseract:
                # Scanned page — render to image and OCR
                logger.info(f"Page {page_num}: Low text yield, falling back to OCR.")
                text = self._ocr_page(page, page_num)

            full_text.append(f"=== PAGE {page_num} ===\n{text}")

        doc.close()
        return "\n".join(full_text)

    def extract_from_image(self, image_path: str) -> str:
        """Extract text from an image file using Tesseract OCR."""
        if not self.has_tesseract:
            raise RuntimeError("Tesseract required for image OCR. Install tesseract-ocr.")

        import pytesseract
        from PIL import Image

        img = Image.open(image_path)
        text = pytesseract.image_to_string(img, lang="eng")
        return text

    def _ocr_page(self, page, page_num: int) -> str:
        """Render a PDF page to image and OCR it."""
        import pytesseract
        from PIL import Image
        import io

        mat = page.get_pixmap(matrix=__import__("fitz").Matrix(2, 2))  # 2x zoom for quality
        img_bytes = mat.tobytes("png")

        img = Image.open(io.BytesIO(img_bytes))
        text = pytesseract.image_to_string(img, lang="eng")
        return text

    def extract(self, file_path: str) -> str:
        """Auto-detect file type and extract text."""
        path = Path(file_path)
        ext = path.suffix.lower()

        if ext == ".pdf":
            return self.extract_from_pdf(file_path)
        elif ext in [".jpg", ".jpeg", ".png", ".tiff", ".bmp"]:
            return self.extract_from_image(file_path)
        else:
            raise ValueError(f"Unsupported file type: {ext}. Supported: pdf, jpg, png, tiff")


def generate_sample_bill_pdf(output_path: str, bill_data: dict):
    """
    Generate a synthetic hospital bill PDF for testing.
    Uses reportlab if available.
    """
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.lib.units import cm
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Table, TableStyle, Spacer
        from reportlab.lib import colors
    except ImportError:
        logger.warning("reportlab not installed. Cannot generate sample PDFs.")
        return

    doc = SimpleDocTemplate(output_path, pagesize=A4)
    styles = getSampleStyleSheet()
    story = []

    # Header
    story.append(Paragraph(f"<b>{bill_data.get('hospital', 'City Hospital')}</b>", styles['Title']))
    story.append(Paragraph("HOSPITAL BILL / INVOICE", styles['Heading2']))
    story.append(Spacer(1, 0.5 * cm))

    # Patient Info
    patient = bill_data.get("patient", {})
    info_data = [
        ["Bill ID:", bill_data.get("bill_id", ""), "Policy No:", patient.get("policy_number", "")],
        ["Patient:", patient.get("name", ""), "Age:", str(patient.get("age", ""))],
        ["Admission:", str(bill_data.get("admission_date", "")), "Discharge:", str(bill_data.get("discharge_date", ""))],
        ["Diagnosis:", bill_data.get("diagnosis", ""), "", ""],
    ]
    t = Table(info_data, colWidths=[3 * cm, 6 * cm, 3 * cm, 6 * cm])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.lightblue),
        ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
        ('FONTNAME', (2, 0), (2, -1), 'Helvetica-Bold'),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('PADDING', (0, 0), (-1, -1), 6),
    ]))
    story.append(t)
    story.append(Spacer(1, 0.5 * cm))

    # Line Items Table
    story.append(Paragraph("<b>Bill Details</b>", styles['Heading3']))
    headers = [["#", "Description", "CPT Code", "Amount (INR)"]]
    rows = headers + [
        [str(i + 1), item["description"], item.get("cpt_code", "-"), f"₹{item['amount']:,.2f}"]
        for i, item in enumerate(bill_data.get("line_items", []))
    ]
    rows.append(["", "", "<b>TOTAL</b>", f"<b>₹{bill_data.get('total_billed', 0):,.2f}</b>"])

    bill_table = Table(rows, colWidths=[1 * cm, 10 * cm, 3 * cm, 4 * cm])
    bill_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.darkblue),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('ALIGN', (3, 0), (3, -1), 'RIGHT'),
        ('ROWBACKGROUNDS', (0, 1), (-1, -2), [colors.white, colors.lightyellow]),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('PADDING', (0, 0), (-1, -1), 6),
        ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
        ('BACKGROUND', (0, -1), (-1, -1), colors.lightgrey),
    ]))
    story.append(bill_table)

    # Pre-auth info
    if bill_data.get("pre_authorization_obtained"):
        story.append(Spacer(1, 0.3 * cm))
        story.append(Paragraph(
            f"Pre-Authorization: <b>{bill_data.get('pre_auth_number', 'N/A')}</b>",
            styles['Normal']
        ))

    doc.build(story)
    logger.info(f"Generated sample bill PDF: {output_path}")
