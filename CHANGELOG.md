# Changelog

All notable changes to pdf2md. Format loosely follows
[Keep a Changelog](https://keepachangelog.com). The output format is versioned
separately by `FORMAT_VERSION` in `schema.py`; a breaking change there is noted
here.

## [Unreleased]

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
