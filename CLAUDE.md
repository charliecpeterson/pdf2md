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
ingestion workstream is recorded in `docs/archive/quality-and-ingestion-plan.md`; the earlier
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
  pipeline.py   convert_file / convert_dir: the stage list, and per-document failure
                isolation for a batch. 121 lines; the work is in the three below.
  stages.py     the stages themselves, and the `_Run` state they hand along. Each takes
                that one object and mutates it, because extracted plainly they take
                fifteen arguments and read worse than the inline code. Three orderings
                are load-bearing and marked where they matter.
  finalize.py   the last stage: audit the emitted bundle, write the derived files, seal
                the version. provenance.json is written last and atomically, because its
                presence is what marks a version complete.
  run_identity.py what makes this run this run — the inputs `cache.run_fingerprint`
                hashes. Above cache.py because naming the engine means importing engines.
  scan_ocr.py   whole-page VLM transcription for scanned pages, including cache reuse and
                visible failure markers.
  visual.py     what *is* a figure: panel merging, journal-furniture removal, continued-
                fragment joining, caption association.
  figure_passes.py getting content out of one: chart data, printed labels, description.
                Four passes in descending order of trust — vector paths (near-lossless,
                default on), OCR-read axes, then a VLM estimate that only clears the floor
                when a model-free pre-scan calibrated the axes. The crop stays authoritative
                at every tier, which is why a withheld candidate is written, not dropped.
  vision_cache.py document-level inference-cache persistence, integrity checks, and
                  exact lookup/hit/write accounting.
  schema.py     all dataclasses + enums (Document, Section, Block, BBox, TableData, RawTable/RawCell, FigureRef, Provenance, CoverageReport, ConvertResult). FORMAT_VERSION lives here.
  cache.py      source SHA-256, readable document directories, run fingerprints,
                completed-version lookup, and version allocation. `claim_version` allocates
                by *creating* the directory, so the number is exclusive; the `claim.json` it
                leaves is what tells a crashed run apart from one happening right now.
  config.py     frozen Config dataclass loaded from TOML (no Pydantic).
  logging.py    NullHandler in the library; CLI installs the only handler.
  run_metrics.py sequential stage timings and work counts stored with provenance.
  logging.py    ... `Progress.heartbeat` takes a callable, so a long blocking stage that can
                count its own progress reports the count instead of only that it is alive.
  cli.py        the two commands that write: convert and enrich, plus the Typer app.
  cli_inspect.py the ones that read: coverage / compare-runs / list / find / review-tables /
                prune / version / doctor / models / line-reader. Registered by import —
                `cli` imports it after building `app`, so the decorators attach to the same
                instance.
  cli_report.py the last line of a run — accounting first, then the worst outstanding item,
                then where to look. That order is the point: it is the only part of the
                audit most people read.
  models.py     model warm-up and offline/reproducible local snapshots.

  engines/
    base.py     Engine Protocol + EngineResult (the swap seam; carries raw_tables for enrich).
    select.py   select_engine: the seam's front door. `--engine auto` picks MinerU for a scan
                and Docling otherwise, asking `GlyphIndex` rather than pdfium's text presence
                — an OCR-overlay scan has invisible text on every page and reads as 0%
                scanned otherwise. The batch path cannot share one engine under `auto`.
    docling.py  the ONLY module that imports docling. PURE translation → schema (no
                pdfium, no verification); tables ship RawTable cells for enrich to rebuild.
                Also `resolved_device`, the only place that can answer what `auto` meant.
    mineru.py   external-CLI adapter for scans and difficult tables/equations. Reads native
                middle JSON only; pdf2md re-renders crops and ignores MinerU chart tables.

  enrich.py     engine-agnostic verification (GlyphIndex + enrich_blocks/tables/figures):
                ligature/diacritic repair, inline scripts, equation text-layer cross-check, OCR
                detection, font-decode refill (garbage prose refilled from the pdfium glyph
                layer). Reads pypdfium2 glyph geometry; any engine inherits it. resegment_ocr_prose
                re-splits RapidOCR's run-together words in scanned prose (wordninja; OCR blocks only).
                Repairs only; the read-only measuring moved to recall.py.
  recall.py     token-level signals, run after every repair so they measure the finished text.
                Per-block word recall vs the glyph layer (record_recall, a pass after
                enrich_tables so a block that renders from cells is measured against its markup
                rather than its empty text; both sides get the f-ligature expansion, else the
                layer's `con`+`guration` reads as loss), and record_symbol_loss, which recall
                structurally cannot be — one `χ` in 200 words scores 0.995 and passes.
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
  transcribe.py opt-in multi-pass: re-transcribe image-backed equation crops with local math-OCR (Surya). Transcriber seam + SuryaTranscriber + transcribe_equations.
  describe.py   opt-in (--describe): describe figure/table/equation crops with a vision model over an
                OpenAI-compatible API (ollama/vLLM/remote). Describer seam + OpenAIVisionDescriber.
  figure_geometry.py shapes read off a PDF page — drawn paths, the frames around them, and
                stamped marker forms. No notion of what a coordinate means; digitize.py maps
                them to data and judges the mapping. Split out so that dependency runs one way,
                because every figure defect found so far has lived on that boundary.
  digitize_vlm.py tier 2: estimate a raster plot's data with a vision model (vlm_digitize,
                vlm_digitize_consensus, pixel_fit). Shares nothing with the vector tier but the
                Digitization it returns — different input, different failure mode, its own
                consensus and round-trip check. Approximate by construction; the crop stays
                authoritative.
  axes.py       turning tick marks into a coordinate system: tick text from glyphs or text
                objects, the linear/log fit, and the right-hand axis. Everything else in
                digitize reads page points; this is what makes them mean a value.
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
  crops.py      which blocks get a crop and what the image then claims. `crop_path` versus
                `TableData.source_crop` is the load-bearing distinction: every table gets a
                source_crop to check against, and only an authoritative image gets crop_path,
                which routes the emitter to publish the image instead of the cells.
  render.py     pypdfium2 bbox crops → assets/ (Y-flip, per-page geometry, full-page fallback).
                full_page() renders whole scanned pages as verification rasters. svg_crop()
                (--figure-svg) exports a born-digital figure region as lossless SVG via
                pdftocairo — through a temp cropbox'd one-page PDF, because pdftocairo's own
                crop flags are silently ignored for SVG output.
  emit.py       Section tree → .md files + YAML front-matter; the file layout and index.
  render_block.py one block to one piece of Markdown, and the disposition that goes with it.
                This is where the accounting invariant is enforced: every branch returns a
                CoverageStatus with its text and none returns text without one, which is what
                makes accounted_for a check rather than a hope. Its flags say only what they
                can support — "not verifiable" where nothing could have judged, "undecodable
                fragment" where there was no prose to lose.
  emit_figures.py what surrounds a figure crop: caption, recovered labels, table cross-
                reference, and a born-digital chart's series as CSV and a redraw script.
                A digitization below the floor is written as `<stem>.withheld.csv`, not dropped.
  emit_math.py  making an engine's LaTeX safe to emit — closing what it left open, dropping
                spacing-command walls, carrying the printed equation number in as a `\tag`.
                Judges nothing; `confidence.assess_equation` and `--render-check` do that.
  tables.py     GFM table render, HTML fallback for spanning cells.
  table_rebuild.py  born-digital glyph-truth for tables: grid rebuild from whitespace corridors
                (zero-crossing lanes, whole printed tokens per lane) plus row_bands (the same projection over y, so a subscript
                stays on its baseline) and engine_lane_bounds. glyph_grid/grid_markdown read a
                region into the engine's columns with measured rows — written as <block>.glyph.md
                beside the engine's grid, never as the emitted table. check_table_cells is the
                per-engine-cell glyph verification (verdicts + uncovered-ink strays) recorded as
                read-only evidence on TableData.cell_glyph_check during enrich; spacing_only
                verdicts are not flagged.
  table_grid.py the checks that read only the emitted cells and never the source: merged_cells,
                shifted_values, header_absorbed_data, stray_glyphs_in_numeric_column,
                decimal_separator_lost. Owns TableFinding. Stands at medium until the ink
                corroborates it — a signature in the text is a suspicion, ink is evidence.
  table_audit.py  the row-level failures check_table_cells cannot see, because a row the
                engine never created has no cell to verify. row_accounting projects the region's
                ink into rows and requires every value in a row band to reach a cell of the engine
                rows covering it (dropped rows, merged rows); audit_table joins it to
                table_grid's text-only findings. raster_row_findings covers the scanned case
                the glyph path cannot reach, off the table's own crop. running_text_findings is
                the one check with document scope, because one table cannot tell a swallowed
                running footer from its own spanning title; `audit_scanned_tables` and
                `audit_running_text_rows` are the two document-scope entry points, called
                from the pipeline because one needs the rendered crop and the other the
                other pages. Stored on
                TableData.grid_audit; becomes a CoverageFlag in emit. `corroborated` in that
                payload — the ink established the arrangement is wrong — is also what makes enrich
                keep the region's printed lines verbatim (TableData.printed_lines).
  document_metadata.py agent-facing document identity, semantic sections and references, with
                source observations kept separate from parser and registry evidence.
  doi_metadata.py optional DOI registry enrichment via CSL-JSON content negotiation; the raw
                response stays in the bundle and conflicts are recorded, not resolved.
  table_verify.py attaches independent-reader and external-reference evidence to extracted table
                cells. The OCR candidate is never rewritten; each JSONL record keeps the raw
                engine value beside the second reading.
  table_artifacts.py writes the inspectable table candidates and their audit headers
                (candidate/CSV/JSON/glyph-grid/printed-lines files).
  table_panels.py the machine-readable form of a repeated-panel table: one record per printed
                row, tagged with the panel it came from, plus the review signals that say
                where to look. Promotes nothing; the raw grid stays beside it.
  table_resolution.py chooses the consumer-facing value for a cell without discarding evidence:
                external references and reader agreement decide, format rules only diagnose.
  table_reference.py loads and compares semantic external references for table cells; a missing
                key is `no_reference`, never a disagreement.
  table_review.py local review sheets for calibrating numeric table cells — deterministic
                sampling, crops linked in place, completed sheets read back.
  row_locator.py locates table panels, rows and columns from projections alone, with no OCR
                tokens: the independent geometry check behind the raster row audit.
  panel_keys.py finding a row's key words inside one panel of a scanned page — the hard half,
                because on a repeated-panel table the same label appears once per panel and
                matching on text alone puts a value under the wrong element. Aligned panels
                use column bounds from the gaps between centres; the rest are localized
                against their own crop. A panel overlapping its neighbour is refused.
  line_reader.py conservative PP-OCRv6 evidence for table row keys. The recognizer runs in a
                separate environment; this emits hash-pinned inputs and accepts only matching
                returns.
  consensus.py  agreement scoring for repeated model reads of one crop, with different
                strategies for re-read prose and for re-read numbers.
  scan_deskew.py conservatively deskews textless pages before OCR; only a strong projection
                angle triggers a raster replacement, and geometry maps back.
  search.py     literal offline search over completed passage bundles, reading the stable
                passage interface rather than inventing an index.
  doctor.py     environment diagnostics for the engines and optional features.
  metadata.py   ranked local *title* evidence: embedded fields, front-page and repeated
                headings, running titles, early bookmarks, meaningful filenames. Selected,
                alternate, penalized and rejected candidates remain inspectable.
  authors.py    who wrote it. Two readings with different standards: an `Affiliations`
                heading bounds the region exactly and can drop the parts that are not names;
                a line chosen for sitting under the title cannot, so `_author_names` is
                all-or-nothing. It is NOT a name classifier — over 157 corpus title
                candidates it accepts 36, including `Attention Is All You Need`.
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
                several blocks, and keeps a group only when every piece occupies exactly one
                printed line (counted in the block's own region) — band overlap alone also
                describes two consecutive paragraphs. Informational because the detection is now
                exact but the judgement isn't (a masthead's `Received:` / date is also one line in
                two blocks). All three
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

