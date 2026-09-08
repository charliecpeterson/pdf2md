# Public release plan

What has to happen before this repository can be public, and what should
happen so that a stranger can read it. Written 2026-09-07 against `main` at
`2fa398c`, from a survey of the tree rather than from memory; every count below
came from a command and can be re-run. Nothing here changes behaviour. The gate
(`scripts/qa.py --check`, 37 of 37) and the 838 fast tests are what make every
step below safe to do in any order.

The one-line verdict: the converter is in good shape and the repository around
it is not. CI is red, the test suite only passes on one machine, a personal
session log is tracked, and the docs are three very long files that grew by
accretion. None of that is hard to fix, and none of it should be skipped.

## 1. Blockers

Things that make the repo unfit to publish as it stands. Do these first and in
this order; each is a day or less.

### 1.1 CI is failing, and has been

`.github/workflows/ci.yml` runs `uvx ruff check src tests scripts` and it
reports **341 errors**. There is no `[tool.ruff]` section in `pyproject.toml`,
so ruff runs whatever rule set `uvx` gives it by default, and the code was never
held to that. The last four runs are red.

Two decisions, then mechanical work:

- Pick the rule set on purpose and write it down. Add `[tool.ruff]` with an
  explicit `select`, a `line-length` that matches the code (it is 99 in
  practice), and per-file ignores for `scripts/` where a dev harness legitimately
  differs from library code. A rule the project does not intend to keep should be
  in `ignore`, not silently failing.
- Run `ruff check --fix` for the 221 auto-fixable ones (142 are import order),
  then hand-fix the rest by rule. The categories that need a human are `B008`
  (24: function call in a default argument, mostly `typer.Option(...)`, which is
  idiomatic Typer and should be ignored for `cli.py`), `PLW1510` (7: `subprocess.run`
  without `check=`; each is deliberate and should get `check=False` written out),
  and `ISC004` / `RUF007` / `FLY002` (style, 57 total, fix or ignore per rule).

Until this is green, "CI passes" cannot appear in the README.

### 1.2 The test suite only passes here

`tests/test_corpus_hashes.py` asserts that every source named in
`tests/qa_baseline.json`, `tests/accuracy_labels.json` and
`tests/equation_labels.json` exists on disk. Those are the ~40 journal PDFs at
the repository root, which are gitignored (`/*.pdf`) and cannot be redistributed.
On any other machine the test fails at the first missing file. It fails in CI
today for that reason.

Fix: the test's real claim is "each source name maps to exactly one hash across
the label files", which needs no files. Keep that half unconditionally. Make the
"and the file is present with that hash" half skip with a message when the
corpus directory is absent, the way the `integration` tests already skip without
`PDF2MD_TEST_PDF`. Same treatment for `tests/test_agent_benchmark.py` if it reads
anything outside `tests/`.

### 1.3 Files that should not be public

- `sessioinnotes.md` at the root is a tracked personal session log (`Session
  ID: ses_...`). Remove it from the tree and from history if the history is going
  to be published; if the history is not, removing it from the tree is enough.
- `notes/` is inconsistent: `2026-09-05-field-report-ecpgen-numeric.md` is
  tracked and `2026-08-28-field-report-claim-checking.md` is gitignored by name,
  along with three related test and script files. Decide once whether field
  reports are part of the public record. They are good writing and they are the
  evidence behind several design decisions; I would keep them, un-ignore the
  first one, and drop the four per-file ignores.
- `output/pdf/` holds three synthetic degradation PDFs (15 MB, one of them 11 MB)
  plus manifests, referenced only by `docs/archive/scan-degradation-benchmark.md`. They
  are generated, so they are fine to publish, but `output/` reads as build
  output. Move them to `tests/fixtures/degradation/` and update the one doc.
  If 11 MB in the clone matters, regenerate them at lower resolution; the
  manifest records how.

### 1.4 The root directory

Forty-one PDFs, nine `out*` directories, `env/`, `tmp/`, `output/`. All
gitignored, so a clone does not see them, but anyone who runs the tool from a
checkout will, and the README's `uv run pdf2md convert /path/to.pdf` implies a
clean root. Move the local corpus to one directory outside the repo (the
`~/scratch/reconvert/` staging dir already exists and holds 28 of them), point
`tests/qa_baseline.json`'s `source` paths at it through an environment variable
(`PDF2MD_CORPUS`), and drop the `/*.pdf` ignore so a stray PDF at the root is
visible rather than hidden.

### 1.5 A release

