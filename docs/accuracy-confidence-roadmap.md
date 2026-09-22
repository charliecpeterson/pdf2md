# Accuracy and confidence: next experiments

Start with missed-content detection and a fresh audit of unflagged output, then use those results to choose targeted extraction improvements.

Date: 2026-09-22. Status: proposal, with no new experiments run or production
changes made. Local review baseline: commit `ccaab7b`. Numbers below are recorded
project results or explicitly attributed external results, not fresh measurements.

## What is already here

pdf2md already has Docling, MinerU, and Marker adapters; source crops and
provenance; glyph and row checks for tables; equation checks; symbol-loss and
reading-order findings; numeric confidence experiments; and an agent benchmark.
The next work should extend those mechanisms where evidence identifies a gap.

| Area | Current evidence | Remaining question |
|---|---|---|
| Missing content | The recorded olmOCR-bench analysis found 21 genuine text losses that were never detected as blocks. | Can we detect omissions independently of the parser's block inventory? |
| Confidence | Numeric confidence studies exist, and the quality scorecard explicitly marks its dimensions as uncalibrated. | How often does incorrect output pass without an actionable warning, by content type? |
| Equations | The current agreement check compares sets of alphanumeric tokens. Real-crop render similarity failed to distinguish correct from corrupted equations. | Which meaning-changing errors escape the checks? |
| Engine choice | The README records 71.5% for Marker versus 55.4% for Docling through pdf2md on olmOCR-bench. Marker and MinerU adapters supply no positioned raw table cells. | Can their reading improvements retain per-cell verification? |
| Charts | The documented set matches 45/73 anchor points overall, including 27/32 in the score-at-least-0.60 group. | What acceptance policy gives useful coverage at an acceptable error rate on new figures? |