scripts/        72 dev harnesses (not shipped), 22.9k lines. `scripts/README.md` is the tour and
                the gate-vs-probe convention; the ones worth knowing by name:
                qa.py (labels-free regression vs tests/qa_baseline.json, and the staleness report),
                eval_table_audit.py (row/grid findings vs tests/table_audit_labels.json; --check
                gates on precision), eval_figure_axes.py (is a figure recoverable at all, vs
                tests/figure_axes_labels.json), eval_figure_values.py (are the emitted numbers on
                the printed chart, vs tests/figure_values_labels.json),
                eval_recall_precision.py / eval_reading_order_precision.py /
                eval_table_rows_precision.py / eval_symbol_precision.py (poppler as an independent
                adjudicator for the four checks with no labelled set — each documents the blind
                spot that makes it refuse rather than guess),
                eval_table_audit_cross_engine.py (a second engine over the same scans; its
                pre-registered claim was refuted and the docstring says why -- two engines
                OCR the same ink and recover the same *values* while arranging them
                differently, and arrangement is what the audit is about, so a value-multiset
                comparison is orthogonal to it. Also: Docling flags 127 of 138 tables on
                scans, leaving a control of ten, so its audit cannot be scored there at all),
                eval_metadata_precision.py (the DOI registry as the adjudicator for titles and
                authors; the request carries the DOI and nothing else, and the oracle is partial
                — a supplementary file prints its parent article's DOI, and a registry title
                carries the publisher's markup), eval_equations.py, eval_accuracy.py,
                agent_benchmark.py.
                qa.py reports the verification signals (flagged tables, reading-order and
                split-line pages, low-recall and accent-damaged blocks) as drift, never as
                invariants: a document is not worse for having its defects noticed. It keys
                the baseline on `source_sha256`, not the filename — a filename is whatever
                path a conversion was handed, and reconverting the corpus from each bundle's
                own `source.pdf` renamed every document to `source.pdf`, at which point the
                gate reported 30 documents "missing output" while their bundles sat in front
                of it and still exited 0. The same PDF also lives under two directories here,
                which one filename cannot tell apart and one hash can.
```

## Conventions

- `doc_id` is the SHA-256 of source bytes. New document directories use
  `out/<readable-source-name>-<doc_id-prefix>/v<n>/`; the prefix starts at eight
  characters and extends on collision. Existing hash-only directories remain readable.
- A completed version is reused only when its run fingerprint matches and its optional
  model work is healthy. `force=True`, a changed run fingerprint, or a matching partial
  enrichment creates a new `v<n>`; `latest_version()` is what readers use.
- The run fingerprint includes the **resolved** device, not the configured one, so a bundle
  built on CUDA is never reused for an MPS run.
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

- `output format` is a versioned contract: bump `FORMAT_VERSION` in `schema.py`
  when front-matter keys or the file layout change in a parser-breaking way.

## Gotchas

Grouped by what they concern, in the order of the module map above. Each is a
rule and the measurement that produced it — the measurement is the point, and a
rule without its number is a superstition.

### Engines & Scans

Which parser reads what, and the one page shape that inverts every check.


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
- **MinerU reads a scan's tables better, and the claim now has ten documents behind it.**
  It was one: a 1972 compilation where MinerU recovered 99% of the printed grid against
  Docling's 21%. Converting ten scanned documents (251 pages) both ways on one machine at
  one revision -- engine the only variable -- MinerU finds **217 tables against 138**,
  carries a structural finding on **51% of them against 92%**, recovers **12% more clean
  value tokens at a lower malformed rate** (5.3% against 7.7%), and runs in **19 minutes
  against 31**. It never found fewer tables on any document. The per-kind split says where
  the difference lives: `merged_cells` 91 -> 2, `shifted_values` 64 -> 12,
  `header_absorbed_data` 7 -> 0, `row_count` level (105 -> 98). **`decimal_separator_lost`
  reads 6 -> 12 and that comparison is invalid**: the check needs a column of mostly-decimal
  values to judge at all, and Docling's grids offer one in 13 of 138 tables against MinerU's
  155 of 217 (on the 1972 compilation, 3 of 82 against 143 of 144). Per table the check can
  actually judge it is 46% against 8% -- MinerU is six times better on the axis the raw
  counts called worse. Docling's zero there is a grid too collapsed to have a decimal column,
  not a grid without lost decimals. Counting findings across two engines only compares
  populations the checks could reach equally.
  `table_verification_coverage` stays 0/N for
  both, because on a scan the crop is authoritative and every cell is a candidate -- the
  structural findings are the discriminator, not the coverage row. **Read MinerU's table
  artifacts as `mineru_<page>_table_<n>.json`**, not `tables_*.json`: globbing the Docling
  shape made every MinerU finding vanish and the engine read as flawless.
- **Detecting the overlay fixes the posture, not the transcription.** The kept text is still
  whoever digitised the paper, and on an old scan that is the worst reading available.
  Measured over all 99 pages of a 1972 data table, scored against the printed row grid the
  two engines between them establish (97 values, no labels needed — every atom's table uses
  the same grid): Docling on the embedded layer recovers 21% of each page's grid with 22.9%
  of value tokens malformed, and MinerU 99% with 0.6%, on 145 tables against 82. The audit
  built here agrees independently: 1 of MinerU's 145 tables carries a structural finding
  against 79 of Docling's 82. `--force-ocr` sits between them (8% on a three-page sample).
  The pipeline warns and names `--engine mineru` when it detects the case.
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

### Tables

The largest surface here, and the one the field reports care about most.

- **A sign the engine detached still belongs to its number, and a range does not.**
  `merged_cells` skips a cell whose whitespace-separated parts are not all numbers,
  and the engine renders a page's `−3383.702155` as `- 3383.702155` — a lone `-` is
  not a number, so a cell holding a whole collapsed column of negatives was never
  examined. s00214-006-0174-5 table 2 flattened ten elements and thirty energies into
  one data row (source 11 rows against engine 2) and raised nothing. Rejoining
  unconditionally was measured and rejected first: it turned `151 - 153` in an
  `exp. ref` column into two collapsed rows. A collapsed column of negatives leads
  with a sign, a range leads with a value, so the rejoin needs `parts[0]` to be one.
  Two tables newly convicted corpus-wide, none lost, labelled set still 1.00/1.00.
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
- **A column whose cells all hold the same count of values is collapsed.**
  `_numeric_columns` needs most cells to be a *lone* number, so it cannot see a column where
  *every* cell was merged — none is ever lone. Consistency is the signal instead.
- **A cell holding many values is a collapsed column whatever its column looks like.**
  `merged_cells` normally needs the column to be numeric — three lone numbers elsewhere in
  it — which a table flattened to *one* data row can never satisfy. And the row-band check
  can't help there either: a cell holding eleven rows of content overruns its box, so the
  wrapped-cell guard excludes it. So the cell's own contents are the only evidence left,
  and four or more whitespace-separated values in one cell stands on its own.
- **A drawn grid is one path of many closed rectangles, which `_is_rect` does not
  catch.** It spans the plot and is not flat, so nothing else stopped it either: that
  same figure shipped its gridlines as a 60-point series at confidence 0.999, and only
  after the frame guard removed a bogus panel that had been holding its confidence
  under the floor — a fix making a different defect visible. `_axis_aligned` requires a
  *share* (`_AXIS_ALIGNED_SHARE` = 0.9) rather than all segments, because concatenating
  disjoint subpaths leaves a jump between each rectangle and the next: 57 of that path's
  59 segments are axis-aligned and the 2 that are not are those jumps. Measured over
  every candidate path in the labelled figures the distribution is bimodal — 45 at or
  below 0.3, 12 at 1.0, nothing between 0.6 and 1.0 — so the rule sits in an empty band.
  Bars are axis-aligned too; removing them here is what lets them reach `_bar_series`.
- **`eval_table_rows_precision.py` counts printed lines, and a line is not a row.**
  Poppler and the ink projection both count lines, so on a table whose cells span
  several lines they agree with each other and neither says anything about whether
  the grid is right: Intro-to_Relativistic-QC table 28 reads 34 lines for 9 logical
  rows because its irreps are stacked, and abstaining there is correct. That is why
  the harness's control matters and why its 40 "silent" tables are not a recall gap —
  23 have cells long enough to wrap, 7 have no cells, and the 10 whose cells fit one
  line differ by 1-3 rows, which a caption and a header line inside the region
  account for. The two with a genuinely collapsed grid were the detached-sign case
  above.
- **The panel split refuses a row it cannot place, and the emitter dropped it.**
  `split_repeated_panels` records such a row in `refused_rows` rather than guessing which
  panel it belongs to -- a trailing blank where the neighbouring panel has a value, a row
  key shifted across the boundary -- and `panel_tables` rendered only `panel["rows"]`. On
  ct4c00784's 118-element polarizability table that was 22 printed numbers gone from the
  readable grid with no marker (`53 | I | 32.90(10) | 4.2049(18)`, `59 | Pr | 216(20)`,
  `50.0(20) | 4.464(26)`), while `document.md` presented the panels as the table. Nothing
  else caught it: the block was accounted for, the grid audit was silent, and only
  whole-document conservation noticed the tokens vanish -- which is what a high-severity
  `unexplained loss: 5 word(s), 22 number(s)` was reporting. 4 of 18 panel tables corpus-wide
  refuse at least one row. They are now listed under the panels, never folded back into a
  panel: the split declined for a reason, and guessing would put a value under the wrong
  element. Measured after: 646 source numbers, 0 lost, and the conservation action gone.
  **Everything added beside a table has to be inside a marker or made of the source's own
  words**, or the silent loss is simply traded for pdf2md's vocabulary counted as content
  the page never printed. That caught this change twice: `panel`/`column N`/`why` columns
  (moved into the marker, which `_PDF2MD_MARKER` strips) and then a repeated column header
  (dropped -- the merged grid holds one header row for both panels, so a third copy is an
  addition; GFM demands the row, not its content). `*panel N*` labels are stripped in
  `conservation.semantic_output` for the same reason, keeping any title after the dash,
  which is the table's own.
- **A running footer swallowed into a table looks exactly like the table's own title,
  and only the other pages tell them apart.** A spanning cell renders in GFM as the same
  string in every column, so `data/tables/*.csv` writes it as a full row of repeats:
  `Q. Lu and K.A. Peterson, J. Chem. Phys. (2016)` fills whole rows of the Lanthanides SI's
  basis-set tables, where anyone loading the CSV gets citation strings among the exponents.
  Docling emits no PAGE_HEADER/PAGE_FOOTER block on that document (0 of 430), so there is no
  engine-side truth to consult. Repetition is the discriminator: 118 tables corpus-wide carry
  a fully-repeated non-numeric row, and requiring the same string on three distinct pages
  keeps the 34 that are the SI's footer while leaving Atkins section titles and Slater's
  per-atom headings, which differ page to page. One bundle of 32 fires; no other string does.
  The check needs the whole document, so it runs from `stages._render_assets`
  rather than `audit_table`, and it reports rather than deletes -- the row is still ink the
  page printed.
- **A grid can hold every value and still be wrong, and no textual signal tells a
  listing from a table.** The Lanthanides SI is basis sets typeset as fixed-width
  listings; the engine calls them tables and 91 of 117 carry a structural finding
  (second only to the 1972 OCR-overlay scan; born-digital papers with real numeric
  tables sit at 0-19%). Nothing is lost — 98.9% of value tokens are present, which is
  why numeric conservation reads clean — they are in the wrong cells, and a grid that
  keeps every exponent and loses which coefficient it belongs to is not a usable basis
  set. Two textual discriminators were measured and rejected: printed-lines-vs-engine-
  rows fires on 132 tables across 12 documents (mostly scans whose region overlaps
  prose), and line-shape uniformity catches ten well-formed numeric tables at its
  strictest. So the trigger is `grid_audit["corroborated"]`, the audit's own finding
  that the ink contradicts the arrangement, and those tables ship `printed_lines`
  verbatim beside the grid: 99.0% of value tokens in the emitted grid against 100.0% in
  the listing, in printed order. Evidence beside the table, never the emitted table —
  the same boundary the glyph grid keeps.
- **Marker runs outside the project environment, and supplies no `raw_tables`.** Its JSON
  renderer recurses into a block only when the block's class does not derive directly from
  `Block`, and `TableCell` does, so cells are flattened into the table's HTML and never
  appear as children. Tables therefore arrive as markup (`html_to_gfm`, shared with MinerU)
  and the per-cell glyph verification in `enrich`/`table_audit` has nothing to attach to --
  the same trade the MinerU adapter makes. Marker's Surya also drives a vLLM backend that
  wants a Docker container with the `nvidia` runtime registered; where it is not, start the
  server by hand (`scripts/start_surya_vllm.sh`) and set `SURYA_INFERENCE_URL`.
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
- **The wrap guard needs an absolute length, not only box overrun.** A cell can overrun a
  narrow numeric column at eleven characters (`0.965 0.969` does), and no eleven-character
  cell is a wrapped paragraph. Without `_MIN_WRAP_CHARS` the guard excluded every row of a
  table whose columns were merely narrow, which silenced both merge checks on a textbook
  row-pair collapse. Measured: collapse tables max out around 21 characters per cell,
  wrapped-prose tables run to a median of 48 and a max of 583.
- **A lane edge that lands mid-value cuts the number in half, and half a number
  parses.** The glyph grid read the region character by character, each joining the lane
  its own center falls in, so wherever the engine's cell boxes are the wrong shape a value
  is split across two cells: a Lanthanides SI table shipped `2.1999000E-01 1` beside
  `.6203900E-06` where the page prints two whole numbers. That is worse than a contaminated
  cell, which at least fails loudly. Assigning whole printed tokens instead repaired 843 of
  1,019 cut numeric tokens over 2,050 corpus grids and turned 919 cells from several
  fragments into one value, with 3 cells changed the other way -- all three a header row's
  `34` correctly separating into `3` and `4`. Characters lost and gained are both exactly
  zero, which is the invariant: the change moves ink between lanes and creates none. A token
  ends at a whitespace glyph, which these PDFs emit at roughly one per four ink characters,
  so most breaks are read off the page; `_TOKEN_GAP_SHARE` covers the documents that
  position their word spaces instead, and 1.5 sits in the valley of a distribution massed
  below 0.5 and again at 2.0 character widths.

### Running a conversion

Which hardware it lands on, and what two runs at once do to each other.

- **A partial version directory and a running one are indistinguishable from outside, and
  allocation used to delete the difference.** `next_version` returns `max(complete) + 1`,
  so two converts of one document started together both chose the same `v<n>`, and the
  second `shutil.rmtree`'d the first one's in-progress directory on the theory that a
  version without `provenance.json` is a crash. `claim_version` allocates by `mkdir`
  instead -- the directory's creation *is* the claim, so the loser of the race takes the
  next number -- and writes `claim.json` with this host and pid, which is the evidence
  the old code did not have: a claim naming a dead pid on this host is a crashed run and
  its number is reused, anything else is stepped over. A claim from another host counts as
  live, because its pid numbers mean nothing here, and a directory with no claim at all
  predates the file and is treated as abandoned exactly as before. `prune` reads the same
  signal rather than deleting a version a live run is holding. There is still no `--jobs`:
  a batch is parallelised with several processes, which this makes safe.
- **`auto` is not a record of which device ran, and the device changes the output.**
  Docling's layout detector makes marginally different calls on CUDA than on CPU or MPS,
  and `decide_device` resolves `auto` against what torch can see -- quietly landing on CPU
  when it sees nothing -- so a bundle recording `device: "auto"` says nothing about what
  produced it, and a CUDA bundle was reusable for an MPS run. `engines/docling.resolved_device`
  (the engine seam: it is the only module that may ask docling) feeds
  `run_inputs.engine.device`, so the resolution is in provenance *and* in the fingerprint;
  it is logged once per run and reported by `doctor`, which is where "which box should I run
  this on" gets answered before a corpus is copied to the wrong one. An unusable explicit
  device returns `unavailable: ...` rather than raising -- the same configuration raises
  later from the engine, where a failed conversion is the honest report.
- **`OMP_NUM_THREADS` already caps CPU, and nothing in pdf2md does.** `DoclingEngine`
  builds `AcceleratorOptions` without `num_threads`, which is precisely the condition under
  which docling's own `check_alternative_envvars` reads `DOCLING_NUM_THREADS` or
  `OMP_NUM_THREADS` (default 4). `DOCLING_DEVICE` is the opposite case and is ignored: the
  device *is* passed explicitly, and an init argument beats the environment in
  pydantic-settings. Don't add a `--threads` flag for the first one; don't claim the
  environment variable works for the second.

### Equations

What the LaTeX claims, and when the page cannot judge it.

- **Formula enrichment** (`Config.do_formula_enrichment`, default on) turns
  equations into LaTeX but is slow (minutes for equation-heavy papers). Off →
  equations aren't transcribed, so each is cropped to an authoritative image
  (`_eq_crops` crops any equation with no text, not just low-confidence ones) and
  emitted as `![equation](...)`, never a bare "empty equation block". `--no-formula`
  is the CLI lever.
- **A recovered equation number is the page's own, and read as an invented value.**
  `emit` renders it as `\tag{N}` but it lives in `Block.extra`, not in the block's text, so
  the comparison saw a number in the output with no source. 12 of the 17 conservation
  actions on a 28-paper run at default settings were that and nothing else, which on one
  paper meant 6 findings where the honest count is 0. The number joins the source side
  rather than being stripped from the output -- it *is* printed on the page, which is why it
  was recovered -- so every other number stays strictly compared.
- **`Equation text coverage: none (0/11)` does not mean no equation was extracted.**
  The row counts only equations whose text stands without the crop, so a scan whose every
  equation carries LaTeX under an authoritative image scores zero and reads like total
  loss. Measured over the corpus, *every* formula-enabled document transcribes 100% of its
  equations -- 11 of 11, 41 of 41, 194 of 194, 66 of 66 -- and what varies is only how many
  the page's own text layer could confirm. The opposite cause exists and needs the opposite
  sentence: with `--no-formula` nothing is transcribed at all (0 of 1848 on one book), and
  the two are indistinguishable from the ratio alone, which is why `DocumentProfile` now
  carries `equations_transcribed` beside the image-backed count.
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
  cross-check keeps running against it -- the asymmetry is the whole point. **A
  scanned page is the stronger form of the same case and was getting the harsher
  verdict**, purely because it carries no `text_layer` key to test: there is no layer
  at all, so nothing can judge the LaTeX, and the finding asked a reader to check the
  extraction against a reference the page does not have. Over 28 papers at default
  settings that was 57 of the 120 image-backed equations, every one on a scan.
  `_UNTERMINATED_ENVIRONMENT` also drops a runaway `\begin{array} { c c c ...`
  that never closes (3 of 158 equations): a 4075-character spec reached the token
  set as one 1000-character `cccc...` counted as missing content.
- **Equation confidence + image-backing live in `enrich.py`/`confidence.py`, not
  the engine.** When the engine's LaTeX disagrees with the text layer (or a scan
  has none), the equation is cropped to an authoritative image and the text rides
  as a flagged hint. `--transcribe` re-OCRs that crop with Surya (`transcribe.py`).

### Figures

Reading data off drawn geometry, and refusing to when it cannot be calibrated.

- **A curve read at its Bezier control points can miss the curve entirely.** pdfium
  reports a cubic as three BEZIERTO segments — two control points and the endpoint —
  and `_segment_points` appended all three as if they were data. A curve drawn as many
  short Beziers hides the error, which is why the aggregate never showed it; a curve
  drawn as a few long ones is all error. Atkins Fig. 3.4 is `ln(Vf/Vi)` and came back
  with (2.95, 1.86) where the printed curve passes through (2.95, 1.08). Flattening at
  `_BEZIER_STEPS` = 8 tracks it to within 0.01-0.08 across the range.
- **The tier that cannot see colour cannot assign a second axis, so it withholds.**
  A figure reaches `vector_ocr_digitize_page` precisely because its tick text is
  outlined to paths — which is also why it has no text objects and no colours to match
  a curve to a scale. `_fit_right_axis` (shared with the vector tier) detects the axis
  from the OCR'd ticks, and the tier then returns an empty candidate with a note rather
  than mapping everything on the left scale: wires-2020 #/pictures/47 was shipping its
  right-hand bars out by a factor of ten (1 of 5 labelled anchors), and Atkins
  #/pictures/1248 both its E/V and P/(W cm⁻²) curves on the E/V scale. 2 of 15 OCR-tier
  extractions withheld. **A tick nearer another frame's left edge than this frame's
  right edge is that frame's axis** — side-by-side panels put the next panel's y axis
  squarely in the band, and without that exclusion wires-2020 #/pictures/26 (two parity
  plots, 3 of 4 anchors) was withheld too. Colour cannot always resolve it even when
  available: #/pictures/47 uses the same five bar colours on both scales, split only by
  category group, which is why withholding rather than guessing is the rule.
- **A figure with two y scales is drawn so a reader can tell which curve is which, and
  colour is how.** `_fit_ticks` looks only left of the frame and `_neighborhood` reaches
  barely past its right edge — both deliberate, to keep a neighbouring subplot's labels
  out of the fit — so a right-hand axis was invisible and every series got the left
  scale. Atkins Fig. 5.1 shipped its ethanol curve, truly 52.3 to 58.1, as 13.6 to 19.7
  at **confidence 1.0**. `_right_axis_ticks` reads text *objects* (only an object carries
  a colour) in a band bounded by the frame's own width, `_second_y_axis` fits them, and
  `_panel_series` routes a series to the right calibration when its stroke colour matches
  the right ticks' colour: on that figure the right ticks, the word "Ethanol" and its
  curve are all `(113, 45, 125)`. All four anchors now match within 0.12 on a 4-unit
  axis, and the range check is measured before the right-axis series join, or they would
  be convicted for being on the axis they belong to. Watch the adjacent trap: `fit_axis`
  preferred a log fit that beat linear by `1e-6`, and 54/56/58 came back "log" because
  log10 is locally linear over a narrow range and the printed ticks are 5% unevenly
  spaced. `_LOG_MARGIN` is 0.01 — a real log axis fits a line terribly, so the margin
  costs nothing.
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

### Recall & Conservation

Token-level signals: what the page printed against what was emitted.

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
  from 919 to 372 corpus-wide, the Atkins textbook from 628 to 168. A run may also
  match *reversed*, which is what bidi does to one: the first right-to-left document
  measured here reads `اسةمنالم` in its region where the output has the three words
  the other way round. Same claim about the same words, not a weaker one -- the
  pieces must still be adjacent and still concatenate exactly -- and measured before
  it went in: 8 of 40 Arabic blocks improve, 2 of 1002 Latin blocks improve, nothing
  gets worse. A list item's printed
  number is then expected normalization, not loss -- `emit` renders it as the bullet --
  and rides as informational: 81 of 90 numeral-only flags were list items. Recall's
  measured precision against an independent reader (poppler, `eval_recall_precision.py`)
  is 0.72-0.74 over the 36-document corpus (0.79 was the same measurement on the smaller
  earlier one; four seeds span 0.01, so the move is composition, not sampling), and the
  corpus now raises 124 recall actions where it once raised over 1,200. That harness now
  samples the band *above* the flagging threshold too, as the control it never had:
  precision says how many flags are real and nothing said how much loss went unflagged.
  Above the threshold poppler shows a median of 0 content words missing (p75 1, and 4%
  of blocks missing five or more), against a median of 2 and p75 6 for confirmed flags
  below it -- so the silent side is genuinely quiet and the threshold separates the two
  populations. `strict` is the same
  comparison without diacritic folding; the gap is accent damage (`Co te` for `Côté`),
  which is a real defect but a different one from a missing word and stays informational.
- **Conservation compares a block against its own rendered markup, so both sides must be
  normalized the same way.** `token_accounting` runs `_semantic_output` over the source as
  well as the output. Without it an HTML table's `td`/`tr`/`tbody` counted as source words
  and were stripped from the output — one 29-row table reported losing 471 words — and every
  `<sup>` in a prose block cost two phantom words. On a clean paper that was 25 of 25
  conservation flags. Anything emitted beside content (the `*[pdf2md] table source:*` line,
  a marker and its blockquote continuation) is stripped from both readings.
- **One symbol loss reads as ordinary text afterwards, and it is the comparisons.** The
  check already names the characters -- `symbols dropped: the page prints ≥ in this
  block` -- so the defect was never generic; what was wrong was the severity. Losing a
  `Σ` or a `∆` leaves visible nonsense and a lost `±` leaves `9.3 0.2`, two numbers and
  no operator, both of which a reader catches. Losing the `≥` in `restricting the ratio
  between two exponents to be ≥1.6` emits `to be 1.6`: a fluent sentence asserting an
  exact value where the page printed a floor, which for a citation check is the opposite
  claim. `_BOUND_OPERATORS` is therefore high severity and the rest stays medium -- 4 of
  71 corpus findings carry one, so it promotes a handful rather than reclassifying the
  check, and on the one paper affected the marker went from fourteenth in a queue of
  equals to the top. `±` and `∝` are deliberately out: they are relations, but their
  loss is visible, which is the same argument that keeps dashes out of `_SYMBOLS`
  entirely. ASCII `<` and `>` belong in the class by that argument and are not in it:
  they sit outside `_SYMBOLS`, and adding them means first stripping the emitted side's
  `<sup>` markup, which would otherwise supply phantom angle brackets and mask the very
  loss being looked for. The severity is read from a `comparisons` key on the payload,
  not by parsing the human-readable `symbols` string.
- **Word recall cannot see a dropped symbol, and its threshold is not the problem.**
  A 200-word paragraph that loses one `χ` scores 0.995 and passes the floor, which is
  correct -- one word in two hundred is not a lost paragraph -- so the signal has to be its
  own. Over the 28-paper corpus at default settings, 40 blocks across 4 documents drop 87
  Greek letters and math operators from the emitted prose (σ×15, θ×15, °×13, ρ×11, ∆×8,
  α×7, ∞×6, χ×5) and only 4 of those blocks were low-recall as well. It changes meaning:
  `where χ is the van der Waals radius` emits as `where is the van der Waals radius`, and
  one block emits `Dc MX` for the printed `Δχ MX`, which reads as ordinary text and is
  worse. The check can be exact because the character class is narrow -- dashes are
  deliberately out, since `−` emitting as `-` is normalization and a class holding both
  loss and normalization needs a threshold. It is engine-side (the symbols are already
  gone from `base-state.json`), so `record_symbol_loss` reports and never repairs: that a
  `χ` is missing is certain, where to put it back is not. Measured against poppler
  (`eval_symbol_precision.py`): precision **0.97**, 39 of 40 findings corroborated by a
  second reader, stable across four seeds. The one refutation is a `°` pdfium resolves and
  poppler does not. The control matters as much -- of 356 unflagged blocks poppler shows a
  missing symbol in 1, so 0.3% of symbol loss goes unraised, which for a check this narrow
  was the likelier failure. The record writes `χ (4)`, never `χ×4`: `×` is itself one of
  the symbols reported, and using it as a separator made two of the harness's own
  refutations a parsing collision rather than a disagreement.
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
- **`--force-ocr` re-OCRs the page and suppresses the glyph layer.** For a PDF whose
  embedded text is itself bad OCR, the engine OCRs full pages (`force_full_page_ocr`) and
  `GlyphIndex(force_ocr=True)` reports every page as having no text — so the doc is treated
  as a scan and the glyph-based refill/religature/script overlay are skipped (they'd re-derive
  from the bad layer). The engine's fresh OCR text stands; pair with `--ocr-page-vlm` for a
  full-page vision transcription.
- **A layer that spells symbols with letters passes every per-character test and is
  still nonsense.** `is_clean` looked for unmapped glyphs (C0/C1 controls, U+FFFD),
  which a font substituting *ordinary letters* for symbols sails through: Wiley
  draws `(14)` as `ð14Þ` and a square root as a run of `ffi` ligatures. The
  equation is then convicted for disagreeing with a broken reference. 101 of 383
  equation regions carry one of the two signatures, in Wiley and in an ACS review,
  so it is a font property and not a publisher's; of the 19 suspect equations whose
  layer was called fit, 11 carry one and are now informational, leaving 8 genuine
  candidates. This is a classification change, deliberately: dropping `ðNÞ` from
  both sides of the *comparison* was measured and rejected (71 equations improved,
  93 got worse), because removing a token both sides carry only lowers the ratio.

### Reading Order

The defect the rest of the audit is blind to by construction.

- **Poppler is not independent of the reading-order defect it is asked to judge.**
  Its order largely follows the PDF's content stream, which is where a stream-order
  defect comes from, so on exactly the pages whose disorder the engine inherited it
  agrees with the emission and refutes a correct finding — Intro-to_Relativistic-QC
  page 8 emits `#/texts/38` (top 218.8) before `#/texts/39` (top 230.8) on a one-column
  list, and poppler called that agreement. `eval_reading_order_precision.py` now proves
  what it can instead: on a page with one column the printed order IS top to bottom, so
  a block sitting entirely below its predecessor is out of order with no model and no
  threshold. Precision on flagged pages 0.61 → 0.71 (both printed; the poppler-alone
  figure is what compares to earlier runs). **The premise must be tested, not asked of
  `column_starts`** — it drops a cluster holding less than `_MIN_COLUMN_SHARE` of the
  page's blocks, so Atkins page 41 (nine blocks at x=43.5, two at x=391.5) reports as
  single-column, and comparing tops across that pair proves nothing. Assuming it instead
  of checking it inflated the figure to 0.78 and invented a 35-page recall gap that does
  not exist. Two column-model theories were also measured and rejected: neither the
  closest gap between detected column starts (56% of confirmed vs 50% of refuted under
  30pt) nor the column count separates confirmed from refuted.
- **Band overlap is what two consecutive paragraphs have, so it cannot be what a
  split printed line is.** `split_line_findings` grouped blocks whose vertical bands
  overlapped inside a column, and the couple of points between one paragraph's
  descenders and the next's ascenders satisfies that: on Atkins page 92, a
  *single-column* page, a nine-line and a five-line paragraph were reported as one
  printed line in two pieces. Measured against the glyph layer, 363 of the 392 groups
  on pages that have one (93%) held a block spanning several printed lines. The
  definition is the check — count the printed lines in each piece's own region and keep
  the group only when every piece occupies exactly one: 733 groups corpus-wide to 29,
  and the survivors are the shape the docstring always claimed (`'192.'` /
  `'Allinger, N. L. J. Am. Chem. Soc.'`, `'[N 2'` / `'O5]/(mol dm'`). 341 of the 733 were
  on scanned pages with no layer to count; those go silent rather than being reported,
  because band overlap on its own was never evidence. The PDF is opened lazily, only
  once a page produces a candidate.

### Text & Fonts

Characters the engine loses, and what is worth repairing versus reporting.

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
- **A two-character block that will not decode is a marginal mark, not lost prose.** A
  journal prints a decorative glyph at the bottom of every page in a font with no usable
  encoding; `is_garbage` fires on it and `emit` called it `illegible text layer` at high
  severity, which put five of them at the top of an otherwise clean paper's review queue --
  every high item ejic202100500 had. Corpus-wide 17 of 79 illegible flags are blocks of two
  characters or fewer. The block still gets a marker and stays accounted for; what changes
  is the claim, `undecodable fragment` at informational/low, so `illegible_blocks` counts
  paragraphs a reader actually lost. Measured on that paper: 6 high items to 1, and the one
  left is a real recall finding. `legibility.MIN_JUDGED_CHARS` is the floor -- the third
  check to need one, after `reading_order._MIN_FLOW_CHARS` and `enrich._FRAGMENT_CHARS`,
  which keep their own because they ask different questions of a short block.
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

### Metadata

Bibliographic fields, and why page furniture keeps winning.

- **The author line is the block under the title, and so is the affiliation.** Local author
  extraction existed but needed an `Affiliations` heading to bound the region, which one
  corpus document in 37 prints, so 30 of 37 reported no authors at all while naming them in
  the emitted markdown. Anchoring on the title instead covers the rest, but only because the
  name parser is all-or-nothing: the affiliation-bounded reading can collect the parts that
  look like names and drop the others, since nothing else lives in that region, while a line
  chosen for its position cannot -- `Department of Applied Analysis and Computer Science
  University of Waterloo` is otherwise four capitalized words joined by `and`. So one part
  that is not a name disqualifies the line, on an institution word, a function word, a digit
  that survives marker-stripping, or a lowercase initial. The run must also start at the
  first block under the title and continue while each block is names or a bare separator: a
  journal that gives each author its own block with `|` between them yields eight, and
  answering with the first would be a wrong author list, not a partial one (the stranded `2`
  affiliation markers are why the separator pattern takes digits). Measured over 37 bundles,
  10 documents gain a correct author list, none lose or change one. Two books were the only
  false positives, both `VOLUME I` -- a book's front matter is what sits under its title, so
  those words are in the same stoplist as the function words. Slater's real author is one
  block further down and is not claimed: the run starts where it starts. **`_author_names`
  is not a name classifier and must not be used as one** -- it works because of where the
  line sits, not because the text is provably a name. Run over the 157 title candidates in
  the corpus it accepts 36, among them `Attention Is All You Need`, `SELF-CONSISTENT FIELD
  METHODS` and `II. METHODS`, so using it to veto an author-shaped title (`Oleg Borodin*`
  outranks its paper's real title) would take most correct titles with it.
- **A title is more than one word, and it is not the author line.** Measured against the
  DOI registry (`eval_metadata_precision.py`), 11 of 13 checkable titles and 9 of 9 checkable
  author lists already agreed; the two title mismatches are the oracle's own limits, a manual
  citing another paper's DOI and a registry title carrying `<scp>` markup. On a second corpus
  it refuted three, and two were single words -- `AEUROPEANJOURNAL`, `insight`. Of 284
  distinct title candidates corpus-wide, 29 are a single word and not one is a title: section
  headings, journal banners, filenames. Separately, `matches_embedded_author` only caught a
  heading equal to one author's name, so the whole author line (`Qing Lu and Kirk A Peterson
  a)`) sailed past; requiring every *embedded* surname to appear catches it and stays anchored
  on evidence the page did not supply, so it cannot fold back into the heuristics it corrects
  — 3 corpus candidates match, all author lines, no real title. Still unfixed and left alone
  rather than guessed at: `C3DT50599E 8617..8636 ++`, a publisher production string, which
  no rule short of one written for it would catch.
- **Repetition is what makes a journal running head *not* the title.** `repeated_heading`
  counted it as support, so `CHARLOTTE FROESE FISCHER` -- the author of a 1972 compilation,
  printed above the text on 30 pages -- was selected as the title at high quality over the
  real one sitting one block earlier on page 1. The two populations do not overlap: across
  the corpus ten correct titles repeat on exactly two pages and that one wrong title on
  thirty, so `_RUNNING_HEAD_PAGES = 4` sits in an empty band. A book whose title is also its
  running head is exempt by being the document's first heading. Two other shapes reach the
  same ranking as clean text and are now rejected outright rather than scored: a journal's
  own citation line (`Palestine Technical University Research Journal, 2026, 14(02),
  159-176` -- two of year, `NN(NN)`, trailing page range; 1 of 157 corpus candidates), an
  embedded Title that is only an identifier (`doi:`, `PII:`), and a candidate with no
  letters at all (`3.3, 3.5`, a stray CR-category fragment, beat `MARCHING CUBES: A HIGH
  RESOLUTION 3D SURFACE CONSTRUCTION ALGORITHM` on an equal score because digits sort
  first). Measured over 37 bundles, two
  titles are fixed, two stay wrong (one at lower confidence), none regress. **Printed order
  is not the tiebreak** -- it looks obvious and costs three documents: journals print `OPEN
  ACCESS`, the journal name, and `Supporting Information for:` above the title, so
  first-heading-wins fixes one paper and breaks three.
