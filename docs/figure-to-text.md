# Figure-to-text: making charts readable without the pixels

> Research note, 2026-07-03. Historical context is in
> `docs/archive/PROJECT_PLAN.md`; current production behavior and design are
> summarized in `README.md`.
> Question: how far can a figure (especially a scanned chart) be converted to
> text/data/code so a *text-only* LLM can read the markdown and never need the
> crop? Covers where the pipeline already is, a hard-case study, the honest
> ceiling, and the mid-2026 model landscape with a ranked try-list.

## Where the pipeline already is

- **Born-digital vector charts are solved (near-lossless).** `digitize.py`
  reads the drawn path coordinates out of the PDF, calibrates against axis
  ticks, and `emit.py` places a CSV block plus a deterministic matplotlib
  repro script under the figure. On by default (`digitize_figures`), carries a
  confidence, withholds numbers below 0.5. The script is assembled from the
  extracted data, never model-generated, so it cannot invent values.
- **Known limits of the vector tier:** line + single-series scatter only.
  Multi-series scatter is a documented limit; bars, error bars, filled/contour
  plots are unhandled. Axis titles/legend don't ride in the repro script (they
  arrive separately via `--figure-labels`).
- **Raster/scanned charts:** `vlm_digitize` (`--digitize-vlm`, opt-in) has a
  vision model estimate points. It's a flagged estimate; the crop stays
  authoritative. `--describe` and figure labels also run once at convert time.

## Case study: the Ghia figure (out/ae6c469aa42b8fa6)

A scanned, 90°-rotated, two-panel waterfall plot (Ghia, Ghia & Shin 1982,
JCP 48:387 — lid-driven cavity u-velocity profiles). A pixel probe after
rotation correction found a **median of 13 separate ink segments per column
(p90 20, max 36)**: 14 curves, hairline drop lines, four marker shapes, and
in-plot text, all 1-bit black. Automatic tracing must reassign every segment
at every crossing with no color separation — the regime where WebPlotDigitizer
goes interactive. An automated pass lands below the confidence floor and gets
withheld (correct behavior, but no data).

Two lessons that generalize:

1. **Check for the printed table first.** The data plotted in this figure is
   tabulated in the same paper (the caption itself points at "Table 5").
   For scanned scientific figures this is common: the authoritative text form
   already exists as a table the pipeline extracts losslessly. A perfect pixel
   trace would still be worse than the table two pages away.
2. **The ceiling is real.** A raster figure has no path coordinates. Any
   recovery — classical CV or VLM — is an estimate and must stay
   confidence-flagged. "Lossless from pixels" is not achievable in principle;
   the win available is making the one-time text artifacts good enough that a
   session LLM rarely needs the image.

## Honest-ceiling taxonomy

| Figure class | Best text form | Fidelity |
|---|---|---|
| Vector line/scatter chart | CSV + repro script (shipped) | near-lossless |
| Vector bar/multi-scatter chart | same, after digitizer extension | near-lossless |
| Vector diagram/scheme (non-chart) | SVG of the figure region | lossless |
| Clean scanned chart (1–2 curves, no crossings) | pixel-calibrated trace | good estimate |
| Hard scanned chart (crossings, markers, panels) | VLM description + labels + table cross-ref | estimate / narrative |
| Scanned photo/micrograph | VLM description | narrative only |

## Model landscape (researched 2026-07-03)

The 2022-23 dedicated chart-to-table models (DePlot, MatCha, ChartGemma,
UniChart) are legacy; nothing current uses them. The field consolidated into
two lanes, both compatible with the OpenAI-style API seam in `describe.py`.

### Document-OCR VLMs (small, structured output)

