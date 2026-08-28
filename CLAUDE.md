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
                glyph layer (record_block_recall, aggregated by profile) and whole-document
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
  table_rebuild.py  born-digital glyph-truth for tables: independent grid rebuild from whitespace
                corridors (zero-crossing lanes), and check_table_cells — per-engine-cell glyph
                verification (verdicts + uncovered-ink strays) recorded as read-only evidence on
                TableData.cell_glyph_check during enrich; spacing_only verdicts are not flagged.
  metadata.py   ranked local bibliographic evidence from embedded fields, front-page and
                repeated headings, running titles, early bookmarks, and meaningful filenames.
                Selected, alternate, penalized, and rejected candidates remain inspectable.
  grobid.py     optional GROBID enrichment (--grobid-url): header fields + every reference string
                parsed from TEI; fill-gaps-only merge (GROBID's header model can latch onto arXiv
                license boilerplate), raw TEI under data/, unreachable service degrades with a warning.
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
                tests/glyph_table_labels.json; scores positional exactness and row containment).
                benchmark.py.
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
