# Project Plan: pdf2md (rework of docsmcp → PDF-to-markdown converter)

> Living document. Updated incrementally by the deep-planner skill.
> Last updated: 2026-06-21
> Current phase: **Phase 3 (accuracy) underway.** Phase 2 hardening done (CI green,
> Dependabot, prune, benchmark harness, repo docs/LICENSE). Phase 3 shipped so far:
> inline sub/superscript recovery, equation confidence + image-backing, opt-in
> multi-pass transcription (Surya), the engine bake-off (Docling kept, MinerU
> deferred — see Decision Log 2026-06-20), and the accuracy harnesses (labels-free
> regression + labelled-equation). Verification is engine-agnostic in `enrich.py`;
> the engine is pure translation. PyPI declined for now.
>
> **Workstream "Trust, measured" — COMPLETE (2026-06-21).** Two real docs exposed a
> blind spot — coverage measured *disposition* (every block got a status) but not
> *legibility* (the text is real). A broken-font PDF (GRASP2018) came out 67%
> dingbat mojibake and `CoverageReport.lossless` still reported clean, because the
> property is an accounting identity. Shipped: a measured legibility signal that
> (1) repairs font-decode failures from the pdfium text layer pdf2md already loads
> (GRASP illegible 1653 → 0), and (2) makes the lossless invariant refuse to call
> garbage clean. Steps 1–4 landed (`legibility.py`, enrich refill, honest coverage,
> `FORMAT_VERSION` 0.5); Step 5 (scan/OCR honesty) closed by evidence — already
> satisfied. See the 2026-06-21 Decision Log entry and the Phase 3 roadmap items.
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
- **[2026-06-22] "Insight & accuracy" workstream — PLANNED**
  - **Context**: pdf2md is strong on born-digital + broken-font docs. The remaining
    gaps: we improve by eyeballing (no measured accuracy), scans are the weak class,
    complex-layout reading order is unverified, and the output doesn't tell a consumer
    how much to trust it. User also wants richer AI-facing JSON metadata and a
    human-facing run summary.
  - **Unifying insight**: the accuracy harness, the AI metadata, and the human summary
    all draw on one **per-document profile** — content inventory + structure + quality
    signals. Compute it once (`profile.py`), surface it three ways. Build that first.
  - **Roadmap** (ordered by dependency, then value):
    - [x] **Phase 1 — Document profile (2026-06-22).** `profile.py` `build_profile`:
          content inventory (counts by block type; figures/tables/equations/code/
          illegible/image-backed; OCR page count), quality (legibility %, lossless,
          image-backed equations), and a coarse confidence grade + reasons. `schema.py`
          `DocumentProfile`. Wired into the pipeline after coverage.
    - [x] **Phase 2a — AI metadata: `profile.json` (2026-06-22).** Doc-level JSON: the
          profile + output file list + contents pointer. *Remaining:* a few nav keys in
          front-matter and a full section→file→page map in profile.json (refinement).
    - [x] **Phase 2b — Human run summary `README.md` (2026-06-22).** Title, content
          inventory, confidence (high/med/low) with reasons, and where-to-start.
          Validated: a born-digital paper grades "high", a scan grades "medium".
    - [ ] **Phase 3 — Accuracy harness.** Labelled structural facts per archetype
          (text fidelity, table structure, reading order) + metrics + an accuracy
          report that validates the Phase-1 signals against ground truth. The
          sustainability foundation (resolves the long-open validation-harness question).
    - [ ] **Phase 4 — VLM page-OCR for scans.** Opt-in: detect scanned pages (no text
          layer), transcribe each page image with the vision client (reuse `describe`),
          use it as the page text (beats RapidOCR). Profile/confidence reflects scan
          fraction + whether VLM-OCR ran. Prose, not crop-authoritative → flag
          hallucination risk; opt-in.
    - [ ] **Phase 5 — Reading-order audit.** Verify two-column / multi-column / sidebar
          layouts on corpus papers (094103, 2207); scope a fix only if Docling
          mis-orders. Investigate-first.
    - [ ] **Phase 6 — Polish.** Ligature-drop repair (broken-font ﬀ/ﬁ/ﬂ gaps,
          dictionary-validated); CrossRef metadata when a DOI is present.
  - **Decisions (locked 2026-06-22)**: AI metadata → doc-level `profile.json` + nav
    keys in front-matter. Human summary → `README.md` in the output dir (renders
    first / "start here"). Confidence → component signals + a coarse high/med/low
    grade with reasons (no false-precision single number). VLM page-OCR → replace
    RapidOCR text on scanned pages (image still referenced).
  - **Revisit if**: measurement (Phase 3) shows a gap not on this list.

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

