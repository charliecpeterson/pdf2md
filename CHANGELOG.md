# Changelog

All notable changes to pdf2md. Format loosely follows
[Keep a Changelog](https://keepachangelog.com). The output format is versioned
separately by `FORMAT_VERSION` in `schema.py`; a breaking change there is noted
here.

## [Unreleased]
### Changed
- `FORMAT_VERSION` is `0.12`: table artifacts carry headers, `<block>.glyph.md` is new, and
  `profile.json` gains `tables_structurally_flagged` beside `tables_verified` (which counts
  text-backed cells and says nothing about whether whole rows survived), plus
  `glyph_accent_damaged_blocks` and `reading_order_pages`.
- Retrieval passages are now bounded after document and section context is added.
  Prose prefers paragraph and sentence boundaries, lists and code preserve lines,
  and GFM table continuations repeat their caption and column header. Equations carry
  a nearby explanatory sentence; figures carry an explicit referring sentence when
  one is present. The offline lexical counter remains the default, while
  `hf:<model-or-local-path>` selects the exact tokenizer used by an embedding model.
  The configured limit is rejected when it exceeds a tokenizer's finite model capacity.
- The agent benchmark can rank `passages.jsonl` and reconstruct passages in memory from
  older stored provenance. On the pinned 11-question corpus, chunk and passage modes both
  produce 11 correct answers and 11 valid page citations; passages use 41.8 percent fewer
  input tokens in the matched `qwen3.6:35b-mlx` run.
- New conversions use readable output directories such as `paper-a1b2c3d4/v1`
  instead of a bare content hash. The hash suffix preserves document identity,
  and existing hash-only libraries remain discoverable.
- `convert --help` groups parser/output, scan/OCR, equation, figure, vision, and
  verification options. Completion guidance now recommends table review only for
  OCR table candidates and uses portable read/review actions for other documents.
- Enrichment preflight now separates total inventory from likely model work. It reports
  image-backed equations, existing transcriptions, accepted chart datasets, remaining
  chart candidates, eligible description crops, and existing descriptions. `--dry-run`
  prints this plan without creating a version or cache file.
- Bibliographic fallback ignores generic section headings, word-processor titles,
  and implausible single-token workstation authors instead of emitting them as facts.
- Bibliographic title selection now ranks embedded fields, front-page and repeated
  headings, repeated running titles, early bookmarks, and meaningful filenames. The
  manifest retains the selected evidence, alternatives, penalties, rejections, and
  uncalibrated quality label. Likely glyph fragmentation loses to clean corroborated
  page evidence; optional GROBID candidates keep the existing fill-gaps-only policy.

### Added
- A scan carrying an embedded OCR text layer is now recognised and treated as a scan. It is
  the one condition under which this project's whole premise inverts: the text layer exists,
  so nothing routes the page down the scanned path, and every glyph-truth check then
  verifies the engine against the same corrupted characters and reports agreement. On a 1972
  data-table paper that meant 79 of 82 tables correctly flagged as structurally broken while
  `O.00]7` was handed over as a value with no marker, `ocr_pages: 0` and `tables_verified:
  82`. Detection is two structural properties — one image covering most of the page, and the
  text over it drawn invisibly, which is what an OCR overlay must do and what page text
  never does. Geometry alone conflates it with a full-page figure plate; render mode
  separates them by construction. Across 44 documents and 828 pages it flags 30/30 pages of
  that scan and nothing else. The pipeline also warns that a fresh transcription is
  available: measured on three of those pages, the embedded layer leaves 22% of numeric
  tokens malformed, `--force-ocr` 8%, and `--engine mineru` 1% with 3.4x as many tokens
  recovered.
- Scanned tables get row accounting too. Everything else in the table audit needs glyph
  geometry, so on a scanned page it refused and a dropped row went unreported — on exactly
  the documents where extraction is worst. `raster_row_findings` reads the table's own crop
  through `row_locator.projection_row_bands`, which projects the panel's leading stripe
  where row labels live, and compares the printed row count against the grid's.
- A grid that is a fragment of its own table is refused rather than confirmed. The engine's
  cells should reach the edges of the block it labelled a table: across 95 tables the
  smallest span was 0.79 of the region and the median 0.95, and the one below that came in
  at 0.09 — two cells emitted for a table another parser read as ninety-five. Because every
  sweep here clamps to the cell extent, that fragment was being compared against itself and
  reported as agreement. Found by running two engines over the same corpus.
- Word recall now refuses on an ambiguous region instead of guessing. It compares a block's
  text against the glyphs in its box, which assumes the box is that block's alone; where two
  prose blocks overlap by more than a sixth of the smaller, a word counted missing may
  simply belong to the neighbour. Those blocks get an informational region-boundary note
  rather than a recall action — the last surviving false positive in this metric was exactly
  that, a stray numeral inside a paragraph's box, and `quality.py` already admits that block
  accounting does not measure region-boundary accuracy.
- `scripts/eval_engine_text_agreement.py`: the same concordance for the prose checks, which
  were changed twice this cycle without anything independent confirming what survived. Over
  1149 matched blocks it puts 12 of 15 recall findings on blocks the two engines read
  differently. Read that cell rather than the recall figure: two parsers differ at word
  level on roughly half of all prose blocks, so disagreement is a weak defect proxy here in
  a way it is not for a table value.
- `scripts/eval_engine_order_agreement.py`: reading-order concordance between two engines.
  Match blocks across parsers by box overlap and ask whether the check fires on the pages
  they order differently. Over 132 pages of the frozen corpus it flags 7 of the 8 pages the
  two engines order differently and 1 of the 123 they agree on — the first unlabelled
  measurement that check has had, against two pages previously verified by hand.
- `tests/blind_pdf_corpus_v2.json`: a replacement unseen corpus, ten papers over 209 pages
  across five arXiv categories, selected by API query rather than by taste and frozen by
  hash without being converted. The first corpus stopped being unseen the day its
  individual tables were inspected to diagnose three false-positive classes.
- Born-digital tables are audited row by row, not only cell by cell. The page's own ink is
  projected into rows and every value a printed row spells has to reach a cell of the grid
  rows covering it, which catches what per-cell verification structurally cannot: a row the
  engine never created has no cell to verify. Three text-only signatures ride alongside and
  need no source — several whitespace-separated numbers in an otherwise single-value column
  (collapsed rows), a lone value in a non-leading column with the rest of its row empty (a
  value that lost its row), and a header region carrying a value shaped like its column's
  data (an absorbed first data row). A text signature stands at medium alone and rises to
  high when the row accounting measures a merge or a loss in the same table.
- Whole columns missing from a table grid are reported. A column the engine never created
  has no cells, so it has no lane for the per-value comparison to use and only its
  out-of-grid ink gives it away. Two guards keep it apart from a column that merely shifted:
  the engine's cells in that lane must be empty, and out-of-grid ink must sit at the same x
  in every row and be no wider than a column.
- Findings reach every artifact derived from a table, not just `document.md`. Each
  `data/tables/<block>.md` opens with its own provenance and audit header, its `.json`
  carries `grid_audit` and `post_emission_warnings`, and findings raised elsewhere on the
  same page become a pointer into `review.md` — a file read on its own can no longer present
  as an unmarked, authoritative grid.
- Every table gets a crop of its printed region under `assets/`, linked from the Markdown
  and from its artifacts, so the source can be checked without opening the PDF. This is
  `TableData.source_crop` and does not make the image authoritative; `crop_path` still means
  that and is still set only where the cells are unusable.
- `data/tables/<block>.glyph.md`: the table region read straight out of the PDF's glyph
  layer in the engine's columns — measured rows against a modelled grid. It ships beside the
  engine's table for comparison and is never the emitted table.
- Reading order is verified. Everything else in the audit is order-insensitive on purpose —
  word recall compares multisets, numeric conservation counts values — so a two-column page
  whose columns the engine interleaves conserved every token and passed every check while
  reading as nonsense; `quality.py` said as much in its own scorecard. A page's columns are
  recovered from where its blocks' left edges cluster, and the emitted order is compared
  against the printed one. It reports the minimum number of blocks whose removal would
  restore the order, not the count of inverted pairs, and sets aside blocks that begin at no
  column start rather than forcing them somewhere.
- Reading order is checked a second way, against the document's own numbering. When a page's
  leading ordinals sort to an unbroken run, that run *is* the printed order — no column
  model, no thresholds, nothing to tune — so a page it convicts is reported high rather than
  medium. The two mechanisms fail for different reasons and cover each other: on one paper's
  reference page, geometry and numbering independently arrive at the same 16 misplaced
  blocks, and the printed reference numbering confirms both.
- Printed lines cut across several blocks are reported, informational. The detection is
  exact (two blocks sharing a vertical band inside one column) but the judgement isn't: a
  reference entry shattered into `'Serre,'`, `'C.;'`, `'rey, G.'` is a defect, a masthead's
  `Received:` and its date are the layout. Measured and shown, verdict left to the reader.
- `scripts/eval_table_audit.py` and `tests/table_audit_labels.json`: precision and recall for
  the table row/grid findings over 13 labelled tables. The labels were established from the
  source page's own text and from two independent line-finding mechanisms agreeing, never
  from running the audit.
- `scripts/eval_engine_table_agreement.py`: two engines' table grids for one source, and a
  contingency of audit findings against engine disagreement. Engine disagreement is
  unlabelled but plentiful, which is what a thirteen-table label set is not.
- `scripts/qa.py` reports the verification signals across a corpus — flagged tables,
  reading-order and split-line pages, low-recall and accent-damaged blocks. Drift, never
  invariants: a document is not worse for having had its defects noticed.
- `pdf2md enrich` now adds equation transcription, raster-chart recovery, or crop
  descriptions to a completed bundle without rerunning its layout parser. Each run writes
  a new version and records the source provenance, stored-state hash, selected stages, and
  output-shaping inputs.
- New bundles carry `base-state.json`, the engine-neutral parser result needed for later
  enrichment. Model results are checkpointed atomically per region, so an interrupted run
  can reuse completed equations and figures.
- Bundles now include `outline.json`, a deterministic section, file, passage, review,
  named-location, and source-dependence map, plus `symbols.json`, a conservative index
  that quotes explicit definitions and keeps overloaded notation local to each section.
- Bundles now include stable block-addressed `passages.jsonl` records for retrieval and
  embeddings plus `passages.schema.json`. Passage IDs survive unrelated block edits;
  content hashes track contextualized retrieval text. Records include breadcrumbs,
  source regions, neighbors, authority, review state, typed assets, and tokenizer counts.
- Profiles now partition block-to-Markdown word and number conservation into exact,
  formatting-only, source-dependent, unexplained-loss, and unexplained-addition counts.
  Unexplained examples link to their page, bbox, block, and Markdown artifact, and only
  those high-confidence block-level differences create new review actions.
- Generated bundles now include `review.md` and `review.json`, sorted so likely
  defects appear before valid image-dependent entries. Review counts agree across
  the README, profile, manifest, CLI, and queue.
- Generated bundles include an evidence-backed quality scorecard with independent
  accounting, structure, text, OCR, equation, table, figure, metadata, and error
  dimensions. Docling-native parse, layout, and OCR grades are retained as
  uncalibrated engine evidence.
- `pdf2md list` discovers completed documents recursively below an output library and
  reports the latest content path, page count, status, and review-marker count without
  requiring the original input PDFs.
- `pdf2md doctor` checks the active engine, required packages, configured paths,
  optional executables, and an OpenAI-compatible vision endpoint on request.
- Conversion completion output now names the main content file, inventory,
  review count, and the next table-review command. Installed-wheel integration
  tests exercise the CLI from a clean environment.

### Fixed
- Merge detection was switched off entirely on a third of tables. `row_accounting` excludes
  header rows from the merge count and decided "header" with `RawCell.header`, which Docling
  sets for a leading label column as well as a column heading — so a table with row headers
  had every row excluded and no merge could ever be reported. It was 32 of 95 tables
  measured, and it is why a textbook row-pair collapse went unreported. `RawCell` carries
  `column_header` again and the exclusion uses that, falling back to row 0 when the engine
  names no column heading. After the fix, 0 of 86; the table concordance moves from 8
  findings on disputed tables to 10, still with none on tables the two engines agree about.
- The wrap guard silenced both merge checks on a row-pair collapse. It excluded any row
  whose cell text overran its box, which a cell reading `0.965 0.969` does in a narrow
  numeric column — so every row of such a table was excluded and a table printing 9 rows as
  6 went unreported. It now also requires the cell to be long in absolute terms; nothing
  under 40 characters is a wrapped paragraph. The wrapped-prose tables it was built for stay
  silent, and the collapses come back.
- `_numeric_columns` could not see a column where every cell had been collapsed. It needs
  most cells to be a lone number, and in a uniformly collapsed column none ever is. A column
  whose cells consistently hold the same count of values, more than one, now qualifies.
- Word recall counted a word the glyph layer drew in two runs as two lost words. A styled
  capital splits `ReAct` into `reac` + `t` with no hyphen to join on, which flagged the title
  block of a paper twice. Adjacent source words are now joined when the emitted text actually
  contains the result — a stricter validator than a page vocabulary, and available because
  the emitted text is the thing being compared.
- `merged_cells` could not see a table flattened to one data row. It requires the column to
  be numeric — three lone numbers elsewhere in it — which one data row can never supply, and
  the row-band check that would otherwise catch it is suppressed by the wrapped-cell guard,
  because a cell holding eleven rows of content does overrun its box. Four or more
  whitespace-separated values in a single cell now stands as its own evidence. Found by
  reading the tables where two engines disagreed and the audit stayed silent.
- `shifted_values` read a multi-line header's label fragments as stray values. A header
  spanning several grid rows leaves rows holding one fragment (`(%)*`, `No. of`); the lone
  cell now has to be a value for the row to count as one that lost its place.
- `merged_rows` counted wrapped cells as collapsed rows. Row-band counting assumes one
  printed line per row, which holds for the dense numeric tables the check was labelled
  against and fails for any table with a paragraph in a cell: a three-row table of model
  answers reported nine merges. It was the most common finding on the frozen unseen corpus
  — 13 of 19 flagged tables, 11 of them with cells of 119 to 889 characters. Two guards fix
  it, and both are needed: a row whose cell text cannot fit its own box is excluded, and a
  printed line that reaches fewer than two columns is a cell's continuation rather than a
  row. Blind-corpus `merged_rows` findings fall from 13 to 2, with the real ones intact.
- Conservation counted markup as content on one side only. `token_accounting` now runs the
  same normalization over the source as over the output, because a table's source *is* its
  own rendered markup: `td`, `tr` and `tbody` were source words and stripped from the
  output, so one 29-row HTML table reported losing 471 words, and every `<sup>` in a prose
  block cost two. On the paper that started this work that was 25 of 25 conservation flags,
  every one an artifact.
- The quality scorecard called table verification `full` for a document whose own review
  queue carried a high-severity dropped-row finding. A table can be text-backed cell by cell
  and still be missing whole rows; the row-level audit now feeds the dimension.
- Word recall measured its own artifacts. The source side is now read script-split and
  hyphen-joined, and script tags become a space rather than nothing, so both sides tokenize
  the same way: a reference marker the draw order glues onto its base word, a word broken
  across a line by a soft hyphen the font can't decode, and a `<sup>` run are no longer read
  as lost words. On a clean paper this took eleven low-recall blocks down to three, and the
  three are real.
- Blocks that lose words now say so where they are read. The measurement has existed since
  the metric was added and reached `profile.json` as a count and nothing else, so a block
  missing eight of its words looked identical to a clean one in the Markdown. Lost
  diacritics are counted and reviewed separately: the content is present and misspelled,
  which a reader checking a reference list needs to know without a marker beside every
  accented surname.
- A table whose caption and column header exceed `passage_max_tokens` aborted the entire
  conversion. It now degrades to unheadered row passages with a warning. Three of the ten
  documents in the frozen unseen corpus converted to nothing because of this, on code that
  predates this cycle.
- Versions with vision calls that exhausted their retries no longer satisfy the exact-run
  cache. They remain valid source-backed bundles, but the CLI marks them `PARTIAL ENRICHMENT`;
  repeating the same command creates a new version and retries cache misses while reusing
  successful regions. An older healthy exact match still wins over a newer partial run.
  Generated READMEs and provenance retain the failed-call count.
- Repeated RapidOCR warnings are collapsed during source reading and chart-axis OCR.
  Each stage retains the first distinct warning and records exact suppressed-repeat counts.
- Docling quality provenance now distinguishes native document scores from page-score
  means instead of describing every retained score as a page average.
- Recursive directory conversion excludes its configured output tree and verified
  stored source copies, preventing repeated runs from converting their own artifacts.
- `pdf2md prune` ignores unrelated directories that happen to contain `v<n>` folders;
  document roots must carry a source whose hash matches the directory identity.
- `coverage` accepts the conversion's output root and identifies the exact selected
  version. Its output now separates block dispositions from review markers, avoiding
  the former `flagged: 0` beside a non-empty `flags` list.
- Numeric review sheets call unfilled cells a prepared sample and render missing
  source table crops on demand from recorded page geometry.
- Docling is capped below 2.109 while RapidOCR remains below 3.9. This removes slow
  pip resolver backtracking across mutually incompatible releases.
- CUDA formula conversion now refuses early with an install or `--no-formula` remedy
  when the matching Python development header is absent. Docling previously printed
  a compiler failure and continued with missing formula enrichment.
- Docling's blocking parse now reports the source page count and states that
  per-page progress is unavailable instead of appearing to promise an ETA.
- A hash-pinned ten-document blind corpus now gates source identity, page counts,
  block accounting, structural completion, main artifacts, and review burden. The
  first 200-page Linux run passes all structural checks; four documents carry 41
  explicit review markers. The corpus makes no semantic-accuracy claim.

### Removed
- `--ocr-vlm`, `--preprocess-scans`, and the per-block OCR/preprocessing code.
  Whole-page `--ocr-page-vlm` is the supported vision path for scans. Recorded
  runs recovered prose, tables, and equations where block OCR only replaced prose.

### Changed
- A hash-pinned post-experiment synthesis now records the numeric replacement
  authority after confidence, held-out review, rendering stability, exact internal
  checks, and new-layout geometry evaluation. Automatic OCR value promotion remains
  undefined: the fixed reader threshold has only two labelled proposals with a 65.8
  percent wrong-replacement upper bound, and the learned threshold admits one
  held-out wrong read. A dated audit finds no current supplement or trusted database
  with semantic fields that overlap extracted values, so no nominal adapter was
  added. Exact user-supplied references remain eligible for semantic override.
- A second source-pinned column-geometry gate covers four new layouts under seven
  deterministic variants, including combined blur and downsampling. It exposed 37
  wrong repeated-consensus mappings on proportional long labels. An exact-count
  persistent-ruling guard reduces the final result to 238/245 exact mappings, zero
  wrong mappings, and seven refusals while leaving the earlier 798-case gate
  unchanged. The locators remain evaluation-only.
- Rendering-instability evaluation now includes 14 human-boxed natural primary errors
  beside 56 clean controls, using 24 frozen render/preprocessing variants and the same
  pinned PP-OCRv6 reader. Instability identifies 13/14 errors and marks 15/56 controls,
  supporting its use as a review-ranking signal but not as verification or correction.
  Expected-value scoring now handles valid Fortran `D` exponents symmetrically.
- Leave-one-document-out review evaluation now fits evidence-signal weights on 30
  documents and freezes them before ranking the excluded error-bearing document. At
  five reviews per document, active ranking finds 8/14 errors versus a 3.74
  confidence-stratified mean, but loses on some NASA and Slater budgets. The default
  therefore remains confidence-stratified rather than being promoted from the pooled
  result.
- A source-pinned exact internal-relation gate checks printed totals, repeated values,
  parameter symmetry, and attention-width conservation without tolerances. Its first
  24 cases produce 21 agreements, two review flags for natural NASA OCR errors, and
  one refusal for a nonnumeric token. The report cannot emit replacement values;
  rounded radial normalization remains in the separate approximate scientific gate.
- The source-labelled figure gate now covers 27 scientific figures in five documents
  from four publisher families. It separately checks content containment, captions,
  fragment groups, initial-block dispositions, and furniture exclusion. Continued
  NASA figures merge only when an explicit continued-caption anchor and aligned
  fragments agree; mixed text/equation graphical abstracts can include nearby visual
  components while their text blocks remain searchable. The frozen result has 27/27
  exact content boxes, 14/14 labelled caption associations, nine of nine furniture
  exclusions, and no remaining fragmented logical figures.
- Local Surya equation transcription adds blank context around the in-memory crop
  before recognition, preventing edge-token loss without changing the saved source
  image. A hash-pinned recovery gate improves the equation corpus from 8/12 to 10/12
  fully exact, reaches exact scores for every component except one scanned subscript,
  and records zero regressions among crop-backed exact controls.
- Output format 0.11 selectively expands Part-like bookmark containers into chapter
  files. It handles explicit Part headings, Roman-numeral Part titles, mixed front and
  back matter, and out-of-order bookmark destinations without splitting index letters
  or ordinary appendix subsections. Part openers remain ordered files, the root index
  stays file-level, and each content file carries its detailed local contents. Coarse
  Part bookmarks can fall back to two or more numbered chapter headings.
- Explicit multi-panel captions now join adjacent detected panels into one figure,
  including nearby unnumbered panel-title text that the detector placed outside the
  image box. Labelled publisher UI and promotional graphics are removed before crop
  rendering; source-labelled figure checks pin retained content boxes and captions.
- Output format 0.8 moves accepted chart CSV and deterministic plotting code
  from inline Markdown fences into linked `data/` and `code/` files.
- Output format 0.7 replaces the overloaded profile `lossless` field with separate
  `accounted_for`, `complete`, `needs_review`, and review-reason signals. The
  in-memory coverage report keeps `lossless` as a compatibility alias for
  `accounted_for`.
- QA and labelled-accuracy checks can pin source SHA-256 values, preventing a
  same-named replacement PDF from being scored against the wrong baseline.
  Restored arXiv 1706.03762v7, arXiv 2207.10841v3, and the first 50 pages of the
  pinned Slater scan now carry hashes in their applicable QA and label files.
- Equation evaluation has a `--check` gate and rejects missing labels, empty runs,
  and source-hash mismatches instead of printing a clean-looking partial report.
  Scan labels can pin a source page so the evaluator follows equations into the
  whole-page transcription that replaced historical per-equation block IDs.
- Unavailable historical QA entries were retired after repository, filesystem, and
  source searches could not identify their original bytes. Every active QA,
  equation, and labelled-accuracy source is now present and checked against one
  consistent SHA-256.
- Completed conversions are now reused by a run fingerprint covering the source,
  pdf2md implementation, effective non-secret configuration, engine identity,
  relevant dependency versions, model identifiers, and vision prompt/cache schema.
  Changing those inputs creates a new version without requiring `--force`.
- Vision inference cache keys now include the exact prompt and context, endpoint,
  model, image bytes, temperature, and token cap. Raster chart reads use the same
  cache instead of calling the model again on identical inputs.
- TOML configuration rejects unknown keys.
- Each document directory keeps one atomically written, hash-verified `source.pdf`
  outside its version directories so every derived version shares the same audit
  source.
- Identical files under `assets/` are hard-linked across completed versions, reducing
  repeated page-raster and crop storage without changing bundle paths or prune behavior.
- Scan OCR, figure enrichment, and vision-cache persistence now live in focused modules.
  `pipeline.py` retains conversion ordering and output finalization.

### Added
- The agent benchmark now supports OpenAI-compatible Chat Completions and Responses
  endpoint shapes. A frozen six-question equation/figure extension pins conversion
  provenance and required crops, accepts exact fractional numeric answers, and records
  a paired 5/6 result with 23.2 percent fewer bundle input tokens. Its Slater
  `rho_nu` failure remains a release blocker rather than being hidden by the aggregate.
- Numeric benchmark questions can declare a hash-pinned deterministic calculation.
  The first paired control verifies operand selection and decimal arithmetic
  separately, correcting the model's `2.587225` ratio to `2.589085` for 79 additional
  input tokens.
- A source-pinned agent benchmark compares exact-answer accuracy from bounded
  bundle chunks against rendered source-PDF pages while recording citations,
  opened assets, review flags, token use, and scientific-answer release gates.
  Its JSON summary includes per-mode totals, token reduction, accuracy delta, and
  the retrieval/generation limits used for the run. Chunk retrieval prioritizes
  distinct exact query terms, and chunks stop at page boundaries so citations are
  unambiguous. Paired `--check` runs require bundle accuracy to meet PDF accuracy
  and at least 20 percent fewer bundle input tokens.
- A MinerU CLI adapter provides the measured high-accuracy path for scanned pages
  and difficult tables/equations while keeping MinerU in a separate environment.
  pdf2md re-renders source crops and ignores MinerU's unverified chart data.
  Long runs stream debug output and retain a bounded error tail instead of holding
  the complete CLI log in memory until conversion finishes. The adapter raises
  MinerU's supported task deadline from one hour to six hours for large books,
  while preserving an explicit environment override.
- A source-pinned native engine bake-off runner and scorer cover deterministic
  vector charts, a broken-font table, a numbered equation, a raster scientific
  chart, and a scanned mixed-layout page. Native readers support pdf2md, Docling,
  PaddleOCR-VL, and MinerU without forcing them through one conversion schema.
- `manifest.json` gives agents a compact entry point with Markdown navigation,
  quality signals, representation paths, and direct review links to crops and
  source pages. Full content remains in `provenance.json`.
- `chunks.jsonl` emits bounded, section-local retrieval units with block IDs,
  Markdown paths, source-page links, asset paths, and review status.
- Visible review markers link directly to the archived source page when no crop
  is available. The manifest carries the same source-page target for every flag.
- Tier 1.5 handles multi-panel figures too: OCR'd tick tokens map back to page space
  through the crop's render geometry and each frame calibrates with the same band/fit
  machinery as tier 1 (shared `_fit_ticks`). Axis fits gained leave-one-out outlier
  rejection: one stray in a tick band (an OCR fragment of a rotated axis title reading
  as "1", a misread label) no longer wrecks a clean fit — this also fixed the arxiv
  6-panel figure's "weak" panels, which now all calibrate at R^2 1.000 with none
  skipped.
- Tier 1.5 digitization (`vector_ocr_digitize`, model-free, default on): journals often
  OUTLINE figure fonts to paths, leaving exact vector curves but no text to calibrate
  against — tier 1 was blind to every figure in such papers. The new tier keeps the
  near-lossless vector curve geometry and reads the axis numbers off the rendered crop
  with RapidOCR (`calibrate.analyze_raster`, upright-assumed for born-digital renders);
  confidence is capped by the OCR read (`vector-path/ocr-axes`). The OCR tick parser
  learned the flattened log-superscript form ("10-2" is 10^-2) and scientific notation.
  Single-frame figures only (multi-panel raster/vector frame correspondence is
  ambiguous). Verified on a journal paper whose figures were invisible before: reads
  correct to ~2 significant figures at confidence 0.89-0.90. Also: `_calibrate` merges
  text read from OBJECTS (container transform composed) with the textpage chars, since
  pdfium reports form-local charboxes for text nested in form XObjects.
- Multi-panel vector figures digitize per panel: every axes frame in the figure — a rect
  path, or spines assembled from separate strokes — that calibrates against its own
  neighboring tick labels contributes its series, tagged "panel N series M" in the CSV
  (`Digitization.series_names`). Weakly-calibrated panels are skipped with a visible
  note instead of dragging the whole figure below the emit floor. Also fixed along the
  way: figures embedded as form XObjects (LaTeX `\includegraphics` of a matplotlib PDF)
  had form-local coordinates — the container transform chain is now composed, which is
  what made these figures invisible to the digitizer before; and a dropped superscript
  minus on log ticks (10^-3 read as 10^3, monotonic so the linear sign repair can't see
  it) is repaired by the powers-of-ten prior (`restore_log_signs`), hedged like
  `restore_signs`. A real 2-panel/7-series-each log figure now recovers at R^2 0.992.
- `scripts/eval_raster.py`: measures the raster pre-scan against synthetic scans with
  self-generated truth — gate verdicts (tangled charts must gate, crossing pairs must
  not), calibration endpoint error, skew detection — plus an optional live-VLM half
  (`eval_raster.py <model>`) reporting point error and whether the numbers would print.
- `--figure-svg`: export each born-digital figure's region as SVG beside the PNG crop —
  the lossless text form of a vector figure (diagrams and schemes, not just charts).
  Via pdftocairo (poppler), through a temp cropbox'd one-page PDF because pdftocairo's
  crop flags are silently ignored for SVG output; degrades to PNG-only with a log line
  when pdftocairo is absent. Scanned pages skip (their SVG would just wrap the raster),
  and so does any embedded-raster figure on a born-digital page — a journal's
  pre-rasterized plot exports as an SVG wrapping the bitmap as base64 (bigger than the
  PNG, no text value), so only genuinely vector output is kept.
- Table cross-reference: when a figure's caption or recovered labels name a printed
  table ("Listed in Table 5") and the figure's plot data was NOT printed (none, gated,
  or withheld), the markdown emits a pointer to that table as the authoritative data
  for the figure — the Ghia lesson: the lossless text form often already exists as a
  table the pipeline extracts exactly.
