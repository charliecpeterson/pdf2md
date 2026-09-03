# olmOCR-bench: what I expect, written before the score

Committed while the conversion was at 500 of 1,403, with no complete per-file
score in existence. An external benchmark is exactly where a bad number invites a
good excuse, so the excuses go first, in public, and get judged against the
result.

## What the run is

1,403 single-page PDFs, 7,010 tests, five classes: text present, header/footer
text absent, the relative order of two spans, table cell neighbours, math formula
layout. No figure or chart tests at all, so nothing this session spent its time
on is measured here.

pdf2md's default engine is Docling, reported at roughly 50 overall against
MinerU's ~73 and Marker 2's ~76. A run therefore mostly measures Docling. The
question it can answer that nothing else has is **how far the enrichment layer
moves that number** — ligature repair, font-decode refill, inline script
recovery, the glyph-verified table pass.

## Predictions

| class | tests | prediction | why |
|---|---|---|---|
| baseline | 1,403 | **>95%** | one test per page that some content is present; anything else is a plumbing failure, and at 135 files converted it was already passing on essentially all of them |
| present | 721 | tracks Docling | pdf2md emits the engine's text; the repairs help only where a font is broken |
| **absent** | 823 | **poor** | see below — a detection gap, not a design choice |
| order | 1,061 | tracks Docling | pdf2md *reports* a reading-order defect, it does not reorder. The check measured at 0.90 precision changes review output, not emission |
| table | 1,020 | tracks Docling, marginally better | the glyph grid is evidence written beside the table, never the emitted table. Only the ligature and refill repairs reach the cells a scorer reads |
| math | 3,385 | tracks Docling where formula enrichment ran | pdf2md emits Docling's LaTeX; its own contribution is to *flag* a suspect equation, not to improve it |

**The `absent` class is the interesting one, and I expect pdf2md to do badly at
it** — but not for the reason I first wrote down, and the correction matters more
than the prediction.

My first version said this was the accounting invariant costing points: every
detected block lands somewhere, so headers and footers are emitted like any other
content. **That is wrong.** `emit.py` has had `_BOILERPLATE = {PAGE_HEADER,
PAGE_FOOTER}` all along, and a block of either type returns
`(None, CoverageStatus.EMITTED, None)` — intentionally stripped from the Markdown
while still accounted for, and recorded with `intentional_omission: True`. The
invariant explicitly permits omission with a record. The design already does the
right thing.

The real reason is narrower and duller: **Docling never assigns those labels.**
Zero `page_header` or `page_footer` blocks across all 36 documents of the working
corpus, and converting four pages of the benchmark's own `headers_footers` subset
gives 40 paragraphs, 2 figures, 2 headings, 1 table — no furniture at all. The
machinery is correct and never fires.

So a poor `absent` score is an engine detection gap, not a design principle, and
it should not be defended as one.

## What would change my mind

- `absent` scoring well would mean I am wrong about how the tests match, and the
  header/footer emission is less visible to them than I think.
- `table` or `math` beating Docling's published rate by a wide margin would mean
  the enrichment layer does more than I credit it with, and is worth measuring
  per-repair rather than in aggregate.
- `baseline` below 95% is a bug in the harness, not a finding about quality.
