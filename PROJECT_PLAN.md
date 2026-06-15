# Project Plan: pdf2md (rework of docsmcp → PDF-to-markdown converter)

> Living document. Updated incrementally by the deep-planner skill.
> Last updated: 2026-06-14
> Current phase: **Phase 2 (hardening) essentially done.** CI (green),
> Dependabot, prune, benchmark harness, repo docs/LICENSE. PyPI declined for now;
> smart reconvert-stale deferred to a real Docling upgrade.
>
> Timeline: open-ended side project; sustainability over speed.

## Goal
Rebuild docsmcp into a best-effort PDF→markdown converter: faithful markdown
for text and tables, cropped images referenced for visuals that are hard to
get right (charts/figures/complex layout), output split to mirror the source
structure. "Whatever produces the best markdown from a PDF wins."

## Archetype
- **Primary**: library/CLI — a converter invoked as a library and from a CLI,
  run in batch over folders of documents.
- **Secondary**: pipeline — ingestion over hundreds of scanned docs pulls in
  pipeline concerns (idempotency, partial-failure, resumability, schema
  evolution of the output format, backfill/re-run when the engine improves).
- **Expertise calibration**: User is an HPC sysadmin + computational chemist
  + tool builder. OCR/VLM/document-parsing methodology is *joint* — conductor
  leads with researched, cited recommendations; user defers on model choice
  but owns the definition of "correct enough" for chemistry tables/figures.
  Engineering, output-format, and architecture decisions: conductor leads,
  user pushes back.

## Scope
### In scope
- PDF (and image/scan) → markdown conversion as a library + CLI.
- Faithful text and heading extraction; OCR for scanned pages.
- Numeric/simple tables rendered as markdown tables.
- Cropped-image extraction + reference-in-markdown for hard visuals.
- Output structure mirroring source (per-page files, folder hierarchy).

### Out of scope
- MCP server / ~40 tool surface (deferred — becomes an optional layer later).
- Hybrid search index (SQLite FTS5 + sqlite-vec), reranker.
- Heavy verification apparatus (dual-engine disagreement, per-block VLM verify).
- Chart-data extraction to "exact numbers" / reproduction code (later version;
  crop-and-reference is the backbone instead).

## Decision Log
- **[2026-06-13] Deliverable shape: focused converter, not MCP server**
  - **Choice**: Rework as a PDF→markdown converter (library + CLI). MCP,
    search, and verification become separate optional layers added later.
  - **Why**: The converter core is the unproven, value-bearing part; the goal
    hinges on it. Bolting search + 40 MCP tools onto unproven output is what
    made the prior version feel like vaporware.
  - **Alternatives considered**: Keep "MCP server that also converts" and
    rework in place — rejected; couples the unproven core to a large surface.
  - **Revisit if**: converter output is validated and the user wants agent
    access or corpus search.

