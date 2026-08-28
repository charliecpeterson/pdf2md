# Engine bake-off

This comparison runs each parser through its own CLI and keeps the resulting
files unchanged. Its measurements selected Docling as the default, MinerU for
scans and difficult tables or equations, and pdf2md's own vector path for chart
recovery. The MinerU adapter was added only after that result.

## What gets compared

The candidates are:

- `pdf2md-current`, the incumbent local pipeline
- `pdf2md-page-vlm`, the supported high-accuracy scan path using
  `--ocr-page-vlm --vlm-ocr-model glm-ocr:q8_0`
- `docling-standard`, with formula, picture, and chart enrichments enabled
- `docling-vlm`, using the `granite_docling` preset
- `docling-heron`, `docling-heron-101`, and the three `docling-egret-*`
  candidates, which vary only the standard pipeline's layout detector
- `paddleocr-vl`, using its document parser on CPU
- `mineru`, using its high-effort hybrid backend with image analysis enabled

The committed manifest at `tests/bakeoff_manifest.json` pins each source by
SHA-256. A missing or changed source stops that run. It currently covers the
three real-document sources and five deterministic chart fixtures. Full-book and
sampled-page cases produce 11 manifest entries. Do not add unverified hashes
copied from old output metadata.

A manifest entry may select one source page. The runner uses Poppler's
`pdfseparate` to create one source-hash-keyed artifact under
`out/bakeoff/_inputs`, then records the parent PDF hash, source page, derived PDF
hash, and producer command. Every engine receives the exact same derived bytes
rather than processing an entire book for one labelled case.

## Run it

List the available executables and corpus entries:

```console
uv run python scripts/engine_bakeoff.py --list
```

Preview a resolved command without writing output:

```console
uv run python scripts/engine_bakeoff.py \
  --engine docling-standard \
  --document vector-plot \
  --dry-run
```

Run one or more combinations:

```console
uv run python scripts/engine_bakeoff.py \
  --engine pdf2md-current \
  --engine docling-standard \
  --document vector-plot \
  --document bar-plot
```

PaddleOCR-VL and MinerU should live in separate environments. Point the runner
at those executables instead of adding either model stack to pdf2md's
environment:

```console
uv venv env/paddleocr --python .venv/bin/python
uv pip install --python env/paddleocr/bin/python \
  'paddlepaddle>=3.2.1' 'paddleocr[doc-parser]' python-docx

uv venv env/mineru --python .venv/bin/python
uv pip install --python env/mineru/bin/python 'mineru[all]'

uv run python scripts/engine_bakeoff.py \
  --engine paddleocr-vl \
  --document raster-scientific-figure \
  --executable paddleocr-vl=env/paddleocr/bin/paddleocr
```

The explicit `python-docx` dependency is required by PaddleOCR's document export
path in the tested release, although it was not installed by the `doc-parser`
extra. Use the same executable override form for MinerU. An unavailable
executable creates an `unavailable` run record and makes the command exit
nonzero. A native process failure also stays on disk with its logs and exit code.

The environment split follows the official
[PaddleOCR-VL Apple Silicon guide](https://www.paddleocr.ai/main/en/version3.x/pipeline_usage/PaddleOCR-VL-Apple-Silicon.html)
and [MinerU installation guidance](https://github.com/opendatalab/MinerU#quick-start).

## Pinned Docling layout sweep

Docling 2.108.0 defines Heron as the standard pipeline default and exposes
Heron-101 plus medium, large, and xlarge Egret alternatives through
`PdfPipelineOptions.layout_options`. Its CLI does not expose that selection, so
`scripts/docling_layout_candidate.py` runs the same standard-pipeline settings
for each candidate and changes only the layout model.

`tests/docling_layout_candidates.json` records an immutable Hugging Face commit,
weight byte count, and weight SHA-256 for every candidate. The native runner
verifies the downloaded weight before a run is accepted and writes
`candidate.json` beside the untouched Docling JSON and Markdown. That record
also captures the installed Docling, Docling Core, IBM Models, Docling Parse,
and RapidOCR versions plus every fixed pipeline option.

Run the four labelled failure strata offline after downloading the pinned
snapshots:

```console
HF_HUB_OFFLINE=1 uv run python scripts/engine_bakeoff.py \
  --engine docling-heron \
  --engine docling-heron-101 \
  --engine docling-egret-medium \
  --engine docling-egret-large \
  --engine docling-egret-xlarge \
  --document grasp-table-3-1 \
  --document grasp-equation-4-1 \
  --document slater-page-37 \
  --document raster-scientific-figure
```

The model family and evaluation method are described in
[Advanced Layout Analysis Models for Docling](https://arxiv.org/abs/2509.11720).
The project publishes the model artifacts in its
[layout-model collection](https://huggingface.co/collections/docling-project/layout-models).
These upstream measurements motivate candidates, but only the source-labelled
pdf2md corpus can change this project's default.

`tests/bakeoff_engine_pins.json` retains the MinerU package, dependency, and
model-snapshot pins used by its comparison rows. The local configuration that
named those snapshots predates the recorded runs; its hash and modification
time are part of the evidence.

## Output contract

Runs are append-only under `out/bakeoff` by default:

```text
out/bakeoff/<document>/<engine>/<utc-run-id>/
  run.json
  stdout.log
  stderr.log
  native/
```

Sampled inputs shared across engines live under `out/bakeoff/_inputs/`.

`native/` contains the engine output without normalization. `run.json` records
the exact command, executable and version probe, source hash, host, duration,
peak resident memory, CPU time, exit code, status, and a hash of every native
file. The runner detects both the
single-command Docling CLI installed in this project and the newer `docling
convert` form.

Do not score converted Markdown alone. Inspect raw output before adding an
engine-specific reader, then measure the relevant facts from its native
structured files.

The scorer reads chart series, table grids, Markdown reading order, figure bounds
and text, and equation LaTeX from the native formats emitted by pdf2md, Docling,
PaddleOCR-VL, and MinerU. It also checks that figures and equations retain source
crops when required, and that raster-chart extraction does not emit unverified
structured data. It compares those native fields against source-derived facts in
`tests/bakeoff_labels.json`:

```console
uv run python scripts/score_bakeoff.py out/bakeoff \
  --engine docling-standard \
  --engine pdf2md-current \
  --document vector-plot
```

Omit `--strict` while comparing imperfect candidates. Add it when every pinned
fact is expected to pass and a failed fact should produce a nonzero exit status.
The scorer always uses the newest run, including failed runs, so an older good
result cannot hide a current engine failure.

Equation comparison removes only presentational LaTeX differences: whitespace,
alignment environments, `\left`/`\right`, spacing commands, redundant
single-character braces, and the `\mathbf`/`\bm` aliases for `\boldsymbol`. It
does not discard font weight, symbols, operators, indices, or grouping. The
equation number is checked as a separate fact. A transcription that drops vector
bolding therefore fails even when the scalar expression is otherwise identical.
