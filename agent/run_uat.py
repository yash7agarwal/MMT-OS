"""
agent/run_uat.py — CLI entry point for UAT runs

Usage:
  python agent/run_uat.py \\
    --candidate path/to/new.apk \\
    --feature "hotel detail gallery redesign" \\
    --accounts accounts.json \\
    [--baseline path/to/old.apk] \\
    [--criteria "Gallery should show 15 images"] \\
    [--run-id my_custom_run_id]

accounts.json format:
  [{"id": "acc1", "type": "returning_user"}, ...]
  Optional fields per account: "email", "password" (used for future auto-login)
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

# Ensure the project root is on sys.path when running as a script
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from agent.orchestrator import Orchestrator

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prism Phase 2 — Multi-agent UAT runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--candidate",
        required=True,
        metavar="APK_PATH",
        help="Path to the candidate (new) APK to test",
    )
    parser.add_argument(
        "--feature",
        required=True,
        metavar="DESCRIPTION",
        help="Natural-language description of the feature under test",
    )
    parser.add_argument(
        "--accounts",
        required=True,
        metavar="JSON_FILE",
        help="Path to accounts JSON file",
    )
    parser.add_argument(
        "--baseline",
        default=None,
        metavar="APK_PATH",
        help="(Optional) Path to the baseline APK for comparison",
    )
    parser.add_argument(
        "--criteria",
        default="",
        metavar="TEXT",
        help="(Optional) Acceptance criteria text for scenario generation",
    )
    parser.add_argument(
        "--run-id",
        default=None,
        metavar="RUN_ID",
        help="(Optional) Custom run ID — auto-generated if omitted",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity (default: INFO)",
    )
    return parser.parse_args()


def _load_accounts(accounts_path: str) -> list[dict]:
    path = Path(accounts_path)
    if not path.exists():
        logger.error(f"Accounts file not found: {accounts_path}")
        sys.exit(1)
    try:
        with open(path) as f:
            accounts = json.load(f)
        if not isinstance(accounts, list) or not accounts:
            logger.error("accounts.json must be a non-empty JSON array")
            sys.exit(1)
        # Ensure every account has at least an "id" field
        for i, acc in enumerate(accounts):
            if "id" not in acc:
                acc["id"] = f"acc_{i + 1}"
                logger.warning(
                    f"Account at index {i} has no 'id' field — assigned: {acc['id']}"
                )
        logger.info(f"Loaded {len(accounts)} accounts from {accounts_path}")
        return accounts
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in accounts file: {e}")
        sys.exit(1)


def main() -> None:
    args = _parse_args()

    # Apply log level
    logging.getLogger().setLevel(getattr(logging, args.log_level))

    # Validate candidate APK exists
    if not Path(args.candidate).exists():
        logger.error(f"Candidate APK not found: {args.candidate}")
        sys.exit(1)

    # Validate baseline APK if provided
    if args.baseline and not Path(args.baseline).exists():
        logger.error(f"Baseline APK not found: {args.baseline}")
        sys.exit(1)

    accounts = _load_accounts(args.accounts)

    logger.info("=" * 60)
    logger.info("Prism Phase 2 — UAT Run")
    logger.info(f"  Feature   : {args.feature}")
    logger.info(f"  Candidate : {args.candidate}")
    logger.info(f"  Baseline  : {args.baseline or '(none)'}")
    logger.info(f"  Accounts  : {len(accounts)}")
    logger.info(f"  Criteria  : {args.criteria or '(none)'}")
    logger.info("=" * 60)

    orchestrator = Orchestrator(
        candidate_apk=args.candidate,
        baseline_apk=args.baseline,
        feature_description=args.feature,
        accounts=accounts,
        acceptance_criteria=args.criteria,
        run_id=args.run_id,
    )

    run_summary = orchestrator.run()

    # Print the full run summary as formatted JSON
    print("\n--- RUN SUMMARY (JSON) ---")
    # Remove the verbose 'results' list from the printed output to keep it readable;
    # the full summary is saved to disk by the orchestrator.
    printable = {k: v for k, v in run_summary.items() if k != "results"}
    print(json.dumps(printable, indent=2))
    print(f"\nFull results written to: {run_summary.get('summary_path', 'reports/')}")


if __name__ == "__main__":
    main()
