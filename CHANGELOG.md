# Changelog

All notable changes to pdf2md. Format loosely follows
[Keep a Changelog](https://keepachangelog.com). The output format is versioned
separately by `FORMAT_VERSION` in `schema.py`; a breaking change there is noted
here.

## [Unreleased]
### Added
- `--describe`: vision-model descriptions of image crops (opt-in). Figures, image-
  fallback tables, and image-backed equations are opaque PNGs to a text consumer;
  `describe.py` sends each crop to an OpenAI-compatible vision endpoint (`vlm_base_url`
  / `vlm_model`, so localhost ollama/vLLM/LM-Studio or a remote host with no code
  change) and emits a labelled description block below the image — except an equation,
  whose transcription rides as its existing hint (and never overrides math-OCR). The
  crop stays authoritative; the kind-aware prompt forbids inventing values. Needs the
  `describe` extra (just the `openai` client) and a reachable endpoint. Validated
  against ollama `qwen3-vl:8b`: accurate figure descriptions (the Transformer
  architecture and attention diagrams read correctly). Descriptions are cached at the
  doc level by (model, kind, crop bytes), so a `--force` re-run reuses them instead of
  paying the vision model again.
- Cross-reference links: a "see section 9.2" reference is turned into a link to that
  heading (in the same file or another), resolved against the actual headings so a
  number with no matching section is left as plain text. Dotted numbers only (a bare
  "section 9" is ambiguous with a chapter), and code fences are skipped so a console
  session that prints "section 9.2" stays verbatim.
- `index.md` contents file for split books: every section file linked, with its
  chapters and numbered sections nested beneath as in-file anchor links, built from
  the actual emitted (deduped/merged/nested) headings. One navigation entry point and
  a map of where everything lives, for a human or a model. Single-file papers are
  unaffected.
- Heading hierarchy for split books. Bookmarks only mark Parts, so chapters and
  sections arrived flat (everything `#`) with the bookmark title duplicated by the
  page's own heading ("# I Overview" then "# Part I Overview"; "# Chapter 1" then
  "# GRASP2018"). `emit._heading_plan` now drops a heading that restates the file
  title (normalised so "Part IV: Issues …" matches the bookmark "IV Issues …"),
  merges a bare "Chapter N" / "Part N" label into the title heading after it
  ("## Chapter 1: GRASP2018"), and deepens body headings under the file-title H1 so
  Part → Chapter → numbered section nest as `#`/`##`/`###`. Single-file papers are
  unchanged.

### Fixed
- A crashed conversion no longer wedges the cache. The version number is assigned
  before output is written and provenance.json is written last, so an interrupted run
  (e.g. `--describe` without the `openai` extra, which died after rendering crops but
  before emit) left a `v<n>` dir with assets but no markdown — and the cache counted
  it, so every later run reported "cached" and no-opped. `latest_version`/`next_version`
  now ignore versions without provenance.json (prune still removes them). And the
  vision client is built before the engine runs, so a missing `describe` extra fails
  fast instead of after a full conversion. A cached doc with `--describe`/`--transcribe`
  now says those passes need `--force`.
- Figure captions in a broken font stayed symbol-font garbage: `enrich_figures` only
  ligature-repaired them, never font-decode-refilled. The docling adapter now carries
  the caption's own bbox (`FigureRef.caption_bbox`), and enrich refills a garbled
  caption from the pdfium glyph layer ("❋✐❣✉/a114❡ ✸✳✶" -> "Figure 3.1: ...").

