# Contributing

pdf2md is maintained by one person, best-effort. Issues and pull requests are
welcome, but there's no response-time guarantee and some may sit.

## Working on it

- `uv sync`, then `uv run pytest` (the fast suite needs no models).
- `uv run pytest -m integration` runs real Docling; set `PDF2MD_TEST_PDF`.
- `uv run python scripts/benchmark.py <pdfs|dir>` reports per-document time,
  pages/sec, and coverage; add `--no-formula` to compare, `--json` to record.
- After a change that reconverts the corpus, put the latest version of every
  pinned source directly under one output root, then run the accuracy gates:
  `scripts/qa.py <corpus-out> --check` (labels-free regression vs
  `tests/qa_baseline.json`, fails on a hard-invariant regression),
  `scripts/eval_equations.py <corpus-out> --check` (labelled equation accuracy),
  and `scripts/eval_accuracy.py <corpus-out> --check`
  (source-checked structural facts and profile signals).
- After retrieval, chunking, prompt, or bundle-format changes, run the paired
  command in `docs/agent-benchmark.md`. Its `--check` gate requires bundle
  accuracy to meet PDF-page accuracy and at least 20 percent fewer input tokens.
- Match the conventions in `CLAUDE.md` (dataclasses, no Pydantic, stdlib logging,
  the engine seam stays the only place that imports docling).
- `README.md` records current product decisions, methods, and deferred boundaries.
  Candidate accuracy work lives in `docs/accuracy-improvement-notes.md`; the completed
  quality and ingestion workstream lives in `docs/quality-and-ingestion-plan.md`; the
  earlier decision log remains in `docs/archive/PROJECT_PLAN.md`.

## Output is a versioned contract

The markdown layout and front-matter keys are what downstream tools parse. Any
change that would break a naive parser must bump `FORMAT_VERSION` in `schema.py`
and add a `CHANGELOG.md` entry.

## Versioning

Pre-1.0: the public surface is the CLI plus a small library entrypoint
(`convert_file` / `convert_dir`); everything else is internal and may change.
