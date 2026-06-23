# pdf2md

Lossless PDF→markdown converter (library + CLI), built by wrapping Docling and
adding the layer Docling lacks: logical-section splitting into files,
bibliographic front-matter, figure crops, and a per-document coverage audit that
enforces "nothing silently dropped." The README is the user-facing tour; this
file is for working *on* the code.

This is a rebuild of an abandoned MCP server (the old `docsmcp`). The MCP server,
hybrid search, and verification apparatus were dropped; see `PROJECT_PLAN.md` for
the full rationale, decision log, and roadmap. `PROJECT_PLAN.md` is the source of
truth for scope and what's deferred.

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
  pipeline.py   convert_file / convert_dir — orchestrates engine → structure → render ∥ emit → coverage → disk.
  schema.py     all dataclasses + enums (Document, Section, Block, BBox, TableData, RawTable/RawCell, FigureRef, Provenance, CoverageReport). FORMAT_VERSION lives here.
  cache.py      doc_id (sha256), out_root(), doc_dir(), version helpers.
  config.py     frozen Config dataclass loaded from TOML (no Pydantic).
  logging.py    NullHandler in the library; CLI installs the only handler.
  cli.py        Typer surface (convert / coverage / prune / version / models pull).
  models.py     model warm-up (pinning + local-dir override still TODO).

  engines/
    base.py     Engine Protocol + EngineResult (the swap seam; carries raw_tables for enrich).
    docling.py  the ONLY module that imports docling. PURE translation → schema (no
                pdfium, no verification); tables ship RawTable cells for enrich to rebuild.

  enrich.py     engine-agnostic verification (GlyphIndex + enrich_blocks/tables/figures):
                ligature/diacritic repair, inline scripts, equation text-layer cross-check, OCR
                detection, font-decode repair (garbage prose refilled from the pdfium glyph
                layer). Reads pypdfium2 glyph geometry; any engine inherits it.
  normalize.py  text cleanup (Greek glyph names, orphan combining marks, clean_reading) + vocab-
                validated ligature/diacritic word repair (religature, rejoin_split_word, vocabulary).
  scripts.py    inline sub/superscript detector from glyph geometry (PageChars, apply_scripts).
  legibility.py symbol-font garbage detector (score_legibility/is_garbage): dingbat/PUA/glyph-name
                density. Gates the enrich refill and the emit `illegible` flag.
  preformat.py  console/ASCII-table detector (is_preformatted): banner/rule lines (+ pipe columns
                for tables). Routes code blocks, mislabelled console prose, and ASCII tables to
                fenced code-block emission with line structure preserved.
  confidence.py equation LaTeX vs text-layer cross-check scoring (assess_equation; RECOVER_BELOW, SCRAMBLED_ABOVE, HINT_MIN_CONF).
  transcribe.py opt-in multi-pass: re-transcribe image-backed equation crops with local math-OCR (Surya). Transcriber seam + SuryaTranscriber.
  describe.py   opt-in (--describe): describe figure/table/equation crops with a vision model over an
                OpenAI-compatible API (ollama/vLLM/remote). Describer seam + OpenAIVisionDescriber.
  structure.py  Section tree → file layout. bookmarks → heading outline → single document.md.
                emit dedups/merges/nests book headings (_heading_plan) and writes index.md.
  bookmarks.py  read embedded PDF TOC via pypdfium2.
  outline.py    heading depth (from section numbering) + section kind.
  render.py     pypdfium2 bbox crops → assets/ (Y-flip, per-page geometry, full-page fallback).
  emit.py       Section tree → .md files + YAML front-matter; sets coverage_status, collects flags.
  tables.py     GFM table render, HTML fallback for spanning cells.
  metadata.py   bibliographic fields: embedded PDF metadata + first-page heuristic.
  coverage.py   tally block dispositions into a CoverageReport.
  profile.py    DocumentProfile (inventory + quality + confidence grade) → profile.json (AI)
                + README.md (human run summary). build_profile / write_profile / write_readme.

scripts/        dev harnesses (not shipped): qa.py (labels-free regression vs tests/qa_baseline.json),
                eval_equations.py (labelled equation accuracy vs tests/equation_labels.json),
                eval_accuracy.py (labelled per-archetype facts vs tests/accuracy_labels.json + profile.json),
                benchmark.py.
```

## Conventions

- `doc_id` = SHA-256 of source bytes; first 16 chars name the dir
  (`out/<doc_id[:16]>/v<n>/`). Don't truncate elsewhere.
- Re-running a file is a no-op unless `force=True`. New runs create a new
  `v<n>`; `latest_version()` is what readers use.
- `provenance.json` is the on-disk source of truth; `.md`/`assets` are derived.
- The **lossless invariant** is the project's whole point: every block lands in
  the output as text/table/LaTeX/crop, or as a VISIBLE marker. `emit.py` sets
  each block's `coverage_status`; `CoverageReport.lossless` is the check.
- The engine seam is load-bearing: only `engines/docling.py` may import docling.
  Everything downstream sees pdf2md types. Keep it that way so a second backend
  is a contained change.
- Dataclasses + `asdict` everywhere; no Pydantic. New schema → `schema.py`.
- stdlib `logging` under `pdf2md.*`, never `print`. NullHandler in the library.
- Soft ~700-line file ceiling. Don't recreate the old project's God-files.

## Gotchas

- **Formula enrichment** (`Config.do_formula_enrichment`, default on) turns
  equations into LaTeX but is slow (minutes for equation-heavy papers). Off →
  equations become flagged markers. `--no-formula` is the CLI lever.
- **Equation confidence + image-backing live in `enrich.py`/`confidence.py`, not
  the engine.** When the engine's LaTeX disagrees with the text layer (or a scan
  has none), the equation is cropped to an authoritative image and the text rides
  as a flagged hint. `--transcribe` re-OCRs that crop with Surya (`transcribe.py`).
- **Broken-font text (dingbat mojibake) is repaired from pdfium, not the engine.**
  A font with no usable ToUnicode CMap makes Docling's default backend emit symbol-
  font garbage (`/a114❛❝...`); pypdfium2 decodes it correctly. `enrich.py` detects
  garbage prose (`legibility.is_garbage`) and refills it from `PageChars.text_region`.
  A block that's still garbage after the refill is flagged `illegible` by `emit.py`,
  never emitted as prose. Residual: the font's ﬀ/ﬁ/ﬂ ligatures also lack ToUnicode,
  so pdfium drops them ('e cient'); legible but imperfect.
- Docling block/prov bboxes are bottom-left origin (`y0 > y1`); `render.py` flips Y.
  Don't re-flip elsewhere. **Exception: table-cell bboxes are TOPLEFT** — the docling
  adapter (`_cell_bbox`) flips them to bottom-left so enrich's glyph lookups (script
  overlay, font-decode refill) land on the right region.
- Docling formulas are `TextItem`s with label `formula` (self_ref `#/texts/N`),
  not a separate collection. The adapter maps label → `BlockType.EQUATION`.
- Book splits currently land at top-level bookmarks (Parts, not chapters) — a
  known coarse-granularity limitation. Inline sub/superscripts ARE recovered from
  glyph geometry (`scripts.py`, default on); a residual ceiling remains where the
  engine renders an exponent unlike the raw glyphs.
- `output format` is a versioned contract: bump `FORMAT_VERSION` in `schema.py`
  when front-matter keys or the file layout change in a parser-breaking way.
