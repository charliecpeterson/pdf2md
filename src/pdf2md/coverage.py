"""The lossless auditor: tally how every block was accounted for. The emitter
sets each block's `coverage_status` and collects flags as it writes; this just
turns that into a report. `CoverageReport.lossless` is the enforceable invariant.
"""

from __future__ import annotations

from pdf2md.schema import Block, CoverageFlag, CoverageReport, CoverageStatus

# Flag reason for a prose block whose text stayed symbol-font garbage after the
# pdfium refill (emit sets it; this module tallies it). Shared so the producer and
# the counter can't drift on the string.
ILLEGIBLE_REASON = "illegible text layer"


def build_report(doc_id: str, blocks: list[Block], flags: list[CoverageFlag]) -> CoverageReport:
    def count(status: CoverageStatus) -> int:
        return sum(1 for b in blocks if b.coverage_status == status)

    return CoverageReport(
        doc_id=doc_id,
        total_blocks=len(blocks),
        emitted=count(CoverageStatus.EMITTED),
        cropped=count(CoverageStatus.CROPPED),
        flagged=count(CoverageStatus.FLAGGED),
        dropped=count(CoverageStatus.DROPPED),
        illegible=sum(1 for f in flags if f.reason == ILLEGIBLE_REASON),
        flags=flags,
    )
