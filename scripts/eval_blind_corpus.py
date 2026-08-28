"""Audit structural conversion results for the frozen unseen-PDF corpus.

This gate checks source identity, block accounting, artifact presence, and review
burden. It has no semantic labels and cannot support an extraction-accuracy claim.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


_ROOT = Path(__file__).parent.parent
_DEFAULT_CORPUS = _ROOT / "tests" / "blind_pdf_corpus.json"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _latest_version(document_dir: Path) -> Path | None:
    versions = sorted(
        document_dir.glob("v*"),
        key=lambda path: int(path.name[1:]) if path.name[1:].isdigit() else -1,
        reverse=True,
    )
    return next((path for path in versions if (path / "provenance.json").is_file()), None)


def evaluate(source_dir: Path, output_dir: Path, corpus: dict) -> dict:
    records = []
    for expected in corpus["documents"]:
        source = source_dir / f"{expected['id']}.pdf"
        source_valid = source.is_file() and _sha256(source) == expected["sha256"]
        version_dir = _latest_version(output_dir / expected["sha256"][:16])
        record = {
            "id": expected["id"],
            "source_valid": source_valid,
            "version": version_dir.name if version_dir else None,
            "pages": None,
            "accounted_for": False,
            "structurally_complete": False,
            "content_present": False,
            "review_flags": None,
            "tables": None,
            "figures": None,
            "equations": None,
        }
        if version_dir is not None:
            provenance = json.loads((version_dir / "provenance.json").read_text())
            profile = json.loads((version_dir / "profile.json").read_text())
            coverage = provenance.get("coverage") or {}
            total = int(coverage.get("total_blocks", 0))
            dispositions = sum(
                int(coverage.get(field, 0))
                for field in ("emitted", "cropped", "flagged", "dropped")
            )
            content = version_dir / str(profile.get("contents") or "")
            record.update({
                "pages": provenance.get("page_count"),
                "accounted_for": total == dispositions,
                "structurally_complete": bool(
                    total == dispositions
                    and int(coverage.get("flagged", 0)) == 0
                    and int(coverage.get("dropped", 0)) == 0
                ),
                "content_present": bool(profile.get("contents") and content.is_file()),
                "review_flags": len(coverage.get("flags") or []),
                "tables": profile.get("tables", 0),
                "figures": profile.get("figures", 0),
                "equations": profile.get("equations", 0),
                "source_matches_output": provenance.get("source_sha256") == expected["sha256"],
                "page_count_matches": provenance.get("page_count") == expected["pages"],
            })
        records.append(record)

    summary = {
        "documents": len(records),
        "sources_valid": sum(record["source_valid"] for record in records),
        "conversions": sum(record["version"] is not None for record in records),
        "accounted_for": sum(record["accounted_for"] for record in records),
        "structurally_complete": sum(record["structurally_complete"] for record in records),
        "content_present": sum(record["content_present"] for record in records),
        "documents_requiring_review": sum((record["review_flags"] or 0) > 0 for record in records),
        "total_review_flags": sum(record["review_flags"] or 0 for record in records),
    }
    return {"schema_version": 1, "summary": summary, "documents": records}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_dir", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--corpus", type=Path, default=_DEFAULT_CORPUS)
    parser.add_argument(
        "--check", action="store_true", help="Compare the summary with the frozen baseline."
    )
    parser.add_argument(
        "--strict", action="store_true",
        help="Require valid sources, outputs, exact page counts, accounting, and main content.",
    )
    args = parser.parse_args()

    corpus = json.loads(args.corpus.read_text())
    report = evaluate(args.source_dir, args.output_dir, corpus)
    print(json.dumps(report, indent=2))

    failed = False
    if args.check:
        expected = corpus.get("expected_summary")
        if expected is None:
            raise SystemExit("corpus has no expected_summary to check")
        evaluator_hash = corpus.get("evaluator_sha256")
        if evaluator_hash is None:
            raise SystemExit("corpus has no evaluator_sha256 to check")
        failed = (
            report["summary"] != expected
            or _sha256(Path(__file__)) != evaluator_hash
        )
    if args.strict:
        failed = failed or any(
            not record["source_valid"]
            or record["version"] is None
            or not record.get("source_matches_output")
            or not record.get("page_count_matches")
            or not record["accounted_for"]
            or not record["content_present"]
            for record in report["documents"]
        )
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
