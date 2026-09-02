# pdf2md

Auditable PDF-to-Markdown bundle converter (library + CLI), using Docling by
default and MinerU for measured high-risk cases. It adds logical-section splitting,
bibliographic front-matter, figure crops, and a per-document coverage audit that
enforces "nothing silently dropped." The README is the user-facing tour; this
file is for working *on* the code.

This is a rebuild of an abandoned MCP server (the old `docsmcp`). `README.md` is
the source of truth for current product scope, design, methods, and user-facing
behavior. Candidate accuracy work and experiment status live in
`docs/accuracy-improvement-notes.md`; the completed 2026 quality, performance, and
ingestion workstream is recorded in `docs/quality-and-ingestion-plan.md`; the earlier
rationale and decision log remain in `docs/archive/PROJECT_PLAN.md`.

## Run and develop

```bash
uv sync
uv run pdf2md convert /path/to.pdf            # convert (see README for flags)
uv run python -c "from pdf2md.pipeline import convert_file; print(convert_file('x.pdf').coverage)"
uv run pytest                                 # fast unit/snapshot tests (no Docling)
uv run pytest -m integration                  # opt-in: runs real Docling (slow)
```

The fast test suite never invokes Docling or downloads models; it drives the
pipeline stages with synthetic `EngineResult`/`Document` fixtures. The
`integration` tests run real Docling and are skipped unless selected (and need
`PDF2MD_TEST_PDF` set to a real PDF).

## Module map