- Vector-tier chart coverage: bar charts (filled rects on a common baseline → (x, top)
  per bar) and multi-series scatter (markers grouped into series by stamped size +
  fill/stroke color) now digitize near-losslessly alongside lines; `Digitization.kind`
  (line/scatter/bar) drives the repro script (`ax.bar` for bars). The figure's caption
  and recovered printed labels ride at the top of the repro script as comments, so the
  script alone carries what the plot says. Eval: `scripts/eval_digitize.py` gained
  `scatter, 2 series` and `bar chart` cases (both ≤0.1% y-error).
- Raster-chart pre-scan (`calibrate.py`): under `--digitize-vlm`, a model-free pixel pass
  now runs before the VLM estimate — orientation/deskew correction, axes-frame detection,
  tick calibration from OCR'd numbers (same fit as the vector tier), and a
  runs-per-column ambiguity measure. A chart with too many overlapping ink traces
  (crossing curves, multi-panel scans) is vetoed: the VLM is never called and the figure
  emits a visible `[pdf2md: plot data not extracted — raster-gated]` marker with the
  reason, instead of an invented data table. Research note and method record in
  `docs/figure-to-text.md`.
- Anchored VLM digitization: when the pre-scan calibrates the axes, the measured ranges
  ride into the digitize prompt and the returned points are checked against them —
  out-of-range points cut the confidence (`vlm-anchored` method, axis kinds forwarded to
  the CSV/repro script). A well-calibrated, in-range, pixel-agreeing read can now clear
  the 0.5 emit floor and print its numbers; unanchored estimates stay withheld as before.
  `digitize` calls route to `--vlm-ocr-model` when set (reasoning VLMs think past the
  token budget on the JSON prompt), and looping/slightly-malformed JSON replies are
  salvaged instead of dropped (named in the note). Bake-off findings in
  `docs/figure-to-text.md`.