### Added
- Preformatted-content handling for console transcripts and ASCII-art tables
  (software manuals like GRASP). These are monospace text whose meaning is the line
  layout: the engine flattens a console session into one run-on paragraph or
  mis-grids an ASCII table, and (with the broken font) emits dingbats.
  - `preformat.py` (`is_preformatted`): banner/rule-line detection (a line that is
    almost entirely `*`/`-`/`=`/`_`/`#`), plus literal `|` column rows for tables.
    Banner detection tested at zero false positives on a clean paper.
  - `PageChars.text_lines`: pdfium's native bounded text, with line breaks preserved
    (unlike `text_region`'s flat join). `normalize.clean_preformatted` cleans it
    while keeping the lines.
  - `enrich.py`: a `code` block (Docling labels console sessions as code) is refilled
    from the pdfium glyph layer line-preserved; a prose block whose re-read carries
    banner lines (console the engine mislabelled) is marked `preformatted`; a "table"
    that is really ASCII-art (`TableData.preformatted`) keeps its line layout. All
    three emit as fenced code blocks instead of flattened prose or mangled grids.
  - Validated on GRASP: console I/O sessions and energy-level listings now render
    readably with structure intact; the clean control paper gains zero code fences.
- Font-decode repair extended to table cells. The prose refill (below) left table
  cells in symbol-font garbage because cells aren't prose blocks and the `illegible`
  metric is prose-only. Now `enrich._table_grid` refills garbage cells from the same
  pdfium glyph layer (shared `enrich.refilled` helper), forcing a rebuild even when
  no scripts are present. Fixing this surfaced a coordinate bug: **table-cell bboxes
  are TOPLEFT origin** (unlike block prov bboxes, which are BOTTOMLEFT), so the
  docling adapter (`_cell_bbox`) now flips Y to page-bottom — which also repairs the
  glyph alignment the table sub/superscript overlay had been getting wrong. `qa.py`
  gains an `illegible_table_rows` gated invariant (rendered GFM rows that are garbage)
  so a broken-font table can't pass silently the way GRASP's did. `refilled` no longer
  replaces a cell with an empty pdfium reading (which would lose the cell). Validated:
  GRASP TOC/data tables now readable, atkins-50page tables unaffected.
- Legibility signal + font-decode repair ("Trust, measured"). A PDF whose embedded
  font lacks a usable ToUnicode CMap extracts as symbol-font garbage (dingbats and
  `/aNNN` glyph-name tokens — `❆ ♣/a114❛❝/a116✐❝❛❧` for "A practical guide"), which
  Docling's default backend trusts; pypdfium2 decodes the same file correctly.
  - `legibility.py`: a pure `score_legibility` / `is_garbage` over the
    symbol-substitution signal (no vowel-ratio/dictionary check, which would
    false-flag dense chemistry/math notation).
  - `enrich.py` refills any garbage prose block from the pdfium glyph layer
    (`PageChars.text_region`), stamping `text_source="pdfium"`; only swaps when
    pdfium is actually cleaner, so a truly undecodable block stays flagged.
  - `emit.py` flags a block that's still garbage after the refill (visible marker +
    `illegible` coverage tally + front-matter `illegible_blocks`) instead of
    passing it off as readable prose — the blind spot that let a 67%-dingbat doc
    report lossless. `scripts/qa.py` gains an `illegible` gated invariant and now
    audits split/book outputs (it was skipping any doc without `document.md`).
    **`FORMAT_VERSION` 0.4 -> 0.5** (optional `illegible_blocks` front-matter key;
    `illegible` count added to the coverage report).
  - Validated on the GRASP2018 manual: illegible prose blocks 1653 → 0, text
    readable, bibliographic metadata recovered. Known residual: the broken font's
    ﬀ/ﬁ/ﬂ ligature glyphs also lack a ToUnicode mapping, so pdfium drops them
    ('e cient' for 'efficient'); legible but imperfect, far better than dingbats.
- Diacritic-split repair: words the text layer fractures where a diacritic was
  dropped ('Löwdin' -> 'Lo wdin', 'Schädel' -> 'Scha del') are rejoined, reusing
  the ligature machinery's vocabulary validation. Guarded on the *stem* (left
  piece): join only when the stem isn't a word the document uses on its own but
  the joined form is — so a consistent split that leaks the broken tail into the
  vocabulary can't defeat it, and real pairs ('of the', 'data set') are never
  fused. Corpus audit: 4 joins, all correct author names, zero false positives.
- Labelled equation accuracy harness: `tests/equation_labels.json` (10
  hand-checked equations, born-digital and scanned) plus
  `scripts/eval_equations.py`, which scores the engine LaTeX and the math-OCR
  transcription against ground truth. This is the measured complement to the
  labels-free `scripts/qa.py`. It immediately earned its keep: it showed the
  transcription beats the engine 4/4 on clean equations, and (over 6 scanned
  equations) that raising the transcription crop DPI is a net regression on
  degraded scans and that cross-DPI self-consistency voting isn't worth its cost —
  so both were rejected by measurement rather than shipped on a hunch.