```
src/pdf2md/
  pipeline.py   convert_file / convert_dir: orchestrates parse, repair, render, emit,
                audit, and immutable bundle finalization.
  scan_ocr.py   whole-page VLM transcription for scanned pages, including cache reuse and
                visible failure markers.
  visual.py     figure crops, SVG export, labels, descriptions, and chart recovery.
  vision_cache.py document-level inference-cache persistence, integrity checks, and
                  exact lookup/hit/write accounting.
  schema.py     all dataclasses + enums (Document, Section, Block, BBox, TableData, RawTable/RawCell, FigureRef, Provenance, CoverageReport). FORMAT_VERSION lives here.
  cache.py      source SHA-256, readable document directories, run fingerprints,
                completed-version lookup, and version allocation.
  config.py     frozen Config dataclass loaded from TOML (no Pydantic).
  logging.py    NullHandler in the library; CLI installs the only handler.
  run_metrics.py sequential stage timings and work counts stored with provenance.
  cli.py        Typer surface (convert / enrich / coverage / compare-runs / list /
                review-tables / prune / version / doctor / models / line-reader).
  models.py     model warm-up and offline/reproducible local snapshots.

  engines/
    base.py     Engine Protocol + EngineResult (the swap seam; carries raw_tables for enrich).
    docling.py  the ONLY module that imports docling. PURE translation → schema (no
                pdfium, no verification); tables ship RawTable cells for enrich to rebuild.
    mineru.py   external-CLI adapter for scans and difficult tables/equations. Reads native
                middle JSON only; pdf2md re-renders crops and ignores MinerU chart tables.

  enrich.py     engine-agnostic verification (GlyphIndex + enrich_blocks/tables/figures):
                ligature/diacritic repair, inline scripts, equation text-layer cross-check, OCR
                detection, font-decode refill (garbage prose refilled from the pdfium glyph
                layer). Reads pypdfium2 glyph geometry; any engine inherits it. resegment_ocr_prose
                re-splits RapidOCR's run-together words in scanned prose (wordninja; OCR blocks only).
                Also the read-only token-level consistency signals: per-block word recall vs the
                glyph layer (record_recall, a pass after enrich_tables so a block that renders
                from cells is measured against its markup rather than its empty text; both sides
                get the f-ligature expansion, else the layer's `con`+`guration` reads as loss)
                and whole-document
                numeric conservation between the embedded layer and the emitted markdown
                (numeric_conservation; informational only — never rewrites a value).
  normalize.py  text cleanup (Greek glyph names, orphan combining marks, clean_reading) + vocab-
                validated ligature/diacritic word repair (religature, rejoin_split_word, vocabulary)
                + TeX f-ligature glyph expansion (expand_ligature_glyphs: pdfium's C0 control
                bytes \x1b-\x1f -> ff/fi/fl/ffi/ffl, \x02 soft hyphen -> join)
                + resegment_words (wordninja re-split of run-together OCR words, English-only).
  scripts.py    inline sub/superscript detector from glyph geometry (PageChars, apply_scripts).
                Also text_scriptsplit: whole-page reading with spaces at script-group boundaries,
                the numeric-conservation source side so glued layer exponents (`1019`) tokenize
                like typeset output (`10`, `19`).
  legibility.py symbol-font garbage detector (score_legibility/is_garbage): dingbat/PUA/glyph-name
                density. Gates the enrich refill and the emit `illegible` flag.
  preformat.py  console/ASCII-table detector (is_preformatted): banner/rule lines (+ pipe columns
                for tables). Routes code blocks, mislabelled console prose, and ASCII tables to
                fenced code-block emission with line structure preserved.
  confidence.py equation LaTeX vs text-layer cross-check scoring (assess_equation; RECOVER_BELOW, SCRAMBLED_ABOVE, HINT_MIN_CONF). Also render-back
                verification (--render-check, eqrender extra): draw an image-backed equation's LaTeX with mathtext and soft-IoU its stretched ink mask
                against the source crop — only where the text layer couldn't judge (scans/unjudged); evidence tiers on Block.extra.render_check.
  transcribe.py opt-in multi-pass: re-transcribe image-backed equation crops with local math-OCR (Surya). Transcriber seam + SuryaTranscriber.
  describe.py   opt-in (--describe): describe figure/table/equation crops with a vision model over an
                OpenAI-compatible API (ollama/vLLM/remote). Describer seam + OpenAIVisionDescriber.
  digitize.py   figure data recovery. VectorPathDigitizer reads born-digital chart data from the
                drawn vector paths (default on; near-lossless): lines, scatter (multi-series, split
                by marker style), bars on a common baseline (Digitization.kind), and MULTI-PANEL
                figures — every frame (rect path or assembled spines) that calibrates against its
                own neighboring ticks contributes series tagged in series_names; weak panels are
                skipped with a visible note. Handles figures embedded as form XObjects (LaTeX
                includegraphics) by composing the container transform chain. vlm_digitize (raster
                estimate, opt-in) with anchored prompts + malformed-reply salvage;
                vlm_digitize_consensus (--digitize-consensus): N samples aggregated per-bin by
                median over a shared x-domain, dispersion scaling confidence; scatter-like or
                non-aligning reads fall back to the best single read.
                vector_ocr_digitize (tier 1.5, model-free, default on): a journal that outlines
                figure fonts leaves vector curves but no tick text — curves stay exact vector
                paths, axes get OCR'd off the rendered crop (single-frame figures only).
  labels.py     a figure's printed text. figure_labels_* tiers: textlayer (born-digital, exact),
                ocr (scanned crop, model-free upright re-OCR, best of 4 rotations), figure_labels
                (--figure-labels vision read, consensus votes). extract_caption splits a 'Fig N.'
                caption out of that text. Split from digitize.py.
  calibrate.py  model-free raster-chart pre-scan (analyze_raster): orientation/deskew, axis
                calibration from pixels + OCR'd ticks, and an ambiguity measure that gates AND
                anchors vlm_digitize — a tangled scan (overlapping curves) emits a visible "not
                extracted" marker instead of an invented table; a calibrated one rides its measured
                axis ranges into the VLM prompt, and only an in-range, pixel-agreeing read clears
                the emit confidence floor. See docs/figure-to-text.md.
  structure.py  Section tree to file layout. Papers stay in one document; books split at
                top-level bookmarks and selectively expand Part-like chapter containers.
                It restores source-page order, supports a conservative chapter-heading
                fallback, and writes shallow root plus detailed local contents.
  chunks.py     section- and page-local retrieval chunks with source-page and asset pointers.
  bookmarks.py  read embedded PDF TOC via pypdfium2.
  outline.py    heading depth (from section numbering) + section kind.
  render.py     pypdfium2 bbox crops → assets/ (Y-flip, per-page geometry, full-page fallback).
                full_page() renders whole scanned pages as verification rasters. svg_crop()
                (--figure-svg) exports a born-digital figure region as lossless SVG via
                pdftocairo — through a temp cropbox'd one-page PDF, because pdftocairo's own
                crop flags are silently ignored for SVG output.
  emit.py       Section tree → .md files + YAML front-matter; sets coverage_status, collects flags.
  tables.py     GFM table render, HTML fallback for spanning cells.
  table_rebuild.py  born-digital glyph-truth for tables: grid rebuild from whitespace corridors
                (zero-crossing lanes) plus row_bands (the same projection over y, so a subscript
                stays on its baseline) and engine_lane_bounds. glyph_grid/grid_markdown read a
                region into the engine's columns with measured rows — written as <block>.glyph.md
                beside the engine's grid, never as the emitted table. check_table_cells is the
                per-engine-cell glyph verification (verdicts + uncovered-ink strays) recorded as
                read-only evidence on TableData.cell_glyph_check during enrich; spacing_only
                verdicts are not flagged.
  table_audit.py  the row- and grid-level failures check_table_cells cannot see, because a row the
                engine never created has no cell to verify. row_accounting projects the region's
                ink into rows and requires every value in a row band to reach a cell of the engine
                rows covering it (dropped rows, merged rows); grid_findings reads only the emitted
                cells (merged_cells, shifted_values, header_absorbed_data) and stands at medium
                until the accounting corroborates it. raster_row_findings covers the scanned case
                the glyph path cannot reach, off the table's own crop. Stored on
                TableData.grid_audit; becomes a CoverageFlag in emit.
  metadata.py   ranked local bibliographic evidence from embedded fields, front-page and
                repeated headings, running titles, early bookmarks, and meaningful filenames.
                Selected, alternate, penalized, and rejected candidates remain inspectable.
  grobid.py     optional GROBID enrichment (--grobid-url): header fields + every reference string
                parsed from TEI; fill-gaps-only merge (GROBID's header model can latch onto arXiv
                license boilerplate), raw TEI under data/, unreachable service degrades with a warning.
  reading_order.py  the one thing the rest of the verification layer is blind to by
                construction: word recall compares multisets and numeric conservation counts
                values, so a page whose columns the engine interleaves conserves everything and
                still reads as nonsense. Two mechanisms. page_findings reads geometry: columns
                come from where blocks' left edges cluster (a corridor search dies on an
                overhanging abstract), a block running into the next column separates the flow,
                and within a segment the printed order is column-major; blocks starting at no
                column start are set aside, not forced. ordinal_findings reads the document's own
                numbering: when a page's leading ordinals sort to an unbroken run, that run IS the
                printed order — no column model, no thresholds, and a page it convicts is high
                severity rather than medium. split_line_findings reports printed lines cut across
                several blocks, informational because the detection is exact but the judgement
                isn't (a masthead's `Received:` / date is also one line in two blocks). All three
                report the minimum number of blocks whose removal restores order, never the count
                of inverted pairs.
  coverage.py   tally block dispositions into a CoverageReport.
  quality.py    independent evidence-backed scorecard dimensions and engine-grade evidence.
  review.py     action/source-dependence classification plus sorted review.md and review.json.
  conservation.py block-to-Markdown word and number conservation, with source-dependent
                and expected-normalization categories kept separate from unexplained drift.
  profile.py    DocumentProfile (inventory + independent evidence-backed quality scorecard +
                text-sufficiency split, orthogonal to accounting; deprecated confidence field
                retained for compatibility) → profile.json (AI) + README.md (human run summary).
                build_profile / _sufficiency / write_profile / write_readme.
  engine_state.py serializes the engine-neutral pre-postprocessing state and reloads it
                through StoredEngine for parser-free derived versions.
  enrichment.py resolves completed bundles, reports cost-aware preflight counts, overlays
                source configuration, and runs selected optional stages.
  passages.py   stable block-addressed retrieval records with source, context, authority,
                review, asset, and tokenizer metadata.
  passage_split.py structure-aware prose, line, and GFM-table splitting.
  passage_tokenizer.py deterministic lexical counting or an explicit Hugging Face tokenizer.
  document_map.py outline.json hierarchy, file/passage ranges, hotspots, and source map.
  symbol_index.py conservative, section-local symbol definitions quoted from the source.

scripts/        dev harnesses (not shipped): qa.py (labels-free regression vs tests/qa_baseline.json),
                eval_equations.py (labelled equation accuracy vs tests/equation_labels.json),
                eval_accuracy.py (labelled per-archetype facts vs tests/accuracy_labels.json + profile.json),
                eval_digitize.py (synthetic vector-chart digitization accuracy, self-generated truth),
                eval_raster.py (raster pre-scan gate/calibration accuracy on synthetic scans,
                self-generated truth; optional live-VLM half with a model argument),
                eval_figure_labels.py (labelled --figure-labels accuracy vs tests/figure_labels_labels.json),
                agent_benchmark.py (paired bundle-vs-source-page QA with citations, assets, and token counts),
                benchmark_digitize_bundle.py (replay chart work from a completed bundle without re-running the parser),
                eval_digitize_ocr_gate.py (geometry-only recall/cost gate for outlined-axis OCR),
                eval_table_rebuild.py (glyph-grid rebuild vs the source-checked cells in
                tests/glyph_table_labels.json; scores positional exactness and row containment),
                eval_table_audit.py (row/grid findings vs tests/table_audit_labels.json, whose
                labels were established from the source text and from two independent line-finding
                mechanisms agreeing, never from running the audit; precision is the number that
                matters and --check gates on it).
                eval_engine_table_agreement.py (two engines' table grids for one source, and
                whether the row/grid audit flags the tables they disagree about — engine
                disagreement is unlabelled but plentiful, which is what the thirteen-table label
                set is not; also scores the shipped <block>.glyph.md against the second engine,
                because nothing else ever has),
                eval_engine_order_agreement.py (the same trick on block order: match blocks
                across engines by box overlap and ask whether reading_order flags the pages the
                two order differently),
                eval_engine_text_agreement.py (and on block text, for the prose checks — note
                that word-level engine disagreement is a weak defect proxy for prose, so read the
                flagged-where-they-agree cell, not the recall figure),
                eval_recall_precision.py and eval_reading_order_precision.py (poppler as an
                independent adjudicator, for the two checks with no labelled set: pdftotext over
                the same region for recall, and its reading order — no `-layout` — for block
                order. Both refuse rather than guess: recall skips a page whose font makes
                poppler drop ligatures, since a reader that loses characters is biased toward
                refuting, and the order harness cannot see blocks under four words at all, which
                is exactly the fragment class, so read its single-column figure with that in
                mind),
                benchmark.py. qa.py also reports the verification signals (flagged tables,
                reading-order and split-line pages, low-recall and accent-damaged blocks) as
                drift, never as invariants: a document is not worse for having its defects
                noticed.
```

