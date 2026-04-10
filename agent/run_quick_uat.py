"""agent/run_quick_uat.py — QuickUAT CLI entry point.

Usage:
  # With APK install:
  python agent/run_quick_uat.py \\
    --candidate candidate.apk \\
    --feature "hotel details page" \\
    --criteria "Hotel Details Page shows photos, amenities, price, and Book Now button"

  # Skip install (app already on device):
  python agent/run_quick_uat.py \\
    --skip-install \\
    --feature "hotel details page" \\
    --criteria "Hotel Details Page shows photos, amenities, price, and Book Now button"
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from agent.quick_uat import QuickUATRunner

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="QuickUAT — sub-20s targeted feature verification",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--feature",
        required=True,
        metavar="DESCRIPTION",
        help="Feature description — used to match a LOB (e.g. 'hotel details page')",
    )
    parser.add_argument(
        "--criteria",
        required=True,
        metavar="TEXT",
        help="Acceptance criteria for Claude to evaluate against the screenshot",
    )
    parser.add_argument(
        "--candidate",
        default=None,
        metavar="APK_PATH",
        help="Path to candidate APK (optional — omit with --skip-install)",
    )
    parser.add_argument(
        "--skip-install",
        action="store_true",
        help="Skip APK install and use whatever version is already on the device",
    )
    parser.add_argument(
        "--manual-nav",
        action="store_true",
        help="Skip automated navigation — user navigates manually, system only verifies",
    )
    parser.add_argument(
        "--nav-mode",
        choices=["vision", "deterministic", "auto"],
        default="vision",
        help="Navigation strategy: 'vision' (Claude vision loop, default), "
             "'deterministic' (predefined steps), 'auto' (deterministic + vision fallback)",
    )
    args = parser.parse_args()

    if args.candidate and not Path(args.candidate).exists():
        print(f"ERROR: APK not found: {args.candidate}", file=sys.stderr)
        sys.exit(1)

    if not args.candidate and not args.skip_install:
        print(
            "ERROR: Provide --candidate <apk> or --skip-install to use the installed app.",
            file=sys.stderr,
        )
        sys.exit(1)

    runner = QuickUATRunner(
        candidate_apk=args.candidate,
        feature_description=args.feature,
        acceptance_criteria=args.criteria,
        skip_install=args.skip_install,
        manual_nav=args.manual_nav,
        nav_mode=args.nav_mode,
    )
    result = runner.run()

    # Print result without the screenshot path (large base64 not useful in terminal)
    printable = {k: v for k, v in result.items() if k != "screenshot"}
    print(json.dumps(printable, indent=2))

    sys.exit(0 if result.get("verdict") == "PASS" else 1)


if __name__ == "__main__":
    main()
