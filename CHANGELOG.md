# Changelog

All notable changes to pdf2md. Format loosely follows
[Keep a Changelog](https://keepachangelog.com). The output format is versioned
separately by `FORMAT_VERSION` in `schema.py`; a breaking change there is noted
here.

## [Unreleased]
### Added
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

### Fixed
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

### Internal
- Table grid→markup assembly moved to `tables.py` (`build_html`/`build_gfm`);
  GFM header row derived from cell header flags instead of assuming row 0;
  spanning tables no longer persist a flattened GFM. `PageChars` reads page text
  in one call instead of one per character (faster on large books).

### Changed
- Output format → **0.2**: front-matter key `engine` renamed to
  `engine_versions` (`engine` is reserved by Quarto's YAML front-matter).

### Fixed
- Multi-line equations with alignment markers (`&`, `\\`) are wrapped in
  `\begin{aligned}` so KaTeX/MathJax render them instead of throwing.
- Unmapped Greek-letter font glyph names (`/Delta1`→Δ, `/Pi1`→Π, `/Sigma1`→Σ,
  and the rest of the Greek alphabet) are normalized to Unicode in text, tables,
  and captions.

### Known limitations
- Inline sub/superscripts (citation markers, chemistry multiplicities and
  counts) are still flattened by Docling's text extraction — Docling's `script`
  formatting is per-text-item, not per-inline-run, so they can't be recovered
  reliably without guessing. Deferred.

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