## Conventions

- `doc_id` is the SHA-256 of source bytes. New document directories use
  `out/<readable-source-name>-<doc_id-prefix>/v<n>/`; the prefix starts at eight
  characters and extends on collision. Existing hash-only directories remain readable.
- A completed version is reused only when its run fingerprint matches and its optional
  model work is healthy. `force=True`, a changed run fingerprint, or a matching partial
  enrichment creates a new `v<n>`; `latest_version()` is what readers use.
- `provenance.json` is the on-disk source of truth; `.md`/`assets` are derived.
- The **accounting invariant** is the project's foundation: every detected block
  lands in the output as text, table, LaTeX, crop, or a visible marker. `emit.py`
  sets each block's `coverage_status`; `CoverageReport.accounted_for` is the
  check. Completeness, review status, and text sufficiency are separate signals.
- The engine seam is load-bearing: only `engines/docling.py` may import Docling,
  and MinerU stays behind its external CLI adapter. Everything downstream sees
  pdf2md types, so parser dependencies remain contained.
- Dataclasses + `asdict` everywhere; no Pydantic. New schema → `schema.py`.
- stdlib `logging` under `pdf2md.*`, never `print`. NullHandler in the library.
- Soft ~700-line file ceiling. Don't recreate the old project's God-files.

## Gotchas

