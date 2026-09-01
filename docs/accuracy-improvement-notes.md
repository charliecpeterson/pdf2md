# Accuracy improvement notes

Brainstorm notes from 2026-08-22 on where conversion accuracy can still improve,
focused on tables, charts, figures, equations, and structural metadata. These are
candidate work items, experiment records, and implementation notes rather than a
commitment to build every item. Stable product decisions are summarized in the
README. External claims are cited so they can be re-checked before being trusted.

## Where the measured gaps are

Phase 6/7 made scanned numeric tables very solid. The project's own diagnostics
point at three weaker areas:

1. **Born-digital tables.** Both engines match only 13/41 numeric tables exactly;
   an oracle over three engines reaches only 15 (`docs/bakeoff-results.md`,
   `pdf-parse-bench` diagnostic). Almost none of the Phase 6 machinery applies,
   because it assumes scanned input.
2. **Raster charts.** Vector fixtures pass 5/5 deterministically; the raster/VLM
   path (`calibrate.py` gate + `vlm_digitize`) is the least verified element class.
3. **Engine drift.** Mid-2026 comparisons put Docling at 50.3 on olmOCR-Bench
   (~64 restricted to born-digital PDFs) while MinerU's VLM backend (~93-95),
   PaddleOCR-VL 1.6 (~94-96 self-reported), and dots.ocr (~90.8) sit far above on
   OmniDocBench v1.6. The engine bake-off conclusions were measured against older
   releases.

## Idea 1: Rebuild born-digital tables from glyphs (highest value)

Status: first measured pass 2026-08-22 (`src/pdf2md/table_rebuild.py`,
`scripts/eval_table_rebuild.py`), diff-only as planned — nothing wired into
production. Scored against the 77 source-checked cells already pinned in
`tests/glyph_table_labels.json` (two born-digital documents, five tables),
anchoring each label by its engine cell's bbox center.

Results:

- Content recall 77/77: every labelled value appears in the rebuilt grid
  (41 positional exact, 36 present-but-lane-drifted, 0 missing).
- Positional exactness 41/77 overall, bimodal by table: 3 of 5 tables are
  perfect (including Attention's multi-row variants table); the other two
  degrade only in *lane* resolution, never content.
- The degrading cases are genuine typographic ambiguity, now documented: one
  journal spaces numbers internally in two ways (`-2 846` thousands thin-space,
  `1. 984` post-decimal gap) and both corridors repeat at identical x in every
  row, so they are indistinguishable from column gutters by any geometric rule;
  Attention Table 2's right-aligned sparse sub-columns have no zero-crossing
  boundary at all between EN-DE and EN-FR.

Design conclusions for the production shape:

1. Separator criterion settled empirically: a lane boundary is a whitespace
   corridor no glyph crosses in any row (`zero-crossing`), not a width
   threshold — width heuristics failed on tight numeric tables before this.
2. Fully independent structure is the wrong goal for verification. The hybrid
   is stronger and matches the scanned pipeline's philosophy (engine provides
   structure, glyphs provide truth): read the glyphs inside each engine cell
   bbox and diff against engine text per cell — no lane resolution needed —
   plus an ink-outside-all-cells check for dropped rows/columns.
3. Independent rebuild remains valuable where geometry is unambiguous (3/5
   tables perfect) and as the spanning-cell evidence source; keep it emitted
   beside the hybrid verdicts.

Conclusion 3 shipped 2026-08-31 in a form the lane-resolution finding argues
for: `table_rebuild.glyph_grid` reads the region with *measured rows and the
engine's columns*, which sidesteps lane ambiguity entirely — the two failing
tables degraded only in lane resolution, never content, and lanes are the one
thing the engine gets right. It is written as `data/tables/<block>.glyph.md`
beside the engine's grid, never as the emitted table.

Engine choice on a scan carrying someone else's OCR (2026-08-31, all 99 pages
of Atomic Data 4, 301-399, S0092640X72800081, `--no-formula --force`):

Ground truth without labels: every atom in that paper is tabulated on the same
radius grid, so the grid the two engines between them establish is the printed
one. It came out at 97 values and matches the page exactly. Scoring each engine
against the union means neither is judged by its own self-consistency -- an
engine that misreads a value consistently shows up as missing it, not as
redefining the grid.

