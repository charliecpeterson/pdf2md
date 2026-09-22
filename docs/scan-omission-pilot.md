# Scanned-text omission experiment

This is a read-only development experiment on the known Dibeler scan, not a
production confidence check or an untouched evaluation set.

## Rules fixed before the second-reader run

The existing five-page bundle is immutable. The three source-reviewed omissions
are footnote 2 on physical page 1, the computed/observed hydride comparison on
page 3, and the carbon-12/plant-material clause on page 4. Their text is already
absent from saved engine state, before pdf2md enrichment and Markdown emission.

Render all five source pages at 300 dpi. Read them locally with Tesseract,
English, automatic page segmentation (`--psm 3`), keeping raw word TSV and page
images. Pin source, bundle, reader executable, trained-data, implementation,
and policy hashes. Record OCR failures and unsupported rotations explicitly.

Compare each reader line against emitted text blocks intersecting its source
region, including all recorded spans. Normalize Unicode, case, whitespace,
punctuation, and script markup for matching. Use minimum Levenshtein distance
to a substring, divided by source-line length; keep disagreements above 20%
for lines with at least 20 alphanumeric characters and mean reader confidence
of at least 70. These are starting thresholds, not calibrated confidence.
Short and low-confidence lines remain recorded as unassessed.

A large paragraph box does not establish text coverage. Explicit content crops
may represent a line only when they cover all its word boxes; a page-evidence
image does not. Declared page headers/footers are intentional furniture, not
body omissions. Retain these exclusions and their block IDs in the report.
Do not replace or repair any extracted text.

Inspect every candidate crop against the source page and full output. Label
meaningful omissions separately from OCR disagreement and page furniture.
Recheck the three known omissions even if the reader does not flag them.
These labels are assistant-reviewed, not independent human ground truth.

Negative controls must cover retained lines inside a paragraph, missing lines
inside the same box, repeated text in another column, crop-authoritative tables,
partial crops, general page images, whitespace/word-segmentation changes, and
low-quality or absent reader output. Synthetic controls are separate from
naturally occurring omissions.

This experiment can establish whether a local second reader exposes the known
losses at a tolerable review cost. It cannot establish recall on content both
readers miss. No production promotion without untouched multi-document sources,
independent labels, and a review budget fixed before that evaluation.

## Result: useful review hints, no production promotion

On 2026-09-22, the probe surfaced all three known missing passages in the
five-page Dibeler scan, plus two first-page journal-banner lines. All five
candidate crops were checked against the source and full Markdown. The two
banner lines are furniture under the existing rubric, so they are false alarms
for meaningful body omissions. No thresholds were adjusted after this run.

| Physical page | Source content | Review result |
|---|---|---|
| 1 | Journal name and issue/date banner, two lines | Furniture, not a body omission |
| 1 | Footnote 2 explaining bracketed references | Missing from output |
| 3 | Computed/observed Sn-120 hydride values, 11.20 and 11.21 | Missing from the comparison paragraph |
| 4 | Carbon-12 concentrated in plant material | Missing from output |

The result is **three known omissions surfaced among five candidates**, not
a general precision or recall estimate. This document was selected because
those losses were already known. Labels are assistant source-review, not
independent human adjudication. The
[result record](results/scan-omissions-20260922.json) stores every candidate's
page, region, reader text, distance, source-crop hash, and review evidence.

The reader returned 518 lines: 391 agreements, 5 disagreements, 30 short lines,
6 low-confidence lines, and 86 represented by content crops. Reader agreement
does not establish completeness; the 36 short/low-confidence lines remain
unassessed. Content-crop coverage establishes a visual representation, not
correct OCR. All five pages completed without reader failures.

### Where the loss occurs

A separate Docling 2.108.0 trace used forced OCR, MPS, formula enrichment off,
and `generate_parsed_pages=True` to retain page OCR cells. The trace contains
929 cells across five pages (119, 340, 284, 137, 49). The missing passages are
already absent from these cells and from the raw Docling document, as well as
the original bundle's saved engine state. That places the loss at or before
retained OCR cells, not in pdf2md's Markdown emitter. It does not distinguish
OCR detection failure from recognition failure.

Tesseract 5.5.0 exposes the three gaps but misreads the footnote identifier and
chemical isotope notation. Its text is a review hint, not a safe replacement.
The probe compares local emitted provenance text; source/output review checks
the actual Markdown and content fallbacks before labelling a loss.

### Reproduce and inspect

Use the existing project environment and a local Tesseract installation with
English trained data. No new Python dependencies or parser are required.
The CLI arguments follow the installed reader's `tesseract --help-extra` and
`tesseract --list-langs` output. On this Mac, trained data is available under
`/opt/homebrew/share/tessdata`.

```bash
.venv/bin/python scripts/probe_scan_omissions.py /path/to/bundle/v1 \
  --output "$HOME/scratch/scan-omission-review" \
  --tessdata-dir /opt/homebrew/share/tessdata
```

Use a new output directory outside the source bundle's document directory.
Open its `review.html` for candidate crops, full source pages, local output
text, and a full-Markdown link. `report.json` retains every reader line and
exclusion; `manifest.json` pins the input bundle, reader, trained data, policy,
and implementation. The script never edits the conversion bundle.

The measured files remain in `~/scratch/pdf2md-scan-omissions-20260922/`:
`docling-raw.json`, `docling-cells.json`, and `reader-v2/`. The earlier
`reader-v1/` run preceded the review-sheet addition. Both runs have identical
policy, all five raw TSV hashes, and line decisions. All three original
evaluation bundles were rehashed and remain unchanged. The result record pins
both runs and the protocol before this results section was appended.

Verification: **897 tests passed, 35 skipped, 2 deselected**, including
26 probe tests and the existing emission snapshot. Changed Python files pass
Ruff. Synthetic controls exercise omitted/retained text, another column,
complete/partial crops, page-evidence images, normalization, unlocated blocks,
invalid reader data, timeout, reader failure, empty output, rotation refusal,
source/bundle immutability, and review-crop generation.

## Next step

Keep this out of production confidence scoring and automatic repair. Before
promotion, freeze an untouched multi-document scan set, a review budget, and
acceptance criteria. Independently label both flagged and unflagged pages.
Do not suppress the observed banner false alarms by tuning on this document
and then call it held-out performance.

The probe cannot detect content both readers miss, and short losses can stay
below the line-distance threshold. Repeated text inside a block may mask a
missing occurrence. The current reader is English-only and explicitly refuses
rotated pages; neither limitation is a clean-page verdict.