- Multi-pass equation transcription (`--transcribe`, opt-in). Re-reads each
  image-backed equation crop with a local math-OCR model (Surya, the maintained
  successor to texify) and emits the result as the equation's text hint — turning
  an OCR/garbled equation's wrong LaTeX (the scanned `c^5`-for-`c^3` case) into a
  real transcription. The crop image stays the authoritative source, so a bad
  transcription is never worse than before. `transcribe.py` is a small seam:
  `Transcriber` (anything with `transcribe(image)->latex`) plus a lazy-imported
  `SuryaTranscriber` whose only version-specific surface is one `_run` method;
  with `surya-ocr` absent the pass is skipped. Install with the `transcribe`
  extra. The Surya call (`FoundationPredictor` -> `RecognitionPredictor`,
  `ocr_with_boxes` + `math_mode`) is verified against surya-ocr 0.17's API.
- Image-crop fallback for low-confidence equations. Some journals (ACS) draw math
  glyph-by-glyph out of reading order, so the embedded text layer is scrambled
  token soup *before* pdf2md touches it, and the previous text-layer recovery
  would replace good vision-LaTeX with that soup. Now any equation whose
  extraction is suspect (confidence below `RECOVER_BELOW`) is cropped to an image
  — the one fully faithful representation — and the image is emitted as the
  authoritative source. A best-effort text hint rides below it: the clean
  text-layer reading when it is in geometric reading order and shares enough with
  the LaTeX (`PageChars.reading_disorder`, `SCRAMBLED_ABOVE`, `HINT_MIN_CONF`),
  otherwise the vision LaTeX. Because the crop is authoritative, the hint's
  selection is cosmetic and the disorder heuristic carries no correctness risk.
  **`FORMAT_VERSION` 0.3 -> 0.4.** This replaces the earlier text-layer "recover"
  / "text layer reads" behaviour: the same accurate characters are still present
  (as the hint), now backed by the faithful image.
- Equation confidence via text-layer cross-check (`confidence.py`). Docling's
  formula model transcribes the equation *image* to LaTeX and makes character
  errors (`AQCC`->`AQC/CC`, `pVTZ`->`pVTEZ`, dropped equation numbers); for
  born-digital PDFs the embedded text layer holds the correct characters. Each
  equation's LaTeX is scored against the text-layer reading of its bbox. When
  they disagree, the LaTeX is suspect: if the text layer is faithful (clean, no
  dropped Greek glyphs) its reading is recovered as the emitted content;
  otherwise the LaTeX is kept with a low-confidence marker. A per-equation marker
  appears inline and a `equation_confidence` summary (checked / low_confidence /
  min) in YAML front-matter. **`FORMAT_VERSION` 0.2 -> 0.3** (new front-matter
  key; recovered equations change body content). Heuristic, not a proof:
  symbol-heavy multi-line equations can score low even when correct, so the flag
  is conservative (review, not certainty). Recovered text is flat (no script
  detection — it misfires on equation layout); character-accurate but exponents
  are not raised.
- Inline sub/superscript recovery on born-digital pages (`scripts.py`): detected
  from pypdfium2 glyph geometry (smaller + off-baseline), rendered as
  `<sub>`/`<sup>` in prose and table cells — molecular subscripts, term-symbol
  multiplicities, variable indices, affiliation/citation markers. Tables with
  detected scripts are rebuilt from Docling's cell grid. Disable with
  `--no-scripts`. The overlay only *inserts* tags, never alters characters, so a
  mis-detection is cosmetic, never data loss. Line grouping keeps raised/dropped
  scripts attached to their line; superscripts need only be raised (no size
  test); descenders (g,j,p,q,y) are excluded from subscripts; an adjacent sign is
  absorbed into a script run (so `mol⁻¹` keeps its minus when alignment allows).
  Known ceiling: scripts are overlaid onto Docling's text, so an exponent Docling
  renders differently from the raw glyphs (a spaced hyphen vs a raised minus) is
  recovered only partially.
- Scanned/OCR page handling. A page with no embedded text layer (a full-page
  scan image) was the one input where the safety net inverted: nothing could be
  cross-checked, so equation confidence came back `None` and `None` meant "trust
  the LaTeX" — which on a scan is an OCR mis-transcription (a Rayleigh-Jeans law
  emitted with `c^5` instead of `c^3`), presented as authoritative. Now a page
  with no text layer is detected as OCR-sourced: its equations are always
  image-backed (the scan pixels are the only ground truth, the OCR LaTeX rides
  along as an unverified hint), its tables are cropped rather than rendered from
  OCR cells, and front-matter carries `ocr_scanned_pages` so a consumer knows the
  text is a transcription to verify against the images.