Local sources: [accuracy notes](accuracy-improvement-notes.md),
[numeric confidence study](dense-numeric-tables.md#selective-confidence-calibration),
[quality scorecard](../src/pdf2md/quality.py),
[equation checks](../src/pdf2md/confidence.py),
[engine comparison](../README.md#approach-map),
[Marker adapter](../src/pdf2md/engines/marker.py), and
[chart limitations](known-limits.md).

Two details matter when interpreting older notes. `--engine auto` is already
implemented: it selects MinerU when at least half the pages lack a usable text
layer, otherwise Docling, with a fallback if MinerU is unavailable. Also, the
74.7% Marker result in older notes is standalone Marker; 71.5% is the README's
result through pdf2md. These are different comparisons.
[Routing implementation](../src/pdf2md/engines/select.py),
[original Marker measurement](accuracy-improvement-notes.md#marker-measured-and-it-changes-the-engine-picture-2026-09-03).

## Ideas from recent work

This was a focused search of primary papers and official repositories, checked
on 2026-09-22. It covered 2026 work and an older formula study relevant to the
existing verifier. Full-text methods and evaluation sections were inspected for
the papers below. These are experiment candidates, not independently reproduced
results or a claim of community consensus.

### Selective repair with verification and rollback

ParseFixer, June 2026, starts with MinerU output and routes suspicious pages,
tables, and formulas through focused correction. Its triggers include empty or
abnormally short output, repetition, and malformed structures. Candidates pass
verification or revert to the original. The paper reports an overall increase
from 57.98 to 61.78; its component analysis describes progressive submissions,
not independent ablations. Final table TEDS is unchanged from the baseline.
[Paper, §§2.3 and 3.4](https://arxiv.org/html/2606.11977v1#S2.SS3),
[official implementation](https://github.com/iLearn-Lab/CVPRW26-ParseFixer).

For pdf2md, the useful experiment is a bounded repair transaction: one diagnosed
region, one alternative, an explicit acceptance decision, and preserved original
evidence. Syntactically valid HTML or LaTeX cannot establish scientific
correctness. Begin with candidate output beside the original; measure false
corrections before allowing replacements. Existing targeted readers already
provide much of the infrastructure.

### Check whether values retain their meaning and source location

ParseBench, April 2026, evaluates table records keyed by headers, chart values
with associated labels, and visual grounding that includes content attribution.
Those tests can expose a correct number assigned to the wrong column or region.
The paper also identifies table layouts where its record metric is unsuitable
and uses a structural metric instead. It comes from the LlamaParse team and
focuses on enterprise documents, so its ranking is not a scientific-PDF verdict.
[Paper, §§3.1, 3.2 and 3.5](https://arxiv.org/html/2604.08538v1#S3),
[benchmark code](https://github.com/run-llama/ParseBench).

Adapt the idea to scientific records such as
`(species, method, property, unit, value, source region)`. Verify header and unit
associations alongside the existing exact-cell checks. For charts, require the
right series and panel as well as the right number. For provenance, test that
the linked crop contains the asserted content. This adds tests of relationships
without requiring a new parser.

### Recover continuity across page boundaries

MinerU-Popo, first posted May 2026, post-processes OCR blocks to recover paragraph
and table continuations, heading hierarchy, and image-caption associations. It
uses a fine-tuned 4B model with filtered inputs and overlapping chunks. Its own
evaluation reports 87.8% precision for text continuation and 87.0% for image-text
association, so incorrect joins remain relevant. The official repository provides
an inference workflow and a model link.
[Paper, §§4 and 5.3](https://arxiv.org/html/2605.24973v1#S4),
[official repository](https://github.com/opendatalab/MinerU-Popo).

This is worth probing on long books and supplements. Start with explicit
continuation markers, matching headers, and page-boundary candidates, then compare
against a model only if those rules leave a meaningful gap. Keep proposed links
between original blocks before attempting merged emission. pdf2md already has
section splitting and continued-figure handling; the experiment should measure
what remains missing, especially table and paragraph continuity.

### Challenge equation verifiers with controlled errors

Horn and Keuper's formula benchmark appeared as a December 2025 preprint and is
listed by the authors for ICPR 2026. It generates PDFs from known LaTeX while
varying layout, then evaluates extracted formulas. Its human study reports a
0.78 correlation for an LLM judge, versus 0.34 for character detection matching
(CDM). The full text documents CDM failures involving positional relationships
and acknowledges judge errors. This is not evidence that an LLM can certify an
individual formula.
[Paper, §§3.2 and 4](https://arxiv.org/html/2512.09874v1#S3.SS2),
[authors' publication page](https://www.keuper-labs.org/research/2026-benchmarking-document-parsers-on-mathematical-formula-extraction-from-pdfs/),
[benchmark code](https://github.com/phorn1/pdf-parse-bench).

The project already cites `pdf-parse-bench`; the proposed extension is to test
the verifier itself. Pair correct transcriptions with deliberately changed signs,
indices, exponents, and grouping. Include harmless LaTeX variants as controls.
Measure whether each check separates those cases before adding another scoring
method. Faithful transcription remains the objective: mathematical equivalence
does not authorize rewriting the source expression.

### Compare engines without confusing segmentation with accuracy

OmniDocBench's April 2026 update introduced Multi-Granularity Adaptive Matching:
keep reference annotations fixed while adjusting prediction segmentation during
matching. Its repository also records evaluation changes and September 2026
leaderboard updates. This is relevant because pdf2md has already measured how
different block sizes distort raw low-recall block counts.
[Official update history](https://github.com/opendatalab/OmniDocBench#updates),
[local block-size finding](accuracy-improvement-notes.md#the-low-recall-count-is-size-sensitive-2026-09-04).

Use comparable source regions, words, cells, and assertions as denominators when
comparing engines. Pin the evaluator commit, dataset revision, matching method,
and model/configuration. The upstream README carries multiple historical version
labels; a name such as “latest OmniDocBench” is insufficient for reproduction.
Keep literal missing-content counts alongside alignment-tolerant scores so the
matcher cannot make an omission disappear from the report.

## Proposed experiments, in order

### 1. Find content outside the parser's inventory

Extend [coverage](../src/pdf2md/coverage.py) with an independent page-level probe.
For born-digital pages, compare usable source glyph regions with the union of
represented regions. For scans, begin with the simpler case of substantial
visible content but no detected blocks. Residual image regions may need a second
layout or text-detection pass, but should initially produce review evidence.

The existing [conservation checks](../src/pdf2md/conservation.py) provide useful
document-level signals. They do not replace localization of omitted prose or
visual elements. A large block box can also hide a missing paragraph inside it,
so geometric coverage alone must not certify completeness.

First probe: known historical omissions, deliberately removed blocks, and clean
controls containing blank pages, headers, footers, illustrations, and watermarks.
Keep naturally occurring and injected errors separate in the results. Start
with a read-only report containing source page, region, evidence, and disposition.

Success means better recall of real omissions at a fixed review budget, without
turning legitimate page furniture into a dominant source of false alarms. Stop
or narrow the detector if most new findings are harmless regions.

### 2. Audit output that receives no warning

Extend [numeric confidence evaluation](../scripts/eval_numeric_confidence.py)
and the existing review workflow to prose, equations, reading order, and charts.
Use fresh documents covering scans, born-digital papers, and long supplements.
Reserve whole documents for evaluation; group related editions or templates to
avoid near-duplicate leakage. Once a case drives a fix, it becomes development
data and no longer supports an unseen-corpus claim.

Maintain two samples: a representative sample for estimating everyday error
rates, and an error-enriched challenge set for testing detectors. Randomly review
unflagged output as well as suspicious output. If sampling rates differ by
stratum, retain those rates and weight prevalence estimates appropriately.

Report at least:

- Error rate among unflagged outputs, with sample counts and uncertainty bounds.
- Fraction of known errors detected, and fraction of flags that identify errors.
- Fraction of content left unverified or image-authoritative.
- Errors found per fixed number of reviewed items.
- Correct repairs, incorrect repairs, and unnecessary changes to correct output.

Keep content types and evidence classes separate. Agreement with the same PDF
text layer is not an independent visual reading; repeated model samples are not
independent witnesses either. A wrong value that receives a verification label
counts as a failure even if its text never changes.

Success is an honest estimate with useful bounds. Insufficient labels should
remain an explicit result, without producing a new percentage-style confidence
badge. Existing numeric experiments already show why two successful proposed
replacements cannot establish a safe automatic correction policy.
[Recorded promotion decision](dense-numeric-tables.md#selective-confidence-calibration).

### 3. Test equation errors and semantic associations

Create a small set of source-checked examples before expanding the corpus:

- Equations: minus signs, inequality direction, numerator/denominator placement,
  powers, indices, vector notation, and grouping. Include inline expressions.
- Tables: correct digits assigned to wrong headers, lost units, merged headers,
  and rows that continue on another page.
- Charts: swapped series labels, wrong panel or axis, log/linear confusion, and
  unit scaling errors.
- Retrieval: a question whose answer requires the correct qualifier, unit, or
  adjacent-page context, together with the supporting source location.

Reuse [equation checks](../src/pdf2md/confidence.py), the
[table audit](../src/pdf2md/table_audit.py), and the
[agent benchmark](../scripts/agent_benchmark.py). Score literal extraction and
downstream answering separately so an agent's arithmetic or reasoning mistake
does not become a parser failure. Pin the answering model when comparing runs.

For charts, extend the existing axis and value evaluators with label associations
and predetermined tolerances appropriate to printed resolution. Evaluate the
current acceptance floor before changing it. Higher scores, agreement, or lower
dispersion alone cannot establish that a series was identified correctly.

Success means catching the intended corruption while accepting harmless
formatting variants. Report results by failure type so an average cannot conceal
a detector that misses every changed sign or every swapped header.

### 4. Retain table evidence across engine adapters

Inspect the native outputs available from the pinned Marker and MinerU versions
for positioned cells that the adapters could preserve. The current Marker
adapter documents that its JSON renderer flattens cells into table HTML. Confirm
whether an upstream export option or a small renderer change can retain them
before building independent cell reconstruction.

If reconstruction is necessary, treat ambiguous geometry as unavailable. The
existing glyph-grid experiments already found cases where whitespace cannot
reliably separate numeric content from column boundaries.

Compare engines on the same born-digital tables. Measure exact cell attribution,
per-cell verification coverage, false verification, and refusals. On scans,
coordinates improve localization but do not create a trustworthy glyph reference.
Preserve the existing row/grid audit, which already works without positioned
engine cells.
[Adapter evidence](accuracy-improvement-notes.md#what-an-engine-swap-costs-the-table-audit-measured-2026-09-04).

Success means recovering valid checks, not merely populating a geometry field.
Incorrect cell assignment must remain a measured failure even when the resulting
table looks plausible.

### 5. Try selective repair and cross-page links after the gates exist

Choose one failure class from the fresh audit. Run a bounded second read only on
those regions and retain the original, candidate, source crop, trigger, and
decision. Existing OCR/VLM facilities should be the first candidates. A new
reader earns an adapter only after it shows an advantage on the labelled cases.

For cross-page continuity, compare simple continuation rules with an optional
post-processing model on a small book/supplement set. Score true links, false
joins, and missed joins separately, with explicit negative examples of adjacent
unrelated tables. Original page/block identities must survive every proposed
join. Generated summaries should not replace source text.

Promote a repair only if its held-out benefit survives repeat runs and its
false-correction rate meets a criterion fixed before evaluation. For unstable
models such as the measured Marker setup, include run-to-run spread. Log model
revisions, configuration, routing decisions, and candidate hashes.

## First work package

The bounded first work package was completed on 2026-09-22. The
[missing-content pilot](missing-content-pilot.md) provides a frozen-input probe,
stratified review sheet, and descriptive scorer. Its existing-corpus run exposed
cross-page provenance gaps and running-header noise; it is not ready for
production. A fresh three-document evaluation now includes nine assistant-
reviewed page labels, descriptive equal-budget comparisons, finite-corpus
worst-case bounds, and an explicit non-promotion decision. See the
[evaluation and evidence](missing-content-pilot.md#fresh-evaluation-and-decision).

The [multi-page provenance follow-up](missing-content-pilot.md#multi-page-provenance-follow-up)
now retains all Docling block regions in saved state and retrieval records. On
one development paper, using those spans reduced probe-flagged pages from four
to one without changing the output text. Experimental running-header filtering
reduced development flags from 205 to 133, but the fresh probe missed all five
observed omission-bearing pages. The subsequent
[identifier fix](missing-content-pilot.md#list-and-citation-identifier-follow-up)
preserves all 121 supplied identifiers in the two known regression documents,
including Markdown and retrieval output. The
[scanned-text follow-up](scan-omission-pilot.md) now surfaces all three known
OCR omissions in one development scan, with two journal-banner false alarms.
It remains read-only; untouched multi-document scans and independent labels
are the next evaluation step. These findings do not establish calibrated
confidence or justify a new full parser.

The completed pilot delivers:

1. A manifest for a fresh evaluation set, with source hashes, document families,
   sampling rules, development/evaluation split, and a short labelling guide.
2. A read-only missing-region probe that reuses page geometry and rendering.
3. A review sheet that includes randomly selected unflagged pages and saves
   source-addressed labels in the existing evaluation format where practical.
4. A report comparing the existing warnings with the new probe at the same review
   budget, including misses, false alarms, refusals, and uncertainty bounds.
5. A decision record naming the next extraction experiment, or explaining why
   the evidence does not justify one.

Set review budgets and acceptance criteria before inspecting held-out results.
Use the Mac Studio for label preparation and light diagnostics; run sustained
CUDA parser/model comparisons on linux-4090 in their existing separate
environments. Keep corpus PDFs and large outputs outside the repository, following
[the QA corpus convention](qa-corpus.md).

## Approaches that remain deferred

- A new full parser or paid API without a demonstrated gain on this corpus.
- Whole-document multi-parser voting as a default.
- Treating model-reported confidence, valid syntax, or reader agreement as proof.
- Automatic numeric replacement before the existing promotion decision changes.
- Another threshold adjustment to the failed real-crop equation render metric.
- Reopening the abandoned font-only inline-math detector without new boundary evidence.
- Raising chart confidence simply because repeated samples agree.

These boundaries follow the recorded failures in
[the accuracy notes](accuracy-improvement-notes.md) and
[dense numeric-table experiments](dense-numeric-tables.md). The research above
suggests useful tests and narrower interventions; it does not overturn those
measurements.