| Model | Size | Notes |
|---|---|---|
| [DeepSeek-OCR / OCR 2](https://arxiv.org/pdf/2510.18234) | 3B, MIT | **"Deep parsing" mode converts chart images → HTML data tables** (also chem → SMILES). OCR 2 landed Jan 2026. MPS/CPU ports exist. [Guide](https://www.datacamp.com/tutorial/deepseek-ocr-hands-on-guide). |
| [olmOCR-2](https://arxiv.org/pdf/2510.19817) | 8B | AllenAI, RL-trained with verifiable rewards; ~82-83 avg on doc benchmarks. |
| Chandra | 9B | Datalab — same authors as Surya (already shipped in `transcribe.py`). Benchmark leader (~83.1) on complex tables/handwriting. |
| [PaddleOCR-VL 1.6](https://arxiv.org/pdf/2606.03264) | 0.9B | Chart+table→HTML, 109 languages, vLLM-supported, iterated through Jun 2026. |
| dots.ocr | 1.7B | Praised on HN ("crazy good"); figures + LaTeX. |
| Granite-Docling | 258M | IBM's Docling-native model — lowest-friction given the Docling engine. |
| Nanonets-OCR2 | 3B | Flowcharts → Mermaid. |

### General VLMs (chart reasoning)

- [CharXiv-R](https://llm-stats.com/benchmarks/charxiv-r) (Jun 2026): Claude
  Mythos Preview 93.2%, Opus 4.8 89.9%; best open = Kimi K2.6 86.7% (too big
  for local).
- Locally runnable workhorse: **Qwen3-VL** (8B fits the 4090; 32B+ on the M2
  Ultra). [Beats DeepSeek-OCR head-to-head on OCR Arena](https://www.ocrarena.ai/compare/qwen3-vl-8b/deepseek-ocr)
  (74.8% win rate, ELO 1465 vs 1348).

### Chart-to-code

- [ChartCoder](https://github.com/thunlp/ChartCoder) (7B, ACL 2025): chart
  image → matplotlib code, trained on 160k chart-code pairs. Benchmarks:
  [Chart2Code](https://arxiv.org/pdf/2510.17932),
  [RealChart2Code](https://arxiv.org/html/2603.25804). Trained on *rendered*
  charts — expect plausible fiction on old scans; useful on clean raster charts.

### Findings that shape the architecture

- **Crops lose chart structure.** Two-stage pipelines (detect region → OCR the
  crop piecemeal) collapse on chart benchmarks (ChartQA 7-57 vs 88 holistic;
  [Qianfan-OCR](https://arxiv.org/pdf/2603.13398)). Hand the model the whole
  figure, with calibration context, not fragments.
- **Fabrication is a live, unsolved complaint.** Even top models invent
  content (~10% "recitation" errors reported for Gemini; hallucinated text on
  blank pages) — [HN thread](https://news.ycombinator.com/item?id=45640594).
  The flag-don't-fabricate design and the 0.5 confidence floor stay.
- **Classical digitization has no 2026 breakthrough.** One methodology paper
  ([Kyrtos](https://arxiv.org/pdf/2602.09337), Feb 2026); WebPlotDigitizer
  added AI assist. Overlapping-curve scans remain unsolved automatically.

## Identified, not yet built

- **Excel-archetype charts: frameless + categorical x** (found 2026-07-04 in the
  collection audit; test case: `194109_1_online.pdf` p6, a 30-series 3rd-ionization-
  energy chart — the user's own domain, so this archetype is common in the target
  corpus). Two gaps compound: (1) no frame box or left spine — Excel draws horizontal
  gridlines + a bottom axis only, so frame detection finds nothing; the frame could be
  synthesized from the gridlines' common extent. (2) categorical x labels ("La-VDZ"),
  which the numeric `Digitization.series` schema can't carry — needs per-point category
  labels (schema change) and a CSV form like `La-VDZ,438.9`. The y side (numeric ticks,
  gridline positions) is already recoverable with existing machinery.

## Evaluated and shelved (2026-07-04)

Two recovery ideas were prototyped against real evidence this session; both were
shelved. Recorded so they aren't re-run from scratch. Probes live in
`~/scratch/pdf2md-marker-probe/`.

### Marker-based extraction for the Ghia scan

Idea: the fit-lines tangle, but the discrete data *markers* (□ ■ ○ ● △) are
shape-coded by series and mostly separated, so detect them as blobs or by
template match, classify by shape, and recover the sampled points even where the
curves cross. That's how a human digitizes this kind of plot, and the markers,
not the interpolating lines, are the actual data.

Three probes killed it, each obstacle sufficient alone:

1. **Fusion.** Every fit-line runs *through* its markers, so morphological
   opening leaves marker+line as one elongated component that fails a
   compactness test. Only ~37 of ~150 markers (the isolated ones) survived as
   clean blobs.
2. **Text saturation.** At scan resolution the figure's text is pixel-identical
   to its markers (○ vs O vs 0, ● vs a bold dot). OCR text-masking removes the
   axis numbers but a contact sheet of the 142 best marker candidates was almost
   all letters (`g`, `2`, `4`, `H`, `R`, …); it can't catch the a–g curve labels
   sitting inside the plot.
3. **Dense overlap.** Where the curves bunch near the walls — exactly where the
   boundary-layer data matters — markers overlap each other into a smear that no
   per-blob or template peak resolves into individual points.

Verdict: works for *isolated* markers (a sparse scatter on a clean scan would
benefit), not for this dense, text-heavy, line-fused, multi-series class. And a
partial read of a canonical benchmark would violate flag-don't-fabricate. Ghia's
answer stays the gate + the Table 5 pointer.

### Ensemble + anchor for tier 2

Idea (from [Self-Ensembling VLMs for Chart Extraction](https://arxiv.org/pdf/2605.27298),
2026): sample the anchored `vlm_digitize` N times at varying temperature, median
the aligned series to smooth noise, and use inter-sample disagreement (MAD) as a
confidence gate — paired with the pixel anchor pdf2md already has, which the
paper's method lacks.

Measured on a synthetic mid-difficulty raster chart with known truth (clean
calibration, r² 0.9999, ambiguity 3.0 — a fair tier-2 case, not a gated tangle).
The upgrade's *safety* holds but its *value* does not materialize on local models:

- The base model fails **structurally, not with random noise** — the regime
  ensembling needs. glm-ocr (the only local model that returns data; qwen3-vl
  32b/8b think past the token budget to empty, qwen3.6:35b refuses) gave the
  *same* hallucinated linear ramp (`8,6,5,…,-3`, below the axis floor) on 3 of 5
  samples. Median-of-systematic-bias is still biased: consensus error 0.744 ≈
  single-sample error 0.716, and the median even voted down the one good sample.
- The self-ensembling agreement signal read a misleading 0.70. The **pixel
  anchor** (pixel_fit 0.55, in-range 0.82) is what pulled final confidence to
  0.21 and withheld — and the current single-sample tier 2 already withholds this
  chart. So the ensemble adds 5× the VLM cost for no better read and no better
  gate.

Verdict: don't build it. Sound in principle on a roughly-right base model (the
paper's gains are real there), but no local model clears that bar, and the
load-bearing safety — the pixel anchor — is already shipped. The harness
(`ens_harness.py`) takes a model argument and is ready if a frontier or better
local backend ever makes it worthwhile, which trades away the local /
private / pay-once-at-convert premise.

External corroboration: [EpiCurveBench](https://arxiv.org/pdf/2605.27195) (2026)
finds even Claude Opus 4.5 / GPT-5.2 struggle to digitize continuous curves
reliably (systematic axis-scale and dense-cluster failures) — the ceiling is the
frontier, not the local hardware. [PlotPick](https://arxiv.org/pdf/2605.06021) is
the closest turnkey tool (batch scientific-figure extraction, raster + curves)
but ships no uncertainty quantification — a good human-in-the-loop tool, wrong as
an unattended pipeline stage.

## Implementation record

Ordered by value-per-effort; each lands behind an existing seam and gets
measured before the next starts.

1. **Pixel calibration + ambiguity gate (model-free).** Deskew, find the axes
   frame and ticks in the raster, pair with OCR'd tick numbers; compute a
   runs-per-column ambiguity score. Hard cases fail fast to "withheld" instead
   of emitting junk. This is the foundation the later tiers stand on.
   *Shipped 2026-07-03 as `calibrate.py` (`analyze_raster`): under
   `--digitize-vlm`, a chart past `AMBIGUITY_MAX` overlapping traces per column
   skips the VLM and emits a visible "plot data not extracted" marker; the
   calibration result is in place for tier 2 anchoring.*
2. **Anchored VLM digitization.** Upgrade `vlm_digitize`: pass the
   pixel-derived calibration to the model and have it read points against it.
   Backend bake-off via the existing OpenAI-compatible seam: DeepSeek-OCR(2)
   deep-parsing vs Qwen3-VL (8B/32B). Add labelled scanned-chart cases to the
   eval harness (`eval_digitize.py` or a sibling) so it's measured, not
   vibe-checked.
   *Anchoring shipped 2026-07-03: calibrated axis ranges ride in the prompt,
   out-of-range points cut confidence, and an in-range, pixel-agreeing read can
   clear the emit floor (`vlm-anchored`).*

   *Eval shipped 2026-07-04 as `scripts/eval_raster.py` — synthetic scans with
   self-generated truth (same pattern as `eval_digitize.py`), not hand-labelled
   real scans: gate verdicts, calibration error (~2.8% endpoint error where
   claimed), skew detection, plus an optional live-VLM half. Real labelled
   scanned charts can be added when a model worth measuring appears.*

   *Bake-off ran 2026-07-04 on a noisy synthetic scan (known truth,
   y = 100·e^(−x/4)): the pre-scan calibrated it at r² 0.998 with the injected
   1.5° skew detected. **qwen3-vl** (8b and 32b) thinks past the whole token
   budget on the JSON prompt and returns nothing — `digitize` now routes to the
   OCR model; with a 24k budget, 32b eventually answered at 7.6% mean y-error
   (final confidence 0.42, withheld — correctly, but close). **glm-ocr**
   answers in format (sometimes with a looping/one-quote-off reply, now
   salvaged) but read the exponential as a straight line — pixel_fit 0.41
   withheld it. **deepseek-ocr** can't follow an arbitrary JSON prompt
   (fixed-prompt specialist); using its native deep-parsing prompts would need
   a dedicated adapter. Net: no local model currently reads curve shape well
   enough to print, and the verification stack correctly withholds every wrong
   read while leaving the door open for better models.*
3. **Vector-tier extensions.** Bars (axis-aligned rects inside the frame,
   reuses frame detection) and multi-series scatter; fold `FigureLabels`
   (axis titles, legend) into the repro script so the script alone regenerates
   a labeled figure.
   *Shipped 2026-07-04: bars on a common baseline and marker-style-separated
   scatter series (`Digitization.kind`), both ≤0.1% y-error in the eval;
   caption + labels ride in the repro script as comments (comments, not
   `set_xlabel` guesses — which line is which axis isn't known).*
4. **SVG tier for non-chart vector figures.** Emit the figure region as SVG
   (`pdftocairo -svg` or mutool) alongside the PNG — a truly lossless text
   form for born-digital diagrams/schemes.
   *Shipped 2026-07-04 as `--figure-svg` (`render.svg_crop`). Gotcha found:
   pdftocairo silently ignores its -x/-y/-W/-H crop flags for SVG output, so
   the region is cropped upstream via a temp one-page PDF with the CropBox set
   (pypdfium2). Scanned pages skip; missing pdftocairo degrades with a log.*
5. **Table cross-reference.** When a caption references a table ("listed in
   Table 5"), note the link in the figure's emitted block/profile so a reader
   (human or LLM) is pointed at the lossless numbers.
   *Shipped 2026-07-04: emitted whenever the caption or recovered labels name
   a table AND the figure's plot data was not printed (none/gated/withheld) —
   handles the Ghia case's OCR'd "Table5" run-together form.*
6. **Experiments, expectations calibrated:** ChartCoder on clean raster
   charts; Chandra/olmOCR-2 as alternate `--ocr-page-vlm` backends;
   Granite-Docling given the Docling engine.
7. **Tier 1.5: vector curves + OCR'd axes** (added 2026-07-04 after the batch
   sweep): journals often outline figure fonts to paths — exact vector curves,
   no text to calibrate against, so tier 1 was blind to *every* figure in such
   papers (the valeev JCTC version vs its arxiv twin exposed this).
   `vector_ocr_digitize` keeps the vector geometry and reads the axis numbers
   off the rendered crop with RapidOCR; confidence capped by the OCR read
   (0.89–0.90 on the real paper, values correct to ~2 significant figures).
   Model-free, default on. *Extended same day to multi-panel figures (OCR
   tokens map back to page space through the crop render geometry; the shared
   `_fit_ticks` calibrates each frame), and axis fits gained leave-one-out
   outlier rejection — one OCR stray in a tick band no longer wrecks a clean
   fit, which also un-skipped the arxiv figure's "weak" panels (all 6 now at
   R² 1.000).*
8. **Multi-panel vector figures** (added 2026-07-04 after the real-document
   pass): subplots/insets digitize per panel — every frame that calibrates
   against its own neighboring ticks contributes series tagged in
   `series_names`; weak panels are skipped with a visible note. Unblocked two
   upstream bugs the arxiv test doc exposed: figures embedded as form XObjects
   were invisible (form-local coordinates; the container transform chain is now
   composed), and log ticks with a dropped superscript minus read 10^-3 as 10^3
   (`restore_log_signs`, powers-of-ten prior). *Shipped: a real
   2-panel × 7-log-series figure recovers at R² 0.992; a 6-panel orbital figure
   prints its 3 clean panels and names the 3 skipped.*

## What the 840 unmatched figures actually are (2026-09-02)

Across the 36-document corpus, 840 of 1,855 figures end as
`vector_archetype_unmatched` — a vector plot frame was detected and no data came
out. That is the largest single population in the pipeline, and it looked like
the obvious place to improve figure recovery. It is not.

The pipeline's own `has_series_geometry` splits it: **721 are calibration
failures** (the frames do hold line, scatter or bar geometry) and 119 have no
series geometry at all. But 640 of the 721 are in one document, the Atkins
textbook. Outside it, 81 across 35 documents.

Two attempts to characterise the cause by probe both failed, in the same way and
worth recording so they are not repeated:

- **Figure `labels` is not where tick values live.** Not one of the 840 carries
  four or more numeric tokens there — but neither do the 76 *successful*
  extractions (68 with fewer than four, 8 with none). Calibration reads tick text
  off the page region, not off `labels`.
- **Calling `_calibrate(page, frame, geometry.region)` does not reproduce the
  pipeline.** It reports "calibration returned nothing" for 17 of the 76 figures
  that in fact extracted cleanly, and "no frame on re-read" for 56 more. Panels
  calibrate against a per-panel text region, so a probe using the whole figure
  region is measuring something else.

A visual sample settled it where the probes could not. Three classes, all correct
refusals:

- **Symbolic axes.** Atkins Fig. 9.15 (particle in a box) has x ticks `0` and
  `L` and no y scale at all. There is no numeric axis to calibrate against.
- **A qualitative y axis.** Atkins Fig. 13.42 has three numeric x ticks
  (7.1, 10.0, 16.7) against a y axis labelled only "Light intensity". Extracting
  x alone would be half a dataset.
- **Page furniture read as a figure.** A WIREs page masthead is classified as a
  Picture, and its rules and boxes present as plot frames with series geometry.

So the refusal is right in each case sampled, and the headline number is not a
backlog. What would size the genuinely recoverable remainder is a labelled sample
of figures known to carry two numeric axes — which does not exist yet, and is the
prerequisite for any further work here rather than a detail of it.

The `data_extraction_note` now names which of the two causes applies, which is
what made this breakdown possible at all.
