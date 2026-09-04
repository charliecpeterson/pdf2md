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

---

# Results

Scored 2026-09-03, twice, with identical output both times. Conversion: 1,403 of
1,403 pages at 5.8 s/pdf.

| class | tests | predicted | actual |
|---|---|---|---|
| baseline | 1,403 | >95% | **97.1%** |
| absent | 823 | poor | **89.6%** |
| table | 1,020 | tracks Docling, marginally better | 63.3% |
| order | 1,061 | tracks Docling | 52.7% |
| present | 721 | tracks Docling | 41.9% |
| math | 3,385 | tracks Docling | 20.8% |

## The prediction that failed

**`absent` was the one prediction with a stated mechanism, and it was wrong.**
The reasoning: Docling assigns `page_header`/`page_footer` to zero blocks, so
`emit.py`'s `_BOILERPLATE` strip never fires and furniture is emitted as prose.
That reasoning is still factually correct about the labels. It did not predict
the score, which came in at 89.6%.

So the error is in the step from "furniture is emitted" to "the `absent` tests
will catch it". Something about how those tests match makes the emitted
furniture mostly invisible to them, and until that is understood the case for
building header/footer detection is weaker than it looked, not stronger. The
"what would change my mind" section named this exact outcome. It happened.

## There is no overall score, and the cause is ours

The harness prints `FAILED (errors)` and zeroes every per-JSONL row.
`benchmark.py:360` guards the per-file tally behind `if not candidate_errors`,
so a single errored test suppresses the whole aggregation. The per-class
`test_type_breakdown` is accumulated separately and is unaffected, which is why
the table above stands.

All 8 errors were Playwright `Target crashed` on one file,
`arxiv_math/2503.04425_pg8`, and the cause was pdf2md's own output: a 5,564
character equation consisting of `\Gamma _ { \Gamma _ {` nested 492 deep, which
crashes Chromium's KaTeX renderer.

## Runaway formula repetition

Docling's formula model runs to a generation cap and pads the remainder with a
repeating unit. Measured over 2,766 equations in the working corpus and the
bench bundles, the tail-repetition populations are bimodal and separate cleanly:

| longest repeating tail | equations |
|---|---|
| under 100 chars | 2,681 |
| 100-199 chars | **0** |
| 200+ chars | 85 |

The longest tail on legitimate LaTeX is 75 characters (a fraction table, and
`f ( \mathbf r ^ { \prime } ) \, d \mathbf r ^ { \prime }`). Degeneration runs
from 257 to 7,013. `_MAX_TAIL_REPEAT = 200` sits in the empty band.

Two things this measurement corrected along the way. Raw repeat count does *not*
separate the populations -- 156 repeats of `\stackrel` looked like legitimate
chemical arrows and turned out to be `2 ^ { \stackrel { \stackrel { \dots } {
\otimes } } { \in } }` repeated verbatim. And the guard is a *trim*, not a
withhold: the degenerate tail follows a correct formula, so cutting it recovers
content rather than suppressing it.

Effect of `trim_runaway_repetition`, over both corpora: 85 of 2,766 equations
touched, 351,752 characters of padding removed, 71 recovering a real formula and
14 trimming to nothing usable (a bare `\begin{array} {`, which is the verdict
`_UNTERMINATED_ENVIRONMENT` already reaches about an unterminated spec). The
crashing equation trims to 150 characters and keeps its inequality intact.

## Final score, after both fixes

Reconverting the 38 affected pages (36 with a trimmable equation, 2 that had
failed on the JSONL bug) and re-scoring gives a clean run: **0 errors, overall
55.4% +/- 1.1%**.

| subset | pass rate |
|---|---|
| baseline | 97.2% (1355/1394) |
| headers_footers | 88.9% (676/760) |
| table_tests | 63.4% (648/1022) |
| multi_column | 63.0% (557/884) |
| long_tiny_text | 60.6% (268/442) |
| old_scans_math | 29.3% (134/458) |
| old_scans | 21.5% (113/526) |
| arxiv_math | 19.5% (571/2927) |

**The repetition guard did not improve the math score.** It went 20.8% -> 20.8%.
Reporting that plainly matters more than the fix looking good: what the guard
actually bought is the run completing at all (the renderer crash is gone),
351,752 characters of fabricated content removed from emitted output, and 71
real formulas recovered from under padding. None of that is worth anything to
these tests, because the degenerate tail follows the formula the tests look for.
`order` moved 52.7% -> 53.5% and `baseline` 97.1% -> 97.2%, both from the two
JSONL-fixed pages. Everything else is unchanged.

Two caveats on the headline. Docling's own reported ~50 is a different
pipeline at a different version, so 55.4% against it is indicative, not a
controlled comparison. And `old_scans` at 21.5% was run with neither
`--force-ocr` nor `--ocr-page-vlm`, so it measures the default path, not the
ceiling for scanned pages.

---

# Diagnosing the weak subsets

## `absent`: why the prediction failed