- **Formula enrichment** (`Config.do_formula_enrichment`, default on) turns
  equations into LaTeX but is slow (minutes for equation-heavy papers). Off →
  equations aren't transcribed, so each is cropped to an authoritative image
  (`_eq_crops` crops any equation with no text, not just low-confidence ones) and
  emitted as `![equation](...)`, never a bare "empty equation block". `--no-formula`
  is the CLI lever.
- **A table block's `crop_path` means the image is authoritative; `TableData.source_crop`
  does not.** Every table is now cropped so a reader can check the printed region, but
  `crop_path` is load-bearing well beyond emission: it routes the emitter to publish the
  image *instead* of the cells, and marks the block source-dependent for conservation,
  passages, and chunks. `_attach_table_crops` gives every table its `source_crop` and keeps
  `crop_path` only for the tables the old rule selected (no cells, OCR'd scan page, glyph-
  unbacked, or `--table-ocr`, whose independent reader reads that crop).
- **Table artifacts under `data/tables/` carry their own audit header.** They are read away
  from `document.md`, where an unmarked grid presents as a standalone source. The header is
  written during emit from `grid_audit`; `annotate_table_artifacts` runs after the
  conservation pass to add findings that only exist by then. Anything emitted beside content
  as navigation (the `*[pdf2md] table source:*` line) must be stripped in
  `conservation._semantic_output`, or its link labels count as words the source never had.