- `--ocr-page-vlm` skips Docling's redundant OCR. Since the vision model transcribes every page,
  Docling's own OCR was pure waste — it OCR'd all 511 Slater pages, then glm-ocr re-did them. The
  engine now runs with `do_ocr=False` when `--ocr-page-vlm` is on (layout and figure detection still
  run), and `_vlm_ocr_pages` enumerates the scanned pages straight from the PDF rather than from
  Docling's blocks, rebuilding the list page by page: one transcription block per page followed by
  the figures Docling found, and a visible marker for any page the model returns nothing for (its
  only downside — a failed page no longer falls back to Docling's OCR text). On the cached Slater
  book this cut the run from 20 min to 6.7 min (Docling's OCR was the bottleneck); a fresh run drops
  ~20 min (the per-page VLM transcription still dominates). It also fixed coverage: all 511 pages now
  transcribe (was 509 — a text page Docling produced no block for used to be invisible).
- `--ocr-page-vlm`: transcribe each scanned page whole with the vision model, one call per page
  instead of per block. The model sees the full page at once — layout, reading order, tables — so
  it recovers text that per-block OCR (`--ocr-vlm`) or RapidOCR can't, and it's far more accurate
  on a scan. Page-level replacement: a scanned page's prose blocks collapse into a single
  transcription block (`text_source="vlm-page"`), figures still crop, born-digital pages are
  untouched. On the NACA report (via `--force-ocr --ocr-page-vlm`) it transcribed "0.06c to 0.21c",
  the airfoil-designation list, and the data tables exactly, where RapidOCR gave "O.06c tc O.2lc".
  `page` routes to the OCR model (`--vlm-ocr-model`): an OCR-tuned model is the right tool —
  glm-ocr:q8_0 did the whole 14-page report in 6.5 min (7s/page) with all 14 pages transcribed,
  where qwen3-vl:32b took 3.25 hours and left 8 pages empty, and 8b escaped/emptied. Needs the
  `describe` extra + endpoint, doc-level cached by page image, opt-in. Confidence reports "OCR by a
  vision model" (and how many pages, when partial).
- `--force-ocr`: re-OCR the page images instead of trusting the embedded text layer, for a PDF
  whose own "text" is itself degraded OCR ("?3astman" for "Eastman") — which can't be told apart
  from good born-digital text, so it's opt-in. The engine OCRs full pages (Docling's
  `force_full_page_ocr`) and the whole doc is treated as a scan: the glyph layer reports no text
  (`GlyphIndex(force_ocr=True)`), so the glyph-based refill/religature/script overlay are skipped
  and the fresh OCR text stands, page rasters and word-split turn on, and the confidence grades
  honestly (medium, not a false "high"). On a NACA technical report whose text layer read "TEST6
  OF B.A.C.A. AIRFOILS IB THE PABIABLE-IR!%SI2Y … ?3astman N. Jacobs/ … L4emoria.1 Aeronautfcal",
  `--force-ocr` recovered "TESTS OF N.A.C.A. AIRFOILS IN THE VARIABLE-DENSITY … Eastman N. Jacobs …
  Memorial Aeronautical". Pair with `--ocr-vlm` for a vision-model re-read that fixes the residual
  character errors RapidOCR leaves ("O.06c tc" for "0.06c to").
- Word re-segmentation for scanned prose. RapidOCR drops the spaces inside a scanned line
  ("Lookunderthecab"), which wrecks readability for a human and for an AI reading the markdown. A
  new pass re-splits them from English word frequencies (wordninja) for OCR'd prose blocks only —
  born-digital text has a real layer and is left untouched, and wordninja keeps real words and
  proper nouns whole (Sonic, Reynolds, configuration all stay), so a correctly-spaced line is a
  no-op. On a scanned game guide, "Jumpfrom the alcove wherethefirst Chao Box islocatedtotopofthe
  archwayattheendof the single brick stairway" became "Jump from the alcove where the first Chao
  Box is located to top of the archway at the end of the single brick stairway". English-only by
  design; `--no-word-split` (config `resegment_ocr=False`) turns it off for a scanned non-English
  doc, where the split would be wrong. Adds `wordninja` (tiny, pure-Python) as a dependency. The
  same pass also re-inserts the space RapidOCR drops after a comma/semicolon ("ramp,toward" ->
  "ramp, toward") and at a sentence boundary between a lowercase and uppercase letter ("marker.The"
  -> "marker. The"); this part is language-agnostic and runs even with `--no-word-split`, and its
  guards leave decimals ("3,000", "3.5"), acronyms ("N.A.C.A."), and versions ("v2.0") alone.
- Sharper figure crops. Figures were cut from the `crop_dpi` (220) page raster, which caps a small
  vector figure's atom/axis labels at whatever 220 dpi sampled — blurry for the structures, schemes,
  and small plots that make up most figures in a born-digital paper. They now re-render on their own
  through the same adaptive path equation and table crops already use (`dpi_for_region`), climbing
  toward 600 dpi for small regions and staying at `crop_dpi` for full-width ones (self-bounding, no
  bloat on large figures). Tunable via `figure_crop_target_px` (default 1600, the long-side pixel
  budget). On a chemistry structure/plot figure the crop went from 759 to 1252 px wide with visibly
  crisper labels; the raster is still lossy for a vector figure (SVG would be lossless) but far more
  legible. Motivated by the corpus text-sufficiency audit: figures are the dominant asset dependence,
  and ~75% of them are vector, so crop fidelity is where the practical loss was.
- Text-sufficiency grade in the profile. A new axis, orthogonal to losslessness, measuring how much
  of a document a reader can work from the markdown alone versus what still needs its image crop.
  `profile.json` gains `text_sufficient`, `pixel_authoritative`, and `pixel_authoritative_by`
  (a breakdown by kind), and README.md gains a "Text sufficiency" section, e.g. "44 of 50 elements
  are text-sufficient … 6 pixel-authoritative (the image crop is the record): image-only figures
  (4), image-backed equations (2). Deleting `assets/` loses those." An element is text-sufficient when
  it's usable as text/data from the markdown: a figure only if its data was recovered
  (reconstructable), a table with structured cells that isn't an OCR scan, an equation with verified
  LaTeX (not image-backed), prose when it's legible. This is the honest measure of how close a
  document is to needing no `assets/`.
- Figure caption promotion. When the engine didn't supply a caption (a scanned figure, where the
  caption is baked into the image), the recovered figure text is scanned for the line that opens
  with 'Fig'/'Figure' + a number and that caption is lifted into the figure's `caption` field —
  so it renders as the visible italic caption and drops out of the label list instead of being
  buried in it. Model-free heuristic; a caption OCR broke across lines is rejoined. On `image.pdf`
  the figure now carries "FIG. 2a. Comparison of u-velocity along vertical lines through geometric
  center and primary vortex" as its caption. Born-digital figures already have a Docling caption,
  so promotion skips them.
