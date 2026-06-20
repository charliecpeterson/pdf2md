# pdf2md

Lossless PDF → markdown converter. Turns academic PDFs (and bookmarked books)
into faithful markdown: text and tables as markdown, equations as LaTeX, and
anything that can't be represented as text (figures, charts, complex diagrams)
cropped to an image and referenced. Nothing is silently dropped, anything the
tool can't represent emits a visible marker.

Built around [Docling](https://github.com/docling-project/docling) for the heavy
lifting; pdf2md is the orchestration on top: logical-section splitting,
bibliographic front-matter, figure cropping, and a per-document coverage audit.

## Install

```bash
uv sync
uv run pdf2md models pull              # first run downloads Docling's models
uv run pdf2md models pull --local-dir ~/pdf2md-models   # offline/reproducible snapshot

# optional: local math-OCR (Surya) for re-transcribing image-backed equations
uv sync --extra transcribe
```

## Use

```bash
# A single PDF (writes out/<doc_id[:16]>/v<n>/document.md + assets/)
uv run pdf2md convert paper.pdf

# A whole directory (batch; one bad PDF never aborts the run)
uv run pdf2md convert ~/papers --out ~/library

# Skip equation→LaTeX enrichment: ~10-60x faster, equations become flagged
# markers instead of LaTeX. Use for large or equation-light documents.
uv run pdf2md convert book.pdf --no-formula

# Skip inline sub/superscript recovery (a little faster on large docs).
uv run pdf2md convert book.pdf --no-scripts

# Re-transcribe image-backed equations with local math-OCR (needs the transcribe
# extra; slow). Best on scanned or equation-heavy documents.
uv run pdf2md convert scan.pdf --transcribe

uv run pdf2md coverage paper.pdf    # per-document coverage report (no re-run)
uv run pdf2md prune --keep 2        # keep the newest N versions per document
uv run pdf2md version
```

From Python:

```python
from pdf2md.pipeline import convert_file
result = convert_file("paper.pdf")
print(result.coverage.lossless, result.md_files)
```

## Output

```
out/<doc_id[:16]>/v<n>/
  document.md          # paper: one file
  00_front.md ...      # book (bookmarked, large): one file per top-level section
  assets/<id>_p<n>.png # cropped figures, referenced by relative path
  provenance.json      # source of truth: blocks, bboxes, coverage, lineage
```

- `doc_id` is the SHA-256 of the source bytes; re-running the same file is a
  no-op unless `--force`. New runs create `v<n+1>`, never overwriting.
- Front-matter carries `format_version`, bibliographic metadata, and the engine
  + model versions that produced the file.
- `PDF2MD_OUT` sets the output root (default `./out`).

## Known limits (v1)

- **Equation enrichment is slow** (minutes for equation-heavy papers). Use
  `--no-formula` to trade LaTeX for speed.
- **Suspect equations are image-backed.** When the engine's LaTeX disagrees with
  the page's text layer (or there's no text layer — a scan), the equation is
  cropped to a faithful image that becomes the authoritative source, with the
  text as a flagged hint. `--transcribe` upgrades that hint via local math-OCR.
- **Sub/superscripts** are recovered from glyph geometry on born-digital pages
  (`5f²` stays `5f²`), on by default (`--no-scripts` to disable). A residual
  ceiling remains where the engine renders an exponent unlike the raw glyphs.
- **Book splits land at top-level bookmarks** (e.g. Parts, not chapters) and
  crops include journal furniture (logos, banners). Refinements, not yet done.

## Dependencies

- `docling` — conversion engine (layout, tables, formula→LaTeX, bboxes)
- `pypdfium2` — page rendering + figure cropping (permissive license)
- `typer` — CLI
- `pyyaml` — front-matter
