# Changelog

All notable changes to pdf2md. Format loosely follows
[Keep a Changelog](https://keepachangelog.com). The output format is versioned
separately by `FORMAT_VERSION` in `schema.py`; a breaking change there is noted
here.

## [Unreleased]
### Added
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