- **[2026-06-13] Governing principle: lossless by default**
  - **Choice**: Every piece of information in the PDF lands in the output in
    some form; nothing is silently dropped. Text/tables → markdown, equations
    → LaTeX, anything not faithfully representable as text → cropped image +
    reference. Failures and uncertainty emit a visible marker, never an
    omission.
  - **Why**: For a chemistry/research corpus, a silently missing table row or
    equation is worse than a crop, because the user can't know it's gone.
  - **Revisit if**: never (this is the project's acceptance bar).

- **[2026-06-13] Engine: wrap an existing one, single default behind a thin seam**
  - **Choice**: Orchestrate an existing engine rather than build a parser.
    One default implementation behind a narrow internal interface; no plugin
    framework until a real second engine is needed.
  - **Why**: OCR/layout/table parsing is solved by maintained tools; the
    rework's value is the output layer + batch handling. Premature plugin
    abstraction is the over-engineering trap.
  - **Alternatives considered**: build our own (rejected, years of work);
    pluggable framework up front (rejected, abstraction with one caller).
  - **Revisit if**: validation shows no single engine covers the corpus.

- **[2026-06-13] Default engine chosen empirically: Marker provisional**
  - **Choice**: Build against Marker provisionally; run a real-document
    bake-off of Marker vs Docling vs MinerU and lock the winner. License is a
    bake-off criterion (see Open Questions).
  - **Why**: All three handle academic layout/tables/equations locally; the
    best one for this corpus is an empirical question, not a decree.
  - **Revisit if**: bake-off favors a different engine.

- **[2026-06-13] Output: logical-section files mirroring document structure**
  - **Choice**: One `.md` per paper; one per chapter for books; folder
    hierarchy mirrors logical structure. Page numbers preserved as inline
    markers + front-matter, not as file boundaries.
  - **Why**: Strict per-page splitting severs tables and sentences across
    page breaks and hurts machine-readability, which is the actual goal.
  - **Alternatives considered**: strict per-page files (rejected, severs
    content); single flat file per doc (kept as the paper case).
  - **Revisit if**: a downstream consumer needs strict page files.

- **[2026-06-13] Table / equation / figure representation**
  - **Choice**: Hybrid tables (GFM markdown for simple, HTML `<table>`
    fallback for merged/spanning cells). Equations as LaTeX (`$`/`$$`),
    trusting the engine in v1 with accuracy measured by the validation
    harness. Figures/charts/complex diagrams crop-and-reference into an
    `assets/` subfolder; captions captured as adjacent text. Crops are the
    lossless backbone; any text/data extraction is additive.
  - **Why**: Readable where possible, faithful where necessary; crops
    guarantee no visual information is lost.
  - **Revisit if**: engine equation accuracy fails validation (→ targeted
    re-check, first candidate VLM use).

- **[2026-06-13] Provenance: lightweight front-matter**
  - **Choice**: YAML front-matter per file (source filename, content hash,
    page range, engine + version, conversion date). Drop heavy per-block bbox
    provenance from the old version.
  - **Why**: The old bbox provenance existed to feed verification, which is
    deferred. Keep only what aids organization and reproducibility.

- **[2026-06-13] Pipeline: content-hash identity, idempotent cache, isolate-and-flag**
  - **Choice**: `doc_id` = SHA-256 of source bytes; deterministic versioned
    output location; re-run is a no-op unless `--force`. A failed page emits a
    visible marker + logged warning and the document continues. Outputs are
    reproducible-from-source derived artifacts (not hand-edited).
  - **Why**: Carried from the old design (sound). Isolate-and-flag upholds the
    lossless principle; reproducible outputs make corpus-wide re-conversion on
    engine upgrade a clean operation.
  - **Revisit if**: user needs to preserve manual corrections (see Deferred).

- **[2026-06-13] Platform: M2 Ultra primary, CUDA-capable**
  - **Choice**: Target the M2 Ultra (192 GB) as the primary runtime; keep the
    code CUDA-compatible so the RTX 4090 works too. No MLX-only lock-in.
  - **Why**: Always-available, ample memory, fine for overnight batch; speed
    isn't critical for a personal converter. Staying CUDA-capable costs little
    and preserves options.

- **[2026-06-13] Packaging: CLI + library, no VLM in v1 default path**
  - **Choice**: Ship as a `uv` tool / pipx CLI plus an importable library.
    Pure structural engine in v1; model weights download on first run. No VLM
    in the default path.
  - **Why**: Smallest thing that works; VLM uses (equation verify, chart
    extraction) stay deferred until validation proves they're needed.

- **[2026-06-13] Inputs: PDF only in v1**
  - **Choice**: Accept PDF only. Loose images (PNG/JPG), DjVu, and EPUB are
    deferred; user converts other formats to PDF first for now.
  - **Why**: Least surface to maintain; matches the dominant corpus.
  - **Revisit if**: scanned books arrive as image/DjVu often enough to hurt.

- **[2026-06-13] Bibliographic metadata in v1 (lightweight)**
  - **Choice**: Extract title/authors/year/DOI from the PDF's own metadata and
    first page into front-matter. Defer CrossRef/DOI network lookups.
  - **Why**: Makes the corpus organizable (and seeds a future library/search)
    without adding a network dependency or reviving the old apparatus.

- **[2026-06-13] Clean rebuild, keep the repo**
  - **Choice**: Treat `src/docsmcp/` and existing `out/` as throwaway; rebuild
    internals from scratch. Keep the git repo; rewrite CLAUDE.md and README to
    match the new converter (the current CLAUDE.md describes the old MCP design
    and is now stale).
  - **Why**: Full rework with no output worth migrating.

- **[2026-06-13] Rename: docsmcp → pdf2md**
  - **Choice**: Rename the project, package, import path, and CLI to `pdf2md`.
    The old name implied an MCP server that no longer exists.
  - **Why**: Cheap to change now, expensive once published (PyPI name, import
    path, shell aliases). The name should describe the converter.

- **[2026-06-13] Content handling defaults**
  - **Choice**: Strip running headers/footers as boilerplate but keep page-
    number markers; preserve footnotes as markdown footnote syntax (`[^n]`);
    capture figure captions as text adjacent to each crop; emit a batch run
    report (converted / flagged pages / failed docs) after a folder ingest.

- **[2026-06-14] Engine: Docling (research-confirmed)**
  - **Choice**: Docling (MIT code / Apache-2.0 weights, LF AI & Data governed,
    native MLX) as the single default behind the swap seam. MinerU /
    PaddleOCR-VL deferred as optional higher-accuracy table/equation backends.
  - **Why**: Best architectural fit (structured doc model with bboxes, reading
    order, PNG figure crops — exactly what our split/crop/provenance layer
    consumes), cleanest license for an open-source target, lowest abandonment
    risk. Equation/table accuracy trails MinerU/PaddleOCR but its classic
    TableFormer pipeline is solid; add a second backend only if validation
    shows it's needed.
  - **Alternatives considered**: Marker (ruled out — GPL-3.0 + revenue-gated
    OpenRAIL weights, incompatible with permissive open-source); MinerU (best
    accuracy but custom license, weaker figure-crop reputation); PaddleOCR-VL
    (Apache + high accuracy but PaddlePaddle-runtime tax on Apple Silicon);
    olmOCR-2 (ruled out — NVIDIA-only local, no figure bboxes).
  - **Revisit if**: validation shows Docling equation/table accuracy is
    insufficient on the corpus (→ wire MinerU as a second backend).

- **[2026-06-14] License: MIT, permissive open-source**
  - **Choice**: Publish under MIT (simplest permissive). PDF render/crop via
    pypdfium2 (BSD/Apache), NOT PyMuPDF (AGPL). Confirm Docling's transitive
    deps contain no AGPL/copyleft before first release.
  - **Why**: User confirmed permissive open-source intent. MIT is the boring,
    widely-understood choice; Apache's patent grant is overkill here.

- **[2026-06-14] Stack (research-confirmed)**
  - **Choice**: Typer CLI (Click underneath, matches type-hint style); PyPI
    wheel via `uv tool` / `pipx`, models lazy-downloaded on first run behind an
    explicit `pdf2md models pull`, never bundled; config via stdlib `tomllib` +
    frozen dataclass (project convention is no Pydantic); stdlib logging +
    NullHandler in the library, handler only at the CLI entrypoint; testing via
    pytest + syrupy snapshots + OlmOCR-style structural-fact assertions over a
    ~12-doc fixture corpus.
  - **Why**: Boring, maintained, sustainability-first choices for a solo
    maintainer; matches the engineering profile.

- **[2026-06-14] Readiness items folded into v1**
  - **Choice**: (1) `format_version` stamped in front-matter + provenance so
    the output is a versioned contract for downstream consumers. (2) Provenance
    records generator versions (pdf2md + Docling + model id) for full lineage.
    (3) Per-document coverage report (flagged blocks / image-fallbacks / failed
    pages) as the runtime enforcement of the lossless invariant. (4) Model
    revision pinned + checksum + local-model-dir override (survives host churn,
    unblocks air-gapped HPC). (5) 0.x policy: public surface = CLI + a small
    documented library entrypoint; everything else internal; output-format
    breaks bump `format_version` + changelog note. (6) Document-level
    isolate-and-flag for batch runs (one crashing PDF never aborts the batch).
  - **Why**: Each is cheap now and expensive to retrofit; together they make
    the lossless guarantee enforceable and the corpus re-convertible.

- **[2026-06-14] Heading depth from section numbering (evidence-driven)**
  - **Choice**: Infer heading depth from leading numbering (`3.5.1` → depth 3)
    in `outline.heading_depth`, falling back to Docling's level then 1.
  - **Why**: Validation showed Docling's heading level flattens everything to
    `#`. Numbering inference restores proper `#`/`##`/`###` nesting on papers
    and books. Verified on the attention paper.

- **[2026-06-14] Formula enrichment is a toggle, not always-on (evidence-driven)**
  - **Choice**: `do_formula_enrichment` config flag + `--no-formula` CLI flag,
    default on. Off → equations become visible flagged markers (lossless) and
    conversion is ~10–60x faster.
  - **Why**: Validation on real papers showed formula enrichment dominates
    runtime (10–20 min for 12–17pp equation-heavy papers vs ~20s without).
    Books at 500–1000+ pages are infeasible with it on.

- **[2026-06-14] Security: low threat model, hardening deferred**
  - **Choice**: Document "not a sandbox for untrusted PDFs"; rely on
    pypdfium2 hardening. No malicious-PDF containment in v1.
  - **Why**: User's corpus is his own papers, not adversarial uploads.
  - **Revisit if**: the tool is ever pointed at untrusted/third-party PDFs.

## Deferred Register
| Item | Why deferred | Trigger to revisit |
|------|--------------|--------------------|
| MCP server + tool surface | Layer on top of proven output | Converter validated on real docs |
| Hybrid search / RAG index | Separate product from conversion | User needs corpus search |
| Verification apparatus (disagreement, VLM verify) | Expensive; spot-check not gate | User needs to trust specific tables |
| Chart-data → exact numbers / repro code | Hallucination-prone, partly ill-posed | A later version after crops work |
| Preserve manual corrections across re-runs | Outputs are derived artifacts in v1 | User starts hand-editing outputs |
| VLM equation verification | Don't build speculatively | Engine equation accuracy fails validation |
| CI matrix (Py versions × MLX/CUDA/CPU) + snapshot determinism | Phase 2 hardening | Before inviting external contributors |
| Automated release (tag CI + PyPI trusted publishing) + dep scanning | Phase 2 hardening | First public release |
| Performance benchmark harness + `prune` for old versions | Phase 2 hardening | Disk growth or perf regression bites |
| Deprecation windows, CONTRIBUTING, docs beyond README | Phase 2 hardening | Approaching 1.0 / external users |
| Malicious-PDF hardening / sandboxing | Low threat model | Tool points at untrusted PDFs |

## Open Questions
- **Default engine** — resolved empirically via a real-document bake-off of
  Marker vs Docling vs MinerU (roadmap Phase 1). Criteria: layout/table/
  equation fidelity on the user's docs, OCR quality on the scanned subset,
  local-on-Mac performance, and **license** (Marker has commercial
  restrictions; Docling is MIT; MinerU is AGPL — matters if docsmcp is ever
  open-sourced).
- **Validation harness** — the 5-10 real-document set and the metrics that
  define "best effort" (text fidelity, table structure, equation accuracy).
- Hidden-decision items under discussion in Phase 4 (input formats,
  bibliographic metadata, boilerplate/footnote handling, batch reporting).

## Research Summary
- **Stack**: Typer (CLI), pypdfium2 (PDF render/crop, permissive vs PyMuPDF's
  AGPL), `uv tool`/`pipx` + lazy model download, `tomllib`+dataclass (config),
  stdlib logging+NullHandler, pytest+syrupy+structural-fact assertions.
- **Engine (build-vs-buy = buy)**: Docling chosen. Ranked alternatives:
  MinerU (top table/formula accuracy, custom license), PaddleOCR-VL (Apache,
  high accuracy, PaddlePaddle tax), Marker (ruled out: GPL + gated weights),
  olmOCR-2 (ruled out: NVIDIA-only, no bboxes).
- **Bibliographic metadata**: a separate tool family. Offline embedded +
  first-page heuristic in v1; CrossRef (lightweight) and GROBID (heavy Java)
  deferred.
- **Prior-art / differentiation**: No existing end-to-end tool produces
  structured logical-section markdown + cropped-referenced figures +
  bibliographic front-matter + a lossless guarantee. Every engine stops at one
  flat .md per PDF with inline crops. pdf2md's value is the orchestration
  layer (section-splitting, front-matter, lossless-verify), not a new engine.
  The "nothing silently dropped" invariant is the sharpest, most durable
  differentiator — every engine fails it quietly (incomplete crops,
  word-splitting, Nougat hallucination on 5%+ of non-arXiv pages).
- **Things to avoid**: bundling models in the wheel; `uvx` ephemeral runs for
  a model-heavy tool; PyMuPDF as a published dep (AGPL); loguru inside library
  code; global edit-distance as the conversion-quality metric (superseded by
  structural-fact checks); dynaconf (overkill).

## Production-Readiness Audit
### Addressed in v1
- Distribution, idempotency, partial-failure (page + document level).
- Output contract: `format_version` stamp, generator-version lineage in
  provenance, per-document coverage report (runtime lossless enforcement).
- Upstream-dep hardening: pinned model revision + checksum + local-model-dir.
- API policy: 0.x "public = CLI + small library entrypoint; rest internal."
### Deferred (see Deferred Register, → Phase 2 hardening)
- CI matrix + snapshot determinism across accelerators; automated release +
  dependency scanning; performance/resource benchmark harness + disk `prune`;
  deprecation windows, CONTRIBUTING, docs beyond README; malicious-PDF
  hardening.
### Not applicable
- Lag/freshness and cost model — user-invoked local batch tool, no service,
  no paid API.

## Architecture

Data flow: **PDF → engine → structure → (render ∥ emit) → coverage → disk**,
driven by a thin `pipeline` orchestrator with a Typer `cli` on top.

### Components
- **`engine`** — calls Docling and translates its output into pdf2md
  dataclasses. The only module that knows Docling exists; behind an `Engine`
  Protocol so MinerU/PaddleOCR-VL can slot in later. Pure: PDF bytes →
  in-memory `EngineResult`.
- **`structure`** — flat block stream + reading order + section source →
  `Section` tree that determines the output file hierarchy.
- **`render`** — crops figure/complex-visual bboxes to PNG via pypdfium2 into
  `assets/`. Owns the bbox→pixel math (Y-flip ported from old `image.py`).
- **`emit`** — serializes the `Section` tree to logical-section `.md` files:
  front-matter, inline page markers, GFM/HTML tables, LaTeX equations,
  relative asset links.
- **`coverage`** — reconciles the full block inventory against emitted
  artifacts; unresolved/low-confidence blocks get a VISIBLE marker + a report
  row. The lossless invariant made executable.
- **`pipeline`** — `convert_file` / `convert_dir`: doc_id, versioned dirs,
  page- and document-level isolate-and-flag, writes `provenance.json`.
- **`cli`** — Typer surface; installs the only logging handler; no logic.

### Data model (dataclasses in `schema.py`, no Pydantic)
`Document` owns a recursive `Section` tree + flat `Block` / `TableData` /
`FigureRef` lists referenced by id, plus `Provenance` (tool + engine + model
versions, format_version, source sha256, `section_source` used) and
`CoverageReport` (counts + `CoverageFlag` list). `Block.coverage_status` ∈
{emitted, cropped, flagged, dropped} replaces the old verify field.
`provenance.json` is the on-disk source of truth; `.md`/`assets` are derived.

### Module layout
```
pdf2md/
  __init__.py   schema.py   config.py   cache.py   pipeline.py
  engines/ base.py docling.py
  structure.py  outline.py  bookmarks.py
  render.py  emit.py  tables.py  metadata.py  coverage.py
  models.py  logging.py  cli.py
```

### CLI surface
- `pdf2md convert PATH [--force] [--out DIR] [--config FILE]` (auto-detects
  file vs directory; prints coverage summary; non-zero exit on hard failure).
- `pdf2md models pull [--revision REV] [--local-dir DIR]` (only network path;
  pinned revision + checksum).
- `pdf2md coverage PATH` (re-print report from provenance; no re-run).
- `pdf2md version` (pdf2md + Docling + model id).

### Trust boundaries
Local-first single-user tool. PDF parsing is the primary untrusted surface
(hardening deferred; isolate-and-flag contains crashes that raise, not
segfaults — documented). Model download is the only network boundary (pinned
revision + checksum; local-dir override for air-gapped). FS writes confined to
`out_root()`; section titles slugified before becoming filenames. No
authn/authz/multi-tenancy.

### Resolved architectural decisions (Phase 8)
1. **Crops re-cut via pypdfium2**, not reused from Docling — engine-independent,
   DPI-controlled across future backends.
2. **No usable bookmarks or heading outline → single `document.md`** (graceful
   degrade, never a garbage deep hierarchy).
3. **Coverage flag threshold**: inline VISIBLE marker only when a block is
   dropped or below a config-tunable confidence floor; merely-low-confidence
   emitted blocks go in the report, not inline. Defaulted conservative.
4. **`format_version` bump** when a front-matter key is removed/renamed or the
   file layout changes in a way that breaks a naive parser; changelog noted.
5. **Per-page geometry carried per page**; `render` never assumes uniform page
   size.
6. **Re-run is a no-op until `--force`**, but the CLI warns when the cached
   version's engine/model id differs from current. Bulk re-convert is Phase 2.

### Open architectural questions (deferred, low-stakes)
- Complex-table HTML rendering vs crop-and-reference fallback (Phase 1 uses
  crop fallback; HTML rendering is Phase 3).
- Whether a changed engine/model id should auto-invalidate the cache (currently
  warn-only; revisit with the Phase 2 bulk re-convert command).

## Roadmap

### Phase 1: Working lossless converter (prove the thesis on real docs)
- [x] `engines/base.py` Engine protocol + `EngineResult`; `engines/docling.py` adapter → pdf2md schema _(verified on a real arXiv PDF: 200 blocks, 4 tables, 6 figures w/ captions+bboxes)_
- [x] `schema.py` dataclasses (Document, Section, Block, BBox, TableData, FigureRef, Provenance, CoverageReport)
- [x] `cache.py` (doc_id = sha256, versioned dirs, latest_version) ported from old design
- [x] `pipeline.py` convert_file + convert_dir; page- and document-level isolate-and-flag; write provenance.json
- [x] `bookmarks.py` + `structure.py` + `outline.py`: section tree (bookmarks → heading outline → single document.md). Split gated on page count (SPLIT_MIN_PAGES=40) so papers stay single-file.
- [x] `render.py`: pypdfium2 bbox crops → assets/ — wired into pipeline, 6/6 crops on test doc
- [x] `emit.py` + `tables.py`: section .md files, YAML front-matter (format_version + lineage), page markers, GFM tables + caption, LaTeX equations (`$$`), relative asset links
- [x] `metadata.py`: embedded PDF metadata + first-page heuristic _(weak: year/authors imperfect; CrossRef deferred)_
- [x] `coverage.py`: inventory reconciliation, visible markers, per-doc report
- [x] `models.py`: `pull()` warms the cache or downloads a 1.2GB local snapshot (`--local-dir`); `local_model_dir` config loads Docling from it via `artifacts_path`. Verified offline (`HF_HUB_OFFLINE=1`) end-to-end. Per-model revision/checksum pinning isn't exposed by Docling — the immutable local snapshot is the reproducibility mechanism instead.
- [x] `config.py` (tomllib + frozen dataclass), `logging.py` (NullHandler in lib)
- [x] `cli.py` (Typer): convert, models pull, coverage, version — all verified
- [x] Enabled Docling formula enrichment so equations → LaTeX (was empty/flagged without it)
- [x] Tests: 20 fast deterministic tests (synthetic fixtures, no Docling, 0.12s) + syrupy snapshot on emit + opt-in `integration` marker that runs real Docling against `PDF2MD_TEST_PDF` (the validation harness). Chose synthetic fixtures over a committed PDF corpus (copyright/size; keeps CI fast).
- [x] Confirm no AGPL transitive dep (PyMuPDF) — _Docling does NOT require PyMuPDF; pypdfium2 crop path verified_
- [x] Rename + cleanup: pyproject, CLAUDE.md, and README rewritten for pdf2md; `src/docsmcp/` deleted; memory updated

**Out of scope for this phase**: HTML complex-table rendering (crop fallback instead), book chapter-splitting refinement, everything in Phase 2/3.
**Effort**: a few focused weekends. This is the real deliverable.

### Phase 2: Hardening (readiness-audit gaps) — in progress
- [x] CI (GitHub Actions): fast suite on Python 3.11/3.12/3.13, docling skipped
  to stay lean (and to enforce lazy engine imports). Accelerator matrix
  (MLX/CUDA) deferred — no GPU runners, low value for a personal tool.
- [x] Dependency scanning — Dependabot (uv + github-actions, weekly).
- [x] `prune` command for old versioned outputs (`--keep N`, `--dry-run`).
- [x] CONTRIBUTING + deprecation/versioning policy; CHANGELOG started; LICENSE.
- [x] Performance benchmark harness (`scripts/benchmark.py`): per-doc time,
  pages/sec, coverage; `--no-formula` to compare, `--json` to track over time.
- [~] PyPI publishing — **declined for now**. Install-from-repo
  (`uv tool install git+…`) is enough for personal use; revisit if others need
  `pip install pdf2md`.
- [ ] Bulk re-convert / reconvert-stale — basic `convert <dir> --force` already
  re-runs a corpus; the smart "only re-run docs whose engine/model changed"
  version is the open arch question, deferred until a Docling upgrade needs it.

**Out of scope for this phase**: accuracy/breadth work (Phase 3).
**Depends on**: Phase 1 shipped and used enough to know real perf/disk behavior.
**Effort**: incremental, as real use exposes the gaps.

### Phase 3: Accuracy and breadth (evidence-gated)
- [ ] HTML complex-table rendering (upgrade from crop fallback)
- [ ] Second engine backend (MinerU / PaddleOCR-VL) behind the seam — ONLY if Phase 1 validation shows Docling accuracy insufficient
- [ ] CrossRef metadata enrichment when a DOI is present
- [ ] Non-PDF inputs (images / DjVu / EPUB) for the scanned-book slice
- [ ] Originally-deferred layers if still wanted: MCP server, search/RAG, VLM equation verification, chart-data extraction

**Depends on**: Phase 1's validation results to decide what's actually worth building.
**Effort**: per-item, only where evidence justifies it.

## Dependencies & Risks
- Depends on a fast-moving upstream OCR/VLM ecosystem; engine choice must be
  swappable so the tool survives model churn.
- VLM-based extraction can produce fluent-but-wrong tables/numbers; the
  crop-and-reference backbone is the hedge against silent data corruption.

### Validation findings (2026-06-14, 4 real domain papers, all lossless)
- **Strong**: equations → faithful LaTeX (bra-ket, tensors), tables → clean GFM,
  real figures/schemes crop completely, front-matter excellent when the PDF
  embeds metadata (full author list, correct title/notation/year).
- **Perf risk (biggest)**: formula enrichment dominates runtime (10–20 min for
  12–17pp). Mitigated by the `--no-formula` toggle. 500–1000pp books need
  `--no-formula` and likely an overnight run; the scanned solutions manual also
  needs OCR (slower still) — books deferred pending a decision.
- **Fidelity gap**: sub/superscripts flatten (`5f 2` not `5f²`) — Docling text
  limitation; data present but unformatted. Matters for chemistry. Candidate
  Phase 3 fix.
- **Noise**: journal logos / graphical-abstract banners get cropped as figures.
  Candidate filter (min size / first-page banner heuristic) later.
- **Heading hierarchy was flat** (`#` for everything) — FIXED via numbering
  inference; now nests correctly.
- **Greek-letter font glyph names** (`/Delta1`, `/Pi1`, `/Sigma1`) leaked as raw
  text in chemistry/physics PDFs — FIXED via `normalize.unglyph` (→ Δ Π Σ).
- **Equations with alignment** (`&`, `\\`) broke KaTeX in bare `$$` — FIXED by
  wrapping in `\begin{aligned}`. (Some equations Docling extracts are genuinely
  truncated/malformed; those still won't render — a Docling limitation.)
- **`engine` front-matter key** collided with Quarto — renamed `engine_versions`
  (output format → 0.2).
- **Sub/superscripts STILL flatten** (citation markers, `²Π`, `Si₂H₂`). Docling's
  `script` formatting is per-text-item, not per-inline-run, so inline scripts
  can't be recovered without unreliable guessing. Open; deferred.

### Book validation (2026-06-14, Atkins Physical Chemistry 8e, 1085pp)
- Born-digital book with `--no-formula`: **8.3 min for 1085pp**, lossless,
  bookmarks→split produced 16 files mirroring top-level structure. Feasible.
- **Split granularity too coarse**: split at top-level bookmarks = *Parts*, so
  "Part 2" is a 19k-line file; chapters live a level deeper. The deferred
  "book chapter-splitting refinement" — needs descending to a chapter-sized
  bookmark level. Phase 3.
- **Equation-dense books**: with `--no-formula`, ~3100 equations became visible
  flagged markers (lossless but not transcribed). Formula-on would take hours.
  Fundamental tradeoff for textbooks; no clean v1 answer.
