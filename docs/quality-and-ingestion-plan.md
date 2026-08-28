# Quality, performance, and ingestion plan

This document records the completed pdf2md workstream for faster large-document
conversion, clearer quality reporting, and output that humans and retrieval systems
can consume without losing source evidence. All sixteen scoped milestones are complete;
the remaining items are evidence-triggered maintenance under
[Product boundary](#product-boundary), not unfinished implementation work.

Last updated: 2026-08-25

## Status conventions

- `[ ]` planned
- `[~]` in progress
- `[x]` complete and verified
- `blocked` requires a named external dependency or decision
- `deferred` is outside the current product boundary

A completed item must name the implementation, the check that could have falsified
it, and the measured result. Shipping code without that record does not close an
item.

## Current evidence

The August 2026 conversions of two large chemistry books exposed the current
bottlenecks more clearly than the small-paper corpus:

| Document | Pages | Blocks | Figures | Tables | Equations | Result |
|---|---:|---:|---:|---:|---:|---|
| Atkins' Physical Chemistry, 8th ed. | 1,085 | 19,664 | 1,269 | 349 | 3,099 | Base conversion succeeded with 3 genuinely illegible blocks. Most equations remained image-backed. |
| Introduction to Relativistic Quantum Chemistry | 545 | 5,701 | 36 | 47 | 1,848 | Base conversion succeeded. Formula enrichment did not finish in a practical time, so all equations remained image-backed. |

At the start of this workstream, these runs revealed five concrete problems:

1. Chart digitization repeatedly opens the same PDF, once or twice per figure. On
   Atkins, post-parse figure work dominated the run time.
2. The document-level confidence label conflates structural coverage, text
   sufficiency, source dependence, and actual defects. Relativistic QC reports
   `high` confidence and 1,848 review markers at the same time.
3. Expensive enrichment is coupled to conversion. A late failure or impractically
   slow formula pass delays the useful base artifact.
4. `chunks.jsonl` is safe for page citations but uses character-sized, page-local
   chunks with limited structural context.
5. Large books split at top-level bookmarks, producing multi-megabyte Markdown
   files and oversized indexes. Metadata heuristics can also prefer a malformed
   embedded title over cleaner page evidence.

The accounting invariant remains non-negotiable: every detected element must have
an emitted, source-backed, flagged, or failed disposition. This work changes how
quickly and clearly pdf2md presents that evidence. It does not weaken the invariant.

## Work order

### P0. Large-document efficiency

#### E1. Reuse PDF state during chart digitization

Status: `[x]` complete and verified

Current behavior:

- `_digitize_figures` visits figures sequentially.
- `VectorPathDigitizer.digitize` opens and closes the source PDF for each figure.
- The outlined-axis OCR fallback opens and closes it again.

Implementation:

- [x] Open one PDF document for the chart stage.
- [x] Group work by page and reuse the page object for every figure on that page.
- [x] Keep the existing one-shot public functions for callers and tests.
- [x] Report completed figures, remaining figures, and ETA through `Progress`.
- [x] Reuse vector geometry between the text-layer and outlined-axis tiers instead of
      walking the same page objects twice for one figure.
- [x] Replay chart work from an existing bundle with
      `scripts/benchmark_digitize_bundle.py`, avoiding a full parser run while tuning.
- [x] Compute text groups once per figure and filter the result for each candidate
      frame. The old path regrouped every page glyph separately for every frame.
- [x] Add an exact geometry gate before outlined-axis OCR. The gate calls the same line,
      scatter, and bar extractors used after calibration. When all three return empty,
      OCR-read axis values cannot produce a data series.
- [x] Validate the gate against every accepted OCR-axis result in two completed bundles:
      12 of 12 Atkins charts and 3 of 3 GRASP2018 charts remained eligible. The latter
      includes a sparse scatter plot that invalidated an earlier density threshold.
- [x] Measure a fixed 70-figure Atkins slice before and after the change. It fell from
      196.723 seconds to 24.768 seconds, an 87.4% reduction, while preserving the same
      three accepted digitizations.
- [x] Run a full forced Atkins conversion. The chart stage completed in 5m31s, recovered
      58 charts, attempted OCR on 657 figures, and skipped 96 geometrically ineligible
      candidates. The whole conversion completed in 16m00s.
- [x] Replay all 1,269 figures against the pinned `v1` bundle. Current code reproduced
      all 58 accepted IDs and serialized digitizations exactly in 443.931 seconds.
- [x] Leave page-wide vector caching and worker processes out for now. The measured
      bottleneck no longer justifies either change, and OCR runtime still varies between
      runs.

Acceptance:

- A unit test proves a multi-figure stage constructs one `PdfDocument`.
- Existing vector line, scatter, bar, log-axis, form-XObject, and multipanel tests pass.
- The stage emits bounded progress on a large figure set.
- Any OCR eligibility gate retains every accepted OCR-axis figure in the pinned Atkins
  bundle before it can become the default.
- The Atkins rerun preserves accepted chart data and reduces chart-stage wall time.

Evidence:

- [`digitize-ocr-gate-atkins.json`](digitize-ocr-gate-atkins.json)
- [`digitize-ocr-gate-grasp.json`](digitize-ocr-gate-grasp.json)
- `scripts/benchmark_digitize_bundle.py` reports accepted-ID and exact serialized-record
  preservation as part of every replay.

#### E2. Persist stage timings and work counts

Status: `[x]` complete and verified

- [x] Record setup, parse, geometry, rendering, equation, chart, description, emission,
      audit, and finalization times in provenance and the generated README.
- [x] Record work counts. Chart work reports attempted, accepted, declined, failed,
      OCR-axis attempted, and geometrically ineligible figures. Other stages report
      their concrete inventory or option counts. Page OCR, raster-chart reads, figure
      labels, and crop descriptions share exact vision-cache hit, miss, and write counts.
- [x] Report cached conversions as reused completed stages and label the stored time as
      the original run duration. Do not present cache lookup latency as conversion time.
- [x] Add `pdf2md compare-runs BEFORE AFTER`, with human-readable timing deltas and a
      JSON form that includes before/after work counts. It reads completed provenance
      records rather than console output.
- [x] Record main-process and largest-terminated-child peak RSS through the standard
      Unix `resource` interface. Values are normalized to bytes. The scope is labelled
      as a process-lifetime high-water mark because later documents in one batch inherit
      earlier peaks; child memory is separate and cannot be added to the parent peak.

Acceptance:

- Every completed conversion reports a timing breakdown whose stages sum to the
  recorded wall time within a small bookkeeping tolerance.
- A cached run reports which stages were reused rather than claiming zero-cost work.

Evidence:

- `tests/test_run_metrics.py` pins stage sums, comparison deltas, and macOS/Linux RSS
  unit normalization.
- `tests/test_reproducibility.py` verifies fresh-run serialization, cache reload, and
  exact vision-cache event counts.
- `tests/test_profile.py` and `tests/test_cli.py` pin the human README and command output.

#### E3. Add resumable, versioned enrichment

Status: `[x]` complete and verified

Target interface:

```console
pdf2md convert book.pdf
pdf2md enrich <document> --equations
pdf2md enrich <document> --charts
pdf2md enrich <document> --descriptions
```

Implementation:

- Every new bundle stores the pre-postprocessing `EngineResult` in `base-state.json`,
  including blocks, tables, figures, page geometry, native quality evidence, and raw
  table cells. `StoredEngine` feeds that state through the normal pipeline, so enrichment
  shares the existing emitter, quality audit, manifest, and version allocator.
- `pdf2md enrich` accepts a source PDF, document directory, or completed version. It can
  add local equation transcription, model-assisted chart recovery, crop descriptions, or
  any combination. A TOML file overlays the source run's effective configuration rather
  than resetting unrelated source settings to defaults.
- Derived runs write a new immutable `v<n>`. Their provenance records the source version,
  source-provenance SHA-256, base-state SHA-256, selected stages, source hash, effective
  configuration, dependency and model identities, prompt/cache schema, and implementation
  hash. Existing bundles without `base-state.json` remain usable through their serialized
  document provenance, though they lack the original engine's transient raw table cells.
- The document inference cache now writes atomically after every successful region.
  Equation transcription uses a key containing the crop hash, transcriber identity, cache
  schema, and implementation hash. Vision keys already cover crop bytes, endpoint, model,
  prompt/context, temperature, token cap, and cache schema.
- The CLI prints selected stages and exact document/region counts before starting. It warns
  that model stages on documents of 200 pages or more may take hours. Base conversion gives
  the same threshold warning when formula enrichment is enabled and recommends
  `--no-formula` followed by staged equation enrichment.
- Optional clients are constructed before the stored engine runs. A missing dependency
  therefore cannot change a completed source bundle. Killed runs leave a partial version
  without the completion marker; the next run reuses the version number and completed
  document-scoped region-cache entries.

Acceptance:

- Interrupting enrichment and rerunning it reuses completed region results.
- A base bundle remains valid when every optional enrichment service is unavailable.
- Provenance distinguishes base extraction from later derived evidence.

Evidence:

- `tests/test_enrichment.py` round-trips raw engine state, rejects source/state hash
  mismatches, preserves source configuration under overlays, pins output-root selection,
  and runs a complete `v1` to `v2` replay whose original parser is called only once.
- The interruption test stops on the second equation, reloads the on-disk cache, and proves
  the rerun calls the transcriber only for the unfinished region.
- `tests/test_cli.py` pins stage selection, region counts, the large-document warning, and
  the requirement to select at least one enrichment stage.

#### E4. Collapse repeated third-party warnings

Status: `[x]` complete and verified

The Atkins parse prints repeated RapidOCR `text detection result is empty` warnings.
One warning is useful; dozens obscure progress. Capture identical engine warnings,
print the first occurrence, and summarize the repeated count at stage completion.
Do not suppress distinct errors or pdf2md's own review warnings.

Implementation:

- Attach a temporary exact-message filter only to the `RapidOCR` and Docling RapidOCR
  loggers while source reading or chart-axis OCR runs.
- Keep the first instance of every warning and pass non-warning records through.
- Remove the filters when source reading ends, including exceptional exits, then report
  each suppressed warning with its source and exact repeat count.
- Store unique warning types and suppressed repeats in the corresponding parse or chart
  stage metrics.

Acceptance:

- Repeated identical third-party warnings produce one example and one exact count.
- Distinct warnings and document failures remain visible.

Evidence:

- The Atkins `v3` parse retained two distinct warning types and reported 26 suppressed
  repeats. The later chart pass exposed the same noise source outside parsing, so the
  filter now also covers chart digitization.
- `tests/test_logging.py` pins exact-message filtering and cleanup. A pipeline test emits
  three identical chart OCR warnings, retains one, reports two repeats, and records the
  same counts in chart-stage metrics.

#### E5. Make enrichment preflight cost-aware

Status: `[x]` complete and verified

The first enrichment interface printed total equation and figure counts, then immediately
started. Total inventory overstates model work when the source version already contains
transcriptions or chart datasets, and it gives no safe way to inspect a large job before
launching it.

Implementation:

- Read the completed provenance alongside `base-state.json` and report both inventory and
  current derived evidence.
- For equations, count image-backed regions and existing transcriptions. For charts, count
  accepted data artifacts and figures that may still need the model tier. For descriptions,
  count crop-backed eligible regions and descriptions already present.
- Add `pdf2md enrich ... --dry-run`. It resolves and validates the source bundle, loads any
  TOML overlay, prints the same plan as a real run, and returns before constructing optional
  model clients or calling the conversion pipeline.

Acceptance:

- A dry run creates no output version or document cache file.
- Counts come from completed bundle evidence rather than assuming every block needs a model.
- A real run and dry run print the same preflight fields.

Evidence:

- `tests/test_enrichment.py` pins evidence-aware counts from a stored bundle containing an
  image-backed equation, chart crop, and table crop.
- `tests/test_cli.py` makes the enrichment function fail if called during `--dry-run`, then
  verifies successful exit and the exact chart/evidence counts.

#### E6. Retry partial model-backed enrichment

Status: `[x]` complete and verified

A vision endpoint can fail on a few regions after other calls succeed. The resulting bundle is
still source-complete because every affected region retains its crop or page image, but optional
text or chart evidence is missing. Treating that version as an exact cache hit prevents the same
command from retrying the missing work.

Implementation:

- Define partial optional work from the exact `vision_failures` counts already recorded in stage
  metrics. Parser, chart-geometry, review, and dropped-block failures do not enter this count.
- Keep the completed partial bundle immutable and readable. Exclude it from exact-run cache reuse,
  so the same command allocates a new version after the endpoint is repaired. If an older healthy
  version has the same fingerprint, select it instead of launching redundant work.
- Retain the document-scoped region cache. Successful model results are reused; only prior misses
  call the endpoint again.
- Print `PARTIAL ENRICHMENT`, the failed-call count, and the recovery command in CLI output. Add
  the same explanation to the generated bundle README. The runtime warning now says to repeat the
  same command rather than requiring an undocumented enrichment `--force` flag.

Acceptance:

- A matching version with no failed optional calls remains a normal cache hit.
- A matching version with failed optional calls creates a new immutable version.
- A healthy older exact match takes precedence over a newer partial exact match.
- A retry does not repeat model calls for regions that completed successfully.
- Source accounting remains independent of optional enrichment status.

Evidence:

- `tests/test_reproducibility.py` changes only the stored failed-call count on an otherwise matching
  run and proves the engine executes into `v2` rather than returning cached `v1`.
- `tests/test_describe.py` interrupts the second crop after the first is cached, then proves the
  rerun calls the model only for the second crop.
- `tests/test_run_metrics.py`, `tests/test_profile.py`, and `tests/test_cli.py` pin failure counting,
  generated README language, partial status, and retry guidance.

### P0. Honest quality and review signals

#### Q1. Replace scalar confidence with a quality scorecard

Status: `[x]` complete and verified

The current `high | medium | low` label is not a calibrated probability and hides
which part of the document is weak. Replace it with independent dimensions:

- accounting coverage;
- structural completeness;
- text sufficiency;
- layout quality;
- OCR dependence and OCR quality;
- equation text coverage;
- table verification coverage;
- figure text/data coverage;
- metadata quality;
- unresolved error severity.

Preserve Docling's native layout, OCR, and parse grades as engine evidence. Do not
present its raw scores as stable probabilities, and do not imply table confidence
from a native score while Docling's table score remains unimplemented.

Implementation:

- [x] Added eleven independent dimensions to `profile.json` and `manifest.json`.
      Every dimension names its evidence source and states that it is uncalibrated.
- [x] Made the generated README lead with a compact scorecard. The old aggregate
      remains as an explicitly deprecated compatibility field and is no longer the
      main quality claim.
- [x] Kept structural representation independent from text sufficiency. A crop can
      completely represent an equation while equation-text coverage remains zero.
- [x] Classified unaccounted, dropped, and illegible prose blocks as high-severity
      unresolved errors. Other flagged blocks remain a separate medium-severity case.
- [x] Preserved Docling's native parse, layout, and OCR grades plus raw engine scores.
      The records label the values as engine-specific and uncalibrated. Native table
      confidence is omitted because Docling does not implement that score.
- [x] Reported missing layout or OCR quality evidence as `not_measured` rather than
      deriving a score from coverage counts.

Acceptance:

- A fully represented image-backed equation lowers equation text coverage without
  lowering structural coverage.
- A genuinely dropped or illegible prose block appears as an unresolved error.
- No aggregate label contradicts its component fields.
- Every scorecard field states its evidence source and whether it is calibrated.

Evidence:

- `tests/test_profile.py` pins the image-backed-equation, illegible-prose, metadata,
  evidence-source, calibration, manifest, profile, and generated-README contracts.
- `tests/test_docling_adapter.py` pins native grade preservation and exclusion of the
  unimplemented table score.

#### Q2. Separate review actions from source dependence

Status: `[x]` complete and verified

Use three top-level dispositions:

- `action_required`: likely error or missing representation;
- `source_dependent`: valid bundle entry whose authoritative representation is an
  image, SVG, or source region;
- `informational`: limitation or provenance note requiring no action.

Image-backed equations produced intentionally by `--no-formula` belong under
`source_dependent`. Suspect LaTeX, illegible prose, dropped blocks, and unresolved
table discrepancies belong under `action_required`.

Generate `review.md` and a machine-readable review queue sorted by severity, content
impact, and page. A human reviewing Atkins should see the three illegible blocks
before thousands of intentional equation crops.

Implementation:

- [x] Added `disposition`, `severity`, and `content_impact` to every coverage flag.
- [x] Formula-disabled equation crops are `source_dependent`; suspect formula output
      from an enabled enrichment pass remains `action_required`.
- [x] Image-only table fallbacks are source-dependent. Unverified OCR table candidates,
      missing crops, illegible prose, dropped blocks, and suspect equations require action.
- [x] Added `review.md` and `review.json`, both generated from one queue sorted by
      disposition, severity, content impact, page, and block ID.
- [x] Made the CLI, README, profile, manifest, chunks, and audit-stage metrics distinguish
      action counts from source dependence. Chunk schema v2 retains the dispositions and
      sets `needs_review` only for action items.

Acceptance:

- The Relativistic QC base bundle reports 1,848 source-dependent equations and no
  false action-required entries for that reason alone.
- The Atkins bundle keeps its three illegible blocks in the action queue.
- Counts in README, manifest, profile, and review queue agree exactly.

Evidence:

- `tests/test_review.py` pins a synthetic 1,848-equation source-dependent bundle with
  zero false actions, three illegible blocks ahead of intentional crops, deterministic
  ordering, and exact cross-artifact counts.
- `tests/test_emit.py` proves the formula option distinguishes an intentional crop from
  a suspect extraction.
- `tests/test_chunks.py`, `tests/test_cli.py`, and `tests/test_profile.py` pin the human,
  retrieval, and machine interfaces.
- A forced Relativistic QC `v3` conversion produced 1,848 source-dependent equation
  entries and zero action-required entries. README, profile, manifest, review queue, and
  audit-stage counts agree exactly; `needs_review` is false.
- A forced Atkins `v3` conversion produced 3 action-required entries followed by 3,092
  source-dependent equation entries. The three actions are the known illegible paragraph
  blocks on page 856. README, profile, manifest, review queue, and audit-stage counts
  agree exactly; unresolved-error severity is high.
- The Relativistic QC run completed in 173.288 seconds. Atkins completed in 1,007.967
  seconds, including 454.586 seconds for parsing and 336.756 seconds for charts.

#### Q3. Make conservation checks representation-aware

Status: `[x]` complete and verified

Partition missing words and numbers into:

- expected absence from source-image-authoritative regions;
- expected normalization or formatting differences;
- unexplained loss from text-intended content;
- unexplained additions.

Only unexplained loss and additions should raise review actions. Retain expected
differences as audit evidence.

Implementation:

- [x] Kept the existing whole-document numeric multiset for compatibility and added a
      schema-versioned block report with conserved, source-dependent, formatting,
      unexplained-loss, and unexplained-addition word and number counts.
- [x] Captured the exact text and Markdown filename emitted for each block. The new
      check compares enriched logical block content with that emitted representation,
      so every high-confidence discrepancy has a block, page, bbox, and artifact.
- [x] Counted tokens inside image-backed equations, figures, and other crop-authoritative
      blocks as expected source dependence. Those tokens cannot create loss actions.
- [x] Classified case, Unicode, numeric punctuation, minus-sign, and intraword-hyphen
      changes as expected normalization. Intentional heading merging and page-furniture
      removal are formatting evidence rather than loss.
- [x] Excluded blocks already flagged or dropped from duplicate conservation actions.
      Their original, more specific review item remains authoritative.
- [x] Kept PDF-to-block word recall and whole-document numeric conservation as separate
      signals. A trial that treated raw bbox glyph text as stable reading order produced
      1,642 changed blocks on Relativistic QC and 6,110 on Atkins. Those were dominated
      by overlapping regions, repaired word fragments, and math draw order, so that
      approach was rejected rather than exposed as false review work.

Acceptance:

- Numeric content inside an image-backed equation is not reported as unexplained
  Markdown loss.
- Every unexplained example links to a page, region, block, and emitted artifact.
- Synthetic deletion and insertion fixtures are detected exactly.

Evidence:

- `tests/test_conservation.py` pins exact deletion and insertion counts, normalization,
  source-dependent equation handling, evidence links, and action generation.
- `tests/test_emit.py` pins the block-to-Markdown emission index. Pipeline tests prove a
  clean emitted paragraph produces no unexplained changes.
- A fresh Docling conversion of the one-page vector fixture produced zero unexplained
  changes and zero actions. Its figure region contained 11 numeric tokens, all recorded
  as expected source dependence.

### P1. Retrieval and ingestion contract

#### I1. Add passage schema v2

Status: `[x]` complete and verified

Keep page-local `chunks.jsonl` as the citation-safe interface. Add
`passages.jsonl` for retrieval and embeddings, with a published JSON Schema.

Each passage should carry:

- a stable ID derived from source block IDs and split position;
- a content hash for incremental reindexing;
- document ID, title, authors, and language;
- full section breadcrumb;
- display text and separately contextualized retrieval text;
- content types;
- source pages and bounding boxes;
- previous and next passage IDs;
- authority and review disposition;
- assets with type and provenance;
- tokenizer identity and token count.

Passages may cross page boundaries within one logical section, but every contributing
source region must remain explicit. Sequential IDs alone are insufficient because an
early insertion would force downstream systems to reindex every later chunk.

Implementation:

- [x] Added block-local `passages.jsonl` records alongside the unchanged page-local
      `chunks.jsonl` citation interface. Oversized blocks split deterministically; I2
      owns later tokenizer-aware merging across blocks.
- [x] Derived each passage ID from the stable source filename, block ID, and within-block
      split index. The document content hash is deliberately excluded, so editing one
      block does not rename every passage in a new PDF version.
- [x] Hashed contextualized retrieval text separately. A title, author, breadcrumb, or
      block-text change therefore requests reindexing without changing the passage ID.
- [x] Added document identity, authors, explicit `und` language fallback, full section
      breadcrumbs, display and retrieval text, content type, exact source region,
      neighboring passage IDs, authority, review dispositions, typed assets, and a named
      tokenizer count.
- [x] Added `passages.schema.json` to every bundle and packaged the canonical Draft
      2020-12 schema as `src/pdf2md/passages-v2.schema.json`.
- [x] Added passage and schema pointers to `manifest.json`, a passage count to the
      inventory and run metrics, and a short entry in the generated README.

Acceptance:

- A one-block edit changes only the affected passage IDs or content hashes.
- Every passage resolves back to existing blocks and source regions.
- Contextualized text can be embedded without polluting human-facing Markdown.
- The schema works for both Docling and MinerU output.

Evidence:

- `tests/test_passages.py` changes one block and the document hash, then proves all
  passage IDs remain stable, the untouched hash remains stable, and only the edited
  block's content hash changes.
- The same tests validate Docling- and MinerU-labelled blocks against the published
  Draft 2020-12 schema and pin breadcrumbs, source geometry, authority, review state,
  assets, neighbors, and tokenizer metadata.
- Pipeline tests verify that `passages.jsonl`, `passages.schema.json`, manifest pointers,
  audit counts, and the README agree. A built wheel contains both the schema and writer.

#### I2. Use token-aware, structure-aware splitting

Status: `[x]` complete and verified

- Size retrieval passages with the target embedding tokenizer, not characters.
- Split prose on paragraph and sentence boundaries.
- Keep lists and code line-aware.
- Repeat table caption, units, and column headers in every continuation.
- Avoid splitting a table row unless one row exceeds the model limit.
- Keep equations with their label and the nearest defining or explanatory paragraph.
- Attach figure captions and paragraphs that explicitly refer to the figure.

Docling's `HybridChunker` and `LineBasedTokenChunker` provide useful reference
behavior: tokenizer-aligned sizing, hierarchical context, peer merging, and repeated
table headers. pdf2md's emitted schema must remain engine-neutral.

Implementation:

- [x] Replaced character sizing for `passages.jsonl` with a token budget applied to
      the final contextualized retrieval text. Each record stores tokenizer identity,
      observed count, and configured limit.
- [x] Kept `lexical` as the deterministic offline default and added
      `hf:<model-or-local-path>` for exact embedding-model tokenization. The model choice
      is explicit because pdf2md cannot infer which downstream embedding service will
      consume the bundle. A configured limit above a finite tokenizer
      `model_max_length` is refused rather than left for the embedding model to truncate.
- [x] Added paragraph-first prose packing with sentence-boundary fallback, plus
      line-aware list and code splitting. A single oversized sentence or line is the only
      case allowed to split inside that unit.
- [x] Added row-aware GFM table splitting. The nearby table caption, column header,
      separator, and header units are repeated in every continuation. A row remains
      intact unless it alone exceeds the configured limit; an unrepeatable oversized
      header is refused instead of silently emitted without context.
- [x] Added a nearby explanatory sentence and its exact source region to equation
      passages. Added explicit figure-referring sentences when the figure label matches,
      while retaining the figure caption already carried by the figure record.
- [x] Exposed tokenizer and limit selection in both TOML configuration and the CLI.
- [x] Ran the pinned agent benchmark against page chunks and reconstructed current
      passages. Both retrieval modes selected the labelled page for 11/11 questions,
      and the matched model runs produced 11/11 correct answers and 11/11 valid page
      citations. Passages used 4,720 input tokens versus 8,105 for chunks.

Acceptance:

- No table continuation lacks the headers required to interpret its cells.
- Every chunk stays within the configured tokenizer limit after context is added.
- Citation precision does not regress on the pinned agent benchmark.

Evidence so far:

- `tests/test_passages.py` pins post-context token counts, paragraph and sentence
  boundaries, intact list and code lines, caption/header repetition on every table
  continuation, intact normal-sized rows, and the source regions added for equation and
  figure context.
- A local saved Hugging Face tokenizer fixture exercises the model-aligned loader without
  network access. Docling- and MinerU-labelled records continue to validate against the
  engine-neutral Draft 2020-12 schema.
- `scripts/agent_benchmark.py --retrieval-audit` rebuilds passages in memory from stored
  provenance when a frozen bundle predates the passage artifact. The top-one audit gives
  both chunks and passages 11/11 labelled-page hits and mean page precision 1.0.
- Matched `qwen3.6:35b-mlx` bundle runs give both formats 11/11 correct answers and
  11/11 valid page citations with no release blockers. Passages reduce input tokens by
  41.8 percent. The frozen summary and raw hashes are in
  `docs/agent-benchmark-passages-2026-08-25.json`.

#### I3. Add deterministic document maps

Status: `[x]` complete and verified

Generate `outline.json` with:

- section hierarchy and page ranges;
- Markdown files and passage ranges;
- counts by content type;
- review hotspots;
- bibliography and glossary locations;
- source-dependent regions.

Add a conservative symbol index for technical books. It should link symbols to local
definitions and occurrences without assigning one global meaning to overloaded
notation.

Implementation:

- [x] Added `outline.json`, derived from the existing section tree and emitted passage
      records. It maps each section and Markdown file to source-page and passage ranges,
      keeps separate block and passage counts by content type, and records review
      hotspots without counting split copies of one primary block more than once.
- [x] Added named bibliography, further-reading, glossary, notation, and index locations.
      Empty structural nodes fall back to an existing Markdown file, so every node remains
      navigable even when it owns no passage directly.
- [x] Added an exact list of source-dependent passages with their source regions and
      assets. The map does not reinterpret authority or collapse source links.
- [x] Added `symbols.json` using the named `explicit-local-definitions-v1` method. Entries
      are created only by explicit source phrases such as “where $E$ is” or “define $x$
      as,” scoped to the deepest section, and linked to same-section occurrences.
- [x] Kept inference disabled. The definition quote and its passage/source links are the
      evidence. The same symbol can have independent entries in different sections.
- [x] Added both artifacts to `manifest.json`, the generated README, and the documented
      output contract.

Acceptance:

- Every outline node resolves to existing files and source pages.
- Symbol entries quote or link a source definition; inferred meanings remain labelled.

Evidence:

- `tests/test_document_map.py` pins hierarchy, page and passage ranges, file resolution,
  block-versus-passage counts, de-duplicated review hotspots, named locations, and exact
  source-dependent regions.
- `tests/test_symbol_index.py` proves that overloaded `$E$` definitions remain local,
  quotes resolve to source passages, contextual copies do not create duplicate
  definitions, undefined `$q$` is declined, and empty output is deterministic.
- Rebuilding both maps in memory from the stored full-book provenance produced 211
  resolvable nodes and 42 explicit definitions for Relativistic QC, and 270 resolvable
  nodes and 174 explicit definitions for Atkins. Across 25,660 passages, no node had a
  missing file, no page range fell outside its source, no definition link was invalid,
  and no normalized definition was duplicated within its section.

### P1. Human reading experience

#### H1. Split books at useful chapter boundaries

Status: `[x]` complete and verified

- Prefer chapter-level bookmarks beneath top-level parts.
- Fall back to numbered heading evidence when bookmarks are coarse.
- Keep the root `index.md` shallow: parts and chapters only.
- Put detailed headings in a local table of contents inside each chapter file.
- Preserve stable section IDs and source page anchors when split policy changes.

Implementation:

- [x] Replaced the global depth choice with selective container expansion. Explicit
      Part bookmarks and Roman-numeral Part titles emit their opener separately, then
      emit each direct chapter subtree. Mixed top-level front matter, bibliography,
      data, solutions, and index branches remain intact.
- [x] Added an appendix-group rule for books whose single `Appendices` bookmark contains
      separately named `Appendix A`, `Appendix B`, and similar children. Ordinary
      `Appendix 1` subsections stay in one file.
- [x] Sort bookmark destinations into source-page order while preserving source order
      for same-page parent/child entries. This corrects the Relativistic QC outline,
      where `Contents` precedes an earlier `Notation Conventions` destination.
- [x] Added a conservative fallback for Part containers with no chapter bookmarks. Two
      or more `Chapter N` or integer-numbered heading blocks become stable child sections;
      other top-level branches never use this fallback.
- [x] Made `index.md` file-level only and added `## In this file` links for detailed
      headings in each split content file. Existing GitHub-style heading anchors and
      stable block-derived section IDs remain unchanged.
- [x] Passed the exact block-to-Markdown emission map into `chunks.jsonl`, so nested
      chapter records point to the emitted chapter file instead of their parent Part.

Acceptance:

- Atkins and Relativistic QC produce chapter-sized files rather than multi-megabyte
  part files.
- Every heading remains reachable from either the root or a chapter-local index.
- Existing source links and block accounting remain exact.

Evidence:

- Structure tests pin mixed top-level leaves, Roman-numeral containers, out-of-order
  destinations, selective index handling, and the numbered-heading fallback. Emission
  tests prove the root index omits local detail, chapter-local links resolve to emitted
  headings, and numbered cross-file references keep their existing anchors.
- The source-pinned GRASP corpus remains unchanged: four Part openers and 15 chapters
  produce 19 files with every expected boundary preserved.
- Rebuilding structure from the stored full-book provenance gives Relativistic QC 44
  content files with a maximum 34-page span, and Atkins 40 content files with a maximum
  51-page span. File starts are source ordered. All 5,669 Relativistic QC blocks and all
  19,662 Atkins blocks outside root front matter occur in exactly one file unit.

#### H2. Rank metadata candidates instead of trusting the first source

Status: `[x]` complete and verified

Score title and author candidates from embedded metadata, first-page headings,
bookmarks, repeated running titles, and the filename fallback. Store the selected
candidate, evidence source, alternatives, and quality status. Penalize obvious glyph
fragmentation such as `Qu u an tum`.

Implementation:

- [x] Added the deterministic `ranked-local-metadata-v1` selector. It keeps embedded
      titles, headings from the first four source pages, repeated front headings,
      repeated page-header titles, early top-level bookmarks, and a meaningful filename
      fallback as separate evidence.
- [x] Scores remain relative ranking points and are explicitly labelled as
      uncalibrated. Corroboration across sources and occurrences raises a candidate;
      probable glyph fragmentation, section-like titles, and very short strings lower it.
- [x] Retained existing generic/generated-title and placeholder-author refusals. Author
      selection remains conservative: embedded names may be split on explicit separators
      and gain corroboration from the front pages, but page prose alone does not create an
      author list.
- [x] Added `metadata_evidence` with the selected record, up to five ranked alternatives,
      rejected candidates, exact page/block evidence where available, penalties, and a
      high/medium/low evidence label. The established top-level `title` and `authors`
      fields remain compatible.
- [x] Added the selected fields and full evidence to `manifest.json`, candidate quality to
      the profile scorecard, and a short human summary to the generated README.
- [x] Kept GROBID fill-gaps-only. A GROBID value fills missing local metadata; a conflicting
      value is retained as a non-selected alternative with the policy reason.

Acceptance:

- The Relativistic QC title resolves to `Introduction to Relativistic Quantum
  Chemistry`.
- Existing labelled metadata fixtures do not regress.
- Metadata changes are explainable from recorded candidate evidence.

Evidence:

- The six existing labelled metadata fixtures retain their expected title and author
  results. New tests pin clean-versus-fragmented selection, exact source evidence,
  embedded author splitting/corroboration, meaningful filename fallback, repeated
  running-title fallback, GROBID evidence merging, manifest storage, profile quality,
  and the generated human summary.
- Re-evaluating stored Relativistic QC provenance selects `Introduction to Relativistic
  Quantum Chemistry` from matching page 2 and page 4 headings at high evidence quality
  (91 ranking points). `Relativistic Qu u an tum Chemistry` remains the first alternative
  at low quality (37 points) with `probable_glyph_fragmentation` recorded.
- Re-evaluating Atkins selects `ATKINS' PHYSICAL CHEMISTRY` from the embedded title plus
  matching page 1/page 3 headings at high quality. `Peter Atkins . Julio De Paula` now
  resolves to two authors, both corroborated on the front pages.

### P2. Accuracy measurement and model decisions

#### A1. Expand the agent benchmark to long technical books

Status: `[x]` complete and verified

Implementation:

- [x] Added 14 source-checked questions over the 545-page Relativistic QC book and
      1,085-page Atkins book. The set covers prose facts, equation definitions, symbol
      meanings, table cells, figures, cross-page explanations, references, and an
      unanswerable source-corruption prompt.
- [x] Kept both copyrighted PDFs outside the tracked corpus. The committed manifest and
      labels contain filenames, SHA-256 hashes, page counts, provenance hashes, expected
      pages, evidence block IDs, answer rules, representation and document classes, and
      review dispositions.
- [x] Added repeated `--retrieval-budget` arguments and measured chunk and passage page
      recall and precision at top 1, 3, and 5. Reports include per-query ranking time and
      the original conversion time from pinned provenance.
- [x] Added deterministic action-queue precision and recall against labelled evidence
      block IDs and `review.json`. This metric is independent of lexical retrieval.
- [x] Added evidence-preparation and inference timings, correct-refusal counts, input and
      output tokens, and result strata by representation and document class.
- [x] Added a deterministic `contains_all` answer rule for concepts whose word order can
      vary without changing the claim.
- [x] Declared calibration not applicable because the benchmark emits categorical
      outcomes and no confidence-like probabilities.

Measure:

- answer correctness;
- citation correctness;
- retrieval recall at fixed passage budgets;
- correct abstention;
- action-queue precision and recall;
- input tokens;
- conversion and retrieval time.

Acceptance:

- [x] Results identify failures by representation, document class, retrieval type, and
      passage budget, not only as one aggregate score.
- [x] No confidence-like probability is emitted, and both frozen reports record why
      calibration is not applicable.

Evidence:

- The model-free audit shows that top-3 passages match top-3 chunk mean page recall at
  0.8571 and improve mean page precision from 0.3095 to 0.4821. Top-1 passages regress
  by two page hits, so that lower budget is not accepted for long books.
- The labelled action queue has one true positive, no false positives, and no false
  negatives, for precision and recall of 1.0.
- The matched `qwen3.6:35b-mlx` run answers 10 of 14 questions correctly with 11 valid
  page citations and 11,508 input tokens. It passes all prose, symbol, table, and figure
  questions. Failures are isolated to one cross-page explanation, one reference, the
  equation-definition case, and the unanswerable case.
- [`agent-benchmark-long-books-2026-08-25-retrieval.json`](agent-benchmark-long-books-2026-08-25-retrieval.json)
  and [`agent-benchmark-long-books-2026-08-25-model.json`](agent-benchmark-long-books-2026-08-25-model.json)
  retain the full settings, timings, hashes, per-query outcomes, and strata.

#### A2. Re-run parser and layout bake-offs against pinned releases

Status: `[x]` complete and verified

- Preserve native engine quality evidence in the engine-neutral result.
- Compare current Docling layout presets on a stratified page subset before paying for
  full-book runs.
- Re-run Docling versus MinerU only where the labelled corpus shows an unresolved
  layout, table, equation, or scan failure.
- Pin model and dependency revisions in every result.
- Promote a new default only when it improves the relevant acceptance metrics without
  unacceptable runtime or memory cost.

Acceptance:

- [x] A model change must cite a frozen result artifact and the exact affected
      archetypes. No model change was accepted because none improved the labelled
      subset.
- [x] Leaderboard claims did not change the production default.

Evidence:

- All five Docling 2.108.0 layout models ran on the broken-font table, numbered
  equation, scanned mixed-layout page, and raster scientific figure. Every weight was
  pinned by repository commit, byte count, and SHA-256 and verified before scoring.
- Default Heron scores 29/78 facts. Heron-101 and both medium and xlarge alternatives
  score 28/78; Egret large scores 26/78. No alternative improves a labelled stratum.
- The Docling candidates take 149.7 to 155.3 seconds in aggregate and peak at 2.46 to
  2.70 GB RSS. The small resource differences do not accompany a quality gain.
- The pinned MinerU comparison scores 74/78 and remains the targeted table, equation,
  and scan fallback. Its raster result still emits unsupported structured chart data,
  so blanket routing remains unsafe.
- [`layout-bakeoff-2026-08-25.json`](layout-bakeoff-2026-08-25.json) retains source
  hashes, exact facts, dependency and model pins, native run IDs, timings, and Docling
  resource measurements. [`bakeoff-results.md`](bakeoff-results.md) records the
  decision and its limits.

## Product boundary

General vector search, corpus management, embedding services, rerankers, and RAG
orchestration remain outside pdf2md. The converter should emit a stable ingestion
contract that SQLite FTS, a vector store, an MCP service, or an agent can consume.
This keeps source conversion reproducible and avoids coupling it to one retrieval
stack.

Deferred unless evidence changes the boundary:

- a built-in vector database;
- an MCP server inside the converter package;
- automatic model-generated document summaries presented as source facts;
- blanket multi-engine parsing of every page;
- probability-labelled confidence without a held-out calibration set.

## Progress log

### 2026-08-24

- Created this plan from the Atkins and Relativistic QC full-book findings.
- Implemented shared PDF/page state, cross-tier geometry reuse, and figure progress
  reporting for E1. Added a regression test proving three figures on two pages use one
  document and two page handles.
- Added `scripts/benchmark_digitize_bundle.py` so chart changes can be timed against an
  existing bundle without rerunning Docling.
- Ran the fast suite: 570 passed, 2 integration tests deselected.
- A forced Atkins measurement reached chart digitization at 10m25s. The initial
  document-handle-only change processed 70 figures in about 133 seconds and projected
  another 38 minutes, falsifying the idea that PDF opens were the dominant cost. The
  run was stopped and its incomplete 227 MB `v2` directory was removed.
- Replayed the first 70 figures after geometry reuse: 196.723 seconds, 50 OCR-axis
  attempts, three accepted `vector-path` results, and no accepted OCR-axis result. OCR
  cost and repeated text grouping dominated this slice.
- Changed text-layer calibration to group page text once per figure, then filter those
  groups for each candidate frame. A full geometry-only Atkins pass now completes in
  174.005 seconds; the earlier implementation did not finish within ten minutes.
- Rejected a fitted path-density gate after a GRASP2018 scatter chart showed that an
  Atkins-only threshold would be too corpus-specific. Replaced it with a definitional
  gate based on the production line, scatter, and bar extractors.
- Froze gate results for 1,280 figures across Atkins and GRASP2018. All 15 known
  OCR-axis recoveries remain eligible. The exact gate reduces Atkins candidates from
  753 to 657 without claiming that this small labeled set estimates unseen recall.
- The fixed 70-figure replay now takes 24.768 seconds instead of 196.723 seconds and
  preserves all three accepted digitizations exactly.
- Completed a forced Atkins `v2` conversion in 16m00s. Its chart stage took 5m31s and
  produced the same 58 accepted chart IDs and 46 `vector-path` plus 12
  `vector-path/ocr-axes` method counts as `v1`. All structural and review counts match.
- The `v1` to `v2` comparison exposed one nondeterministic mixed-panel `kind` tie on
  figure 409. Replaced set iteration with a stable line-first tie rule and pinned it in
  a unit test.
- Replayed all 1,269 Atkins figures after that fix. All 58 accepted digitizations match
  `v1` exactly; there are no changed IDs. The replay took 443.931 seconds, showing that
  OCR latency still varies even after the repeated CPU work is gone.
- Ran the full suite after the gate implementation: 571 passed and 2 integration tests
  were deselected. Focused line, scatter, bar, negative-gridline, and deterministic-kind
  tests also pass.
- Added sequential run metrics to provenance and the generated README. The stored total
  is defined as the exact sum of ten named stage durations, with stage-specific work
  counts beside each duration.
- Added mutually exclusive chart outcomes: attempted equals accepted plus declined plus
  failed. OCR-axis attempts and geometry-gate refusals remain separate diagnostic counts.
- Cached CLI results now say how many completed stages were reused and identify the
  stored duration as the original run time.
- Added `pdf2md compare-runs` for stage-by-stage wall-time deltas. Its JSON output also
  carries each stage's before and after work counts, so a faster run with less attempted
  work cannot masquerade as an implementation speedup.
- Added deterministic clock and conversion-path tests for timing sums, provenance
  serialization, README output, chart accounting, and cache reload. The full suite now
  passes 577 tests, with 2 integration tests deselected.

### 2026-08-25

- Moved vision-cache accounting into the shared dictionary boundary. Every `.get()` and
  successful store now contributes to exact lookups, hits, misses, and writes without
  duplicating counters across four model-backed features.
- Added main-process and largest-terminated-child peak RSS to run metrics using the
  standard Unix `getrusage` interface. The README states that these are process-lifetime
  high-water marks, which keeps batch comparisons from implying per-document isolation.
- Extended `pdf2md compare-runs` to compare main-process peak RSS when both bundles have
  memory evidence.
- Collapsed exact duplicate RapidOCR and Docling OCR warnings during source reading.
  The filter is logger-scoped and temporary, so distinct engine warnings and pdf2md's
  own review messages remain visible.
- Added a unit test that emits three copies of one warning plus a distinct warning. The
  output contains the first copy, the distinct warning, and an exact two-repeat summary.
  The full suite passes 578 tests, with 2 integration tests deselected.
- Replaced the user-facing scalar confidence heading with an eleven-dimension quality
  scorecard. Machine-readable dimensions include exact counts or ratios where they are
  definitional, named evidence sources, and explicit calibration status.
- Preserved Docling's native parse, layout, and OCR grades at the adapter boundary.
  Image-backed equations now lower equation-text coverage without lowering structural
  completeness, and illegible prose appears as a high-severity unresolved error.
- Ran the full suite after the scorecard change: 582 passed and 2 integration tests
  were deselected in 43.45 seconds.
- Split review markers into action-required, source-dependent, and informational
  dispositions. Added a sorted `review.md` and exact `review.json`; the CLI and chunk
  records no longer treat intentional `--no-formula` equation crops as defects.
- Ran the full suite after the review-queue change and module split: 586 passed and 2
  integration tests were deselected in 41.77 seconds.
- Forced new Relativistic QC and Atkins bundles to test Q2 against the real books. The
  Relativistic QC bundle has zero actions and 1,848 source-dependent entries. Atkins has
  three actions and 3,092 source-dependent entries, with the three known illegible page
  856 paragraphs first in the queue. All human and machine count surfaces agree.
- Corrected Docling quality provenance to identify whether each retained score is the
  document score or a mean of available page scores. Both full-book runs supplied native
  document scores; raw values remain explicitly uncalibrated.
- Extended duplicate RapidOCR warning collapse through chart digitization after the
  Atkins rerun showed that axis OCR could repeat the same warning cluster after parsing.
- Ran the full suite after the book validation and reporting fixes: 588 passed and 2
  integration tests were deselected in 44.48 seconds.
- Added representation-aware word and number conservation at the logical-block to
  Markdown boundary. A raw glyph-region prototype was rejected after it produced 1,642
  changed Relativistic QC blocks and 6,110 changed Atkins blocks from normal PDF layout
  effects. The shipped check retains exact block evidence without creating those false
  actions.
- Ran the full suite after Q3: 592 passed and 2 integration tests were deselected in
  50.72 seconds.
- Added block-stable `passages.jsonl` records and a packaged Draft 2020-12 schema. A
  one-block edit keeps every passage ID stable and changes only that block's retrieval
  content hash. The manifest, README, and audit metrics expose the new artifact.
- Built the wheel and confirmed it contains the passage writer and canonical schema.
  The full suite passes 595 tests, with 2 integration tests deselected, in 43.07 seconds.
- Added post-context token budgets and structure-aware passage splitting. GFM table
  continuations repeat their caption and header; equations and explicitly referenced
  figures carry the extra source regions used for context.
- Added the offline lexical default plus explicit Hugging Face model or local-path
  tokenizers. The emitted count includes special tokens, and a limit above a tokenizer's
  finite model capacity is refused.
- Built the wheel and confirmed that both passage modules, the writer, schema, and direct
  Transformers dependency are present. The full suite passes 603 tests, with 2 integration
  tests deselected, in 45.96 seconds.
- Kept I2 open because the frozen agent-benchmark bundles predate `passages.jsonl` and
  still exercise `chunks.jsonl`. Re-running that benchmark against regenerated passage
  bundles remains the citation-precision gate.
- Extended the benchmark with `--retrieval passages` and a model-free
  `--retrieval-audit`. Passage records can be reconstructed in memory from stored
  provenance, so frozen bundles remain untouched.
- Closed I2 after the top-one audit and matched model comparison. Chunks and passages
  both hit 11/11 labelled pages with mean page precision 1.0, then both produced 11/11
  correct answers and valid page citations. Passage input fell from 8,105 to 4,720
  tokens, a 41.8 percent reduction.
- Ran the full suite after closing I2: 604 tests passed and 2 integration tests were
  deselected in 44.55 seconds.
- Added deterministic `outline.json` and conservative `symbols.json` artifacts. Full-book
  reconstruction checks covered 25,660 passages, 481 section nodes, and 216 explicit
  local definitions with no unresolved file links, invalid page ranges, invalid
  definition links, or duplicate normalized definitions within a section.
- Built the wheel and confirmed it contains both map writers. The full suite passes 608
  tests, with 2 integration tests deselected, in 53.17 seconds.
- Replaced depth-wide book splitting with selective Part and appendix-group expansion,
  restored bookmark destinations to page order, added conservative heading fallback,
  kept the root index file-level, and added local contents to split files. Prospective
  regeneration from stored provenance yields 44 Relativistic QC files with a 34-page
  maximum and 40 Atkins files with a 51-page maximum, with exact block partitioning.
- Built the wheel after H1. The full suite passes 612 tests, with 2 integration tests
  deselected, in 48.43 seconds.
- Added ranked, inspectable bibliographic evidence. Relativistic QC now selects the clean
  repeated title and records the broken cover form as a penalized alternative; Atkins
  retains its corroborated title and resolves its embedded author separator into two
  names. Manifests and generated READMEs expose the evidence without changing the
  established `title` and `authors` fields.
- Built the wheel after H2. The full suite passes 617 tests, with 2 integration tests
  deselected, in 44.04 seconds.
- Added the long-book agent corpus with 14 source-rendered labels across eight
  representation classes. The repository stores only hashes and labels; local setup and
  provenance-pinning instructions are in `docs/agent-benchmark.md`.
- Extended the retrieval audit to fixed top-1, top-3, and top-5 budgets, exact page
  recall, action-queue precision and recall, ranking time, original conversion time,
  and nested representation and document-class strata.
- Ran the long-book model-free audit and matched `qwen3.6:35b-mlx` evaluation. Top-3
  passages match chunk recall with better precision; the model answers 10 of 14 and
  exposes four specific ingestion gaps for later retrieval work.
- Ran the full suite after A1: 620 tests passed and 2 integration tests were deselected
  in 44.28 seconds.
- Added an offline Docling layout runner that pins all five model repositories to exact
  commits and verifies each weight's size and SHA-256. Native records include the fixed
  pipeline configuration, dependency versions, source hashes, wall and CPU time, and
  peak resident memory.
- Ran the four unresolved labelled strata through Heron, Heron-101, and all three Egret
  sizes. Default Heron leads the Docling candidates at 29/78 facts; the alternatives
  score 26 to 28 and do not improve any stratum.
- Froze the six-engine comparison in `docs/layout-bakeoff-2026-08-25.json`. MinerU keeps
  its targeted fallback role at 74/78, while its unsupported raster chart data keeps it
  out of the blanket default path.
- Ran the full suite after A2: 623 tests passed and 2 integration tests were deselected
  in 47.00 seconds.
- Added `base-state.json`, `StoredEngine`, and `pdf2md enrich` for parser-free equation,
  chart, and description passes. Derived versions retain their exact parent, state,
  configuration, model, and implementation lineage. Region caches are written after
  each successful model call and survive an interrupted version.
- Added evidence-aware enrichment preflight and `--dry-run`. The command reports current
  transcriptions, chart data, descriptions, and remaining eligible regions without
  allocating a version or creating a cache file.
- Made partial optional model work explicit. Completed source-backed bundles with failed
  vision calls remain readable, but they no longer satisfy the exact-run cache. Repeating
  the command creates an immutable retry version and reuses successful region results.
- Added readable document directories, recursive library discovery, output-tree exclusion,
  and source-identity checks for listing and pruning. Existing hash-only libraries remain
  compatible.
- Removed the superseded top-level `ROADMAP.md`. This record now preserves the completed
  workstream; `README.md` holds current product behavior and boundaries, while focused
  research notes retain candidate methods and measured decisions.
- Re-ran the complete active suite from the cleaned tree: 639 tests passed and 2
  integration tests were deselected in 49.70 seconds.

## References

- [pypdfium2 Python API: threading and process guidance](https://pypdfium2-team.github.io/pypdfium2/python_api.html)
- [Docling chunking concepts](https://docling-project.github.io/docling/concepts/chunking/)
- [Hugging Face Transformers tokenizer documentation](https://huggingface.co/docs/transformers/fast_tokenizers)
- [Docling confidence scores](https://docling-project.github.io/docling/concepts/confidence_scores/)
- [Docling model catalog](https://docling-project.github.io/docling/usage/model_catalog/)
- [JSON Schema Draft 2020-12](https://json-schema.org/draft/2020-12)
- [Python `resource` module](https://docs.python.org/3/library/resource.html)
- [Linux `getrusage(2)` units and scope](https://man7.org/linux/man-pages/man2/getrusage.2.html)
- [pdf2md accuracy improvement notes](accuracy-improvement-notes.md)
- [pdf2md agent benchmark](agent-benchmark.md)
