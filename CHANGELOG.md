# Changelog

All notable changes to pdf2md. Format loosely follows
[Keep a Changelog](https://keepachangelog.com). The output format is versioned
separately by `FORMAT_VERSION` in `schema.py`; a breaking change there is noted
here.

## [Unreleased]
### Added
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