`CHANGELOG.md` has one released version, `0.1.0` on 2026-06-14, and **1,008
lines under `[Unreleased]`** since. `FORMAT_VERSION` is `0.13`. Nobody can cite a
version. Cut `0.2.0` (or `1.0.0`, if the output format is being promised), tag
it, and make the changelog's unreleased section the short list it is meant to
be. Keep the long narrative: move it under the version heading, where it is a
release note rather than a backlog.

## 2. Structure

The soft ceiling in `CLAUDE.md` is 700 lines per file. **Twelve modules are over
it**, and one function is the size of a module. None of this is a defect a user
sees; all of it is the thing that makes a new reader give up.

### 2.1 `pipeline.convert_file` is 600 lines

Lines 364 to 965. It is one function with ten `progress.stage(...)` markers in
it, which means the stages already exist as a sequence and only need to become
functions. The seams are the markers themselves:

```
setup            _store_source, _run_inputs, engine selection
parse            engine.convert, normalize_page_origin
verify           enrich_blocks / enrich_tables / record_recall / symbols
metadata         DOI, GROBID
structure        build_structure, bookmarks
render           crops, page rasters, SVG
equations        render-check, transcribe
emit             emit_document, table artifacts
audit            coverage, conservation, review, profile
finalize         provenance, manifest, atomic version move
```

Each becomes `_stage_<name>(state) -> state` over one small dataclass carrying
what the stages share (`doc`, `result`, `metrics`, `vdir`, `config`). The body of
`convert_file` becomes the list. This is a refactor with no behaviour change,
and the gate is the proof: every bundle's signature must be identical before and
after.

**Done in part, 2026-09-08.** `_Run` now names the shared state and
`_finalize_bundle` is the first stage to take it, which took `convert_file` from
599 lines to 438. The remaining stages should move the same way, one at a time,
with the gate proving each — the pattern is established and the type exists.
The original note, kept because the measurement is why the approach changed:

**Attempted 2026-09-08 and deliberately stopped.** The state is wider than
"`doc`, `result`, `metrics`, `vdir`, `config`" — measured, 20 locals cross the
first boundary alone, and the last two stages taken by themselves need 14 inputs
and return one value. A function with 14 parameters is not an improvement on the
inline code, so the split is not a rename: it needs a `ConversionState` dataclass
designed on purpose, and that is a design change to review rather than a
mechanical refactor. Do it as its own piece of work with the gate before and
after, not as part of a cleanup pass. Step 2.2 below was done first and is what
took the file from 1,271 lines to 1,105.

### 2.2 `pipeline.py` has absorbed document-scope passes

Beyond the God function, `pipeline.py` (1,271 lines) holds `_audit_scanned_tables`,
`_audit_running_text_rows`, `_attach_table_crops`, `_table_crops`,
`_eq_crops`, `_transcribe_equations`, `_warn_about_scan_overlays`,
`_scanned_share`, `_auto_engine`. These are not orchestration. Move them:

| function | belongs in |
|---|---|
| `_audit_scanned_tables`, `_audit_running_text_rows` | `table_audit.py` (they are audits with document scope) |
| `_attach_table_crops`, `_table_crops`, `_eq_crops` | `render.py` or a new `crops.py` |
| `_transcribe_equations` | `transcribe.py` |
| `_warn_about_scan_overlays` | `enrich.py`, beside `scanned_overlay` |
| `_scanned_share`, `_auto_engine`, `_get_engine` | `engines/__init__.py` as `select_engine` |

After that `pipeline.py` is the stage list and the version/cache plumbing, and
should land near 400 lines.

### 2.3 The other eleven

| module | lines | seam |
|---|---|---|
| `emit.py` | 1,144 | table emission (`_table_*`, ~300 lines) into `emit_tables.py`; equation emission into `emit_equations.py` |
| `table_audit.py` | 1,007 | `grid_findings` and its detectors (text-only) from `row_accounting` (geometry); they are documented as independent checks and should be two files |
| `enrich.py` | 994 | the recall/symbol/neighbour-attribution family (~350 lines) into `recall.py`; `GlyphIndex` stays |
| `cli.py` | 959 | one file per command group is the Typer idiom; `convert` and `enrich` are most of it |
| `digitize.py` | 927 | already split once (`figure_geometry`, `digitize_vlm`); the multi-panel path is the next seam |
| `profile.py` | 857 | README writing (`_*_lines`, ~400 lines) into `run_readme.py`; `build_profile` stays |
| `line_reader.py` | 828 | evaluation-only per the README; consider `scripts/` |
| `table_artifacts.py` | 778 | artifact headers (`_*_header`) from artifact writing |
| `metadata.py` | 749 | title evidence from author evidence |
| `table_verify.py` | 724 | leave; it is one thing |
| `visual.py` | 703 | leave; one line over |