| | tables | pages with a grid | mean grid recovery | >95% recovered | value tokens | malformed |
|---|---|---|---|---|---|---|
| MinerU | 145 | 69 | 99% | 67 | 73,342 | 0.6% |
| Docling on the embedded layer | 82 | 68 | 21% | 0 | 70,583 | 22.9% |

Docling's failures are the 1972 OCR showing through unchanged: `0,0002`,
`O.O001`, `0.000[`. MinerU's 450 are almost all header strings the token filter
cannot classify (`ZETA(4D)`, `1/R**3`), not misread values. The row/grid audit
reaches the same verdict by a different route: 1 of MinerU's 145 tables carries
a structural finding against 79 of Docling's 82.

Caveat on what was measured: the radius column is the row *label*, chosen
because it is the one column with checkable structure. There is no independent
ground truth here for the wavefunction values themselves -- only that they are
well-formed and that the two engines' structures now agree.

Engine-differential concordance (2026-08-31, `scripts/eval_engine_table_agreement.py`
over the ten-document corpus converted by both Docling and MinerU, 61 matched
tables from 9 documents):

|                | audit flagged | audit silent |
|----------------|---------------|--------------|
| engines differ | 8             | 7            |
| engines agree  | 0             | 44           |

Every table the audit flags is one the two parsers read differently, and it is
silent on all 44 they read identically. Engine agreement is not ground truth --
both can be wrong together, and a difference can be one parser's OCR rather than
the other's structure -- so this is concordance, not precision. It is still the
stronger measurement: the labelled set is thirteen tables chosen by hand, this is
sixty-one nobody chose. Recall against disagreement is 8/15; the audit refuses
far more than it reports, which is the intended trade.

Reading order got the same treatment (`scripts/eval_engine_order_agreement.py`,
blocks matched across engines by box overlap, 132 pages): 7 of the 8 pages the
two engines order differently are flagged, and 1 of the 123 they agree on. That
check previously rested on two pages verified by hand.

The shipped `<block>.glyph.md` was scored the same way for the first time: judged
against MinerU it is closer than the engine's grid on 6 tables, further on 8, and
level on 30 of 44. It does not systematically beat the engine, so it does not
earn a promotion — but the wins are large where they happen (+0.84, +0.94, +1.0),
which is the badly-extracted table it exists for. Keep it as a fallback, not as a
general second opinion.

Four false-positive classes were found and closed by this measurement, all of
them invisible to the labelled set because every labelled table is a dense
numeric grid: `merged_rows` counting a wrapped cell's continuation lines as
collapsed rows (13 findings to 2), `shifted_values` reading a multi-line header's
label fragments as stray values, and a grid covering 9 per cent of its own region
being compared against itself and reported as agreement, and `merged_cells`
being unable to see a table flattened to a single data row (no column profile to
compare against, and the row-band check suppressed by the wrapped-cell guard).

Note on the corpus: it is no longer blind. This session inspected individual
tables, pages, and blocks in it to diagnose those classes. A future unseen-corpus
claim needs a fresh selection.

Measured on the frozen ten-document unseen corpus (2026-08-31, `--no-formula
--force`): 19 of 74 tables carry a structural finding, 16 of ~200 pages a
reading-order finding, and 13 a split-line note. The ordinal oracle fired on
none of them — arXiv bibliographies arrive as a single block, so the only
ordinal-bearing blocks are section headings, one per page, below the five a page
needs to be called a list. It is validated on journal-style bibliographies
(dolg-ecp p9, where it and the geometry independently agree on 16 misplaced
blocks) and has no coverage on the arXiv style. A document-level variant keyed
on numbered section headings would cover that and does not exist.

The same run turned up a pre-existing hard failure worth recording: three of the
ten documents aborted outright because one table's caption plus column-header row
exceeded `passage_max_tokens`, which `_split_table` treated as unrecoverable. It
now degrades to unheadered row passages with a warning, and the corpus converts
10/10.

Conclusion 2's per-cell check has a structural blind spot the same pass closed:
a row the engine never created has no cell to verify, so it passes silently.
The uncovered-ink sweep does not catch it either, because a neighbouring row's
padded cell box usually contains the dropped row's glyphs. `table_audit`
projects the region's ink into rows (`row_bands`, the lane projection's
transpose) and requires every value a printed row spells to reach a cell of the
grid rows covering that band. On ct6b00664 Table 1 — 60 printed data rows, 57
in the grid — the per-cell check reported 19 mismatches and no loss; the row
accounting names `Ag2f2 120.0 3 1.34` as reaching no cell at all.