- Upright re-OCR of scanned figures (model-free, on by default; `--no-figure-ocr` to skip). A
  scanned figure is OCR'd by the engine in the page's own orientation, so a sideways plot's small
  text (axis ticks, titles) comes back as garbage. A post-render pass re-OCRs the figure crop with
  RapidOCR, trying all four 90° rotations and keeping the most legible read, then replaces the
  figure's `labels` with it and records the detected angle. On the Ghia figure (`image.pdf`, a
  sideways scan) this took the label block from `2'0 / \`0- / 口` salad to clean tick values
  (0.0–1.0 on both axes), "PROFILES THROUGH GEOMETRIC CENTER"/"PRIMARY-VORTEX CENTER", and the full
  caption. Born-digital figures have a text layer and are skipped (they keep their exact labels);
  `--figure-labels` still supersedes with the vision read.
- Vector-chart data recovery is on by default (was `--digitize`, now the default with
  `--no-digitize` to skip). The tier-1 reader is model-free and declines unless it finds a
  framed plot box with numeric ticks, so a scheme/structure/raster figure stays an untouched
  crop; a born-digital chart now ships its extracted data (CSV + a repro script) instead of a
  bare crop, so the figure is reconstructable from the markdown alone. `--digitize-vlm` (the
  low-confidence raster estimate) is unchanged and still opt-in.
