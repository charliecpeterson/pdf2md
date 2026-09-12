# Output format

What a conversion bundle contains and what each file promises.

This is the contract `FORMAT_VERSION` in `src/pdf2md/schema.py` versions —
currently **0.13**. A parser should pin that, not the package version: it
changes only when front-matter keys or the file layout change in a way that
breaks a reader.

## Output

```
out/<source-name>-<doc_id[:8]>/
  source.pdf            # exact, hash-verified source shared by every derived version
  v<n>/
    document.md         # paper: one file
    00_front.md ...     # book: front matter, Part openers, chapters, and back matter
    index.md            # book: shallow file-level contents tree
    README.md           # human run summary: contents, quality scorecard, conversion work
    manifest.json       # compact navigation, representations, and review links
    metadata.json       # document kind, fields, semantic sections, references, verification
    chunks.jsonl        # bounded text units with source and asset pointers
    passages.jsonl      # stable retrieval records with contextualized text
    passages.schema.json # JSON Schema for each passage record
    outline.json        # hierarchy, file/passage ranges, review and source map
    symbols.json        # source-quoted, section-local technical symbol definitions
    profile.json        # machine-readable inventory and quality signals
    review.md           # human queue: likely defects before valid image dependence
    review.json         # the same queue as structured records and exact counts
    base-state.json     # engine-neutral parse state for parser-free enrichment
    assets/<id>_p<n>.png
    data/<id>_p<n>.csv  # accepted chart series
    data/doi-metadata.csl.json # optional raw DOI registry response
    data/tables/<block>.md   # table transcription candidate, with its own audit header
    data/tables/<block>.glyph.md # the same region read out of the PDF's glyph layer
    data/tables/<block>.csv  # raw cell grid
    data/tables/<block>.json # authority, crop, audit findings, and lineage
    data/tables/<block>.cells.jsonl # per-cell reader and reference evidence
    data/tables/page_<n>_panels.csv  # stitched long-form repeated panels, when detected
    data/tables/page_<n>_panels.json # stitched rows and review checks
    code/<id>_p<n>.py   # deterministic chart reproduction
    provenance.json     # full blocks, bboxes, coverage, and lineage
```

- Every table artifact opens with its own provenance and audit header. A file under
  `data/tables/` is read away from `document.md`, and an unmarked grid there would
  present as a standalone source; findings on the table's own block reproduce in full
  and findings elsewhere on its page become a pointer into `review.md`. Each table also
  gets a crop of its printed region under `assets/`, so the source can be checked
  without opening the PDF.
- `<block>.glyph.md` is the table region read straight out of the glyph layer, in the
  engine's columns: measured rows against a modelled grid. It is never the emitted
  table, and exists so a suspect row can be diffed rather than trusted.
- A table typeset as repeated side-by-side panels is emitted one grid per panel, and any
  row the split could not confidently assign to a panel is listed below them, unassigned,
  under a marker naming the reason. Those rows are printed data: dropping them cost one
  118-element table 22 numbers, `53 | I | 32.90(10) | 4.2049(18)` among them. The merged
  grid in the table artifact keeps every row in its printed place.
- A running header or footer the engine swallowed into a table is named as a finding. One
  table cannot tell it from its own spanning title -- both render as the same string in
  every column -- so the discriminator is the rest of the document: a running line repeats
  verbatim across pages and a table's title does not.
- `doc_id` is the SHA-256 of the source bytes. A completed version is reused only when
  its run fingerprint also matches the effective configuration, pdf2md implementation,
  engine identity, dependency versions, model identifiers, and prompt/cache schema.
  `--force` always creates a new version; runs never overwrite completed output.
- Engine identity includes the **resolved** accelerator device, not the configured one:
  `run_inputs.engine.device` reads `mps` or `cuda:0`, never `auto`. Docling's layout
  detector makes marginally different calls per device, so a CUDA bundle and an MPS
  bundle are different output and do not share a cache entry.