- **[2026-06-20] Engine bake-off ran: MinerU evaluated, Docling kept**
  - **Method**: Native-CLI head-to-head (no adapter), scored against the
    labelled equation set — born-digital equations (transformer), scanned
    equations (slater), a dense 24-row data table (transformer Table 3).
  - **Result**: MinerU ≈ Docling on born-digital equations (mean 0.921 vs 0.905,
    wins 2 / ties 1 / loses PE on array formatting); *better* on scanned
    equations — it recovers the clipped left-hand sides (`ρ_ν =`, `Frequency =`)
    that Docling's tight bbox crops drop and that has no downstream fix, in one
    pass (no Surya multipass needed); tables a **wash** (both parse Table 3 with
    comparable cell fidelity). MinerU's `middle.json` carries typed blocks +
    bboxes + reading order, so an adapter is viable. License OK (Apache-2.0; the
    100M-MAU / $20M-mo commercial terms are irrelevant here).
  - **Choice**: Keep Docling as the single default; defer the MinerU adapter.
    MinerU's only decisive edge is scanned-equation completeness, on content
    that is image-backed anyway — the gain doesn't clear the cost of a new
    adapter + re-validating section-splitting against MinerU's layout structure
    (vs Docling's semantic hierarchy) + a second heavy model stack.
  - **Revisit if**: scanned / equation-heavy documents become a priority. MinerU
    is the validated, license-clear second backend; the swap seam keeps wiring
    it a contained change.

- **[2026-06-21] VLM crop interpretation (opt-in, OpenAI-compatible API) — PLANNED**
  - **Context**: Figures/charts/diagrams are opaque PNG crops — invisible to any text
    consumer (human screen-reader or LLM). The biggest remaining AI-readability lever.
    Image-fallback tables and image-backed equations are the same opaque-crop problem.
  - **Choice (decided 2026-06-21, not yet built)**:
    - **Backend**: one **OpenAI-compatible vision client** (`/v1/chat/completions` with
      a base64 image). Configurable `base_url` + `model` + optional `api_key`, so it
      points at localhost (ollama / vLLM / LM Studio) or a remote endpoint with no code
      change and no model lock-in. Opt-in via `--describe` (off by default), the
      client library an optional extra (like surya-ocr for `--transcribe`).
    - **Scope**: all crops — figure pictures, image-fallback tables, image-backed
      equations — with a type-aware prompt (figure → concise factual description;
      table → GFM transcription else structure description; equation → LaTeX).
    - **Output**: a labeled block **below the image** —
      `> **[pdf2md: AI-generated description]** …` — keeping the real caption as alt
      text. Additive and clearly marked generated.
    - **Lossless stance**: the crop stays authoritative; the VLM text is an aid/hint
      (same as equation LaTeX today), never the source of truth, and never gates the
      lossless invariant. Prompt forbids inventing exact numeric values.
    - **Caching**: keyed by crop content hash so re-runs and incremental conversion
      don't re-infer.
  - **Roadmap** (SHIPPED 2026-06-21):
    - [x] `describe.py`: `Describer` Protocol + `OpenAIVisionDescriber`; `get_describer(config)`.
    - [x] config/cli: `describe_figures`, `vlm_base_url`/`vlm_model`/`vlm_api_key`; `--describe`, `--vlm-model`.
    - [x] pipeline `_describe_crops`: describer over each crop after render; figure/table → description,
          equation → hint (never overrides math-OCR).
    - [x] emit: labelled description block below figure/table images (`_description`).
    - [x] cache by (model, kind, crop bytes) at the doc level; verified `--force` reuses it.
    - [x] Validated against ollama `qwen3-vl:8b`: accurate Transformer-architecture / attention-diagram
          descriptions. (`qa.py` figures_described counter not added — deferred as marginal.)
  - **Revisit if**: descriptions prove low-value or the hosted-model cost/latency doesn't
    justify; equation-crop description overlaps `--transcribe` (Surya) — keep whichever wins.

- **[2026-06-21] Readability pass: headings, index, cross-refs, figure captions**
  - **Context**: With GRASP's font/console/table content readable, a survey exposed
    structural readability gaps that hurt both human and AI consumers.
  - **Choice**: (1) **Heading hierarchy** for split books (`emit._heading_plan`): drop
    a page heading that restates the bookmark file title, merge a bare "Chapter N"
    label into the title after it, and deepen body headings so Part → Chapter →
    section nest as `#`/`##`/`###`. (2) **`index.md`** contents file: every section
    file linked, chapters/sections nested as in-file anchors, built from the emitted
    headings. (3) **Cross-reference links**: dotted "section 9.2" refs linked to the
    resolved heading, fence- and front-matter-aware, map-resolved so no dangling
    links. (4) **Figure-caption font-decode refill** (`FigureRef.caption_bbox`) — the
    last dingbat leak. Single-file papers unchanged throughout.
  - **Deferred**: VLM figure/diagram descriptions (the biggest remaining AI-readability
    lever) — a separate plan; figures are still opaque PNGs to a text consumer.

- **[2026-06-21] Legibility as a measured signal; font-decode repair from pdfium**
  - **Context**: A broken-font born-digital PDF (GRASP2018 manual) converted to
    67% dingbat mojibake (`❆ ♣/a114❛❝/a116✐❝❛❧` = "A practical guide") while
    coverage reported it lossless. Root cause: Docling's default `DoclingParseV4`
    backend reads the font's built-in glyph codes instead of applying the
    ToUnicode CMap; pypdfium2 reads the same file perfectly. A separate doc (Slater
    vol. 1) is a genuine scan (zero text chars/page) — its image-backed equations
    are correct, a different failure not addressed here.
  - **Choice**: (1) Make text legibility a measured signal (`legibility.py`):
    PUA/dingbat/glyph-name density + a plausibility check. (2) Repair font-decode
    failures per-block inside `enrich.py` by refilling garbage block text from
    `PageChars.text_region(bbox)` — the pdfium layer pdf2md already loads. (3) Make
    the lossless invariant honest: a block still garbage after repair is FLAGGED
    with a visible marker, never silently EMITTED; coverage reports legibility
    alongside disposition.
  - **Why per-block refill, not a Docling backend swap**: Docling's *layout* on
    GRASP is correct — right blocks, right bboxes; only its glyph→char mapping is
    broken. Refilling text per block keeps all of Docling's structure work, needs
    no second Docling pass, stays engine-agnostic, and lives where the project
    already puts verification (`enrich.py` reads pdfium geometry; any engine
    inherits it). It generalizes beyond GRASP to the whole font-decode class
    (common in older physics/chem PDFs, subset/Type 3 fonts, bad OCR text layers).
  - **Alternatives considered**: re-run Docling with `PyPdfiumDocumentBackend` on
    detection (clean text + Docling's reading order, but a second full Docling pass
    on bad docs and risk of weaker tables/layout — kept as the documented fallback
    if per-block pdfium text comes out mis-ordered on wrapped/multi-column blocks,
    a question the corpus decides); always use the pypdfium backend (rejected —
    regresses normal docs where DoclingParseV4 is stronger).
  - **Revisit if**: the labelled corpus shows per-block pdfium text is mis-ordered
    often enough to need the backend-swap fallback.

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
- **Default engine** — RESOLVED [2026-06-20]: bake-off ran (see Decision Log).
  Docling kept as the single default; MinerU is the validated second-backend
  candidate, deferred until scanned/equation-heavy docs warrant the adapter.
  License note corrected: MinerU is **Apache-2.0** (not AGPL) with commercial
  thresholds that don't bite here; Marker stays ruled out (GPL-3.0 + gated
  weights); Docling MIT.
