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
| **absent** | 823 | **poor, and by design** | see below |
| order | 1,061 | tracks Docling | pdf2md *reports* a reading-order defect, it does not reorder. The check measured at 0.90 precision changes review output, not emission |
| table | 1,020 | tracks Docling, marginally better | the glyph grid is evidence written beside the table, never the emitted table. Only the ligature and refill repairs reach the cells a scorer reads |
| math | 3,385 | tracks Docling where formula enrichment ran | pdf2md emits Docling's LaTeX; its own contribution is to *flag* a suspect equation, not to improve it |

**The `absent` class is the interesting one, and I expect pdf2md to do badly at
it.** Those tests check that running heads, page numbers and footers are *not* in
the output. pdf2md's foundational rule is the opposite: every detected block
lands somewhere, and `CoverageReport.accounted_for` is the check that it did.
Headers and footers are emitted as blocks like any other content. That is not an
oversight the benchmark has caught, it is the accounting invariant doing exactly
what it says, and it costs real points here.

If that reading is right, the honest summary is that olmOCR-bench and pdf2md
disagree about what a converter is for — one wants a clean reading copy, the
other wants a complete audited one — and the score should be reported with that
stated rather than folded into an average.

## What would change my mind

- `absent` scoring well would mean I am wrong about how the tests match, and the
  header/footer emission is less visible to them than I think.
- `table` or `math` beating Docling's published rate by a wide margin would mean
  the enrichment layer does more than I credit it with, and is worth measuring
  per-repair rather than in aggregate.
- `baseline` below 95% is a bug in the harness, not a finding about quality.