Do `pipeline.py` and `emit.py` first; they are the two a reader opens first.
The rest can wait for a reason to touch them.

### 2.4 `scripts/`

Eighty files, roughly as many lines as the product. `scripts/README.md`
documents the gate-versus-probe convention and names the ones that matter.
Eight are referenced by no document at all:

```
eval_claim_checking.py            (gitignored anyway; see 1.3)
eval_digitize_ocr_gate.py
eval_engine_text_agreement.py
eval_figure_labels.py
eval_pdf_parse_bench.py
mine_numeric_reader_disagreements.py
mine_pdf_parse_bench_cells.py
run_atsp_hf_reference.py
```

**Checked 2026-09-08 and rejected.** The eight came from grepping a narrow set
of documents. Widening it: `eval_pdf_parse_bench`, `mine_numeric_reader_disagreements`,
`mine_pdf_parse_bench_cells` and `run_atsp_hf_reference` each have a test in
`tests/`; `eval_engine_text_agreement` is cited in `CHANGELOG.md` and
`eval_figure_labels` in `docs/archive/PROJECT_PLAN.md`. Only
`eval_digitize_ocr_gate.py` is named nowhere outside itself, and its results are
`docs/digitize-ocr-gate-*.json`. Across the whole directory, 55 of 81 Python
harnesses are exercised by a test. A harness whose measurement is still cited is
live code however long ago it last ran, so nothing was deleted; the stale counts
in `scripts/README.md` were corrected instead (72 -> 83 files, 22.9k -> 24.2k
lines).

### 2.5 Tests

814 tests in 98 files, fast, no engine — that is the right shape and should be
kept. Two things:

- The `integration` marker is the right mechanism for tests that need a model or
  a corpus; extend it to the two local-only tests (1.2) rather than inventing a
  second mechanism.
- `tests/qa_baseline.json` is keyed by source hash now and the entries carry an
  absolute-ish `source` path from one machine. With 1.4 done, those become
  relative to `PDF2MD_CORPUS`.

## 3. Documentation

Three long files carry everything: `README.md` (1,032 lines), `CLAUDE.md` (840),
and `docs/accuracy-improvement-notes.md`. They are thorough and they are hard
to enter.

### 3.1 README

It is the user-facing source of truth, and it is currently a tour, a reference,
a methods section, and a results log in one file. Split by reader:

- **README.md** (target ≤ 250 lines): what it is, the accounting promise in one
  paragraph, install, the five commands a user runs, one annotated bundle
  listing, the approach map table, and links. The approach map is the best
  thing in the current README and should stay near the top.