- **Word recall measures the emitted text against a *script-split, hyphen-joined* reading
  of the region.** Both sides get the same tokenization or the metric reports its own
  artifacts: the layer glues a reference marker onto its base word (`technetium67`) where
  the output separates it, it breaks a word across a line with a soft hyphen the font
  can't decode (U+FFFE, U+00AD, `\x02`) where the emitter rejoins it, and script tags
  become a space on the output side to match the split source. Before those three, ten of
  eleven low-recall blocks on a clean paper were metric bugs. A fourth: the layer *glues*
  words the output separates (`carlocalculations`, `articlesyoumaybeinterestedin` -- a
  journal that draws a heading with no space glyphs glues all of it), so `_split_glued`
  splits a source word into output words that actually sit adjacent, the mirror of
  `_rejoin_split` and validated the same strict way. That alone took low-recall blocks
  from 919 to 372 corpus-wide, the Atkins textbook from 628 to 168. A list item's printed
  number is then expected normalization, not loss -- `emit` renders it as the bullet --
  and rides as informational: 81 of 90 numeral-only flags were list items. Recall's
  measured precision against an independent reader (poppler, `eval_recall_precision.py`)
  is 0.79, and the corpus now raises 124 recall actions where it once raised over 1,200. `strict` is the same
  comparison without diacritic folding; the gap is accent damage (`Co te` for `Côté`),
  which is a real defect but a different one from a missing word and stays informational.
- **Conservation compares a block against its own rendered markup, so both sides must be
  normalized the same way.** `token_accounting` runs `_semantic_output` over the source as
  well as the output. Without it an HTML table's `td`/`tr`/`tbody` counted as source words
  and were stripped from the output — one 29-row table reported losing 471 words — and every
  `<sup>` in a prose block cost two phantom words. On a clean paper that was 25 of 25
  conservation flags. Anything emitted beside content (the `*[pdf2md] table source:*` line,
  a marker and its blockquote continuation) is stripped from both readings.
- **A pdf2md marker above a table is not part of the table's repeated header.**
  `passage_split._split_table` repeats the caption and column header on every continuation
  passage; a marker belongs to the table as a whole and rides only with the first. A caption
  stays in the repeated header, a `>` line or `*[pdf2md]` line does not. When the header
  genuinely cannot fit the budget the split degrades to unheadered rows with a warning
  rather than raising — aborting lost the whole document over one wide table, which cost
  three of ten conversions on the frozen unseen corpus.
- **A printed table row reaches more than one column; a wrapped cell's continuation does
  not.** Row-band counting assumes one printed line per row, which holds for a dense
  parameter table and fails for any table with a paragraph in a cell. Unguarded it reported
  nine merges for a three-row table of model answers, and `merged_rows` was the most common
  finding on a corpus of unseen papers — 13 of 19 flagged tables, of which 11 had cells of
  119-889 characters. Two guards, both needed (10 false positives with only the lane rule,
  6 with only the width rule, 2 with both): a row whose own cell text cannot fit its box is
  excluded, and a printed line reaching fewer than `_MIN_ROW_LANES` columns is a
  continuation, not a row. `row_locator.projection_row_bands` gets this free on the raster
  path because it projects only the panel's leading stripe, where row labels live.
- **Every sweep in the table audit clamps to the engine's cell extent, so a grid that is a
  fragment of its table measures the fragment against itself.** `_covers_little_of` refuses
  when the cells span under half the block's region in either axis; healthy grids span 0.79
  to 1.0 (median 0.95 across 95 tables), and the one fragment measured 0.09. Found by
  running two engines over the same corpus and asking where they disagreed.
