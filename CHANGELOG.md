# Changelog

All notable changes to pdf2md. Format loosely follows
[Keep a Changelog](https://keepachangelog.com). The output format is versioned
separately by `FORMAT_VERSION` in `schema.py`; a breaking change there is noted
here.

## [Unreleased]
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