- Figure captions render as visible text below the image. The caption previously lived only in
  the image alt attribute, invisible to anyone reading the markdown, so the figure wasn't
  text-sufficient without opening the crop. It now prints in italics under the image (the PDF's
  own caption, so no `[pdf2md: ...]` annotation).
- Model-free recovery of a figure's baked-in text. Docling attaches a figure's caption, axis
  titles, and tick labels to the Picture rather than the body reading order, so `iterate_items`
  never yielded them and they were silently dropped — the caption included. The Docling adapter
  now recovers those text items into the figure they sit inside and surfaces them as `labels`
  (exact characters for a born-digital figure, Docling's OCR for a scan; the crop stays
  authoritative), no VLM and no flags. On the Ghia cavity figure (`image.pdf`), the default
  convert went from emitting only `center.` to recovering the full caption ("Comparison of
  u-velocity along vertical lines through geometric center and primary vortex") plus the
  in-figure titles and axis labels. `--figure-labels` still overrides with a dedicated
  text-layer or vision read when passed, and now keeps these labels if that read comes back
  empty.
- Full-page verification rasters for scanned pages. Each OCR page is rendered to
  `assets/page_NNN.png` and linked from its `<!-- page N -->` anchor (`[page N scan]`),
  so "verify against the image" works for prose, not just for element crops — closing
  the gap where a garbled OCR paragraph had nothing to check against. Only scanned pages
  (born-digital pages have an authoritative text layer); on by default, `--no-page-images`
  to skip, `page_image_dpi` (default 150) to tune. The README notes it when present.
- Dropped-ligature repair. A broken font whose ﬀ/ﬁ/ﬂ ligatures lack a ToUnicode
  mapping makes pdfium drop them, leaving a gap ("e cient" for "efficient"). `enrich`
  now reinserts the ligature for a curated set of unambiguous multi-fragment words
  ("e cient"→efficient, "con guration"→configuration, "di erent"→different,
  "coe cient"→coefficient, …) — these spaced forms never occur in clean text, so the
  fix is zero-risk; word-initial drops ("rst"→first) are left as too ambiguous.
  Validated on GRASP: "more e cient use … con guration state functions … di erent
  computer systems" → "more efficient use … configuration state functions … different
  computer systems".
- `--ocr-vlm`: re-OCR scanned prose blocks with the vision model instead of the
  engine's RapidOCR (opt-in, same `describe` extra + endpoint). Crops each scanned
  prose block, transcribes it, and replaces the text — much more accurate on degraded
  scans. Validated on the Slater scan: RapidOCR's "the dificulty … equipartition of
  euergy … in classical statistical mecbanics" becomes "the difficulty … energy … in
  classical statistical mechanics". The page image stays the source of truth, the text
  is still OCR (the profile grades the doc "medium" and notes "OCR by a vision model"),
  and results are cached by crop bytes. Slow (one call per block).
- Labelled accuracy harness (`scripts/eval_accuracy.py` + `tests/accuracy_labels.json`):
  the third leg beside `qa.py` (labels-free regression) and `eval_equations.py`
  (equation accuracy). It scores converted output against per-archetype facts a human
  verified from the source — text that must appear, font-decode dingbats that must
  not, a legibility floor, the expected confidence grade, scan detection — and
  validates the profile.json signals against ground truth, turning "is the conversion
  accurate?" into a number per document type. `--check` gates. Resolves the long-open
  validation-harness question. (Currently 16 facts across 4 archetypes, all passing.)
- Per-document profile, written on every conversion: `profile.json` (machine-readable
  content inventory + quality signals + a coarse confidence grade, for an AI consumer)
  and `README.md` (a human run summary: what the doc is, what's in it, a high/medium/low
  confidence read *with reasons* — "50/50 pages scanned: verify against images",
  "29/41 equations image-backed" — and where to start). `profile.py` computes the
  `DocumentProfile` once; both files render from it. A born-digital paper grades "high";
  a scan grades "medium" with the scan reason. (Front-matter nav keys + a full
  section→file→page map in profile.json are a planned refinement.)
- `--describe`: vision-model descriptions of image crops (opt-in). Figures, image-
  fallback tables, and image-backed equations are opaque PNGs to a text consumer;
  `describe.py` sends each crop to an OpenAI-compatible vision endpoint (`vlm_base_url`
  / `vlm_model`, so localhost ollama/vLLM/LM-Studio or a remote host with no code
  change) and emits a labelled description block below the image — except an equation,
  whose transcription rides as its existing hint (and never overrides math-OCR). The
  crop stays authoritative; the kind-aware prompt forbids inventing values. Needs the
  `describe` extra (just the `openai` client) and a reachable endpoint. Validated
  against ollama `qwen3-vl:8b`: accurate figure descriptions (the Transformer
  architecture and attention diagrams read correctly). Descriptions are cached at the
  doc level by (model, kind, crop bytes), so a `--force` re-run reuses them instead of
  paying the vision model again. Models route by crop kind: figures and equations use
  `vlm_model` (a general VLM describes plots and transcribes LaTeX cleanly), while
  tables can use an optional OCR-tuned `vlm_ocr_model` (`--vlm-ocr-model`, e.g.
  glm-ocr) that reads dense grids more faithfully. (A review found OCR models add a
  `\tag` and CJK punctuation to equations, so equations stay on the VLM; a bigger VLM
  like qwen3-vl:32b also reads embedded figure text better than the 8B.) The equation
  hint now labels its source ("math OCR" vs "vision model"), not always math OCR.
- Cross-reference links: a "see section 9.2" reference is turned into a link to that
  heading (in the same file or another), resolved against the actual headings so a
  number with no matching section is left as plain text. Dotted numbers only (a bare
  "section 9" is ambiguous with a chapter), and code fences are skipped so a console
  session that prints "section 9.2" stays verbatim.
- `index.md` contents file for split books: every section file linked, with its
  chapters and numbered sections nested beneath as in-file anchor links, built from
  the actual emitted (deduped/merged/nested) headings. One navigation entry point and
  a map of where everything lives, for a human or a model. Single-file papers are
  unaffected.
- Heading hierarchy for split books. Bookmarks only mark Parts, so chapters and
  sections arrived flat (everything `#`) with the bookmark title duplicated by the
  page's own heading ("# I Overview" then "# Part I Overview"; "# Chapter 1" then
  "# GRASP2018"). `emit._heading_plan` now drops a heading that restates the file
  title (normalised so "Part IV: Issues …" matches the bookmark "IV Issues …"),
  merges a bare "Chapter N" / "Part N" label into the title heading after it
  ("## Chapter 1: GRASP2018"), and deepens body headings under the file-title H1 so
  Part → Chapter → numbered section nest as `#`/`##`/`###`. Single-file papers are
  unchanged.

