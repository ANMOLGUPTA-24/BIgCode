#!/usr/bin/env python3
"""
demo.py
=======
One-command end-to-end demonstration of the Insurance Claim Settlement Agent.

Shows the complete pipeline:
  Hospital Bill PDF  →  OCR  →  Gemini NLP  →  Rule Engine  →  Cited Decision

Usage:
    python demo.py                    # Run all 3 demo modes
    python demo.py --mode json        # Fast demo using structured JSON bills
    python demo.py --mode pdf         # Full OCR + Gemini pipeline on sample PDFs
    python demo.py --mode eval        # Rule engine accuracy evaluation
"""

import sys
import os
import json
import time
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from src.claim_agent import ClaimAgent

BASE_DIR    = Path(__file__).parent
POLICY_PATH = BASE_DIR / "data" / "sample_policy.json"
BILLS_PATH  = BASE_DIR / "data" / "test_bills.json"

SAMPLE_PDFS = [
    ("data/sample_bill_APPROVED_appendectomy.pdf",    "APPROVED"),
    ("data/sample_bill_REJECTED_dental.pdf",          "REJECTED"),
    ("data/sample_bill_PARTIAL_knee_replacement.pdf", "PARTIAL"),
]

GROUND_TRUTH = {
    "BILL-2024-001": "APPROVED",
    "BILL-2024-002": "PARTIAL",
    "BILL-2024-003": "REJECTED",
    "BILL-2024-004": "REJECTED",
    "BILL-2024-005": "PARTIAL",
    "BILL-2024-006": "REJECTED",
}

BANNER = """
╔══════════════════════════════════════════════════════════════════╗
║       Insurance Claim Settlement Agent — Live Demo               ║
║       Powered by Google Gemini 2.0 Flash + Deterministic Rules   ║
╚══════════════════════════════════════════════════════════════════╝
"""

def print_sep(char="─", n=70):
    print(char * n)

def demo_json(agent):
    """Mode 1: Fast demo using pre-structured JSON bills (no API key needed)."""
    print("\n" + "═"*70)
    print("  MODE 1 — JSON BILLS  (Rule Engine Demo, No API Key Required)")
    print("═"*70)

    with open(BILLS_PATH) as f:
        bills = json.load(f)["test_cases"]

    total = len(bills)
    correct = 0

    for bill in bills:
        bid = bill["bill_id"]
        expected = GROUND_TRUTH.get(bid, "?")
        print(f"\n  Processing {bid} ({bill['diagnosis'][:50]})...")

        t0 = time.time()
        decision = agent.process_json_bill(bill)
        elapsed = time.time() - t0

        match = decision.overall_decision == expected
        if match:
            correct += 1

        icon = {"APPROVED": "✅", "REJECTED": "❌", "PARTIAL": "⚠️ "}.get(
            decision.overall_decision, "?"
        )
        print(f"  {icon}  Decision : {decision.overall_decision:10s}  (expected: {expected})  {'✓ CORRECT' if match else '✗ WRONG'}")
        print(f"      Billed   : ₹{decision.total_billed:>10,.0f}")
        print(f"      Approved : ₹{decision.total_approved:>10,.0f}")
        print(f"      Rejected : ₹{decision.total_rejected:>10,.0f}")
        print(f"      Net Pay  : ₹{decision.net_payable:>10,.0f}  ({elapsed*1000:.0f}ms)")

        # Show first citation
        if decision.citations_used:
            print(f"      Cite [1] : {decision.citations_used[0]}")
        if decision.blocking_rejections:
            print(f"      Reason   : {decision.blocking_rejections[0][:80]}")

    print()
    print_sep("═")
    print(f"  Result: {correct}/{total} correct  ({correct/total*100:.0f}% accuracy)")
    print_sep("═")