- **Header exclusion uses `column_header`, not `header`.** `RawCell.header` is
  `column_header or row_header`, and a table whose leading label column is a row header has
  *every* row looking like a heading — which switched merge counting off entirely on 32 of
  95 tables measured. `_header_rows` uses column headers only, falling back to row 0 when
  the engine names none (every table has a heading, and a two-line heading is what the
  exclusion exists for). After the fix: 0 of 86.
- **The wrap guard needs an absolute length, not only box overrun.** A cell can overrun a
  narrow numeric column at eleven characters (`0.965 0.969` does), and no eleven-character
  cell is a wrapped paragraph. Without `_MIN_WRAP_CHARS` the guard excluded every row of a
  table whose columns were merely narrow, which silenced both merge checks on a textbook
  row-pair collapse. Measured: collapse tables max out around 21 characters per cell,
  wrapped-prose tables run to a median of 48 and a max of 583.
- **A column whose cells all hold the same count of values is collapsed.**
  `_numeric_columns` needs most cells to be a *lone* number, so it cannot see a column where
  *every* cell was merged — none is ever lone. Consistency is the signal instead.
- **A cell holding many values is a collapsed column whatever its column looks like.**
  `merged_cells` normally needs the column to be numeric — three lone numbers elsewhere in
  it — which a table flattened to *one* data row can never satisfy. And the row-band check
  can't help there either: a cell holding eleven rows of content overruns its box, so the
  wrapped-cell guard excludes it. So the cell's own contents are the only evidence left,
  and four or more whitespace-separated values in one cell stands on its own.
- **Recall is not claimed where a neighbour actually accounts for the missing words.**
  The metric compares a block's text against the glyphs in its box, which assumes the box
  is that block's alone. Across 951 prose blocks the median overlap with a neighbour is
  zero and the 97th percentile 0.085, so `_AMBIGUOUS_REGION_SHARE = 0.15` is far outside
  normal. Overlap alone is not enough to excuse a loss, though: the guard's claim is that
  a missing word may belong to the neighbour, and `_record_neighbour_attribution` checks
  it. Of 63 findings the overlap once silenced, 20 had every missing word present in an
  overlapping block and 16 had none of them anywhere -- and poppler, reading the same
  regions independently, corroborated 19 of the 24 it could judge, the same rate as the
  findings the check does raise. Suppressing those was hiding content, not deferring on
  it, so `missing_in_neighbour` now gates the note. This is the honest form of the
  admission `quality.py` already makes about region-boundary accuracy.
- **A block of one or two characters is a shattered fragment, not content.** Docling
  breaks a display equation into per-glyph `paragraph` blocks -- one Atkins page yields
  `A`, `d`, `G`, `dx`, `=m`, `p`, `,` as fourteen of them -- and emits them after the
  prose they sit above. Three checks had to learn this separately. `reading_order`
  excludes them from the flow (`_MIN_FLOW_CHARS`), via `_flow_blocks` so the order and
  split-line checks cannot disagree about what a block is again: 20 order findings and 26
  split-line findings cleared, and 3 order findings *revealed* where fragments had been
  padding the ordered run. `record_block_recall` skips them when the source region is as
  small as the output (`_FRAGMENT_TOKENS`, `_FRAGMENT_CHARS`) -- both sides must be tiny,
  so a region holding a hundred words that emits two characters is still a catastrophic
  loss, which is what keeps table blocks measured against their markup.
- **A scan carrying someone else's OCR is detected and treated as a scan.** This is the one
  condition under which the whole verification layer inverts: the text layer exists, so
  nothing routes the page down the scanned path, and every glyph check then verifies the
  engine against the same wrong characters and reports agreement. `GlyphIndex.scanned_overlay`
  identifies it from two properties, both structural — one image covering most of the page,
  and the text over it drawn in render mode 3 (invisible), which is what an OCR overlay must
  use and what page text never does. Geometry alone is not enough: a full-page figure plate
  carries labels inside its own bounds and is indistinguishable by position. Measured across
  44 documents and 828 pages, the pair flags 30/30 pages of a 1972 scan and nothing else.
  `page_chars` then reports those pages as having no layer, so the existing scanned-page
  machinery takes over.