### Fixed
- Whole-page VLM cleanup now detects repeated multi-line page sections, trims the
  second copy, and flags the transcription for review. The earlier detector only
  caught one line repeated at least six times.
- A `--ocr-page-vlm` page whose first block was a table lost its transcription. The subsume
  repurposes the first block's id into the transcription paragraph, but `_table_crops` keyed on the
  `#/tables/` id prefix, so it cropped the paragraph and emitted a table image instead of the text —
  14 pages on the Slater book. `_table_crops` now keys on block type, and `_sufficiency` skips a
  `TableData` whose block was subsumed (it was miscounting those as pixel-authoritative).
- Redundant crops on `--ocr-page-vlm` pages. The whole-page transcription already contains the
  equations (as LaTeX) and tables (as Markdown), but Docling's separately-detected equation/table
  regions were kept and cropped anyway — 1039 equation crops + 49 table crops on the 511-page
  scanned Slater book. A transcribed page now subsumes all its text blocks (prose, equations,
  tables) into the one transcription; only figures survive as crops. This also fixes the
  text-sufficiency count, which was calling those in-text equations "pixel-authoritative".
- Honest partial vision-OCR coverage. `--ocr-page-vlm` transcribes each scanned page, but a page
  whose transcription comes back empty keeps its engine OCR — so grading the whole doc "OCR by a
  vision model" overstated it. The profile now counts the pages actually transcribed (`vlm_pages`)
  and says so: "OCR by a vision model on 8/14 pages, engine OCR on the rest — verify". Surfaced by
  the 8B run, where 6 of 14 pages returned empty and fell back silently.
- Escaped-newline vision output. Some models (seen on qwen3-vl:8b) return a whole transcription as
  one `\n`-joined line with literal backslash-n instead of real newlines, which rendered as a single
  broken line. `clean_vlm_text` now decodes the newline/tab escapes when a reply is escaped (literal
  `\n`, no real newline), leaving normal multi-line output and unicode untouched. Surfaced by a full
  `--ocr-page-vlm` run on the 8B model, which also returned empty on several pages — hence the CLI
  help now steers `--ocr-page-vlm` toward a capable model like qwen3-vl:32b.
- Orphaned equation crops. An equation whose region was cropped (its extraction judged unreliable)
  but whose text came out empty — routine under `--no-formula`, where no LaTeX is produced — hit the
  generic empty-block branch first and rendered as "empty equation block" (dropped), silently
  discarding the crop that faithfully held the equation. The crop is now emitted as the authoritative
  image whenever one exists, regardless of the text or confidence. On a 10-equation paper converted
  `--no-formula`, dropped blocks fell from 10 to 1 (the one equation with no crop at all) and the
  other 9 now show their image. Also stops `_equation_latex("")` from emitting an empty `$$ $$`.
  A new `orphaned_crops` gated invariant in the qa harness (a dropped block that still has a rendered
  crop) prevents this class from silently returning — it was 0 nowhere in the corpus before the fix.
  `_eq_crops` now also crops an equation with no text at all (not just a low-confidence one), so a
  `--no-formula` equation that was never transcribed is preserved as an image instead of rendering as
  an empty marker; across the corpus this took equation-heavy `--no-formula` docs to zero dropped
  blocks (every equation is now LaTeX or a crop). Formula-on runs are unaffected — their equations
  have text, so only the low-confidence clause fires.
- The digitization pass now runs on `config.digitize_figures or config.digitize_vlm`, so a
  programmatic `Config(digitize_vlm=True)` no longer needs `digitize_figures` set too. (The CLI
  already coupled the two, so `--digitize-vlm` on the command line was never affected.)
- Pin `pypdfium2>=4.30,<5`. pypdfium2 5.x rewrote the TOC API (`bookmark.get_dest()
  .get_index()` instead of `.page_index`, and no longer yields the bookmark level the
  heading outline needs), so on a freshly-resolved `uv tool install` (which pulled 5.10)
  bookmark reading raised, chapter splitting silently collapsed to one `document.md`, and
  `index.md` vanished. render/scripts target the 4.x glyph API too. Migrating to 5.x is a
  deliberate change, tracked separately.
- Vision passes (`--describe` / `--ocr-vlm`) survive a busy endpoint. A whole-document
  run fires one call per crop/block (thousands on a long scan), which makes a local
  server (ollama/vLLM) drop connections under load; every drop was swallowed as a warning
  and the block fell back silently — so a 500-page `--ocr-vlm` run could report "lossless"
  while most of its OCR was the engine's degraded text. The client now retries transient
  errors with backoff (`max_retries=5`, configurable `vlm_timeout`, default 180s), and a
  run with remaining failures logs a loud `N/M vision calls failed … rerun with --force`
  summary instead of passing as clean.
- Born-digital f-ligatures are no longer corrupted. A broken TeX font (Computer Modern /
  OT1, no ToUnicode) makes pdfium surface its ﬀ/ﬁ/ﬂ/ﬃ/ﬄ ligatures and discretionary
  hyphen as C0 control bytes (`\x1b`-`\x1f`, `\x02`); `clean_reading` was stripping them
  to spaces, manufacturing "rst"/"con guration"/"di erence"/"e cient" from
  first/configuration/difference/efficient (an external review counted 106 "les" for
  "files", 36 "rst" for "first" in GRASP). `normalize.expand_ligature_glyphs` now maps
  those bytes back to letters before the control-strip — deterministic and complete, not
  a word list. This supersedes the curated `repair_ligature_drops` dictionary, now
  removed. Soft hyphens at line breaks ("practi-cal") are joined in the same pass.
- Depend on `onnxruntime` so OCR uses a stable backend. Docling drives OCR through
  rapidocr, whose default backend is onnxruntime — but with onnxruntime absent it
  silently falls back to a torch backend that breaks at OCR init in two different ways
  across versions: "Unsupported configuration: torch.PP-OCRv6.det.small" (rapidocr 3.9)
  and "storage has wrong byte size" loading a `.pth` (torch 2.12). Both failed every
  conversion, even born-digital PDFs that never OCR, and both came from `uv tool install`
  resolving newer transitive versions than the tested lockfile. Installing onnxruntime
  (light, ~30 MB) makes OCR independent of the torch version Docling pulls. `rapidocr<3.9`
  stays as a secondary guard until 3.9 is verified against the onnx backend.
