#!/usr/bin/env python3
"""
main.py
=======
Command-line interface for the Insurance Claim Settlement Agent.

Usage:
    # Process all test bills and print reports
    python main.py

    # Process a specific bill
    python main.py --bill-id BILL-2024-003

    # Process a scanned PDF bill
    python main.py --pdf /path/to/hospital_bill.pdf

    # Export JSON output
    python main.py --bill-id BILL-2024-001 --json

    # Run evaluation metrics
    python main.py --evaluate
"""

import argparse
import json
import logging
import os
import sys
from pathlib import Path

# ── Add project root to path ──────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent))

from src.claim_agent import ClaimAgent

# ── Paths ─────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
POLICY_PATH = BASE_DIR / "data" / "sample_policy.json"
BILLS_PATH = BASE_DIR / "data" / "test_bills.json"

# Expected decisions for evaluation (ground truth)
GROUND_TRUTH = {
    "BILL-2024-001": "APPROVED",
    "BILL-2024-002": "PARTIAL",
    "BILL-2024-003": "REJECTED",
    "BILL-2024-004": "REJECTED",
    "BILL-2024-005": "PARTIAL",
    "BILL-2024-006": "REJECTED",
}


def setup_logging(verbose: bool = False):
    level = logging.DEBUG if verbose else logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )


def run_all_bills(agent: ClaimAgent, as_json: bool = False):
    """Process all test bills and print reports."""
    decisions = agent.process_json_file(str(BILLS_PATH))

    for decision in decisions:
        if as_json:
            print(json.dumps(agent.export_json(decision), indent=2))
        else:
            print(agent.format_report(decision))


def run_single_bill(agent: ClaimAgent, bill_id: str, as_json: bool = False):
    """Process a single bill by ID."""
    decision = agent.process_json_file(str(BILLS_PATH), bill_id=bill_id)

    if as_json:
        print(json.dumps(agent.export_json(decision), indent=2))
    else:
        print(agent.format_report(decision))


def run_evaluation(agent: ClaimAgent):
    """
    Evaluate the rule engine against ground truth labels.
    Prints accuracy metrics.
    """
    import json as _json

    with open(BILLS_PATH) as f:
        bills = _json.load(f).get("test_cases", [])

    print("\n" + "=" * 60)
    print("     RULE ENGINE EVALUATION METRICS")
    print("=" * 60)
    print(f"{'Bill ID':<20} {'Expected':<12} {'Got':<12} {'Match'}")
    print("-" * 60)

    correct = 0
    total = len([b for b in bills if b["bill_id"] in GROUND_TRUTH])
    results = []

    for bill in bills:
        bid = bill["bill_id"]
        if bid not in GROUND_TRUTH:
            continue

        decision = agent.process_json_bill(bill)
        expected = GROUND_TRUTH[bid]
        got = decision.overall_decision
        match = "✅" if expected == got else "❌"
        if expected == got:
            correct += 1

        results.append({
            "bill_id": bid,
            "expected": expected,
            "got": got,
            "approved": decision.total_approved,
            "rejected": decision.total_rejected
        })
        print(f"{bid:<20} {expected:<12} {got:<12} {match}")

    accuracy = correct / total * 100 if total > 0 else 0
    print("=" * 60)
    print(f"  Accuracy: {correct}/{total} = {accuracy:.1f}%")
    print(f"  Precision (claim-level): {accuracy:.1f}%")

    # Per-category breakdown
    cats = {"APPROVED": {"tp": 0, "fn": 0}, "REJECTED": {"tp": 0, "fn": 0}, "PARTIAL": {"tp": 0, "fn": 0}}
    for r in results:
        exp, got = r["expected"], r["got"]
        if exp == got:
            cats[exp]["tp"] += 1
        else:
            cats[exp]["fn"] += 1

    print("\n  Per-Decision-Type Accuracy:")
    for cat, counts in cats.items():
        tp = counts["tp"]
        total_cat = tp + counts["fn"]
        acc = tp / total_cat * 100 if total_cat > 0 else 0
        print(f"    {cat:<10}: {tp}/{total_cat} = {acc:.0f}%")

    print("=" * 60 + "\n")
    return accuracy


def run_pdf_bill(agent: ClaimAgent, pdf_path: str, as_json: bool = False):
    """Process a PDF bill."""
    print(f"Processing PDF: {pdf_path}")
    decision = agent.process_pdf_bill(pdf_path)
    if as_json:
        print(json.dumps(agent.export_json(decision), indent=2))
    else:
        print(agent.format_report(decision))


def main():
    parser = argparse.ArgumentParser(
        description="Insurance Claim Settlement Agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py                          # Run all test bills
  python main.py --bill-id BILL-2024-001 # Run specific bill
  python main.py --evaluate               # Show accuracy metrics
  python main.py --json                   # JSON output
  python main.py --pdf bill.pdf           # Process scanned PDF
        """
    )
    parser.add_argument("--bill-id", help="Process a specific bill ID")
    parser.add_argument("--pdf", help="Path to a scanned/digital PDF hospital bill")
    parser.add_argument("--evaluate", action="store_true", help="Run evaluation metrics")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--policy", default=str(POLICY_PATH), help="Path to policy JSON")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")

    args = parser.parse_args()
    setup_logging(args.verbose)

    # Initialize agent
    agent = ClaimAgent(args.policy)

    if args.evaluate:
        run_evaluation(agent)
    elif args.pdf:
        run_pdf_bill(agent, args.pdf, as_json=args.json)
    elif args.bill_id:
        run_single_bill(agent, args.bill_id, as_json=args.json)
    else:
        run_all_bills(agent, as_json=args.json)


if __name__ == "__main__":
    main()