Checked every `absent` test against poppler as an independent reader. The
question was whether the furniture reaches our output at all.

| | tests | share |
|---|---|---|
| poppler reads it, we do not emit it | 451 | 59.9% |
| poppler cannot read it either | 176 | 23.4% |
| we emit it (the test fails) | 126 | 16.7% |

**pdf2md already drops most page furniture**, through a mechanism other than the
`_BOILERPLATE` label path: Docling does not emit those marginal regions as
blocks at all, so there is nothing for the label to strip. Both facts behind the
prediction were true (the labels are never assigned; the strip never fires); the
step that was wrong was concluding the text therefore arrives as prose.

**This removes the case for building header/footer detection as a removal
feature.** Six in ten are already gone and two in ten were never extractable.
What remains is 126 tests of page numbers, dates and URLs (`'2'`,
`'21 April 2021'`, `'www.bulletphysics.org'`) -- a narrower and much cheaper
problem than the one predicted, and worth confirming is even worth solving.

## `math`: the delimiter, not the transcription

`MathTest.run` pulls candidate equations by delimiter regex -- `\(...\)`,
`\[...\]`, `$$...$$`, `$...$` -- then renders each with KaTeX and compares
images. Two consequences, one anticipated and one not.

Anticipated: an equation pdf2md does not wrap in a delimiter is invisible, and
443 of 3,385 math tests (13.1%) sit on pages emitting no delimited equation at
all. 99 of those 108 pages emit prose only -- Docling did not detect the
equations -- and 9 emit a crop with no LaTeX. But that is only ~17% of the math
failures, so it is not the main story.

Not anticipated, and the larger finding: **pdf2md has no inline-math emission.**
Across 1,403 candidates, 484 carry `$$` display math and 709 carry HTML
`<sub>`/`<sup>`, while just 19 carry inline `$...$` (incidental dollar signs).
Inline mathematics is recovered by `scripts.py` from glyph geometry and rendered
as HTML scripts, which is legitimate Markdown and completely invisible to any
consumer that looks for LaTeX delimiters. Every inline-math test is therefore
unwinnable by construction.

That is a design decision surfacing as a score, not a transcription defect. It
also generalizes past this benchmark: any downstream consumer expecting LaTeX
sees none of our inline math.

Note the hypothesis this replaced. The guess was that per-glyph spacing
(`E _ { n }`) would break a string comparison. It would not -- the comparison is
a rendered-image comparison, and KaTeX ignores whitespace. Checking the harness
source rather than reasoning from the output is what caught it.

## `old_scans`: `--force-ocr` is a no-op here

Reconverted all 98 `old_scans` pages with `--force-ocr` into a second candidate
directory and scored both. Every subset came back identical to the digit,
`old_scans` included: 21.5% (113/526).

Identical output is the signature of a flag that never took effect, so that was
checked before drawing any conclusion. It did take effect: `config.force_ocr`
reaches the adapter (`engines/docling.py:218`), and 98 fresh bundles were
written. All 98 candidates are nonetheless byte-identical.

The reason is structural. Every one of these pages has **zero characters of text
layer** -- they are pure scans. Docling already OCRs a page with no layer, and
`force_full_page_ocr` only forces OCR where a layer exists, so on this subset
both paths run the same OCR and produce the same bytes. `--force-ocr` cannot
help a document that was never going to use its text layer.

So 21.5% is not a misconfiguration, it is the quality ceiling of the default OCR
on these scans. The lever is a different reader, not a different flag -- and this
project has already measured that gap once, on a 1972 scanned data table where
Docling recovered 21% of the printed grid against MinerU's 99%. That 21% and this
21.5% are the same ceiling showing up twice. Neither MinerU nor a vision endpoint
for `--ocr-page-vlm` is installed on the machine that ran this, so the comparison
is set up but not yet run.

## `math`: inline vs display, measured

Ran all 3,385 math tests individually through olmOCR's own `MathTest` rather
than a reimplementation, so the counts reconcile with the score: 705 pass,
2,680 fail, 20.8% -- the reported figure exactly.

Each failure is then classified by which block type carries the reference's
symbols, using the bundle's typed blocks. Matching is on an alphanumeric
skeleton with LaTeX commands stripped, so `{\mathcal{V}}(\psi_m)` and a prose
`V(psi m)` both reduce to `Vm`. A reference whose skeleton is under 4 characters
cannot be told from ordinary prose and abstains.

| match threshold | nowhere | prose (inline) | equation block | abstain |
|---|---|---|---|---|
| exact substring | 1,323 (49%) | 549 (20%) | 229 (9%) | 579 (22%) |
| >= 0.9 coverage | 333 (12%) | **1,254 (47%)** | 514 (19%) | 579 (22%) |
| >= 0.75 | 124 (5%) | 1,082 (40%) | 895 (33%) | 579 (22%) |
| >= 0.6 | 50 (2%) | 910 (34%) | 1,141 (43%) | 579 (22%) |