### Fixed
- Two-column bleed in equations is now caught. Docling's formula model sometimes
  weaves adjacent-column prose into an equation's LaTeX (`\text{or} & & \text{where}`),
  and the old one-directional confidence missed it: the bled tokens only inflated
  the LaTeX, leaving recall at 1.0 so the equation was trusted. Confidence is now
  the two-way agreement (`min(recall, precision)`) between the LaTeX and the
  single-column bbox text layer; precision drops when the LaTeX carries content the
  bbox doesn't, so the equation is flagged and image-backed (the crop, being the
  clean single column, is the authoritative source) rather than presenting the
  bled LaTeX as fact.
- Parsed table content no longer vanishes when Docling mislabels the block.
  TOC-style pages come through as type `other` yet still carry parsed cells; emit
  rendered tables only for type `TABLE`, so the data was orphaned and the block
  dropped. emit now renders a block's `TableData` wherever it exists, regardless of
  the block's label. A table with genuinely no cells (and a bbox) instead gets an
  `![table](crop.png)` image fallback, the same one equations use, so a failed
  table is never silently lost either.
- Publication year is corrected upward from an arXiv id (in the filename or page
  text) when the first page-1 year is an older dataset/citation year — the
  Transformer paper read "2014" (its WMT dataset) instead of 2017. A year already
  on the page that is newer than the arXiv submission (a journal year) is kept.
- Ligatures Docling splits with a stray space (`di ff erent`, `con fi guration`,
  some publishers decompose ﬀ/ﬁ/ﬂ and pad it) are rejoined. `normalize.religature`
  only merges when the result reconstructs a word pdfium's reading of the page
  actually contains, so a true boundary (`off the`, `cutoff value`, `electric
  field`) is never fused — the validation against the document's own words, not a
  heuristic, is what makes it safe. The vocabulary is pdfium's reading of every
  page (a word kept whole anywhere confirms a split of it elsewhere), built once
  and only when a split is seen, so clean papers pay nothing. Most of one paper's
  135 splits resolved with zero corruption; the unconfirmed rest are left split
  rather than guessed.
- Front-matter omits null-valued keys (`doi`, `authors` when unknown). Quarto's
  YAML schema rejects `doi: null` for a string field and fails the whole render.
- Unverified-equation markers no longer read as a verdict that the equation is
  *wrong*. The cross-check measures whether the extraction could be confirmed, not
  its correctness, and on scrambled-text papers it reads ~0.00 for equations whose
  LaTeX is perfect. The per-equation score is dropped from the marker (now
  "equation extraction unverified — the image below is the authoritative source"),
  and the front-matter summary reports `equations: {total, image_backed}` instead
  of a misleading `low_confidence` count.
- Unbalanced `\left`/`\right` (Docling emitting two `\right` for one `\left` in a
  bra-ket) made KaTeX throw; `emit._balance_delims` drops the auto-sizing commands
  when the pair is unbalanced so the bare delimiters still render.
- Script detection no longer corrupts numeric values: a digit raised or dropped
  *inside* a number (table cells turning 191.4 into ¹91.4, 251.5 into 25¹.5) was
  the worst failure mode for a source-of-truth corpus. `scripts._unsplit_numbers`
  keeps a script only when it is a clean trailing group of a numeric run (a real
  exponent or citation like 191.4⁶⁹); a left-superscript multiplicity (²A₁) is
  kept because the digit precedes a letter, not a digit.
- Equation confidence no longer false-flags (and needlessly recovers) a correct
  equation because of `\exp`/`\max`/`\text{}`: the LaTeX tokenizer kept command
  *structure* but dropped the visible text those commands carry, so a faithful
  `\frac` equation scored ~0.3 and got recovered to flat text, losing the
  fraction bar. The tokenizer now keeps `\text{}`/`\mathrm{}` content and
  text-operator names.
- A low-confidence equation that can't be safely recovered (it has Greek the text
  layer drops) now surfaces the text-layer reading as a `text layer reads: …`
  cross-reference beside the kept LaTeX, so the accurate characters (a dropped
  second term, a `ccCA` the vision model read as `ccA`) are still available.
- Equations no longer render as a wall of empty gaps when Docling encodes
  trailing PDF whitespace as a runaway tail of `\quad`/control-spaces, or pads a
  lost alignment column with repeated empty `& \quad` cells. `emit._tidy_math`
  strips and collapses this spacing noise before wrapping; real `\\` line breaks
  and genuine multi-column equations are left intact.