- Deep-code-review cleanup. (1) The "prose-bearing types" set was defined five times and
  had drifted (emit's omitted FOOTNOTE), so a broken-font footnote was emitted as garbage
  with no marker and inflated `prose_legibility` — now one `schema.PROSE_TYPES` frozenset,
  and illegible footnotes are flagged. (2) A crash between creating the version dir and
  writing provenance left a dirty dir the next run wrote *into* (after the cache fix made
  it reuse the number); the dir is now cleared first, and provenance.json is written
  atomically (temp + `os.replace`) so a truncated marker can't look complete. (3) Added a
  Docling-free fast test of the adapter's cell-bbox TOPLEFT→bottom-left flip and the
  label→BlockType map — the highest-churn code, previously only covered by the opt-in
  integration test. (4) `config.device` now actually reaches the Docling engine
  (`AcceleratorOptions`); it was silently ignored. (5) Removed the dead
  `coverage_confidence_floor` config. Smaller: `--ocr-vlm` added to the cached-run nudge;
  `repair_ligature_drops` preserves all-caps; the title-dedup no longer reads an initial
  ("C. elegans") as a section numeral; `convert_dir` no longer re-hashes a failed file in
  its poison-pill handler; CI now runs `ruff`; untracked a stray `.Rhistory`.
- A crashed conversion no longer wedges the cache. The version number is assigned
  before output is written and provenance.json is written last, so an interrupted run
  (e.g. `--describe` without the `openai` extra, which died after rendering crops but
  before emit) left a `v<n>` dir with assets but no markdown — and the cache counted
  it, so every later run reported "cached" and no-opped. `latest_version`/`next_version`
  now ignore versions without provenance.json (prune still removes them). And the
  vision client is built before the engine runs, so a missing `describe` extra fails
  fast instead of after a full conversion. A cached doc with `--describe`/`--transcribe`
  now says those passes need `--force`.
- Figure captions in a broken font stayed symbol-font garbage: `enrich_figures` only
  ligature-repaired them, never font-decode-refilled. The docling adapter now carries
  the caption's own bbox (`FigureRef.caption_bbox`), and enrich refills a garbled
  caption from the pdfium glyph layer ("❋✐❣✉/a114❡ ✸✳✶" -> "Figure 3.1: ...").

### Added
- Preformatted-content handling for console transcripts and ASCII-art tables
  (software manuals like GRASP). These are monospace text whose meaning is the line
  layout: the engine flattens a console session into one run-on paragraph or
  mis-grids an ASCII table, and (with the broken font) emits dingbats.
  - `preformat.py` (`is_preformatted`): banner/rule-line detection (a line that is
    almost entirely `*`/`-`/`=`/`_`/`#`), plus literal `|` column rows for tables.
    Banner detection tested at zero false positives on a clean paper.
  - `PageChars.text_lines`: pdfium's native bounded text, with line breaks preserved
    (unlike `text_region`'s flat join). `normalize.clean_preformatted` cleans it
    while keeping the lines.
  - `enrich.py`: a `code` block (Docling labels console sessions as code) is refilled
    from the pdfium glyph layer line-preserved; a prose block whose re-read carries
    banner lines (console the engine mislabelled) is marked `preformatted`; a "table"
    that is really ASCII-art (`TableData.preformatted`) keeps its line layout. All
    three emit as fenced code blocks instead of flattened prose or mangled grids.
  - Validated on GRASP: console I/O sessions and energy-level listings now render
    readably with structure intact; the clean control paper gains zero code fences.
- Font-decode repair extended to table cells. The prose refill (below) left table
  cells in symbol-font garbage because cells aren't prose blocks and the `illegible`
  metric is prose-only. Now `enrich._table_grid` refills garbage cells from the same
  pdfium glyph layer (shared `enrich.refilled` helper), forcing a rebuild even when
  no scripts are present. Fixing this surfaced a coordinate bug: **table-cell bboxes
  are TOPLEFT origin** (unlike block prov bboxes, which are BOTTOMLEFT), so the
  docling adapter (`_cell_bbox`) now flips Y to page-bottom — which also repairs the
  glyph alignment the table sub/superscript overlay had been getting wrong. `qa.py`
  gains an `illegible_table_rows` gated invariant (rendered GFM rows that are garbage)
  so a broken-font table can't pass silently the way GRASP's did. `refilled` no longer
  replaces a cell with an empty pdfium reading (which would lose the cell). Validated:
  GRASP TOC/data tables now readable, atkins-50page tables unaffected.
- Legibility signal + font-decode repair ("Trust, measured"). A PDF whose embedded
  font lacks a usable ToUnicode CMap extracts as symbol-font garbage (dingbats and
  `/aNNN` glyph-name tokens — `❆ ♣/a114❛❝/a116✐❝❛❧` for "A practical guide"), which
  Docling's default backend trusts; pypdfium2 decodes the same file correctly.
  - `legibility.py`: a pure `score_legibility` / `is_garbage` over the
    symbol-substitution signal (no vowel-ratio/dictionary check, which would
    false-flag dense chemistry/math notation).
  - `enrich.py` refills any garbage prose block from the pdfium glyph layer
    (`PageChars.text_region`), stamping `text_source="pdfium"`; only swaps when
    pdfium is actually cleaner, so a truly undecodable block stays flagged.
  - `emit.py` flags a block that's still garbage after the refill (visible marker +
    `illegible` coverage tally + front-matter `illegible_blocks`) instead of
    passing it off as readable prose — the blind spot that let a 67%-dingbat doc
    report lossless. `scripts/qa.py` gains an `illegible` gated invariant and now
    audits split/book outputs (it was skipping any doc without `document.md`).
    **`FORMAT_VERSION` 0.4 -> 0.5** (optional `illegible_blocks` front-matter key;
    `illegible` count added to the coverage report).
  - Validated on the GRASP2018 manual: illegible prose blocks 1653 → 0, text
    readable, bibliographic metadata recovered. Known residual: the broken font's
    ﬀ/ﬁ/ﬂ ligature glyphs also lack a ToUnicode mapping, so pdfium drops them
    ('e cient' for 'efficient'); legible but imperfect, far better than dingbats.
- Diacritic-split repair: words the text layer fractures where a diacritic was
  dropped ('Löwdin' -> 'Lo wdin', 'Schädel' -> 'Scha del') are rejoined, reusing
  the ligature machinery's vocabulary validation. Guarded on the *stem* (left
  piece): join only when the stem isn't a word the document uses on its own but
  the joined form is — so a consistent split that leaks the broken tail into the
  vocabulary can't defeat it, and real pairs ('of the', 'data set') are never
  fused. Corpus audit: 4 joins, all correct author names, zero false positives.
- Labelled equation accuracy harness: `tests/equation_labels.json` (10
  hand-checked equations, born-digital and scanned) plus
  `scripts/eval_equations.py`, which scores the engine LaTeX and the math-OCR
  transcription against ground truth. This is the measured complement to the
  labels-free `scripts/qa.py`. It immediately earned its keep: it showed the
  transcription beats the engine 4/4 on clean equations, and (over 6 scanned
  equations) that raising the transcription crop DPI is a net regression on
  degraded scans and that cross-DPI self-consistency voting isn't worth its cost —
  so both were rejected by measurement rather than shipped on a hunch.
- Multi-pass equation transcription (`--transcribe`, opt-in). Re-reads each
  image-backed equation crop with a local math-OCR model (Surya, the maintained
  successor to texify) and emits the result as the equation's text hint — turning
  an OCR/garbled equation's wrong LaTeX (the scanned `c^5`-for-`c^3` case) into a
  real transcription. The crop image stays the authoritative source, so a bad
  transcription is never worse than before. `transcribe.py` is a small seam:
  `Transcriber` (anything with `transcribe(image)->latex`) plus a lazy-imported
  `SuryaTranscriber` whose only version-specific surface is one `_run` method;
  with `surya-ocr` absent the pass is skipped. Install with the `transcribe`
  extra. The Surya call (`FoundationPredictor` -> `RecognitionPredictor`,
  `ocr_with_boxes` + `math_mode`) is verified against surya-ocr 0.17's API.
