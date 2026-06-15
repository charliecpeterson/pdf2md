# Contributing

pdf2md is maintained by one person, best-effort. Issues and pull requests are
welcome, but there's no response-time guarantee and some may sit.

## Working on it

- `uv sync`, then `uv run pytest` (the fast suite needs no models).
- `uv run pytest -m integration` runs real Docling; set `PDF2MD_TEST_PDF`.
- `uv run python scripts/benchmark.py <pdfs|dir>` reports per-document time,
  pages/sec, and coverage; add `--no-formula` to compare, `--json` to record.
- Match the conventions in `CLAUDE.md` (dataclasses, no Pydantic, stdlib logging,
  the engine seam stays the only place that imports docling).
- `PROJECT_PLAN.md` records the design decisions and what's intentionally
  deferred — check it before proposing a structural change.

## Output is a versioned contract

The markdown layout and front-matter keys are what downstream tools parse. Any
change that would break a naive parser must bump `FORMAT_VERSION` in `schema.py`
and add a `CHANGELOG.md` entry.

## Versioning

Pre-1.0: the public surface is the CLI plus a small library entrypoint
(`convert_file` / `convert_dir`); everything else is internal and may change.