- **Detecting the overlay fixes the posture, not the transcription.** The kept text is still
  whoever digitised the paper, and on an old scan that is the worst reading available.
  Measured over all 99 pages of a 1972 data table, scored against the printed row grid the
  two engines between them establish (97 values, no labels needed — every atom's table uses
  the same grid): Docling on the embedded layer recovers 21% of each page's grid with 22.9%
  of value tokens malformed, and MinerU 99% with 0.6%, on 145 tables against 82. The audit
  built here agrees independently: 1 of MinerU's 145 tables carries a structural finding
  against 79 of Docling's 82. `--force-ocr` sits between them (8% on a three-page sample).
  The pipeline warns and names `--engine mineru` when it detects the case.
- **`--force-ocr` re-OCRs the page and suppresses the glyph layer.** For a PDF whose
  embedded text is itself bad OCR, the engine OCRs full pages (`force_full_page_ocr`) and
  `GlyphIndex(force_ocr=True)` reports every page as having no text — so the doc is treated
  as a scan and the glyph-based refill/religature/script overlay are skipped (they'd re-derive
  from the bad layer). The engine's fresh OCR text stands; pair with `--ocr-page-vlm` for a
  full-page vision transcription.
- **`--ocr-page-vlm` transcribes whole scanned pages (page-level replacement).** `_vlm_ocr_pages`
  renders each scanned page, sends it to the vision model, and collapses that page's prose blocks
  into one transcription block (`text_source="vlm-page"`); figures still crop. It runs before
  `build_structure` (which consumes the block list). When it's on, `_get_engine` skips Docling's
  slow `force_full_page_ocr` even under `--force-ocr` — the VLM re-transcribes, so that OCR would
  just be discarded. A failed transcription emits a visible page marker and retains the page image.
- **MinerU runs outside the project environment.** Select it with `--engine mineru` and point
  `--mineru-executable` at that environment's CLI. The adapter consumes native middle JSON,
  then pdf2md renders source crops and applies the normal coverage and chart-safety gates.
  Do not combine MinerU with `--ocr-page-vlm`: page replacement would discard its element structure.
- **A page whose text layer is scrambled cannot judge an equation, so its
  disagreement is not evidence.** `enrich` already records whether the layer was
  clean and in reading order (`Block.extra['ordered']`, from `is_clean` +
  `SCRAMBLED_ABOVE`) to decide whether to *show* it as a hint; `emit` now uses the
  same signal to decide what the finding may *claim*. Measured over the equations
  in bundles converted with formula enrichment on, 52 of 61 `suspect` verdicts came
  from a layer already marked unfit, so the finding reads "equation not verifiable"
  and rides as informational rather than asserting the extraction is wrong. It is
  informational because nothing is withheld: all 494 equations on formula-enabled
  documents emit their LaTeX, and the 277 that are image-backed carry the `$$` block
  under the crop. What is missing is a verdict, for a reason belonging to the page.
  Raised as an action it was 231 entries across the corpora, 78 in one 25-page maths
  paper. Agreement
  from an unfit layer still counts (9 equations verify that way), which is why the
  cross-check keeps running against it -- the asymmetry is the whole point.
  `_UNTERMINATED_ENVIRONMENT` also drops a runaway `\begin{array} { c c c ...`
  that never closes (3 of 158 equations): a 4075-character spec reached the token
  set as one 1000-character `cccc...` counted as missing content.
- **Equation confidence + image-backing live in `enrich.py`/`confidence.py`, not
  the engine.** When the engine's LaTeX disagrees with the text layer (or a scan
  has none), the equation is cropped to an authoritative image and the text rides
  as a flagged hint. `--transcribe` re-OCRs that crop with Surya (`transcribe.py`).
- **A figure's own printed text (caption, axis titles, tick labels) is recovered
  model-free.** The Docling adapter scoops the figure's text items (which Docling
  attaches to the Picture, not the body) into `labels` via `_recover_figure_text`.
  For a *scanned* figure a default post-render pass (`_ocr_scanned_figures`, gated by
  `config.ocr_figures`) re-OCRs the crop upright with `figure_labels_ocr` — the engine
  reads a sideways scan's small text as garbage, so it tries all four 90° rotations and
  keeps the most legible. `--figure-labels` supersedes both with the vision read. Finally
  `_promote_figure_captions` lifts a 'Fig N.' line out of those labels into `caption`
  (`extract_caption`), so a scan's caption renders as the figure's visible caption; a figure
  with a Docling-supplied caption already is left alone.
