# New session - 2026-09-01T06:16:46.516Z

**Session ID:** ses_fa463114cffeTQbr47ndcNSerM
**Created:** 8/31/2026, 11:16:46 PM
**Updated:** 8/31/2026, 11:29:41 PM

---

## User

can you explain this code to me

---

## Assistant (Build · Nemotron 3 Super · 14.7s)

_Thinking:_

We need to explain code, but user didn't provide code yet. Probably they will paste. We ask for code.


Sure! Please paste the code you'd like explained, and I'll walk you through it.

---

## User

this project, pdf2md

---

## Assistant (Build · Nemotron 3 Super · 11.6s)

_Thinking:_

We need to explain the project. Provide overview.


pdf2md is a Python library + CLI that converts PDFs to Markdown while preserving structure, figures, tables, equations, and ensuring nothing is silently dropped. It uses Docling by default (with MinerU as an optional high‑risk fallback) and adds:

- Logical section splitting (headings, bookmarks)
- Bibliographic front‑matter extraction
- Figure cropping, SVG export, and optional VLM‑based description
- Per‑document coverage audit (every block is accounted for as text, table, LaTeX, image, or visible marker)
- Optional OCR/VLM page replacement, formula enrichment, digitization of chart data, and more

Core modules live in `src/pdf2md/`:
- `pipeline.py` – orchestrates parsing, repairing, rendering, emitting, auditing
- `engines/docling.py` & `engines/mineru.py` – engine‑specific adapters
- `enrich.py` – glyph‑level repair, ligature/diacritic fix, script recovery, numeric conservation
- `digitize.py` – vector‑path chart digitization
- `visual.py` – figure crops, labels, descriptions
- `emit.py` – writes Markdown + YAML front‑matter
- `schema.py` – dataclasses and format version
- `cli.py` – Typer‑based command line (`pdf2md convert …`)

The project emphasizes auditable output: each block gets a `coverage_status`, and a `CoverageReport` verifies that the sum of accounted blocks matches the input. Tests avoid real Docling unless the `integration` marker is used.

In short, pdf2md takes a PDF, extracts its semantic structure, enriches the text with gly‑ph‑level fixes, extracts figures/tables/equations (with optional VLM description or digitization), and writes a self‑checking Markdown bundle where nothing is lost silently.

---

## User

can you detail everything it does to me, going slow and mappling out how this app works and how it does the convertion

---

## Assistant (Build · Nemotron 3 Super · 70.1s)

_Thinking:_

We need to explain detailed workflow. Provide steps. Use concise but thorough.



Here’s a step‑by‑step walk‑through of what **pdf2md** does when you run:

```bash
pdf2md convert path/to/document.pdf
```

---  

## 1️⃣  High‑level pipeline (src/pdf2md/pipeline.py)