- Image-crop fallback for low-confidence equations. Some journals (ACS) draw math
  glyph-by-glyph out of reading order, so the embedded text layer is scrambled
  token soup *before* pdf2md touches it, and the previous text-layer recovery
  would replace good vision-LaTeX with that soup. Now any equation whose
  extraction is suspect (confidence below `RECOVER_BELOW`) is cropped to an image
  — the one fully faithful representation — and the image is emitted as the
  authoritative source. A best-effort text hint rides below it: the clean
  text-layer reading when it is in geometric reading order and shares enough with
  the LaTeX (`PageChars.reading_disorder`, `SCRAMBLED_ABOVE`, `HINT_MIN_CONF`),
  otherwise the vision LaTeX. Because the crop is authoritative, the hint's
  selection is cosmetic and the disorder heuristic carries no correctness risk.
  **`FORMAT_VERSION` 0.3 -> 0.4.** This replaces the earlier text-layer "recover"
  / "text layer reads" behaviour: the same accurate characters are still present
  (as the hint), now backed by the faithful image.
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
- Scanned/OCR page handling. A page with no embedded text layer (a full-page
  scan image) was the one input where the safety net inverted: nothing could be
  cross-checked, so equation confidence came back `None` and `None` meant "trust
  the LaTeX" — which on a scan is an OCR mis-transcription (a Rayleigh-Jeans law
  emitted with `c^5` instead of `c^3`), presented as authoritative. Now a page
  with no text layer is detected as OCR-sourced: its equations are always
  image-backed (the scan pixels are the only ground truth, the OCR LaTeX rides
  along as an unverified hint), its tables are cropped rather than rendered from
  OCR cells, and front-matter carries `ocr_scanned_pages` so a consumer knows the
  text is a transcription to verify against the images.

### Fixed
- Two-column bleed in equations is now caught. Docling's formula model sometimes
  weaves adjacent-column prose into an equation's LaTeX (`\text{or} & & \text{where}`),
  and the old one-directional confidence missed it: the bled tokens only inflated
  the LaTeX, leaving recall at 1.0 so the equation was trusted. Confidence is now
  the two-way agreement (`min(recall, precision)`) between the LaTeX and the
  single-column bbox text layer; precision drops when the LaTeX carries content the
  bbox doesn't, so the equation is flagged and image-backed (the crop, being the
  clean single column, is the authoritative source) rather than presenting the
  bled LaTeX as fact.
- Parsed table content no longer vanishes when Docling mislabels the block.
  TOC-style pages come through as type `other` yet still carry parsed cells; emit
  rendered tables only for type `TABLE`, so the data was orphaned and the block
  dropped. emit now renders a block's `TableData` wherever it exists, regardless of
  the block's label. A table with genuinely no cells (and a bbox) instead gets an
  `![table](crop.png)` image fallback, the same one equations use, so a failed
  table is never silently lost either.
- Publication year is corrected upward from an arXiv id (in the filename or page
  text) when the first page-1 year is an older dataset/citation year — the
  Transformer paper read "2014" (its WMT dataset) instead of 2017. A year already
  on the page that is newer than the arXiv submission (a journal year) is kept.
- Ligatures Docling splits with a stray space (`di ff erent`, `con fi guration`,
  some publishers decompose ﬀ/ﬁ/ﬂ and pad it) are rejoined. `normalize.religature`
  only merges when the result reconstructs a word pdfium's reading of the page
  actually contains, so a true boundary (`off the`, `cutoff value`, `electric
  field`) is never fused — the validation against the document's own words, not a
  heuristic, is what makes it safe. The vocabulary is pdfium's reading of every
  page (a word kept whole anywhere confirms a split of it elsewhere), built once
  and only when a split is seen, so clean papers pay nothing. Most of one paper's
  135 splits resolved with zero corruption; the unconfirmed rest are left split
  rather than guessed.
- Front-matter omits null-valued keys (`doi`, `authors` when unknown). Quarto's
  YAML schema rejects `doi: null` for a string field and fails the whole render.
- Unverified-equation markers no longer read as a verdict that the equation is
  *wrong*. The cross-check measures whether the extraction could be confirmed, not
  its correctness, and on scrambled-text papers it reads ~0.00 for equations whose
  LaTeX is perfect. The per-equation score is dropped from the marker (now
  "equation extraction unverified — the image below is the authoritative source"),
  and the front-matter summary reports `equations: {total, image_backed}` instead
  of a misleading `low_confidence` count.
- Unbalanced `\left`/`\right` (Docling emitting two `\right` for one `\left` in a
  bra-ket) made KaTeX throw; `emit._balance_delims` drops the auto-sizing commands
  when the pair is unbalanced so the bare delimiters still render.
- Script detection no longer corrupts numeric values: a digit raised or dropped
  *inside* a number (table cells turning 191.4 into ¹91.4, 251.5 into 25¹.5) was
  the worst failure mode for a source-of-truth corpus. `scripts._unsplit_numbers`
  keeps a script only when it is a clean trailing group of a numeric run (a real
  exponent or citation like 191.4⁶⁹); a left-superscript multiplicity (²A₁) is
  kept because the digit precedes a letter, not a digit.
- Equation confidence no longer false-flags (and needlessly recovers) a correct
  equation because of `\exp`/`\max`/`\text{}`: the LaTeX tokenizer kept command
  *structure* but dropped the visible text those commands carry, so a faithful
  `\frac` equation scored ~0.3 and got recovered to flat text, losing the
  fraction bar. The tokenizer now keeps `\text{}`/`\mathrm{}` content and
  text-operator names.
- A low-confidence equation that can't be safely recovered (it has Greek the text
  layer drops) now surfaces the text-layer reading as a `text layer reads: …`
  cross-reference beside the kept LaTeX, so the accurate characters (a dropped
  second term, a `ccCA` the vision model read as `ccA`) are still available.
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
- Multi-line equations with alignment markers (`&`, `\\`) are wrapped in
  `\begin{aligned}` so KaTeX/MathJax render them instead of throwing.
- Unmapped Greek-letter font glyph names (`/Delta1`→Δ, `/Pi1`→Π, `/Sigma1`→Σ,
  and the rest of the Greek alphabet) are normalized to Unicode in text, tables,
  and captions.

### Internal
- Keystone refactor increment 2: the engine is now pure translation. Moved table
  and figure verification (ligature repair + inline sub/superscript rebuild from
  glyph geometry) out of the Docling adapter into the engine-agnostic `enrich.py`
  (`enrich_tables` / `enrich_figures`). The adapter no longer imports pypdfium2 —
  it ships each table's structured cells as a transient `RawTable` on
  `EngineResult`, and `enrich` (one `GlyphIndex` pass, shared with block
  enrichment) does the religature/script work. Removes the duplicate pdfium pass
  and means a second engine inherits all verification for free. Verified
  behaviour-preserving: a real reconvert leaves every table's GFM/HTML and figure
  caption byte-identical; the script-rebuild path is covered by a unit test.
- Quality-audit / regression harness (`scripts/qa.py` + `tests/qa_baseline.json`).
  Reads existing outputs (no reconversion) and reports per-document signals — the
  things we keep fixing: dropped content, split-ligature residue, unbalanced
  equation LaTeX, image-backing, scanned-page count, losslessness. `--check` gates
  on the hard invariants (lossless / dropped / ligature / unbalanced must not
  regress) and reports the rest as drift; `--update` refreshes the baseline. The
  labels-free half of the accuracy story: it can't say the LaTeX is correct, but
  it catches the day it silently gets worse, instead of finding out one paper at a
  time. (It already caught a stale scanned output that predated OCR detection.)
- QA note: `qa.py --check` flags GRASP2018 with `ligature_residual` 2, from the
  boilerplate "Normally M fi N". The source text layer literally encodes the `≠`
  symbol as the ASCII bytes `f i` (0x66 0x69), so it's an upstream font-decode
  artifact, not a dropped ligature — `repair_ligature_drops` leaves it alone by
  design (repairing a lone symbol to `≠` would be a guess). The `_LIG` proxy can't
  tell a mis-decoded symbol from a split word. Baseline is deliberately held stale
  (its curated corpus predates the current scratch outputs); refresh it against a
  curated corpus, not `out/`, when one is set.
- Verification layer extracted from the Docling adapter into an engine-agnostic
  `enrich` stage (`enrich.py`: `GlyphIndex` + `enrich_blocks`), run by the pipeline
  on the `EngineResult`. The block-level scripts/ligatures/equation-cross-check/OCR
  logic no longer lives behind the engine seam, so a second engine inherits it and
  it is now unit-testable with a fake glyph source (the adapter's `_blocks` was
  0%-covered). Behaviour-preserving — verified the same per-equation confidences on
  a real PDF. The table/figure paths still build their own glyph index in the
  adapter; a follow-up moves them too and removes the duplicate pass.
- Table grid→markup assembly moved to `tables.py` (`build_html`/`build_gfm`);
  GFM header row derived from cell header flags instead of assuming row 0;
  spanning tables no longer persist a flattened GFM. `PageChars` reads page text
  in one call instead of one per character (faster on large books).

### Changed
- Output format → **0.2**: front-matter key `engine` renamed to
  `engine_versions` (`engine` is reserved by Quarto's YAML front-matter).

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
