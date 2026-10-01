# Fresh-scan review is ready

Three new scans, 23 pages, and a seven-page human review sheet are ready.
No accuracy score or production promotion is justified until independent
source-review labels arrive.

## What ran

The [protocol](scan-evaluation-protocol.md) and
[source identities](results/scan-evaluation-20260922-sources.json) were frozen
before conversion. Source hashes had no match in 907 checked metadata files
and 40 local QA PDFs, representing 177 distinct prior source hashes. This is
a scoped inventory, not proof of novelty outside this machine or exclusion
from model training.

Docling converted all three documents serially on MPS with forced OCR, formula
enrichment off, and chart/figure OCR enrichment off. The unchanged Tesseract
comparison policy screened all 23 pages without page-reader failures. Source,
bundle, trained-data, implementation, and report hashes remain recorded.
Original bundles were rehashed after review preparation and are unchanged.

The fixed sampler selected seven pages rather than its maximum of twelve:
two from each document's flagged group and the sole page in a neither-flagged
group. Do not interpret this small control group as strong evidence about
unflagged-page safety. All three documents come from the same NIST archive.

## Human review

Open this local file in a browser:

```text
~/scratch/pdf2md-scan-evaluation-20260922/human-review/review.html
```

Compare each full source page with the output and its content fallback crops.
Use the full-Markdown link to check continuation text outside page-attributed
passages. Enter yes, no, or uncertain, your reviewer name, and a source/output
evidence note. Download `labels.json` before closing or reloading the page;
the form does not save automatically.

Review before reading detector reports or the aggregate result record. The
sheet contains no detector hints or statuses, but original production warnings
remain visible in the output. It is detector-masked, not fully blinded. The
assistant has not labelled the seven pages; all entries remain unreviewed.

After review, return the downloaded labels for scoring. The existing scorer
checks experiment identity and requires the exact frozen sample, including any
uncertain or unreviewed entries:

```bash
.venv/bin/python scripts/probe_missing_content.py score \
  "$HOME/scratch/pdf2md-scan-evaluation-20260922/human-review/report.json" \
  /path/to/downloaded/labels.json --budget 4 \
  --output "$HOME/scratch/pdf2md-scan-evaluation-20260922/scores-human.json"
```

Keep the fixed four-page strategy comparison descriptive. It is not corpus-wide
recall, candidate localization precision, or calibrated confidence. Apply the
protocol's rejection rules after labels arrive; even a clean small sample
supports further evaluation only.

## Reproduction and verification

The [result record](results/scan-evaluation-20260922.json) pins the full run,
sampling probabilities, reader counts, and unreviewed status. Large artifacts
remain under `~/scratch/pdf2md-scan-evaluation-20260922/`. To regenerate a
review package from the frozen reader reports, choose a new output directory:

```bash
scan_eval_root="$HOME/scratch/pdf2md-scan-evaluation-20260922"
.venv/bin/python scripts/prepare_scan_review.py \
  docs/results/scan-evaluation-20260922-sources.json \
  "$scan_eval_root/readers/fumigants" \
  "$scan_eval_root/readers/gallium" \
  "$scan_eval_root/readers/noble-gases" \
  --output "$scan_eval_root/human-review-new"
```

The harness reuses the existing sampler, review renderer, label format, and
scorer. It rejects changed source bundles, protocol, reader evidence, mixed
reader implementations, duplicate sources, and missing/duplicated page records.
Reader refusals stay in the sampling frame and are reported separately.

Verification: **909 tests passed, 35 skipped, 2 deselected**, including 12 new
review-preparation tests and the existing emission snapshot. Ruff and diff
whitespace checks pass. All seven HTML sections, blank labels, and local link
targets were checked. The first source page of each PDF was rendered and
visually inspected for suitability. Live HTML browser inspection was blocked
by the browser's local-file policy; no workaround was attempted.

Nothing in this change repairs extracted text, changes confidence scores, or
adds a parser. Existing older bundles have not been reconverted; their list
identifier repair remains a separate, source-specific task.