- A garbled equation with unbalanced `{`/`}` (Docling misreading a `}` as `)`,
  say) is brace-padded so KaTeX renders it instead of dumping the raw TeX source
  as literal `\[...\]` text. The underlying OCR garble is unchanged; only the
  renderability is fixed.
- Orphaned combining marks (a lone U+0338 long solidus overlay Docling emits for
  a struck-through or dropped glyph) no longer surface as stray `/` lines:
  `normalize.strip_orphan_combining` removes them, and a block left empty by the
  strip is dropped rather than printed as a slash. Legitimate base+mark pairs
  (≠, accented letters) are kept.
- Multi-line equations with alignment markers (`&`, `\\`) are wrapped in
  `\begin{aligned}` so KaTeX/MathJax render them instead of throwing.
- Unmapped Greek-letter font glyph names (`/Delta1`→Δ, `/Pi1`→Π, `/Sigma1`→Σ,
  and the rest of the Greek alphabet) are normalized to Unicode in text, tables,
  and captions.

### Internal
- Keystone refactor increment 2: the engine is now pure translation. Moved table
  and figure verification (ligature repair + inline sub/superscript rebuild from
  glyph geometry) out of the Docling adapter into the engine-agnostic `enrich.py`
  (`enrich_tables` / `enrich_figures`). The adapter no longer imports pypdfium2 —
  it ships each table's structured cells as a transient `RawTable` on
  `EngineResult`, and `enrich` (one `GlyphIndex` pass, shared with block
  enrichment) does the religature/script work. Removes the duplicate pdfium pass
  and means a second engine inherits all verification for free. Verified
  behaviour-preserving: a real reconvert leaves every table's GFM/HTML and figure
  caption byte-identical; the script-rebuild path is covered by a unit test.
- Quality-audit / regression harness (`scripts/qa.py` + `tests/qa_baseline.json`).
  Reads existing outputs (no reconversion) and reports per-document signals — the
  things we keep fixing: dropped content, split-ligature residue, unbalanced
  equation LaTeX, image-backing, scanned-page count, losslessness. `--check` gates
  on the hard invariants (lossless / dropped / ligature / unbalanced must not
  regress) and reports the rest as drift; `--update` refreshes the baseline. The
  labels-free half of the accuracy story: it can't say the LaTeX is correct, but
  it catches the day it silently gets worse, instead of finding out one paper at a
  time. (It already caught a stale scanned output that predated OCR detection.)
- Verification layer extracted from the Docling adapter into an engine-agnostic
  `enrich` stage (`enrich.py`: `GlyphIndex` + `enrich_blocks`), run by the pipeline
  on the `EngineResult`. The block-level scripts/ligatures/equation-cross-check/OCR
  logic no longer lives behind the engine seam, so a second engine inherits it and
  it is now unit-testable with a fake glyph source (the adapter's `_blocks` was
  0%-covered). Behaviour-preserving — verified the same per-equation confidences on
  a real PDF. The table/figure paths still build their own glyph index in the
  adapter; a follow-up moves them too and removes the duplicate pass.
- Table grid→markup assembly moved to `tables.py` (`build_html`/`build_gfm`);
  GFM header row derived from cell header flags instead of assuming row 0;
  spanning tables no longer persist a flattened GFM. `PageChars` reads page text
  in one call instead of one per character (faster on large books).

### Changed
- Output format → **0.2**: front-matter key `engine` renamed to
  `engine_versions` (`engine` is reserved by Quarto's YAML front-matter).

## [0.1.0] - 2026-06-14
Initial release, rebuilt from the abandoned `docsmcp` MCP server.

### Added
- Lossless PDF → markdown conversion (library + `pdf2md` CLI): text and tables
  as markdown, equations as LaTeX, hard visuals cropped and referenced, and a
  per-document coverage audit that emits a visible marker for anything it can't
  represent.
- Docling engine behind a swap seam; `--no-formula` toggle to trade equation
  enrichment for speed.
- Logical-section splitting (papers single-file, bookmarked books per section),
  `pypdfium2` figure crops, YAML front-matter with bibliographic metadata and
  `format_version` (output format **0.1**).
- Offline model use via `pdf2md models pull --local-dir` + `local_model_dir`.
- `pdf2md prune` to drop old output versions.
- Fast deterministic test suite plus an opt-in real-Docling integration harness.