- A `v<n>` directory is allocated by creating it, which is what makes the number
  exclusive. While a run holds one it contains `claim.json` (host, pid, timestamp);
  `provenance.json` replaces it as the completion marker, so a finished bundle carries
  no claim. A directory with a claim whose process is gone is a crashed run, and the
  next conversion of that document reuses the number after clearing it.
- The readable source-name prefix is for navigation; the `doc_id` suffix prevents files
  with the same name or changed contents from sharing a version tree. It is normally eight
  characters and extends automatically if that name already belongs to different content.
  Existing hash-only output directories remain valid and are reused when found.
- **Never cite a bundle by its slug.** The prefix is the filename of the *first*
  conversion and is never reconciled afterwards, by design: the hash decides identity, so
  the same bytes handed over under a different name reuse the directory they already have,
  and renaming it would break every path already cited. A mislabelled input therefore
  produces a correct bundle under a misleading name. The authoritative answers are
  `provenance.json`'s `source_path` (the file each version was actually given) and the
  extracted title, which is what `pdf2md list` and `pdf2md find` print as the headline.
- Directory conversion excludes its configured output tree, so a repeated
  `pdf2md convert .` does not ingest the stored `source.pdf` copies. `pdf2md list`
  finds verified document roots recursively and reports their latest completed content.
- `source.pdf` is written atomically and verified against `doc_id`. Versions refer
  to this single audit copy rather than duplicating the source.
- Book output expands chapter bookmarks beneath Part-like containers while leaving
  local section, appendix-subsection, and index-letter bookmarks inside their parent
  file. Out-of-order bookmark destinations are restored to source-page order. If a Part
  has no chapter bookmarks, two or more numbered chapter headings provide the fallback.
  The root `index.md` lists files only; each content file links its own detailed headings.
- `pdf2md prune` only treats a directory as a document when its name and stored
  `source.pdf` agree on the content identity. Unrelated `v1`/`v2` directories are ignored.
- `manifest.json` is the machine entry point. It points to the Markdown, source,
  assets, review targets, profile, base state, and full provenance without duplicating their
  contents.
- `metadata.json` carries the selected bibliographic fields, inferred document kind,
  semantic roles for paper and book sections, and one record per extracted reference.
  Numbered reference sections report sequence gaps; continuation blocks retain every source
  block and page. DOI matches and GROBID reads can corroborate an entry, while disagreements
  remain visible as conflicts. Verification labels describe traceable evidence and extractor
  agreement, not calibrated probabilities.
- `manifest.json` also carries the selected bibliographic fields and their ranked
  evidence. Title candidates retain source pages and block IDs where available;
  alternatives record ranking points, quality labels, and penalties such as probable
  glyph fragmentation. Rejected generic/generated titles and placeholder authors stay
  visible. Scores order candidates and are not calibrated probabilities.
- `chunks.jsonl` groups consecutive blocks from one section and source page into
  records of at most 6,000 characters. Each record names its Markdown file,
  exact source page, block IDs, assets, and review dispositions. `needs_review`
  means an action is required; valid image dependence is recorded separately.
- `passages.jsonl` is the stable retrieval and embedding interface. Its IDs derive
  from source block IDs rather than sequence position, so editing one block does not
  rename every later passage. Records carry separate display and contextualized text,
  content hashes, full breadcrumbs, source regions, neighbors, authority, review state,
  typed assets, and tokenizer identity, count, and limit. The limit applies after
  document and section context is added. Prose splits at paragraph or sentence
  boundaries, list and code lines stay intact when they fit, and table continuations
  repeat their caption and column header. Equation passages include a nearby explanatory
  sentence; figure passages include their caption and an explicit referring sentence
  when found. Retrieval context also names the semantic section role, such as `abstract`,
  `methods`, `results`, `conclusions`, or `references`. `passages.schema.json` is the
  bundled copy of the
  [published schema](../src/pdf2md/passages-v2.schema.json).

  The default `lexical` tokenizer is deterministic and requires no model files. To size
  passages for a real embedding index, select that model's tokenizer, for example:

  ```bash
  pdf2md convert paper.pdf \
    --passage-tokenizer hf:sentence-transformers/all-MiniLM-L6-v2 \
    --passage-max-tokens 256
  ```

  The Hugging Face model name or local path is stored in every passage. Use the same
  tokenizer and limit when indexing; pdf2md counts special tokens in the recorded limit
  and rejects a configured limit above the tokenizer's finite `model_max_length`.