**The exact-match reading was an artifact and had to be thrown out.** At exact
substring, 1,323 failures look like content the document does not contain at
all, which would point the blame at Docling's detection. Relaxing to 90%
character coverage collapses that to 333: the mathematics *is* in the document,
transcribed a character or two differently. Reporting the strict number alone
would have sent the next piece of work at the wrong target.

So the honest figure for what inline emission could reach is a **range, 549 to
1,254**, not a point. Three caveats travel with it:

- 579 failures (22%) abstain and are unmeasured in either direction.
- The prose bucket *shrinks* at looser thresholds only because references begin
  matching equation blocks too and the classifier prefers that label. That is
  reclassification, not disappearance.
- A skeleton appearing in a prose block is evidence the expression is there, not
  proof it is inline mathematics.

Upper bound if every inline case converted: math 20.8% -> ~58%. That assumes
each recovered expression then renders and matches, which some will not, so it
is a ceiling and not a forecast.

## `present`: the OCR ceiling, and the invariant holds

`present` tests exist in only two subsets, both OCR-hard, which is most of the
explanation before any measurement: `long_tiny_text` (442) and `old_scans` (279).
Run per-test with olmOCR's own class, the split reconciles with the score exactly:

| subset | pass rate |
|---|---|
| long_tiny_text | 268/442 (60.6%) |
| old_scans | 34/279 (**12.2%**) |
| overall | 302/721 (41.9%) |

Poppler then adjudicates the 419 failures, refusing where there is no text layer
to read, because on a scan its silence is not evidence:

| | tests | share |
|---|---|---|
| no text layer, poppler cannot judge | 348 | 83.1% |
| not in the layer either (OCR-only text) | 42 | 10.0% |
| **poppler has it, we do not** | **21** | **5.0%** |
| present in ours, test still failed | 8 | 1.9% |

So 41.9% is the OCR ceiling showing up a third time, not a dropping defect.

**All 21 genuine losses were never detected as a block.** Not one was detected
and then lost, across 14 PDFs. The accounting invariant holds perfectly over the
whole benchmark: every block pdf2md detected reached the output.

### The boundary that finding draws

The invariant guarantees that every *detected* block lands somewhere. It cannot
see content the engine never proposed, and that is where all 21 losses live. The
guarantee is airtight within its scope, and its scope begins after the engine.

One page makes this concrete. `headers_footers/b4c3c4ac...page_3` produced the
run's only empty candidate: 55 characters of text layer, Docling detected **zero
blocks**, and the coverage report reads `total_blocks: 0` -- which satisfies
"every block accounted for" vacuously. Here the outcome is defensible, since the
page holds only a title, a collection number and a page number, all furniture.
But nothing in the bundle would have said otherwise if it had held a paragraph.
It is 1 of 1,403, so this is a boundary worth knowing rather than a defect worth
fixing today.

## MinerU on the scanned subsets, measured

MinerU 3.4.5 installed in its own environment (it is deliberately not a project
dependency) and both scanned subsets reconverted into a separate candidate
directory -- 98 `old_scans` pages and 36 `old_scans_math` -- so the remaining
1,269 candidates stay byte-identical and the comparison isolates the engine.
Verified before scoring: 0 failures, all 134 candidates differ from the Docling
ones, and provenance records `mineru 3.4.5`. The
`--force-ocr` run earlier produced byte-identical output, so "did the flag
actually change anything" is now a standing check rather than an assumption.

| | Docling | MinerU | change |
|---|---|---|---|
| **old_scans_math** | 29.3% (134/458) | **64.8% (297/458)** | **+35.5** |
| **old_scans** | 21.5% (113/526) | **36.3% (191/526)** | **+14.8** |
| math (class) | 20.8% | 25.6% | +4.8 |
| present (class) | 41.9% | 48.4% | +6.5 |
| order (class) | 53.5% | 56.6% | +3.1 |
| baseline | 97.2% | 97.8% | +0.6 |
| absent (class) | 89.6% | 89.4% | -0.2 |
| **overall** | **55.4%** | **61.8%** | **+6.4** |

Only the two scanned subsets were reconverted, so every other subset is
unchanged by construction and the class-level moves are their contribution.

**The two scanned subsets move very differently, and that is the finding.**
Scanned mathematics gains 35.5 points; scanned prose gains 14.8. The note
carried forward from Phase 6/7 has Docling at 21% against MinerU's 99% on a 1972
scanned numeric data table, and the first read here -- taken from `old_scans`
alone -- was that the 99% does not transfer. With both subsets measured that is
too flat. MinerU's advantage is largest on scanned *technical* content, which is
exactly the kind of document the original measurement used, and roughly half as
large on ordinary scanned prose. The prior generalizes along the axis it was
established on, not to scans in general.

Cost: 60.6 s/pdf against Docling's 5.8 s/pdf on an RTX 4090, a little over ten
times slower. That is the trade for +6.4 points overall and +35.5 on scanned
mathematics.

`absent` moving down 0.2 is consistent with MinerU emitting more page furniture
than Docling, which is the same behaviour that helps it elsewhere.