def demo_pdf(agent):
    """Mode 2: Full OCR + Gemini pipeline on sample PDFs."""
    has_key = bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))

    print("\n" + "═"*70)
    print("  MODE 2 — PDF BILLS  (Full OCR + Gemini NLP Pipeline)")
    print("═"*70)

    if not has_key:
        print("\n  ⚠️  GEMINI_API_KEY not set. Skipping PDF mode.")
        print("     Set it with: export GEMINI_API_KEY=your_key")
        print("     Get a free key at: https://aistudio.google.com/app/apikey")
        print("     Then re-run: python demo.py --mode pdf")
        return

    for pdf_path, expected in SAMPLE_PDFS:
        full_path = BASE_DIR / pdf_path
        if not full_path.exists():
            print(f"\n  ⚠️  PDF not found: {pdf_path}")
            print(f"     Generate it first: python scripts/generate_sample_bills.py")
            continue

        print(f"\n  Processing: {pdf_path}")
        print(f"  Step 1/3: OCR extraction...", end="", flush=True)
        t0 = time.time()

        decision = agent.process_pdf_bill(str(full_path))
        elapsed = time.time() - t0

        print(f" done")
        print(f"  Step 2/3: Gemini NLP parsing... done")
        print(f"  Step 3/3: Rule engine evaluation... done")
        print(f"  Total: {elapsed:.1f}s")
        print()

        match = decision.overall_decision == expected
        icon = {"APPROVED": "✅", "REJECTED": "❌", "PARTIAL": "⚠️ "}.get(
            decision.overall_decision, "?"
        )
        print(f"  {icon}  Decision : {decision.overall_decision:10s}  (expected: {expected})  {'✓ CORRECT' if match else '✗ WRONG'}")
        print(f"      Patient  : {decision.patient_name}")
        print(f"      Hospital : {decision.hospital}")
        print(f"      Billed   : ₹{decision.total_billed:>10,.0f}")
        print(f"      Net Pay  : ₹{decision.net_payable:>10,.0f}")
        if decision.citations_used:
            print(f"      Cite [1] : {decision.citations_used[0]}")


def demo_eval(agent):
    """Mode 3: Accuracy evaluation with detailed per-decision breakdown."""
    print("\n" + "═"*70)
    print("  MODE 3 — EVALUATION METRICS")
    print("═"*70)

    with open(BILLS_PATH) as f:
        bills = json.load(f)["test_cases"]

    print(f"\n  {'Bill ID':<20} {'Scenario':<35} {'Exp':<10} {'Got':<10} {'Match'}")
    print_sep()

    results = []
    for bill in bills:
        bid = bill["bill_id"]
        if bid not in GROUND_TRUTH:
            continue
        decision = agent.process_json_bill(bill)
        exp = GROUND_TRUTH[bid]
        got = decision.overall_decision
        match = exp == got
        results.append((bid, exp, got, match, decision))
        icon = "✓" if match else "✗"
        scenario = bill["diagnosis"][:33]
        print(f"  {bid:<20} {scenario:<35} {exp:<10} {got:<10} {icon}")

    correct = sum(1 for *_, m, _ in results if m)
    total   = len(results)
    acc     = correct / total * 100

    print_sep()
    print(f"\n  Claim-level accuracy   : {correct}/{total} = {acc:.1f}%")

    # Per-type breakdown
    for dtype in ["APPROVED", "PARTIAL", "REJECTED"]:
        tp  = sum(1 for _, e, g, m, _ in results if e == dtype and m)
        tot = sum(1 for _, e, _, _, _  in results if e == dtype)
        pct = (tp/tot*100) if tot else 0
        print(f"  {dtype:<10} accuracy : {tp}/{tot} = {pct:.0f}%")

    print()
    # Financial accuracy check
    print("  Financial summary sample (BILL-2024-005 — TKR):")
    tkr = next((d for bid, _, _, _, d in results if bid == "BILL-2024-005"), None)
    if tkr:
        print(f"    Billed   ₹{tkr.total_billed:>10,.0f}  |  Approved ₹{tkr.total_approved:>10,.0f}")
        print(f"    Rejected ₹{tkr.total_rejected:>10,.0f}  |  Co-pay   ₹{tkr.copayment:>10,.0f}")
        print(f"    Net Pay  ₹{tkr.net_payable:>10,.0f}")
        print(f"    Citations: {len(tkr.citations_used)}")
        for c in tkr.citations_used:
            print(f"      • {c}")

    print()
    print_sep("═")
    print(f"  FINAL SCORE: {correct}/{total} = {acc:.1f}%")
    print_sep("═")


def main():
    parser = argparse.ArgumentParser(description="Insurance Claim Settlement Agent Demo")
    parser.add_argument("--mode", choices=["json", "pdf", "eval", "all"], default="all")
    args = parser.parse_args()

    print(BANNER)
    print("  Initialising ClaimAgent with policy:", str(POLICY_PATH))
    agent = ClaimAgent(str(POLICY_PATH))
    print("  Policy loaded:", agent.policy.get_summary()["policy_name"])
    print()

    if args.mode in ("json", "all"):
        demo_json(agent)

    if args.mode in ("eval", "all"):
        demo_eval(agent)

    if args.mode in ("pdf", "all"):
        demo_pdf(agent)

    print("\n  Done. For the full interactive demo, run:")
    print("    streamlit run streamlit_app.py")
    print()


if __name__ == "__main__":
    main()