- **A cell's glyphs are read from its column lane, not its own box.** An engine draws
  the box inside the ink and `_region` keeps a glyph only when its *center* is inside, so
  a tight box truncates the font-decode refill and it writes the short reading over the
  cell: on the GRASP2018 contents pages `12.1` refilled as `12.`, `A.1` as `A.`, `6.10`
  as `6.1`. `enrich._cell_read_boxes` widens each cell to its column's lane
  (`table_rebuild.engine_lane_bounds`, the union of that column's single-column cells --
  column 0 there spans 90.0-122.9 where the cell claims 99.1-117.6) but never past a
  row-neighbour, and processes a row left to right so the bound is the previous cell's
  *read* edge. Both bounds are load-bearing: the neighbour alone pulls a contents page's
  leader dots into the number cell (848 cells corpus-wide read `. . . . . 13` for `13`),
  the lane alone can overlap the next column and claim a glyph twice. Measured after:
  318 cells recover clipped characters, 0 gain leaders, 0 cell pairs overlap more than
  the engine's own boxes already did. Script detection still uses the cell's own box --
  that is about geometry inside the cell.
- **Broken-font text (dingbat mojibake) is repaired from pdfium, not the engine.**
  A font with no usable ToUnicode CMap makes Docling's default backend emit symbol-
  font garbage (`/a114❛❝...`); pypdfium2 decodes it correctly. `enrich.py` detects
  garbage prose (`legibility.is_garbage`) and refills it from `PageChars.text_region`.
  A block that's still garbage after the refill is flagged `illegible` by `emit.py`,
  never emitted as prose. The font's ﬀ/ﬁ/ﬂ ligatures also lack ToUnicode, but pdfium
  surfaces them as C0 control bytes (TeX OT1 slots, `\x1b`-`\x1f`), not dropped, so
  `normalize.expand_ligature_glyphs` maps them back to ff/fi/fl/ffi/ffl (and `\x02`
  soft-hyphen → join) in `clean_reading` before the control-strip — deterministic, no
  dictionary.
- **A page's visible box does not always start at (0, 0), and engines report
  coordinates relative to it.** pdfium is absolute user space -- charboxes,
  `set_cropbox`, page-object bounds -- so on a page with a non-zero MediaBox or
  CropBox corner every glyph check reads ink that far from the text it is
  scoring. Measured: an ACS paper with origin (9, 9) scored mean word recall
  0.53 and an Elsevier one with CropBox (20, 62) scored 0.21; shifting by
  exactly the origin put both above 0.94. Three of 17 documents were affected,
  and they were the three worst-scoring in the corpus. `engines/base.py`'s
  `normalize_page_origin` canonicalizes on user space at the seam (so
  `Block.bbox`, `TableData.bbox`, `FigureRef.bbox`/`caption_bbox` and
  `RawCell.bbox` are all absolute from there on), and `render.py` subtracts the
  origin again when mapping into the rendered raster, which covers the visible
  box. A (0, 0)-origin document is untouched by both.
- Docling block/prov bboxes are bottom-left origin (`y0 > y1`); `render.py` flips Y.
  Don't re-flip elsewhere. **Exception: table-cell bboxes are TOPLEFT** — the docling
  adapter (`_cell_bbox`) flips them to bottom-left so enrich's glyph lookups (script
  overlay, font-decode refill) land on the right region.
- Docling formulas are `TextItem`s with label `formula` (self_ref `#/texts/N`),
  not a separate collection. The adapter maps label → `BlockType.EQUATION`.
- Book splitting selectively expands Part-like bookmark containers into chapter files,
  restores out-of-order destinations to source-page order, and can use two or more
  numbered chapter headings when a Part has no chapter bookmarks. PDFs without that
  evidence remain split at their top-level bookmarks. Inline sub/superscripts are
  recovered from glyph geometry (`scripts.py`, default on); a residual ceiling remains
  where the engine renders an exponent unlike the raw glyphs.
- `output format` is a versioned contract: bump `FORMAT_VERSION` in `schema.py`
  when front-matter keys or the file layout change in a parser-breaking way.
