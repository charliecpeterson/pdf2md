"""Compare normalized table cells with a pinned semantic reference CSV.

The command distinguishes exact agreement, disagreement, extraction refusal, and
the absence of a reference. `--strict` turns any non-agreement into a failing gate.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pdf2md.table_verify import compare_external_reference


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare normalized table cells with an external reference."
    )
    parser.add_argument("version_dir", type=Path)
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit nonzero on disagreement, refusal, or missing reference.",
    )
    parser.add_argument("--check", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()

    report = compare_external_reference(args.version_dir, args.reference)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(
        f"external table cells: {report['agree']}/{report['checked']} agree, "
        f"{report['disagree']} disagree, {report['tool_refused']} tool-refused, "
        f"{report['no_reference']} no-reference"
    )
    if (args.strict or args.check) and (
        report["disagree"] or report["tool_refused"] or report["no_reference"]
    ):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