- **Validation harness** — PARTIALLY RESOLVED [2026-06-21]: the "Trust, measured"
  workstream builds the diverse labelled corpus (6–8 archetypes: clean
  born-digital, broken-font, pure scan, two-column, table-heavy, equation-heavy)
  and adds a **legibility** metric to the existing text-fidelity / table-structure
  / equation-accuracy set. Storage uses 50-page slices + small public-domain PDFs
  behind `PDF2MD_TEST_PDF` / the `integration` marker so the fast suite stays
  Docling-free.
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
- [x] Inline sub/superscript recovery from glyph geometry (`scripts.py` / `enrich.py`)
- [x] Equation confidence + image-backing of suspect equations (`confidence.py`)
- [x] Multi-pass equation transcription via local math-OCR — Surya (`transcribe.py`, `--transcribe`)
- [x] Accuracy harnesses: labels-free regression (`scripts/qa.py`) + labelled-equation (`scripts/eval_equations.py`)
- **"Trust, measured" — legibility signal + font-decode repair** (2026-06-21, see Decision Log):
  - [x] **Step 1 — legibility primitive** (`legibility.py`): pure `score_legibility` /
        `is_garbage`, scoped to the symbol-substitution signal (dingbat/PUA/glyph-name
        density) — no vowel-ratio/dictionary check, which would false-flag chemistry
        notation. Unit-tested; validated on real output (GRASP blocks median 0.0,
        Slater clean-OCR median 1.0, zero false positives).
  - [x] **Step 2 — corpus + legibility gate** in `scripts/qa.py`: `illegible` signal
        (prose blocks scoring as garbage) added as a **gated invariant** (must not
        rise); `_signals` now audits split/book outputs too (was skipping any doc
        without `document.md`, which is why GRASP was untracked). GRASP added to
        `qa_baseline.json` as the broken-font archetype (illegible 1653/2166 — the
        current broken state; Step 3 drives it to ~0). The committed corpus now spans
        8 archetypes (clean born-digital, scan/OCR, table-heavy, equation-heavy,
        split-book, broken-font). Resolves the validation-harness Open Question.
  - [x] **Step 3 — source selection + pdfium repair** (`enrich.py`): garbage prose
        text refilled from `PageChars.text_region(bbox)` via `normalize.clean_reading`,
        stamped `text_source="pdfium"`; only swaps when pdfium is cleaner. Validated on
        GRASP: illegible prose 1653 → 0, text readable, metadata recovered. Residual:
        broken-font ﬀ/ﬁ/ﬂ ligatures lack ToUnicode for pdfium too, so they drop
        ('e cient'); legible-but-imperfect, far better than dingbats.
  - [x] **Step 3b — table-cell refill (2026-06-21).** Initially deferred; done after
        GRASP's tables proved garbled. Garbage cells refilled via the shared
        `enrich.refilled` helper, forcing a table rebuild even without scripts. Fixing
        it surfaced a coordinate bug — **table-cell bboxes are TOPLEFT** vs blocks'
        BOTTOMLEFT — so `docling._cell_bbox` now flips Y (this also fixes the table
        script overlay's long-standing glyph mis-alignment). Guarded by a new
        `illegible_table_rows` invariant in `qa.py`. Validated: GRASP tables readable,
        atkins-50page unaffected.
  - [x] **Step 3c — preformatted content (console / ASCII tables) (2026-06-21).**
        GRASP's sample-runs chapter is program I/O transcripts and ASCII-art tables —
        monospace text whose meaning is the line layout, which the prose/table model
        flattens or mis-grids. New `preformat.is_preformatted` (banner/rule lines, +
        pipe columns for tables) + `PageChars.text_lines` (pdfium native line breaks)
        + `normalize.clean_preformatted`. `enrich` routes Docling `code` blocks
        (refilled line-preserved), banner-bearing prose the engine mislabelled, and
        ASCII-art tables to fenced code-block emission. Banner detector: 0 false
        positives on the clean control paper. Detection signal: font fixed-pitch flag
        is unusable (pdfium returns empty font info for these subset fonts), so the
        signal is content (banner/rule lines) + table-only pipe columns, not geometry.
  - [x] **Step 4 — honest invariant** (`schema.py`, `coverage.py`, `emit.py`):
        `CoverageReport.illegible` tally; still-garbage prose → FLAGGED + visible
        marker + front-matter `illegible_blocks`, not silent EMITTED. **`FORMAT_VERSION`
        0.4 → 0.5**; CHANGELOG + CLAUDE.md updated.
  - [x] **Step 5 — scan/OCR honesty: CLOSED by evidence (2026-06-21).** The planned
        work (surface Docling's formula LaTeX on scans instead of discarding it) was
        already done: `emit.py`'s unverified-equation branch renders the OCR LaTeX as
        a hint below the authoritative image even without `--transcribe`. Confirmed on
        slater-50page — scanned equations emit clean LaTeX hints. The real residual is
        OCR *prose* quality (garbled lines like "Lyly-aay sogosoidde…" emitted as-is),
        but Docling exposes no per-block OCR confidence — only a page-level
        `ConfidenceReport` — and catching garbled prose needs the vowel-ratio/dictionary
        heuristic Step 1 deliberately rejected (false-flags chemistry notation). Better
        scanned prose is an OCR-engine ceiling → the **deferred MinerU/better-OCR
        decision** (Deferred Register), not a Step increment. Optional, not built:
        piping Docling's page-level `ocr_score` into front-matter as a "verify against
        image" label (marginal — labels, doesn't fix).
- [~] Second engine backend (MinerU / PaddleOCR-VL) behind the seam — bake-off RAN
      [2026-06-20]: Docling kept, MinerU is the validated *deferred* second backend
      (its only decisive edge is scanned equations; revisit if those dominate). See Decision Log.
- [ ] HTML complex-table rendering (upgrade from crop fallback)
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
- **Sub/superscripts** — IMPLEMENTED (2026-06-15, `scripts.py`). Geometry-only
  detector (pypdfium2 glyph boxes: small + off-baseline; font size is useless for
  these symbol fonts) overlaid onto Docling text via character alignment that
  only inserts `<sub>`/`<sup>` tags (never alters text → no data-loss risk).
  Applied to prose blocks and to table cells (tables with scripts are rebuilt
  from Docling's cell grid). Born-digital only; `--no-scripts` to disable.
  Verified on a real chemistry paper: molecular subscripts, term-symbol
  multiplicities, variable indices, affiliation markers all recovered.
  Detection refined (lines grouped by vertical overlap so scripts stay attached;
  superscripts need only be raised; descenders excluded from subscripts; adjacent
  signs absorbed). Residual ceiling: scripts are overlaid onto Docling's text, so
  an exponent Docling renders differently from the raw glyphs (spaced hyphen vs
  raised minus, e.g. some `mol⁻¹`) is only partially recovered. Fixing fully means
  full glyph-reconstruction (abandoning the overlay) — deferred as not worth the
  text-quality risk. The overlay never alters characters, so misses are cosmetic.

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