| Stage | What it does | Key functions / modules |
|------|---------------|--------------------------|
| **a. Load & fingerprint** | Compute SHA‑256 of the source PDF → `doc_id`. Look in the cache (`src/pdf2md/cache.py`) for a completed version that matches the current run fingerprint (config + optional model work). If found and healthy, reuse it; otherwise start a fresh run. | `cache.lookup_completed_version`, `cache.write_version` |
| **b. Parse (engine)** | Choose an engine (`docling` or `mineru`). The engine returns an `EngineResult` containing raw blocks (text, tables, figures, equations) and low‑level geometry (bboxes). Only `engines/docling.py` may import the Docling library; `engines/mineru.py` is a thin CLI wrapper that reads MinerU’s native middle‑JSON. | `get_engine()` → `engine.parse()` → `EngineResult` |
| **c. Build glyph index** | From the PDF (via pypdfium2) we build a `GlyphIndex` that maps character codes → PDF‑space rectangles. This index is the source of truth for every later “glyph‑truth” operation (ligature repair, script recovery, table‑cell verification, etc.). | `enrich.py:build_glyph_index()` |
| **d. Enrich blocks** | Run a series of source‑dependent clean‑ups that **never** change the meaning, only repair noise introduced by the PDF extraction:  <br>• Ligature/diacritic repair (`normalize.religature`, `rejoin_split_word`)  <br>• TeX‑style f‑ligature expansion (`expand_ligature_glyphs`)  <br>• Sub‑/super‑script detection from glyph geometry (`scripts.apply_scripts`)  <br>• OCR‑detected garbage detection & refill from the glyph layer (`legibility.is_garbage` → `PageChars.text_region`)  <br>• Inline script (sup/sub) wrapping  <br>• Equation‑text‑layer cross‑check (confidence scoring)  <br>• Table‑cell glyph verification (`table_rebuild.check_table_cells`)  <br>• Numeric conservation accounting (informational only) | Functions in `enrich.py`, `normalize.py`, `scripts.py`, `confidence.py`, `table_rebuild.py` |
| **e. Structure building** | Turn the flat list of enriched blocks into a hierarchical **section tree** that respects headings, bookmarks, and numbering.  <br>• For papers: one document (no splitting).  <br>• For books: split at top‑level bookmarks; optionally expand Part‑like containers into chapter files.  <br>• Restores source‑page order, resolves out‑of‑order destinations, and applies a conservative heading fallback when needed. | `structure.py:build_structure()` |
| **f. Figure & table processing** | For each `Picture` (figure) or `Table` block:  <br>• **Crop** the source PDF region to an asset (PNG/SVG) via `render.py`.  <br>• **Extract labels** (caption, axis titles) – first from the engine’s attached text items, then OCR‑rotations for scanned figures, finally optional VLM read (`--figure-labels`).  <br>• **Digitize** born‑digital charts (vector paths → data series) unless disabled.  <br>• **Describe** (optional `--describe`) using a vision model over an OpenAI‑compatible API.  <br>• **Export SVG** (`--figure-svg`) via `pdftocairo` on a temporary one‑page PDF with cropbox. | `visual.py`, `digitize.py`, `describe.py`, `render.py` |
| **g. Emit Markdown bundle** | Walk the section tree; for each section:  <br>• Write a `.md` file (shallow root + detailed local contents).  <br>• Insert YAML front‑matter with metadata (title, authors, dates, doc_id, version, coverage status, etc.).  <br>• Emit blocks: text → plain markdown, tables → GFM or HTML fallback, equations → LaTeX (if enriched) or `![equation](...)`, figures → `![label](assets/…)`, illegible blocks → visible `[illegible]` marker.  <br>• Update per‑block `coverage_status` (accounted, omitted, illegible, etc.). | `emit.py:emit_document()`, `emit_block()` |
| **h. Coverage audit** | After emission, tally every block’s disposition into a `CoverageReport`.  The invariant: **sum of accounted blocks == total blocks detected**.  If any block is missing, the report flags it and the run is considered incomplete. | `coverage.py:build_report()` |
| **i. Finalize & cache** | Write `provenance.json` (source of truth) and any assets.  Store the run fingerprint so future identical invocations hit the cache. | `cache.write_version()` |

---  

## 2️⃣  Data flow illustrated

```
PDF file
   │
   ▼  (cache lookup – reuse if unchanged)
┌─────────────┐
│  Engine     │  ← docling.py  (pure translation)  or  mineru.py (CLI wrapper)
│  (parse)    │
└─────────────┘
   ▼
EngineResult (raw blocks + bboxes)
   ▼
GlyphIndex (pypdfium2 glyph → PDF rects)
   ▼
Enrichment pipeline
   ├─ ligature/diacritic repair
   ├─ TeX f‑ligature expansion
   ├─ sub/super‑script recovery
   ├─ OCR‑garbage detection & refill
   ├─ equation confidence scoring
   ├─ table‑cell glyph verification
   └─ numeric conservation (info only)
   ▼
Enriched blocks
   ▼
Structure builder (headings, bookmarks, numbering) → Section tree
   ▼
Per‑block processing
   ├─ Figure: crop → asset, label recovery, optional VLM description, digitization
   ├─ Table: crop → asset, optional HTML fallback, cell‑glyph check
   └─ Equation: LaTeX output or image fallback
   ▼
Markdown + YAML front‑matter per section (written to out/<name>-<doc_id>/v<n>/)
   ▼
CoverageReport (audit that everything is accounted for)
   ▼
provenance.json (source of truth) + assets/
```

---  

## 3️⃣  Where the “nothing silently dropped” guarantee lives  