- `outline.json` is the deterministic navigation map. It derives from the same section
  tree and passage records as the Markdown, and reports section and file page ranges,
  passage ranges, block and passage counts by content type, review hotspots, named
  bibliography/glossary/index locations, and every source-dependent passage. Paths and
  source pages are direct bundle references rather than regenerated summaries.
- `symbols.json` is a conservative, section-local index of explicitly defined technical
  symbols. Each entry quotes the defining source sentence and links its passage, source
  region, and same-section occurrences. The extractor declines unintroduced notation
  and keeps overloaded symbols separate by section; it does not assign inferred global
  meanings.
- `review.md` and `review.json` separate likely defects from valid source dependence.
  Suspect extractions, illegible prose, and missing representations appear first.
  Intentional image-backed equations from `--no-formula` remain searchable without
  making the whole document appear defective.
- Accepted chart values and plotting code live in `data/` and `code/`. Markdown
  links to both while keeping the source figure beside them.
- Figure cleanup can join explicitly captioned multipanel and continued figures, and
  can expand a single graphical-abstract image to include tightly adjacent visual
  text/equation components. These rules require layout evidence and preserve the
  component text blocks for search and provenance.
- Scanned tables keep the crop as the authoritative record. Their structured OCR
  remains searchable as explicitly labelled Markdown, CSV, and JSON candidates.
  Repeated side-by-side panels, unequal-width radial panels, vertical Table I records,
  and headerless continuations get typed long-form CSV. Interleaved Table I lanes are
  parsed independently, and separate or inline scalar properties use an explicit
  `value` column. Atomic number constrains the parsed element symbol. Raw OCR stays
  beside parsed values. Normalized rows include both readers, a conservative
  `best_value`, confidence, the resolution rule, validator preference, and refusal
  details. Continuity and format validators are diagnostic only; they do not rewrite
  the primary value. Reader agreement remains distinct from external verification.
- Unchanged files under `assets/` are hard-linked across completed versions. Each
  version keeps normal relative paths, and pruning an older version does not remove
  bytes still referenced by a newer one. Treat generated assets as immutable: an
  in-place edit to a linked file is visible from every version sharing that file.
- Front-matter carries `format_version`, bibliographic metadata, and the engine +
  model versions that produced the file.
- `provenance.json` records the effective configuration and the complete inputs used
  to compute the run fingerprint. Base runs record `derivation.kind: base`; enrichment
  versions record their parent evidence and selected stages. API credentials are never
  written there.
- Every run reports **text-sufficiency**: how many elements are usable from the
  markdown alone (prose, tables, verified LaTeX, vector-chart data) versus
  pixel-authoritative (a scan or photo where the crop is the real record). It's the
  honest measure of how close the output is to needing no `assets/`.
- `profile.json` and the generated README report independent quality dimensions for
  accounting, structure, text sufficiency, layout, OCR, equations, tables, figures,
  metadata, and unresolved errors. Ratios are evidence summaries, not probabilities.
  The old `confidence` field remains only for compatibility and is marked deprecated.
- Conservation evidence is layered. PDF-to-block word recall and whole-document numeric
  accounting remain broad diagnostics. A separate block-to-Markdown check records exact
  word and number conservation, expected formatting changes, and source-image dependence.
  Only unexplained block-to-Markdown loss or addition creates a new review action; each
  example names its page, bbox, block, and Markdown artifact in `profile.json`.