- **docs/options.md**: the full options reference (currently "Options
  reference", ~70 lines) and "Which options for which PDF".
- **docs/output-format.md**: the "Output" section — the bundle tree and the
  per-file bullets. This is the contract `FORMAT_VERSION` versions, and it
  deserves its own file with the version in its title.
- **docs/methods.md**: "Development and evaluation" (currently ~400 lines) and
  "Methods and references". This is where the measurements live.
- **docs/known-limits.md**: the "Known limits" list.

Every number in the README that came from a measurement should say which
harness produced it, so a reader can re-run it. Most already do; the recent
additions (symbol precision, MinerU ten-document comparison) name the script,
and the older ones should be brought to that standard.

### 3.2 CLAUDE.md

840 lines, most of it a flat "Gotchas" list of measured decisions. The content is
the most valuable documentation in the repo — it is the reasons — and the format
is what a log looks like after three months. Restructure, do not cut:

- Keep the module map at the top; it is what a reader wants first. Trim each
  entry to two lines and move the rest of its prose next to the gotcha it
  belongs to.
- Group the gotchas under the module they concern (`tables`, `equations`,
  `metadata`, `figures`, `recall and conservation`, `engines`), in the order of
  the module map. A gotcha that spans two modules goes under the first.
- Give each gotcha a one-line bold title, which most already have, and keep the
  measurement that justifies it. The measurement is the point; a rule without
  its number is a superstition.
- Move the longest narratives (the recall-tokenization saga, the two-engine
  comparison) into `docs/methods.md` and leave a two-line summary with a link.

Target: 400 lines, same information.

### 3.3 `docs/`

Eleven markdown files and twelve JSON result files. Seven of the markdown files
were last touched 2026-08-28 and describe completed work. Sort into:

- **Current reference** (stay, get linked from the README): `accuracy-improvement-notes.md`,
  `figure-to-text.md`, `qa-corpus.md`, `dense-numeric-tables.md`, plus the four new
  files from 3.1 and this one.
- **Completed workstreams** (move to `docs/archive/` beside `PROJECT_PLAN.md`):
  `quality-and-ingestion-plan.md`, `engine-bakeoff.md`, `bakeoff-results.md`,
  `blind-v3-result.md`, `olmocr-bench-predictions.md`,
  `scan-degradation-benchmark.md`, `agent-benchmark.md`. Each is the record of a
  measurement that is finished; the archive is the right shelf and a one-line
  index in `docs/README.md` is the right pointer.
- **Result JSON** (twelve files, 148 KB the largest): keep them beside the doc
  that cites them, in `docs/results/`, and have each doc say which file it read.

### 3.4 `notes/`

Field reports from a consumer of the tool. Keep, un-ignore, and add a two-line
`notes/README.md` saying what they are and that they are written by the
consumer, not the maintainer.

### 3.5 CONTRIBUTING and CHANGELOG

`CONTRIBUTING.md` already says the right thing ("maintained by one person,
best-effort"). Add the three commands that matter — `uv run pytest`,
`uvx ruff check`, `uv run python scripts/qa.py out --check` — and say that the
gate needs a local corpus and how to get one. `CHANGELOG.md` is covered by 1.5.

## 4. Explicitly not doing

Named so they are decisions rather than omissions.

- **No new features.** The two field reports' asks are met; the review queue is
  mostly true positives; the one open measurement (table audit against
  arrangement, `scripts/eval_table_audit_cross_engine.py`) is research.
- **No packaging to PyPI yet.** Docling, MinerU and Marker each want their own
  environment and a GPU; a `pip install pdf2md` that then fails at first use is
  worse than a clear `uv sync` instruction. Revisit after a release has been
  used from a clean clone.
- **No rewrite of the verification layer to be faster.** 3.4× for formula
  enrichment is the engine, and the tool reports the cost rather than hiding it.
- **No merging of the three engine adapters.** The seam is load-bearing and is
  documented as such.

## 5. What was done, 2026-09-08

Steps 1-5, 2.2, 2.4, 3.1, 3.2 and 3.3 are done and pushed; CI is green for
the first time in this plan's history. The blockers are cleared, so the
repository can be public.

Four of the plan's predictions were wrong, and the corrections are worth more
than the steps that went as written:

- **1.2 was one test; it was 33 across 22 files.** They read either the labelled
  PDFs or the bundles converted from them. Two skip predicates, not one, and the
  PDF one has to ask whether *most* labelled sources resolve — a few baseline
  entries point at a bundle's own `source.pdf`, which exists wherever a
  conversion has ever run.
- **1.4 was a path change; it was a fan-out.** Twenty-three harnesses resolved a
  labelled source themselves in four different spellings. `scripts/_corpus.py`
  now holds the one answer.
- **2.1 does not split without a state object.** Twenty locals cross the first
  stage boundary; the last two stages alone need fourteen inputs. Left undone on
  purpose, with the measurement recorded above.
- **2.4's eight orphan scripts were one.** The grep behind that claim covered too
  few documents: four have tests, two are cited in the changelog and the archived
  plan. 55 of 81 harnesses are exercised by a test. Nothing deleted; the stale
  counts in `scripts/README.md` were corrected instead.

Still open: 2.1 (needs its own session), 2.3 (`emit.py` and the other
over-ceiling modules), and 2.5's second half.

## 6. Order and effort

| step | what | effort | proof it worked |
|---|---|---|---|
| 1 | ruff config + fixes (1.1) | half a day | CI green |
| 2 | corpus-dependent tests skip cleanly (1.2) | an hour | `uv run pytest` passes on a clean clone |
| 3 | remove session log, settle `notes/`, move `output/pdf` (1.3) | an hour | `git ls-files` review |
| 4 | corpus out of the root, `PDF2MD_CORPUS` (1.4) | half a day | gate still 37 of 37 |
| 5 | cut a release (1.5) | an hour | a tag |
| 6a | move the document-scope passes out (2.2) | done | gate 37 of 37 |
| 6b | split `convert_file` (2.1) | two days, needs a state object | gate signatures byte-identical |
| 7 | `emit.py`, `table_audit.py`, `enrich.py` (2.3) | two days | same |
| 8 | prune `scripts/` (2.4) | half a day | `scripts/README.md` lists all of them |
| 9 | README split (3.1) | a day | a stranger finds `convert` in under a minute |
| 10 | CLAUDE.md regroup (3.2) | a day | 400 lines, nothing lost |
| 11 | `docs/` triage (3.3) | half a day | `docs/README.md` index |

Steps 1 through 5 are the public-readiness line: after them the repo can be
public and a clone works. Steps 6 through 11 are what makes it readable, and can
be done afterwards, one at a time, each behind the gate. Roughly two weeks of
focused work in total; the first five are two days.
