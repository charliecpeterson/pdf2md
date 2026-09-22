# Missing-content screening pilot

The bounded pilot is complete; the probe remains experimental. Multi-page
provenance is preserved in new Docling conversions. Repeated-header filtering
reduced development noise, but fresh source review found omissions on five of
nine sampled pages, none flagged by the probe. Do not promote it as a confidence
gate. See the [fresh evaluation](#fresh-evaluation-and-decision).
The subsequent [list-identifier fix](#list-and-citation-identifier-follow-up)
addresses the two observed numbering losses; the historical evaluation remains
unchanged.

Date: 2026-09-22. Part of the
[accuracy/confidence roadmap](accuracy-confidence-roadmap.md#first-work-package).
The implementation is an evaluation-only
[probe](../scripts/probe_missing_content.py), not a parser, repair step, or
calibrated confidence score. The initial pilot changed no production code; the
[provenance follow-up](#multi-page-provenance-follow-up) below does.

## What is implemented

- `freeze` pins source PDFs, all files in each completed conversion bundle, probe
  implementation files, runtime versions, and the screening policy. Duplicate
  sources are refused. Each manifest records document family and prior exposure.
- `scan` compares source glyph centres with the union of emitted, cropped, and
  flagged block boxes. It also checks for nonblank pages with no represented
  blocks. It writes only to a new output directory and refuses changed inputs.
  Emitted blocks without a content crop now contribute all recorded source spans.
  Cropped/flagged blocks and blocks with a `crop_path` contribute only the primary
  location: a first-page image is not evidence for later-page coverage.
- The HTML review sheet shows full source pages beside page-attributed passage
  text, with links to the full Markdown and split-document sections. Detector
  hints are collapsed. Labels can be downloaded as JSON; there is no autosave.
- Sampling is deterministic within each document, separately for pages flagged
  by either detector and pages flagged by neither. Inclusion probabilities and
  the complete sampling frame are recorded. The default is up to three pages
  per stratum per document, not three regions.
- `score` compares existing warnings and probe warnings at the same page-review
  budget within that frozen sample. It also computes an inverse-probability
  weighted omission-page rate among baseline-unflagged pages once all sampled
  pages have definite labels. Unknown labels never count as clean pages.
  It also reports reviewed-page confusion counts and worst-case finite-corpus
  bounds. Those bounds allow every unreviewed page to be either clean or omitted;
  they are not statistical confidence intervals.

The screening policy starts at 12 uncovered non-whitespace glyphs per geometric
line/run, one point of box padding, and a 5% edge margin. Separate columns are
split at large gaps. Short and marginal candidates remain in the report without
triggering a warning. The nonblank-page check uses a 72-dpi render, pixels below
grayscale 200, and a minimum ink fraction of 0.001. These are starting hypotheses,
not measured operating thresholds.

The experimental furniture filter requires an isolated outermost line within
the top/bottom 12%, an inward gap of at least 1.5 line heights, identical
normalized text on at least three distinct pages and 25% of the document, and
height variation no greater than 1% of page height. Text recurring in the body
vetoes suppression. Digits and punctuation are preserved when comparing lines.
Candidates remain in the report as `probable_running_furniture`; the filter
never removes output text or changes production warnings.

## Development run

The corpus was the ten existing `out-blind-v3b/*/v1` bundles. Despite that directory's
historical name, these are **development data**, not a new blind evaluation.
The figures below describe screening activity, not verified omissions.

| Measure | Result |
|---|---:|
| Documents / pages | 10 / 345 |
| Pages with existing `action_required` warnings | 75 |
| Pages flagged by the probe | 205 |
| Probe-flagged pages without an existing action-required warning | 140 |
| Pages with measured glyph geometry | 343 |
| Pages with unlocated represented blocks | 2 |
| Review sample: flagged / unflagged | 30 / 22 |
| Completed page-level omission labels | 0 |

No omission precision, recall, false-alarm rate, or calibrated confidence has
been established. The two spot checks below classify particular candidates;
they are not complete page audits and were not entered as page-level labels.

The [machine-readable run record](results/missing-content-20260922.json) contains
the manifest/report hashes, implementation identity, per-document counts, and
the selected page IDs. Large reports, labels, and rendered pages remain under
`~/scratch/pdf2md-missing-content-20260922/development-v2-review/`. The original
exploratory run is retained alongside it; the second run pins implementation and
runtime identity and corrects review links for split-document output.

### Source-checked false-alarm examples

1. **Continued paragraph, arXiv 2608.29886v1, page 10.** The probe highlights three
   lines below Table 7. Those lines are present in the full output. Provenance
   block `#/texts/185` contains the complete paragraph but records only its
   page-9 bounding box. The source-page rendering and `document.md:403` establish
   this candidate as a geometry gap, not missing prose. The Docling adapter's
   [`_prov`](../src/pdf2md/engines/docling.py) takes `prov[0]` for the primary
   location; before the follow-up, no other locations survived translation.
2. **Running header, arXiv 2608.31138v1, page 18.** The highlighted author names
   are visibly the running header. They lie outside the probe's fixed 5% margin
   exclusion and are not meaningful missing body content. Across this document,
   the alternating author/title strings recur as actionable candidates 37 and
   36 times respectively. Those are candidate counts, not 73 independently
   source-reviewed false-positive pages.

The first result argues for preserving all source spans. The second argues for
document-level furniture evidence. Neither supports simply increasing a margin
threshold or introducing another extraction model.

## Multi-page provenance follow-up

Implemented on 2026-09-22: typed `Block.source_spans` survive Docling translation,
per-page origin normalization, saved-state reload, and retrieval output.
Primary `page`/`bbox` fields remain unchanged. Passages expose all primary and
context regions; chunks carry an additive `sources` list without changing their
legacy primary-page grouping. Existing bundles still load, but missing spans
cannot be recovered by replaying their old state. See the
[output contract](output-format.md).

The regression used a new conversion of the same 13-page paper,
arXiv 2608.29886v1, on the Mac Studio with Docling 2.108.0 and MPS. Formula
enrichment, chart digitization, and figure OCR were disabled. This is development
evidence on one known case, not a fresh parser benchmark.

All 128 surviving blocks' span lists matched the raw Docling document. Three
paragraphs had two spans each: `#/texts/8` on pages 1–2, `#/texts/19` on pages 2–3,
and `#/texts/185` on pages 9–10. Each paragraph's final text matched its saved
pre-enrichment text and appeared in full in the Markdown.

The probe was compared **on the same completed output**, once with primary-only
geometry and once with all emitted spans. No text, parser setting, or screening
threshold changed between those two measurements.

| Measure | Primary-only | All spans |
|---|---:|---:|
| Probe-flagged pages | 4 | 1 |
| Uncovered glyphs on page 2 | 127 | 1 |
| Uncovered glyphs on page 3 | 362 | 1 |
| Uncovered glyphs on page 10 | 226 | 2 |

Page 12 remains flagged. These counts establish removal of the three known
continuation warnings, not omission recall or calibrated accuracy. Regression
tests separately ensure that an unrepresented paragraph outside the continuation
box remains flagged and that a primary crop does not cover a second-page span.
Nothing here certifies text inside a represented box or implements multi-page
text repair and crop rendering.

The [comparison record](results/multispan-provenance-20260922.json) pins source,
raw Docling, completed provenance, and experiment hashes. The raw document,
new conversion bundle, manifest, and review sheet are under
`~/scratch/pdf2md-multispan-20260922/`. The earlier ten-document run is unchanged;
its old bundles have no spans and were not rewritten. To reproduce the comparison,
freeze a new converted bundle with the probe, scan it, then call `inspect_page`
on the same page glyphs and ink fraction with `page_blocks` built from copies of
the blocks whose `source_spans` lists are empty. Compare page flags and uncovered
glyph counts, not outputs from separate parser runs.

Verification: the default suite passed with 840 tests, 35 skipped, and 2
deselected. The real Docling conversion above was run separately. Tests cover
multiple spans on one page, distinct page origins, typed state round trips, old
state fallback, stable passage IDs/text, schema-valid repeated primary sources,
chunk source links, and conservative crop-only screening.

## Header-noise follow-up

With the original ten development bundles unchanged, the frozen furniture
filter reduced probe-flagged pages from 205 to 133. It downgraded 124 candidates;
110 pages remain newly flagged relative to the existing action-required queue.
Six distinct author/title strings were checked visually at representative source
occurrences in three documents. All were running headers. This is targeted
evidence, not 124 independent reviews or a suppression-precision estimate.

Negative controls cover single-page lines, duplicate occurrences on one page,
varying positions, distinct numbers, repeated body text, a table heading near
its rows, and a genuine omission remaining after a footer is downgraded.

## Fresh evaluation and decision

The [protocol](omission-evaluation-protocol.md) was frozen before conversion.
Three public documents, checked against 418 known local source files by hash,
cover a born-digital paper, a technical reference, and a historical chemistry
scan. They contain 107 pages. This is one document per family, not a general
benchmark or a claim that the sources were absent from model training.

Docling ran on MPS with formula enrichment, chart digitization, and figure OCR
disabled; the historical scan used forced OCR. The policy was not tuned on the
evaluation outputs. The deterministic sample used seed `20260922` and up to two
pages per document per flagged/unflagged stratum. All nine selected pages were
visually compared with the full Markdown, continued text, and content crops.
Labels are **assistant-reviewed**, not independent human ground truth.

| Measure | Result |
|---|---:|
| Existing action-required pages | 31 |
| Probe flags before / after furniture filtering | 83 / 0 |
| Glyph geometry measured / unavailable | 101 / 6 |
| Sampled pages reviewed / with omissions | 9 / 5 |
| Baseline: flagged omission pages / unflagged omission pages in sample | 3 / 2 |
| Probe: flagged omission pages / unflagged omission pages in sample | 0 / 5 |
| Four-page review budget: baseline / probe-order omission yield | 3 / 2 |

The probe's two-page yield comes entirely from deterministic tie-breaking among
unflagged pages, not from successful detection. Baseline warnings on three
omission-bearing pages concern other issues; this does not establish omission
localization. Neither comparison is a corpus-wide precision or recall estimate.

The five omission labels have concrete source/output evidence:

- **SP 811, physical page 8:** checklist identifiers (9)–(18) are replaced by
  unnumbered checkbox bullets while their prose remains.
- **Dibeler, page 1:** the footnote explaining bracketed references is absent,
  including from the output's relocated footnotes.
- **Dibeler, page 3:** the tin paragraph drops the computed/observed hydride
  comparison at m/e 121, including 11.20 and 11.21. The observed value elsewhere
  in Table 1 does not preserve the missing comparison.
- **Dibeler, page 4:** the carbon-isotope paragraph drops the clause about
  carbon-12 being concentrated in plant material. The baseline also misses this.
- **word2vec, page 11:** bibliography identifiers [1]–[17] become unnumbered
  bullets while numeric in-text citations remain. The baseline also misses this.

List/citation identifiers count as meaningful content because they preserve
explicit item identity. Wrong values and reading order were not counted as
omissions. General page-scan links do not count as content fallback crops under
the frozen rubric. The two word2vec equation crops and the Dibeler table-3 crop
were inspected: they preserve their intended content, not the missing prose.

All 83 downgraded evaluation candidates are the same SP 811 running title.
Its occurrence on physical page 10 was source-checked as furniture; it is also
visible on reviewed pages 67/70. No meaningful suppression was observed in this
targeted check. All five scanned pages and one blank SP 811 page lack a usable
glyph check. An unflagged page therefore cannot be interpreted as verified.

Among 76 baseline-unflagged pages, five were reviewed: two contain omissions
and three do not. The finite-corpus worst-case omission-rate bounds are
**2/76 to 73/76 (2.6%–96.1%)**, assuming the labels are correct. These are not
confidence intervals. The scorer's weighted point estimate is descriptive only;
the wide bounds and three-document design do not support calibrated confidence.

**Decision:** close this work package without promoting the probe. Preserve the
multi-span provenance fix and the read-only experiment. The next scoped work
was to preserve list/citation identifiers, then separately test source-image
line coverage for OCR omissions. The identifier follow-up is below; the
[scan experiment](scan-omission-pilot.md) now surfaces the three known OCR
omissions among five candidates on the development scan, without promotion.
This pilot does not justify adding DPT-2
or another full parser. Fresh labels are now development evidence for those
future changes; any promotion would need another untouched evaluation set.

The [result record](results/omission-final-20260922.json) and
[source-review labels](results/omission-final-20260922-labels.json) retain source
hashes, sample probabilities, screening counts, bounds, and decision evidence.
Full manifests, reports, source PDFs, and page renders remain under
`~/scratch/pdf2md-omission-final-20260922/`. All source and bundle hashes were
reverified after review. Final default tests: **849 passed, 35 skipped, 2
deselected**; real conversions were run separately.

## List and citation identifier follow-up

The adapter discarded Docling's separate `ListItem.marker` while retaining
`ListItem.text`, which excludes that marker in the two regression documents.
The emitter then wrote an unnumbered bullet. A recall exemption incorrectly
described missing leading numbers as harmless list normalization.

The adapter now puts supplied alphanumeric markers into block text and records
them in `extra.list_marker`. Already-present markers are not duplicated; the
original engine text disambiguates a repeated value such as item 1 beginning
with “1 sample.” Ordinary bullet glyphs remain list structure. There is no
sequence inference, renumbering, or glyph-based identifier repair.

Markdown escapes leading ordered-list and task-checkbox syntax where needed,
so a label cannot become a nested list or an interactive checkbox. Passages and
chunks retain the unescaped identifier. Saved state carries the corrected text.
Old state without markers still loads, but recovering those identifiers requires
reconversion. The blanket recall exemption is removed, including when reading
its old stored flag. The general recall threshold is unchanged.

Both complete source documents were parsed once with Docling 2.108.0 on MPS.
The matched comparison reused those raw parses and engine states: one arm used
the old translated blocks, the other retranslated the same raw documents with
the corrected adapter. Both then ran through the current pipeline with formula
enrichment, chart digitization, and figure OCR disabled.

| Supplied identifiers retained in Markdown, passages, and chunks | Before | After |
|---|---:|---:|
| word2vec, 12 pages | 0/32 | 32/32 |
| SP 811, 90 pages | 0/89 | 89/89 |

Exact checks include bibliography labels [1]–[17] on word2vec page 11 and
checklist labels (9)–(18) on SP 811 page 8. No other blocks changed text. Five
SP 811 items gained subscript/superscript markup through the existing alignment
pass; after removing those tags and the restored prefix, their text is unchanged.
That side effect is recorded rather than counted as a separately verified gain.

The [regression record](results/list-identifiers-20260922.json) pins the inputs,
implementation, bundle hashes, and counts. Raw Docling documents, captured
pre-fix engine states, and `before`/`after` bundles remain under
`~/scratch/pdf2md-list-identifiers-20260922/`. To repeat the matched comparison,
load each captured engine state with `load_engine_state`; for the corrected arm,
replace only its blocks with `DoclingEngine._blocks` applied to the saved raw
`DoclingDocument`. Run `convert_file` through a replay engine for each arm, using
separate output roots. Compare marker-bearing blocks by ID against raw
`ListItem.marker`, including complete Markdown, passages, and chunks.

Tests cover adapter translation, ordinary bullets, missing markers, duplicate
avoidance, empty items, state reload, literal Markdown, retrieval text, reference
gaps across pages, and legacy recall flags. These are known-case regressions,
not fresh accuracy estimates. Parser-supplied identifiers can themselves be
wrong. Bibliography metadata still depends on section detection; word2vec's
reference section is not recognized in these bundles. OCR-dropped clauses and
footnotes remain outside this fix.

Verification: **871 tests passed, 35 skipped, 2 deselected**, including the
existing snapshot. The initial new regression tests reproduced 12 failures
before the fix; all now pass. Changed Python files pass Ruff.

## How to use it

Run from the repository with its existing environment. Use new paths each time:

```bash
.venv/bin/python scripts/probe_missing_content.py freeze /path/to/bundle/v1 \
  --output "$HOME/scratch/omission-pilot/manifest.json" \
  --exposure development --family scientific-paper

.venv/bin/python scripts/probe_missing_content.py scan \
  "$HOME/scratch/omission-pilot/manifest.json" \
  --output "$HOME/scratch/omission-pilot/review" \
  --sample-per-stratum 3 --seed 20260922

.venv/bin/python scripts/probe_missing_content.py score \
  "$HOME/scratch/omission-pilot/review/report.json" \
  /path/to/downloaded/labels.json --budget 10 \
  --output "$HOME/scratch/omission-pilot/scores.json"
```

Open `review/review.html` locally. Compare each source page with the **full**
output, including neighbouring sections and content fallback crops. Page-tagged
passages alone cannot establish an omission, as the continuation example shows.
General page-evidence images do not count as content fallback crops.

Label `yes` only when meaningful source content is absent from the output text
and its content fallback crops. Label `no` after checking the whole page's
meaningful content. Ignore deliberately omitted headers, page numbers, and
decoration. Use `uncertain` when the evidence is insufficient; leave untouched
pages `unreviewed`. Definite labels require a reviewer and a source/output evidence
note. Download labels before closing the page. Labels must retain every frozen
sample ID and the experiment ID.

`--exposure uninspected_output` records an assertion about prior exposure; it
does not manufacture a held-out set. Choose fresh sources, document their
selection, and freeze acceptance criteria before inspecting them.

## Limits and next decision

The geometry check cannot detect missing text inside a large represented box,
wrong numbers, bad equations, or incorrect reading order. It shares pdfium
glyph access with existing checks, so it is independent of the parser's block
inventory, not an independent transcription authority. `force_ocr` and detected
scan overlays disable glyph checking. Scans with existing blocks are not checked
for partial omissions. Rotated pages are explicitly unsupported; blocks without
usable geometry cause localization to be refused rather than declared clean.

Scoring is descriptive. It reports finite-corpus worst-case bounds but does not
calculate document-clustered confidence intervals or localization precision.
A warning anywhere on an omission-bearing page
does not prove the warning points to the omission. The equal-budget comparison
applies only to the frozen review sample, not the full corpus.

The header controls, development rerun, fresh evaluation, source labels,
finite-corpus bounds, and non-promotion decision above close the first work
package. Independent human adjudication and broader coverage remain prerequisites
for any future accuracy claim, not implied accomplishments of this pilot.

Verification covers a real synthetic PDF with omitted prose, preserved crops,
nonzero origins, margins, columns, missing/off-page geometry, forced OCR,
rotation refusal, deterministic sampling, weighted/incomplete labels, changed
inputs, unchanged source bundles, and split-output review links. See
[the tests](../tests/test_probe_missing_content.py).