The hybrid verification (conclusion 2) is implemented: `check_table_cells`
runs during enrichment on every born-digital table and records verdict counts,
uncovered-ink strays, and bounded mismatch samples on
`TableData.cell_glyph_check`, aggregated into profile.json's
`table_cell_glyph_check` and the README. Verdict tiers: exact, spacing_only
(same content, whitespace drift — recorded, not flagged), mismatch,
glyphs_without_engine / engine_without_glyphs / empty_agree / no_bbox.
Scored against all 77 source-checked label cells: zero false flags, zero
missed engine errors on this corpus; end-to-end Attention conversion verifies
222 cells as exact=201, spacing_only=21 with no uncovered ink. Bugs the
measurement caught on the way: glyph readings need visual-line grouping plus
word-gap space insertion (PDFs don't draw space glyphs), and cell boxes carry
Docling's y0>y1 orientation — unnormalized containment never fired and read
the whole grid as stray ink.

Blind-corpus measurement (2026-08-22, the frozen ten-document unseen corpus,
`~/scratch/pdf2md-blind-v1`, converted `--no-formula --force`; aggregate in
`docs/table-glyph-verify-blind.json`): 5,572 cells across 86 tables verified.
exact 4,127 (74%), spacing_only 375 (7%), mismatch 571 (10%),
engine_without_glyphs 688 (12%). Five documents are perfectly clean under the
verifier; the signal mass concentrates in four, and triage of every flag
family found:

- **True catches.** 1911.10683: engine reads checkmark/cross dingbats as
  digits (`✓✓✓` -> `333`, `✗✗✗` -> `777`) while the glyph layer decodes them
  correctly; 2005.12872: engine `✓` where the layer reads `X`; 2308.12950:
  a four-row spanning cell whose Docling bbox stops one value short
  (`20.4%` absent from the box) — exactly the dropped-content class the pass
  exists for.
- **Image-backed tables surfaced.** All 613+75 `engine_without_glyphs` cells
  (1911 pages 5/7/9, 2408) sit on raster tables Docling's TableFormer
  vision-read: engine text with no glyph backing anywhere in the region.
  This is the verifier marking vision-generated table text as unverifiable —
  those tables should be routed like scans (crop authoritative).
- **Known noise class.** Dense-text tables (2308 p6/p8, 2210 p18, 2003 p25,
  2408 p8): Docling's coarse cell boxes genuinely overlap neighbouring
  content, so edge fragments (`.,,` prefixes, row numbers under row labels)
  enter otherwise-correct readings. Reading is already taken from a box
  inset by 0.5pt — the overlap is inside the raw box, so no windowing fixes
  it; it needs nearest-box assignment or engine-grid refinement if this
  class ever matters. Letter-spaced tables (2210) land in spacing_only via
  the squash tier, correctly unflagged.

Net read: the verifier's flags are dominated by real engine defects and
unverifiable vision-read tables on these ten papers; the residual false-flag
noise is bounded, understood, and confined to coarse-box dense-text tables.

Routing implemented same day (`table_rebuild.glyph_unbacked_tables` +
`pipeline._table_crops`): a table whose text-bearing cells are majority
`engine_without_glyphs` is treated like a scanned table — source crop rendered
and marked authoritative in the markdown ("table read from the image by the
engine"), manifest `authority: image`, profile counts it as a candidate not
verified, review flag added. On the blind corpus this reclassified all 16
raster-read tables (15 on 1911.10683, 1 on 2408.09869): corpus totals moved
from 4 docs / 41 review markers to 5 docs / 58, with every structural claim
unchanged (10/10 accounted_for, structurally_complete, content_present). The
frozen blind baseline in `tests/blind_pdf_corpus.json` was updated with a
`baseline_history` entry documenting the delta rather than silently drifted;
`eval_blind_corpus --check --strict` passes against the new baseline.

For a born-digital PDF, glyph coordinates are ground truth for table structure,
the same way vector paths are ground truth for charts in `digitize.py`. All the
machinery already exists: `PageChars` geometry (`scripts.py`, `enrich.py`),
rule-line access via pdfium page objects, and column-lane clustering built for
the scanned projection locators.

A `tables_rebuild` pass would:

- Cluster glyph x-centers into column lanes and y-bands into rows.
  Bordered tables: derive lanes from drawn rules first. Borderless: whitespace
  consensus, mirroring the repeated-row consensus locator from Phase 7 exp 6.
- Fill cells from glyphs inside each lane intersection.
- Diff against the engine's table cell-by-cell. Agreements raise confidence;
  disagreements become review evidence under the existing accounting discipline.

Supporting evidence this works: Phase 7 experiment 6 found 4,184/4,184
born-digital glyph centers agreeing with consensus lanes. That machinery was
used as a geometry reference but never pointed back at born-digital tables
themselves.

Side benefits:

- Spanning cells stop depending on the engine's structure guess: emit HTML with
  rowspan/colspan when glyph reconstruction shows spans, GFM otherwise.
- Attach captions, footnotes, and unit rows to the table object instead of
  letting them float as prose (mirrors `_promote_figure_captions`).
- Emit `data/table-N.csv` for every structured table so tables get the same
  lossless sidecar treatment as figures.

## Idea 2: Consensus sampling for raster charts

Status: implemented 2026-08-22 (`digitize.vlm_digitize_consensus`, opt-in
`--digitize-consensus N` on top of `--digitize-vlm`). Each extra vote samples at
`digitize_consensus_temperature` with its own vision-cache key (vote 0 keeps the
byte-identical default-temperature read); draws aggregate per-bin by median over
ONE shared x-domain — the calibrated axis range clamped to the reads'
intersection. The across-draw mean per-bin dispersion rides on `Digitization`
(`consensus_votes`, `dispersion`) and scales confidence
(`conf * (1 - min(0.9, dispersion*3))`); convergence early-stops at three agreeing
draws; scatter-like (non-x-functional) or non-aligning draw sets fall back to the
highest-confidence single read, flagged in the note. Unit-tested against a fake
describer (median recovery, early stop, scatter fallback, single-vote passthrough).

The eval_raster live half caught a real aggregation bug before it shipped:
resampling each draw over its own x-domain silently misaligned bins whenever two
reads covered different spans — the shared domain is the fix.

Live A/B against the synthetic harness is **inconclusive for a boring reason**:
glm-ocr:q8_0 returned nothing on most calls (and qwen3-vl:8b's empty-reply stall
is already documented), so single-read baselines and consensus reads never saw
the same inputs often enough to compare. The mechanism is measured by unit tests;
endpoint-quality A/B needs a reliable chart-capable model — rerun
`scripts/eval_raster.py <model> --consensus 3` versus plain when one is
available (candidates: qwen3-vl on the RTX box, or a non-reasoning VLM).

Original plan text:

Directly applicable 2026 result: [Self-Ensembling VLMs for Chart Data
Extraction](https://arxiv.org/abs/2605.27298). Sample the same crop N times,
align candidate tables, take per-cell medians, stop when aggregation converges,
and emit dispersion as the uncertainty signal.

This slots into the existing architecture: dispersion becomes another gate input
alongside `calibrate.py`'s pixel-agreement floor; per-cell disagreement marks
cells for review instead of trusting one greedy decode.

Related: ChartRecover ([Nature Commun Eng,
2026](https://www.nature.com/articles/s44172-026-00691-8)) shows tick-mark to
tick-value pairing beats value-only axis fitting. Could sharpen axis estimation
in `calibrate.py` on scans.

## Idea 3: Render-back verification for equations

Status: implemented 2026-08-22 (`confidence.py`, opt-in `--render-check`,
`eqrender` extra: matplotlib). Each image-backed equation's LaTeX is sanitized
(tags/labels/alignment amps stripped, `\text`→`\mathrm`, unicode math mapped,
line breaks joined), drawn with mathtext, and compared against its source crop
by soft-IoU of the stretched ink masks blended with aspect agreement. Verdict
tiers (`similar` >= 0.70 / `unclear` / `dissimilar` < 0.45, provisional) land on
`Block.extra.render_check`, aggregated into profile.json's
`equation_render_check` and the README.

Scope decision that makes it honest: the pass runs **only where the text layer
could not judge** — scanned pages (`ocr` flag) or equations `assess_equation`
left unjudged. On born-digital displays the text-layer check already answers,
and render-back there produced false `dissimilar` on all four correct formulas
(equation numbers and stacked lines distort stretched-mask IoU structurally).
Measured on a 3-page Slater scan under `--force-ocr`: the one OCR'd equation —
which lost its leading `\rho` to page OCR — flags `dissimilar` (0.129) beside
its authoritative crop, the intended review prompt.

Synthetic discrimination is wide: self-renders score 1.0; wrong-topology pairs
0.11-0.35. **Band calibration (2026-08-22) returned a decisive negative**:
against the frozen 12-equation corpus's real crops, GROUND-TRUTH LaTeX scores
0.113-0.373 — below any useful cut and fully overlapping deliberately corrupted
variants (0.114-0.397), with natural production non-exact candidates inside the
same range. Cross-font and cross-layout differences (equation numbers, stacked
displays, journal fonts vs mathtext) dominate dense-mask IoU; the earlier
self-render separation was an artifact of both sides sharing one renderer.
Consequences, encoded in `confidence.py`'s measured-limit note: render-back
verdicts rank layout agreement only — "dissimilar" on a real crop is NOT
evidence of a wrong equation, and no band may gate anything. The pass stays
opt-in, scan-scoped, review-ranking only. Making it a real verifier would need
a different comparison (component/structure matching rather than dense IoU);
harness and frozen report live in `scripts/eval_render_bands.py` +
`docs/render-band-calibration.json` so any successor starts from data.

Original plan text:

The text-layer cross-check (`confidence.py`) fails exactly when there is no text
layer. Complementary model-light check:

1. Render accepted LaTeX back to an image (KaTeX or matplotlib mathtext).
2. Compare ink layout against the equation crop (normalized correlation after
   deskew/scale).

A scrambled fraction or lost subscript changes rendered topology. This gives a
second independent acceptance signal for scanned equations and is deterministic
enough to fit the evidence-gate style.

Optional extension: a second math transcriber (UniMERNet or Texify) as a
selective reader, OCRFlux-style, if the equation corpus grows.

## Idea 4: Structural metadata via GROBID

Status: implemented 2026-08-22 (`grobid.py`, opt-in `--grobid-url`), validated
against a live service (docker `lfoppiano/grobid:0.8.1`) on the pinned
Attention paper. The client posts the PDF to `processHeaderDocument` +
`processReferences` (stdlib urllib, injectable transport for tests) with
`Accept: application/xml` — without it GROBID 0.8 content-negotiates to
BibTeX — parses both TEI documents, and merges fill-gaps-only into the
heuristic metadata: abstract, keywords, venue, structured authors with
affiliations, and all 40 reference strings land; raw TEI is written under
`data/` and pointed at from manifest.json and front-matter.

Measured caveats now encoded as behavior:

- GROBID's header model latches onto arXiv license boilerplate on
  1706.03762v7: it returns the license text as title (glued onto the real one)
  and the arXiv re-posting date (2023) as year. Merge therefore never
  overrides an existing heuristic value; it only fills fields heuristics lack.
- An institution can be parsed as a person ("Google Brain", forename/surname);
  authors whose name equals their own affiliation are dropped.
- An unreachable or failing service logs a warning and the conversion proceeds
  on heuristic metadata; `pdf2md doctor --probe-vlm` probes the endpoint when
  configured.

Original plan text:

[GROBID](https://grobid.readthedocs.io/en/latest/Introduction/) extracts
header metadata, parses reference strings (~0.87-0.90 F1), resolves citation
contexts, normalizes author/affiliation/date fields, consolidates DOIs via
biblio-glutton, and returns PDF coordinates for everything it finds.

`metadata.py` (embedded metadata + first-page heuristic) is the thinnest layer
in the bundle. GROBID runs locally as a service, so it fits the MinerU pattern:
external tool, adapter consumes structured output, pdf2md keeps provenance and
rendering.

This differs from deferred CrossRef enrichment: GROBID derives structure from the
document itself rather than reaching out to a registry.

## Idea 5: arXiv source recovery as verifier

When the PDF carries an arXiv ID (or resolvable DOI), fetching the actual LaTeX
source and converting via pandoc gives exact equations, real table environments,
and figure captions. Marker's evaluation methodology is built on exactly this
PDF-vs-LaTeX-source pairing.

Two modes:

- **Second opinion** keyed by arXiv ID: strongest possible verifier for the
  clean-paper archetype; disagreements become review evidence.
- **Primary with provenance flag**: near-lossless outright, with the bundle
  recording that content came from source rather than extraction.

## Idea 6: Keep pace without reopening settled questions

- **Scheduled gate reruns.** Project policy requires rerunning hash-pinned
  gates on parser/model releases. Concrete targets: MinerU 3.x VLM/hybrid
  backend, PaddleOCR-VL 1.6, and possibly dots.mocr as a *targeted region
  reader* (native bbox/category/text/table-HTML/LaTeX JSON suits the adapter
  seam; DeepSeek-OCR 2's markdown-embedded markers do not). This respects the
  "no new full parser without corpus advantage" rule; it is the selective-reader
  role already held by Tesseract and OCRFlux.
- **olmOCR-Bench unit tests.** Port a slice of its ~7k assertions (old scans,
  math, headers/footers, mixed columns) as fast tests. The pinned corpus is deep
  but narrow; assertion-style checks widen failure-mode coverage cheaply.

## Idea 7: Token-level consistency reporting (cheap, do early)

Extend the accounting invariant from block level to token level as a standard
profile field:

- Word recall vs. glyph layer per prose block.
- Numeric-token conservation per document (already computed ad hoc in the bench
  diagnostic: Docling 0.645, MinerU 0.822 recall).

Silent born-digital table mangling stops being something only discovered during
evaluations. Estimated effort: small.

Status: implemented 2026-08-22, informational only. Per-block word recall is
recorded during enrichment (`enrich.record_block_recall`, born-digital prose
blocks with a bbox) and aggregated into `DocumentProfile.glyph_recall_*`.
Whole-document numeric conservation runs after emit (`enrich.numeric_conservation`)
against the markdown just written and lands in `DocumentProfile.numeric_conservation`
(counts plus up to 12 missing-value examples). Both surface in profile.json and
the README coverage section; neither rewrites a value or gates a conversion.
Unavailable by design under `--force-ocr` (that layer is distrusted bad OCR) and
on fully scanned documents.

First measured run (hash-pinned `1706.03762v7.pdf`, Attention, born-digital,
default pipeline): word recall 5316/5456 = 97.4% over 152 blocks, 5 low-recall;
numeric conservation converged over three iterations from 79 missing values
(v4) to 51 (v5) to 41 (v6) after fixing three systematic comparison artifacts:
exponent digits glued flat in the layer (`1019` for 10^19; fixed by
`PageChars.text_scriptsplit`), typeset decimal spacing (`2 . 3`; fixed by
normalizing digit-spaced-dot on both sides), and dash-style drift in ranges
(`152–159` vs `152-159`; en dash now normalizes to hyphen).

The residual 41 decompose into named, expected families: numbers living only in
crop-backed equations (4/5 equations image-backed here: `-0.5`, `-1.5`,
`10000`), dropped running furniture (`31st Conference` footer, printed page
numbers), one small-font table header the script detector doesn't flag
(`params ×106` vs emitted `×10<sup>6</sup>`), an inline exponent drawn as
separate glyph groups (`ϵ = 10 −9`), and phantom tokens shed by dotted section
numbers (`3.2.2` also emits a `.2`). No evidence of real content loss on this
document; the report's job is to make that statement checkable per run.

## Constraints carried forward

These constraints came out of the measured experiments and remain project policy:

- No new full parser added on leaderboard delta alone.
- No blanket execution of every parser (oracle gain was 2/41 tables).
- No lowering of the 0.99 line-reader threshold without held-out calibration.
- No treating shared-crop-geometry OCR votes as independent verification.
- No automatic promotion of fixed-font glyph-atlas choices.

## Current status and next evidence

Ideas 2, 3, 4, and 7 are implemented. Idea 1 produced a production glyph-cell
verifier and image-authority routing; its fully independent grid remains
diagnostic. The next work, if its prerequisite evidence appears, is:

1. Run raster-chart single-read versus consensus A/B with a reliable chart-capable
   model before changing the current approximate status.
2. Prototype the arXiv-source verifier only on a hash-pinned corpus with recoverable
   sources, explicit network provenance, and a no-network fallback.
3. Rerun existing parser and OCR gates when a pinned release changes, and add an
   assertion-style PDF corpus where it covers failure modes the current sources do
   not.
4. Revisit independent table-grid reconstruction only if a labelled corpus shows a
   structural advantage over cell-level glyph verification.

## Open decisions

- Whether network-fetched arXiv source belongs inside pdf2md or in a separate
  verifier that consumes completed bundles.
- What held-out gate would permit independent glyph geometry to alter table
  structure rather than remain review evidence.
- Which chart-capable model and raster corpus are stable enough for the consensus
  A/B to answer a product question rather than measure endpoint failures.