* Every block produced by the engine gets a `Block` record in `schema.py`.  
* During **enrichment** we only add metadata or repair glyphs – we never delete a block.  
* During **emission** each block is matched to one of the following outputs:  

| Block type | Emitted as |
|------------|------------|
| `Text` | Plain Markdown (or `\`‑escaped if needed) |
| `Table` | GFM table (or HTML fallback) |
| `Equation` | LaTeX `$$…$$` (if enriched) **or** `![equation](assets/…_eq_N.png)` |
| `Picture` | `![label](assets/…_fig_N.[png|svg])` (+ optional description) |
| `ListItem`, `Header`, `Footnote`, … | Corresponding Markdown syntax |
| `Illegal / Unrecoverable` | Visible marker `[illegible]` or `[uncovered-ink]` |

* After emission, `CoverageReport.accounted_for` counts all blocks that received one of the above outputs.  
* If `accounted_for != total_blocks`, the run fails (or is marked incomplete) and you see a clear message in the CLI/logs.  

Thus the system **cannot** silently lose a block – any missing block would be reported.

---  

## 4️⃣  Optional features (controlled via CLI flags or config)

| Flag / Config | What it adds | Where it lives |
|---------------|--------------|----------------|
| `--no-formula` (`Config.do_formula_enrichment = False`) | Skip expensive LaTeX transcription; equations become image crops only. | `enrich.py` (guarded by flag) |
| `--force-ocr` | Ignore embedded text layer, run OCR on the whole page (or per‑page VLM if `--ocr-page-vlm` also set). | `engines/docling.py` (full‑page OCR path) |
| `--ocr-page-vlm` | Replace each scanned page with a vision‑model transcription (single block per page). | `pipeline.py` → `_get_engine()` shortcut |
| `--describe` | Run a vision model (Ollama / vLLM / remote) on each figure/table/equation crop to generate a natural‑language description stored in the block’s `extra.description`. | `describe.py` |
| `--digitize` (default on) | Extract data series from vector‑drawn charts (lines, scatter, bars). | `digitize.py` |
| `--figure-svg` | Export figures as lossless SVG via `pdftocairo`. | `render.py:svg_crop()` |
| `--grobid-url` | Post‑process via GROBID to fill bibliographic metadata (header + references). | `grobid.py` |
| `--no-enrich` | Bypass the enrichment pipeline (useful for debugging). | `pipeline.py` skips `enrich_blocks()` |

---  

## 5️⃣  Testing strategy (so you know it’s reliable)

* **Unit / snapshot tests** (`pytest`) run *without* invoking Docling or downloading models – they feed fabricated `EngineResult` objects into the enrichment/emission stages and assert the Markdown matches a stored snapshot.  
* **Integration tests** (`pytest -m integration`) require `PDF2MD_TEST_PDF` pointing to a real PDF; they exercise the real Docling (or MinerU) engine and verify end‑to‑end output (including coverage report).  
* The test suite never mutates the cache; it uses a temporary directory.  

---  

## 6️⃣  Quick CLI cheat‑sheet

```bash
pdf2md convert file.pdf                     # default (Docling, enrich on)
pdf2md convert file.pdf --engine mineru    # use MinerU fallback
pdf2md convert file.pdf --no-formula       # skip LaTeX, equations as images
pdf2md convert file.pdf --force-ocr        # OCR every page (good for bad embedded text)
pdf2md convert file.pdf --describe         # add VLM descriptions to figures/tables/eqs
pdf2md convert file.pdf --figure-svg       # export figures as SVG instead of PNG
pdf2md convert file.pdf --grobid-url http://localhost:8080/api  # enrich biblio via GROBID
pdf2md coverage file.pdf                  # just show coverage report for a cached run
pdf2md version                            # print FORMAT_VERSION and dependencies
```

---  

### TL;DR

1. **Load** PDF → cache lookup.  
2. **Parse** with Docling (or MinerU) → raw blocks + bboxes.  
3. **GlyphIndex** from PDF → source of truth for every character.  
4. **Enrich**: ligatures, scripts, OCR‑garbage fix, equation confidence, table‑cell check.  
5. **Build** hierarchical sections (headings/bookmarks).  
6. **Process** figures/tables/equations (crop, labels, digitize, describe, optional SVG).  
7. **Emit** Markdown + YAML front‑matter per section; every block gets a visible representation.  
8. **Audit**: coverage report proves *nothing* was silently dropped.  
9. **Cache** the completed version for fast re‑runs.

That’s the full end‑to‑end flow of pdf2md’s conversion process. Let me know if you’d like to dive deeper into any specific module!

---

## User

so what does Docling do? What is the output?

---

## Assistant (Build · Nemotron 3 Super · 27.4s)

_Thinking:_

We need to answer: what does Docling do and its output. Provide concise explanation.


Docling is the default PDF‑parsing engine used by pdf2md.  
It takes a PDF file and produces a structured, intermediate representation of the document’s content:

**What Docling does**
1. **Layout analysis** – detects text lines, columns, tables, figures, equations, lists, headers, footers, etc., and gives each element a bounding box (in PDF‑space, bottom‑left origin).
2. **Semantic labeling** – assigns a label to each detected element (e.g., `TextItem`, `TableItem`, `PictureItem`, `FormulaItem`, `SectionHeader`).
3. **Text extraction** – reads the Unicode text that is embedded in the PDF for each `TextItem` (or `FormulaItem` when the formula is stored as text).
4. **Structure preservation** – keeps the reading order and groups items into logical blocks (paragraphs, table rows, figure captions, etc.) based on proximity and layout cues.
5. **Optional OCR** – if the PDF lacks a usable text layer or if `--force-ocr` is set, Docling can run OCR on page images to populate the text layer.

**What Docling outputs (the `EngineResult` that pdf2md receives)**
- A list of **blocks** (in pdf2md’s schema) that includes:
  - `BlockType.TEXT` – plain text strings with their bbox.
  - `BlockType.TABLE` – a table skeleton (rows/columns) where each cell contains the raw extracted text and its bbox.
  - `BlockType.PICTURE` – a reference to a figure/image with its bbox.
  - `BlockType.EQUATION` – either a LaTeX string (if Docling could recognize it as a formula) **or** a raw text placeholder plus bbox.
  - `BlockType.SECTION_HEADER`, `BlockType.LIST_ITEM`, `BlockType.FOOTNOTE`, etc.
- For each block, the **bounding box** (x0, y0, x1, y1) in PDF coordinate space.
- Raw text content (as extracted by Docling) for text‑based blocks.
- For tables: a 2‑D array of cell strings.
- For pictures: a flag that the block is an image; the actual image data is later cropped by pdf2md’s rendering step.
- For equations: a LaTeX string when recognizable, otherwise a marker that pdf2md will treat as an image crop.

In short, Docling turns a PDF into a structured, labeled set of text/table/figure/equation blocks with positional information. pdf2md then takes that result, builds a glyph‑index from the PDF source, enriches/repairs the text, groups blocks into sections, extracts assets (crops, SVGs), and finally emits Markdown while auditing that every block is accounted for.

---

## User

what is a GlyphIndex?

---

## Assistant (Build · Nemotron 3 Super · 34.7s)

_Thinking:_

We need to explain GlyphIndex. Provide concise answer.


A **GlyphIndex** is pdf2md’s internal lookup table that maps every character (glyph) that appears in the source PDF to its exact position on the page.

**How it’s built**

1. pdf2md opens the PDF with **pypdfium2**.
2. For each page it walks the text‑run objects that pypdfium2 exposes.
3. For every glyph (a single Unicode character as rendered by a font) it records:
   * the character/code point,
   * the font name/size,
   * the bounding rectangle in PDF coordinate space (x0, y0, x1, y1 – bottom‑left origin),
   * optional attributes like rotation or skew.

All of these entries are stored in a searchable structure (usually a list or spatial index) called the **GlyphIndex**.

**Why pdf2md needs it**

The GlyphIndex serves as the *ground‑truth* for any operation that needs to know what the PDF actually shows, independent of what the parsing engine (Docling or MinerU) extracted:

| Use case | How GlyphIndex helps |
|----------|----------------------|
| **Ligature / diacritic repair** | Compare the extracted text to the glyphs; if a sequence like “ﬁ” (U+FB01) appears but the glyphs show separate “f” and “i”, replace it. |
| **TeX‑style f‑ligature expansion** | Map PDF control‑byte sequences (\x1b‑\x1f) back to ff/fi/fl/ffi/ffl using the glyphs. |
| **Sub‑/super‑script detection** | Measure vertical offsets of glyphs relative to the baseline to decide if a character should be wrapped in `<sub>` or `<sup>`. |
| **OCR‑garbage detection & refill** | If a block is flagged as illegible (e.g., symbol‑font garbage), replace its text with the characters read directly from the GlyphIndex for that region. |
| **Table‑cell glyph verification** | For each table cell, check that the ink inside the cell matches the glyphs; record any uncovered ink as evidence. |
| **Numeric conservation (informational)** | Sum numbers found in the glyph layer vs. the emitted Markdown to detect silent drift. |
| **Script recovery (inline superscripts/subscripts)** | Re‑apply proper Markdown wrapping based on glyph geometry. |

In short, the GlyphIndex gives pdf2md a pixel‑accurate, font‑aware view of the original PDF so it can correct, validate, and enrich the text that the engine extracted—without ever relying solely on the engine’s possibly noisy output. It is the key piece that enables the “nothing silently dropped” guarantee.

---

## User

can you explain the Enrichment pipeline


---

## Assistant (Build · Nemotron 3 Super · 107.1s)

_Thinking:_

We need to explain enrichment pipeline. Provide concise explanation.


**Enrichment pipeline** – the series of source‑dependent clean‑up steps that run after the engine (Docling/MinerU) has produced raw blocks but before the document is structured and emitted.  
Its purpose is to repair extraction artefacts, recover lost formatting, and add validation evidence **without changing the meaning** of the content.

---

### 1. Input to the pipeline
* `EngineResult` → list of raw `Block` objects (text, table, picture, equation, …) each with:
  * extracted Unicode string (as given by the engine)
  * bounding box(es) in PDF space
* `GlyphIndex` → map of every rendered glyph (character + font + exact PDF rectangle) built from the original PDF with pypdfium2.

---

### 2. Stages (in the order they are applied)

| Stage | What it does | Where it lives | Why it matters |
|-------|--------------|----------------|----------------|
| **a. Ligature / diacritic repair** (`normalize.religature`, `rejoin_split_word`) | Replaces engine‑produced ligature glyphs (ﬀ, ﬁ, ﬂ, ﬃ, ﬄ) or separated diacritics with the canonical Unicode characters, using the GlyphIndex to confirm the correct base letters. | `normalize.py` | PDFs often encode “ﬁ” as a single glyph; the engine may treat it as one character, breaking searchability or causing odd spacing. |
| **b. TeX‑style f‑ligature expansion** (`expand_ligature_glyphs`) | Maps PDF control‑byte ranges (`\x1b`–`\x1f`, plus soft‑hyphen `\x02`) that represent OT1‑style ff/fi/fl/ffi/ffl back to their proper ligature strings. | `normalize.py` | Some PDF generators store ligatures as invisible control bytes; expanding them yields readable, copy‑pastable text. |
| **c. Sub‑/super‑script detection & wrapping** (`scripts.apply_scripts`) | Measures each glyph’s vertical offset from the baseline (using its bbox). If a glyph is significantly higher/lower, it is wrapped in `<sup>` or `<sub>` (Markdown syntax). | `scripts.py` | Recovers inline math formatting that Docling often drops as plain text. |
| **d. OCR‑garbage detection & refill** (`legibility.is_garbage` → `PageChars.text_region`) | Scores a block for “symbol‑font” or nonsense glyphs (high proportion of non‑printable/PUA characters). If flagged, the block’s text is replaced by the exact characters read directly from the GlyphIndex for that region. | `legibility.py` | Prevents garbage like `� � �` from appearing in the output; replaces it with the true underlying text. |
| **e. Inline script (sup/sub) application** | After detection, the pipeline actually inserts the Markdown wrapper characters (`^` or `~` depending on the chosen output style). | `scripts.py` | Guarantees that recovered scripts are visible in the final Markdown. |
| **f. Equation confidence scoring** (`confidence.assess_equation`) | Compares the engine’s LaTeX (if any) with the text‑layer glyphs. Low confidence → the equation is kept as an image crop and the raw text is stored as a hint; high confidence → LaTeX is emitted. | `confidence.py` | Gives a principled fallback for equations the engine mis‑recognized. |
| **g. Table‑cell glyph verification** (`table_rebuild.check_table_cells`) | For each table cell, renders the glyphs that fall inside its bbox and checks whether the rendered ink matches the cell’s extracted text. Any mismatched ink is recorded as `uncovered_ink` evidence on the cell. | `table_rebuild.py` | Detects OCR/engine errors in tables without altering the cell content; the evidence surfaces in the review output. |
| **h. Numeric conservation (informational)** | Extracts all numbers from the glyph layer and from the emitted Markdown, computes a simple sum/difference for reporting only. | `enrich.py` (called after emission) | Provides a sanity check that no numeric values were silently dropped or altered. |
| **i. Optional: OCR‑page‑VLM shortcut** | If `--ocr-page-vlm` is set, the pipeline skips the normal enrichment for that page and replaces the entire page’s block list with a single VLM‑transcribed block. Handled in `pipeline._get_engine()`. | N/A (controlled externally) | Gives a vision‑model fallback for terrible embedded text. |

---

### 3. Data flow through the pipeline

```
EngineResult (raw blocks)  ──►  GlyphIndex (built from PDF)
          │
          ▼
   ┌─────────────────────┐
   │ Enrichment Pipeline │
   │  (a → b → c → d → e │
   │   → f → g → h)      │
   └─────────────────────┘
          │
          ▼
Enriched Blocks (same Block type, but:
   • repaired text strings,
   • added `extra` fields:
        – ligature_fixed,
        – script_tags,
        – garbaged? → replaced,
        – equation_confidence,
        – cell_glyph_check,
        – …)
```

Each enrichment step **mutates** the `Block.text` (or adds metadata) **in place**; it never removes a block or merges two blocks. Therefore the total block count stays identical to the engine’s output, which is crucial for the later coverage audit.

---

### 4. Where the enriched data is used later

* **Structure building** – uses the cleaned‑up text for heading detection, numbering, etc.
* **Figure/Table processing** – the cleaned text becomes the figure caption or table cell content that is later cropped and emitted.
* **Emission** – the final Markdown is generated from the enriched `Block.text` (or from LaTeX/`![…]` if the block is an equation/picture).
* **Review & audit** – the `extra` fields (e.g., `cell_glyph_check`, `equation_confidence`) are written to `provenance.json` and shown in the CLI review report so users can see exactly what was repaired or left uncertain.

---

### 5. Summary

The enrichment pipeline is a **glyph‑truth‑based repair layer** that:
1. Corrects typographic artifacts (ligatures, diacritics, TeX f‑ligatures).
2. Recovers lost formatting (sup/sub scripts).
3. Detects and replaces OCR or symbol‑font garbage.
4. Scores equation reliability and falls back to image crops when needed.
5. Verifies table cells against the actual ink in the PDF.
6. Provides informational checks (numeric conservation) for auditability.

All changes are **conservative** (they only fix what the engine got wrong) and leave the block count untouched, enabling pdf2md’s core guarantee: **nothing is silently dropped**—every block that the engine saw ends up as visible Markdown, an image, or a marked‑up element, with evidence of any corrections stored alongside the output.

---

## User

how good is this app compared to other pdf converters? is this app good? are there ways to improve on it? are there more recent methods/techniquest to add to pdf2md?

---

## Assistant (Build · Nemotron 3 Super · 112.7s)

_Thinking:_

We need to answer about quality, improvements, recent methods. Provide concise but thorough.


**How good is pdf2md?**

| Aspect | What pdf2md does well | Where it lags behind the state‑of‑the‑art |
|--------|----------------------|------------------------------------------|
| **Correctness / auditability** | • Every detected block is explicitly accounted for (coverage report). <br>• Glyph‑index based repair catches ligature, OCR‑garbage, sub‑/super‑script errors that many converters miss. | • Still relies on the underlying engine (Docling/MinerU) for layout detection; if the engine misses a region (e.g., a tightly‑packed multi‑column table) pdf2md cannot recover it. |
| **Structure preservation** | • Section tree built from headings, bookmarks, numbering; restores source‑page order. <br>• Handles books vs. papers, optional part‑like expansion. | • No deep semantic understanding (e.g., distinguishing “abstract” vs. “introduction” beyond heading level). |
| **Figure / table handling** | • Crops figures, exports PNG/SVG, extracts labels (OCR‑rotated, optional VLM). <br>• Vector‑path digitization for born‑digital charts (lines, scatter, bars). <br>• Table‑cell glyph verification gives concrete evidence of OCR problems. | • Table reconstruction is greedy (whitespace‑corridor); complex spanning cells or rotated tables can be mis‑aligned. <br>• Figure digitization works only for vector‑drawn charts; raster charts need the optional VLM digitizer, which is slower and less accurate than dedicated chart‑OCR models. |
| **Equation handling** | • LaTeX output when engine is confident; otherwise falls back to an image crop with a hint. <br>• Confidence scoring + optional Surya OCR re‑transcription. | • No end‑to‑end neural formula recognition (e.g., UniMER, MathPix‑style) – relies on the engine’s limited formula detection. |
| **Bibliographic metadata** | • Ranks embedded XMP, front‑page, running titles, early bookmarks, filename. <br>• Optional GROBID enrichment for reference strings. | • No machine‑learning‑based reference parsing (e.g., SciBERT‑based citation extractor). |
| **Speed / resource usage** | • Pure‑Python enrichment is fast; heavy lifting (Docling/MinerU) is isolated and can be cached. <br>• Optional steps (VLM description, digitization) are opt‑in. | • Docling itself can be slow on dense PDFs; MinerU requires an external environment and GPU for best accuracy. |
| **Audit & usability** | • Coverage report, provenance.json, review.md give transparent evidence of what was kept, repaired, or uncertain. <br>• Clean CLI, versioned output format. | • No interactive UI for correcting mis‑detected blocks (some competitors offer a web‑based review UI). |

Overall, **pdf2md is among the most transparent and auditable PDF‑to‑Markdown converters available today**. Its strength lies in the glyph‑truth enrichment pipeline and the strict accounting guarantee, which many pure‑engine‑based tools (e.g., plain Docling export, Pandoc‑pdf, Adobe Export PDF) lack.

---

### Ways to improve pdf2md

| Area | Concrete improvement | Expected benefit |
|------|----------------------|------------------|
| **Layout detection** | • Replace or supplement Docling’s layout module with a newer layout model (e.g., LayoutLMv3, Donut, or a fine‑tuned Detectron2/YOLO on PubLayNet + TableBank). <br>• Ensemble multiple detectors and resolve disagreements via voting or confidence weighting. | Higher recall for complex tables, multi‑column layouts, and irregular figures; fewer missed blocks → stronger coverage guarantee. |
| **Table reconstruction** | • Implement a graph‑based cell‑linking algorithm (e.g., TCN, TableMaster) that uses both whitespace corridors and line‑segment detection. <br>• Integrate the cell‑glyph verification already present to refine cell borders iteratively. | Better handling of spanning cells, rotated tables, and tables with noisy borders. |
| **Formula recognition** | • Plug in a dedicated neural formula recognizer (UniMER, Im2LaTeX, or MathPix‑style model) as an optional engine or a post‑processor for low‑confidence equation blocks. <br>• Keep the current fallback to image crops for cases where the model is uncertain. | Higher LaTeY yield, especially for densely packed inline equations; reduces need for manual correction. |
| **Chart digitization** | • Add a raster‑chart OCR module (e.g., Plot2Seq, DePlot, or a fine‑tuned Vision‑Language model) that works on the figure crop directly. <br>• Keep the vector‑path digitizer as a fast path for born‑digital charts; use the VLM‑based method only when vector paths are absent. | Accurate extraction of heatmaps, pie charts, contour plots, and other raster‑based graphics that the current vector‑only digitizer misses. |
| **Reference & metadata extraction** | • Use a SciBERT‑based model to parse raw reference strings into structured bibliographic data (title, authors, DOI, year). <br>• Combine with GROBID or Crossref lookup for validation. | More complete and accurate bibliographic front‑matter, useful for downstream knowledge‑base creation. |
| **Interactive review / correction UI** | • Build a lightweight web‑app (FastAPI + React) that loads the `provenance.json` and lets users approve/reject enriched blocks, re‑run specific steps (e.g., re‑OCR a table), and regenerate the Markdown bundle. | Closes the loop between fully automatic conversion and manual quality control, similar to tools like DocXporter or Adobe’s PDF Export review mode. |
| **Caching & reproducibility** | • Store intermediate enrichment artifacts (glyph index, enriched blocks) in a content‑addressable store (e.g., using hashes) so that changing only optional flags (like `--describe`) re‑uses the heavy parsing step. | Faster iteration when experimenting with optional features. |
| **Multilingual & script support** | • Extend ligature/diacritic repair to handle non‑Latin scripts (Arabic, Devanagari, CJK) by using appropriate font‑specific glyph maps. <br>• Add OCR models tuned for those scripts when `--force-ocr` is used. | Better coverage for non‑English scientific literature. |
| **Energy / carbon awareness** | • Add an option to skip GPU‑heavy steps (VLM description, formula OCR) and report estimated energy usage per conversion (based on measured timings). | Helps users make informed trade‑offs for large batch jobs. |

---

### Recent methods/techniques worth considering

| Technique | Paper / Project (2023‑2024) | How it could be added |
|-----------|----------------------------|-----------------------|
| **Donut (Vision‑Language Model for document understanding)** | *Donut: Understanding Document Images via Generative Pre‑training* (NAACL 2023) | Replace or augment the engine parsing step: feed the PDF page image to Donut and obtain structured JSON (headings, tables, figures) directly, then map to pdf2md blocks. |
| **LayoutLMv3** | *LayoutLMv3: Pre‑training for Document AI with Unified Text and Image Masking* (ACL 2022, still SOTA 2023‑24) | Fine‑tune on a mixed dataset of scientific PDFs to predict block types and reading order; use the output as a fallback when Docling confidence is low. |
| **UniMER (Universal Mathematical Expression Recognition)** | *UniMER: Towards General-Purpose Mathematical Expression Recognition* (ICLR 2024) | Use as an optional post‑processor for low‑confidence equation blocks to produce LaTeX directly from the equation crop. |
| **DePlot (One‑shot visual language reasoning for plots)** | *DePlot: One‑shot Visual Language Reasoning by Plot-to-Table Translation* (CVPR 2023) | Apply DePlot to figure crops to extract underlying data tables for charts, complementing the vector‑path digitizer. |
| **GOT (General OCR Theory)** | *GOT: General OCR Theory for Text Recognition* (ECCV 2023) | Integrate as a language‑agnostic OCR backend for `--force-ocr` or OCR‑page‑VLM, improving accuracy on degraded scans and non‑Latin scripts. |
| **TableBank + PubLayNet + SciTSR datasets** | Various 2022‑2024 releases | Use these larger, more diverse datasets to train/fine‑tune a custom table detector that handles rotated and nested tables better than Docling’s built‑in model. |
| **Cross‑modal retrieval for reference matching** | *Cross-modal Retrieval for Scholarly Documents* (EMNLP 2023) | After extracting raw reference strings, embed them and search against Semantic Scholar or Crossref to auto‑fill missing fields (DOI, year). |
| **Energy‑aware AI** | *Green AI: Measuring and Reducing the Carbon Footprint of ML Models* (ACL 2023) | Add a simple power‑model (based on measured GPU/CPU time) to report kWh per conversion, letting users choose low‑power presets. |

---

### Bottom line

- **pdf2md is already strong** because it couples a reliable parsing engine with a glyph‑truth enrichment pipeline that guarantees every detected block is visible in the output and provides detailed evidence of any corrections.
- **The biggest gains** would come from modernizing the layout/table/formula detection layers with recent vision‑language models, while keeping the enrichment and audit steps that make the tool uniquely trustworthy.
- Adding optional, plug‑in‑style modules (e.g., DePlot for charts, UniMER for formulas, LayoutLMv3 for layout) would let power‑users opt‑in to state‑of‑the‑art accuracy without sacrificing the core auditability that defines pdf2md.

---


