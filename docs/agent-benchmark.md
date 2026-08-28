# Agent benchmark

This benchmark tests the user-facing claim: can an agent answer a pinned question
from a converted bundle as accurately as it can from the source PDF, with less
input context?

It is an answer-quality test with a deliberately small retrieval step. Bundle
mode can rank page-local chunks or stable passages by distinct exact query-term
coverage. It supplies the best record and opens at most one image when that record
is flagged for review or is image-only. PDF mode receives the answer key's pinned
source page as a rendered image. Giving PDF mode the relevant page avoids mixing
PDF navigation quality into the comparison.

## Inputs and scoring

`tests/agent_questions.json` contains 11 source-grounded questions and covers every
document in `tests/bakeoff_manifest.json`. Each question pins the source hash,
bundle hash, relevant pages, accepted answer, tolerance where needed, and the
label from which the answer was derived.

`scripts/agent_benchmark.py` sends the same question to the same model in both
modes. It records the parsed answer, citations, assets opened, review flags,
finish reason, and input/output tokens. The JSON summary reports per-mode totals,
bundle input-token reduction, and bundle accuracy minus PDF accuracy. Outcomes are:

- `correct`: the answer matches the pinned exact, contained-text, or numeric rule.
- `incorrect`: the model made a nonempty claim that does not match.
- `refused`: the model returned no answer, exhausted its output budget, or returned
  the requested `insufficient evidence` response.
- `error`: the source, bundle, endpoint, or model call failed.

An incorrect bundle answer is a release blocker when its selected evidence has no
review flag. Refusals remain visible in the accuracy result but are not scientific
claims and do not trigger that gate.

For a paired run, `--strict` also enforces the release condition: bundle
accuracy must meet or exceed PDF-page accuracy, and bundle input tokens must be at
least 20 percent lower. The threshold makes “materially less context” executable
rather than subjective.

Run the paired comparison with an OpenAI-compatible endpoint:

```console
uv run --extra describe python scripts/agent_benchmark.py \
  --mode bundle \
  --mode pdf \
  --model qwen3-vl:32b \
  --reasoning-effort none \
  --seed 0 \
  --max-chunks 1 \
  --max-assets 1 \
  --max-tokens 4096 \
  --output out/agent-benchmark/qwen3-vl-32b-full-seeded-lean.json \
  --strict
```

The reasoning setting is recorded and held constant between modes. Ollama's
OpenAI-compatible chat endpoint supports `reasoning_effort` values from `none`
through `max`; see its [supported request fields](https://docs.ollama.com/api/openai-compatibility#v1-chat-completions).

## 2026-08-12 full-corpus result

Environment: Apple M2 Ultra, Ollama 0.32.5, `qwen3-vl:32b`, temperature 0,
`reasoning_effort=none`, seed 0, and a shared 4,096-token completion cap. Bundle
retrieval supplied the highest-ranked page-local chunk and at most one review
image. PDF mode received the pinned source page rendered at 180 DPI. The
[result snapshot](agent-benchmark-2026-08-12.json) records these limits under
`settings`. It removes machine-specific absolute paths and raw model scratch
text while retaining answers, citations, asset basenames, and token counts.

| Mode | Correct | Incorrect | Refused | Valid page citations | Assets opened | Input tokens |
|---|---:|---:|---:|---:|---:|---:|
| Bundle | 11/11 | 0 | 0 | 11/11 | 3 | 8,693 |
| PDF page | 10/11 | 0 | 1 | 10/11 | 11 | 21,427 |

Bundle mode used 59.4 percent fewer input tokens and exceeded PDF-page accuracy
by one question. The PDF scatter-value call consumed the entire completion budget
without returning an answer. No bundle answer was incorrect, no release blocker
fired, and all bundle citations named the labelled source page.

`Assets` is the number of images opened. Accepted vector-chart rows and table text
can answer directly from the chunk. Review-marked equations and scanned-book pages
retain an image alongside their searchable text.

| Question | Mode | Answer | Outcome | Citation | Assets | Input tokens |
|---|---|---|---|---|---:|---:|
| Full GRASP radial-wave extension | Bundle | `w` | correct | page 26 | 0 | 754 |
| Full GRASP radial-wave extension | PDF | `w` | correct | page 26 | 1 | 3,095 |
| GRASP table radial-wave extension | Bundle | `w` | correct | page 1 | 0 | 740 |
| GRASP table radial-wave extension | PDF | `w` | correct | page 1 | 1 | 3,095 |
| GRASP equation number | Bundle | `(4.1)` | correct | page 1 | 1 | 1,434 |
| GRASP equation number | PDF | `4.1` | correct | page 1 | 1 | 3,095 |
| Full Slater phase-space shape | Bundle | `an ellipse` | correct | page 37 | 1 | 1,871 |
| Full Slater phase-space shape | PDF | `an ellipse` | correct | page 37 | 1 | 1,634 |
| Slater page phase-space shape | Bundle | `ellipse` | correct | page 1 | 1 | 1,862 |
| Slater page phase-space shape | PDF | `an ellipse` | correct | page 37 | 1 | 1,627 |
| Raster figure table reference | Bundle | `Table 5` | correct | page 1 | 0 | 293 |
| Raster figure table reference | PDF | `Table 5` | correct | page 1 | 1 | 3,095 |
| Vector line value | Bundle | `16.0241` | correct | page 1 | 0 | 247 |
| Vector line value | PDF | `16` | correct | page 1 | 1 | 1,154 |
| Scatter value | Bundle | `4.9086` | correct | page 1 | 0 | 314 |
| Scatter value | PDF | empty | refused | none | 1 | 1,156 |
| Scatter intersection | Bundle | `(4.001, 4.0074)` | correct | page 1 | 0 | 420 |
| Scatter intersection | PDF | `(4, 4)` | correct | page 1 | 1 | 1,152 |
| Two-panel values | Bundle | `6.0117, 40.1189` | correct | page 1 | 0 | 526 |
| Two-panel values | PDF | `6, 40` | correct | page 1 | 1 | 1,168 |
| Bar maximum | Bundle | `4.0005, 9.0094` | correct | page 1 | 0 | 232 |
| Bar maximum | PDF | `4, 9` | correct | page 1 | 1 | 1,156 |

The full-book runs exposed two retrieval defects before this final result. Common
word repetition could outrank broader exact-term coverage, and chunks could span
several source pages, making citations ambiguous. Ranking now prioritizes distinct
exact query terms, and chunk indexes stop at page boundaries. A one-chunk retrieval
budget therefore finds the answer-bearing Slater page without unrelated text.

The installed `qwen3-vl:8b` remains unsuitable for this benchmark. It consumed
the completion budget as thinking and returned empty content for a trivial bundle
question even with `reasoning_effort=none`.

## 2026-08-25 passage regression

I2 changes retrieval sizing and structure, so it needs a matched comparison against
the existing page chunks. The frozen bundles predate `passages.jsonl`; the benchmark
reconstructed passages in memory from each bundle's stored document provenance and
left the completed bundle directories unchanged.

A model-free top-one audit first checked navigation and citation scope. Chunks and
passages both selected a labelled answer page for all 11 questions, and both had mean
selected-page precision of 1.0.

The matched model run used Ollama `qwen3.6:35b-mlx`, temperature 0,
`reasoning_effort=none`, seed 0, one retrieval record, at most one image, and a
1,024-token completion cap.

| Retrieval | Correct | Valid page citations | Assets opened | Input tokens |
|---|---:|---:|---:|---:|
| Page chunks | 11/11 | 11/11 | 3 | 8,105 |
| Passages | 11/11 | 11/11 | 1 | 4,720 |

Passages preserve answer accuracy and citation validity while using 41.8 percent fewer
input tokens. Two legacy chunk records opened scanned-page images through the old
`needs_review` meaning; current passage review semantics did not classify those records
as action-required. Source-page and asset references remain in the passage records.

The frozen summary and raw-result hashes are in
[agent-benchmark-passages-2026-08-25.json](agent-benchmark-passages-2026-08-25.json).
Run the deterministic comparison with:

```console
uv run python scripts/agent_benchmark.py \
  --retrieval-audit \
  --max-chunks 1 \
  --output out/agent-benchmark/passage-retrieval-audit.json \
  --strict
```

For model inference, add `--retrieval passages` to the normal bundle command. When a
bundle lacks `passages.jsonl`, the benchmark rebuilds the current passage records from
its stored provenance without changing the bundle.

## 2026-08-25 long-book benchmark

`tests/long_book_agent_questions.json` adds 14 source-checked questions over two
technical textbooks: the 545-page *Introduction to Relativistic Quantum Chemistry*
and the 1,085-page eighth edition of *Atkins' Physical Chemistry*. The labels cover
prose facts, equation definitions, symbol meanings, table cells, figures, cross-page
explanations, references, and one unanswerable source-corruption case. Each label
pins the source hash, conversion-provenance hash, source pages, evidence block IDs,
answer rule, representation class, document class, and expected review disposition.

The PDFs are not part of the repository. `tests/long_book_agent_manifest.json`
contains their expected filenames, SHA-256 hashes, page counts, and document class.
To prepare a local corpus, place lawfully obtained matching files at the repository
root and verify them before conversion:

```console
shasum -a 256 Intro-to_Relativistic-QC.pdf atkins-physicalchemistry-8th.pdf
.venv/bin/pdf2md convert Intro-to_Relativistic-QC.pdf --no-formula
.venv/bin/pdf2md convert atkins-physicalchemistry-8th.pdf --no-formula
```

The frozen labels select the exact provenance hashes recorded in the question file.
A fresh conversion made after an implementation change is a new candidate bundle.
Source-check affected labels before updating their pinned provenance hashes.

The model-free audit measures chunks and passages at top-1, top-3, and top-5 budgets:

```console
.venv/bin/python scripts/agent_benchmark.py \
  --questions tests/long_book_agent_questions.json \
  --retrieval-audit \
  --retrieval-budget 1 \
  --retrieval-budget 3 \
  --retrieval-budget 5 \
  --output docs/agent-benchmark-long-books-2026-08-25-retrieval.json
```

| Records | Budget | Page hits | Mean page recall | Mean page precision | Ranking time |
|---|---:|---:|---:|---:|---:|
| Chunks | 1 | 12/14 | 0.8214 | 0.8571 | 1.221 s |
| Passages | 1 | 10/14 | 0.7143 | 0.6786 | 2.409 s |
| Chunks | 3 | 12/14 | 0.8571 | 0.3095 | 1.219 s |
| Passages | 3 | 12/14 | 0.8571 | 0.4821 | 2.432 s |
| Chunks | 5 | 13/14 | 0.8929 | 0.2000 | 1.219 s |
| Passages | 5 | 12/14 | 0.8571 | 0.2845 | 2.418 s |

Top-3 passages are the best tested compromise. They match chunk recall and improve
mean page precision by 0.1726. Top-1 passages regress by two page hits, while top-5
chunks gain one page hit at the cost of low precision. The action queue finds its one
labelled positive block group with no labelled false positives at every budget, for
precision and recall of 1.0. This queue score compares labelled block IDs directly
with `review.json`; it does not infer queue quality from lexical retrieval.

The audit also records the original conversion times from pinned provenance:
173.288 seconds for Relativistic QC and 1,007.967 seconds for Atkins. Result strata
are stored separately for every representation, retrieval type, budget, and document
class. The frozen model-free report is
[agent-benchmark-long-books-2026-08-25-retrieval.json](agent-benchmark-long-books-2026-08-25-retrieval.json).

The matched model run used local Ollama with `qwen3.6:35b-mlx`, temperature 0,
`reasoning_effort=none`, seed 0, top-3 passages, at most one image, and a 1,024-token
completion cap:

```console
.venv/bin/python scripts/agent_benchmark.py \
  --questions tests/long_book_agent_questions.json \
  --mode bundle \
  --retrieval passages \
  --model qwen3.6:35b-mlx \
  --reasoning-effort none \
  --seed 0 \
  --max-chunks 3 \
  --max-assets 1 \
  --max-tokens 1024 \
  --output docs/agent-benchmark-long-books-2026-08-25-model.json
```

| Representation | Correct | Valid page citations |
|---|---:|---:|
| Prose fact | 2/2 | 2/2 |
| Symbol meaning | 2/2 | 2/2 |
| Table cell | 2/2 | 2/2 |
| Figure | 2/2 | 2/2 |
| Cross-page explanation | 1/2 | 1/2 |
| Reference | 1/2 | 1/2 |
| Equation definition | 0/1 | 0/1 |
| Unanswerable | 0/1 | 1/1 |

The model answered 10 of 14 correctly, produced valid labelled-page citations for
11, and used 11,508 input tokens. Evidence preparation took 24.658 seconds and model
inference took 11.394 seconds. The review queue retained precision and recall of 1.0.
Correct abstention was 0 of 1: the model guessed `n` for source glyphs that are
missing in both the PDF and bundle.

The four failures name the next retrieval work rather than a parser-wide regression:

- The kinetic-balance explanation and its nearby citation were not in the top-three
  passages. One prompt refused; the other answered from a later, wrong citation.
- The Clapeyron prose passage reached the model, but the source-dependent equation
  crop ranked outside the budget, so the model refused the exact ratio.
- The RRK query retrieved adjacent page-856 prose but not the action-required
  missing-glyph passages, so the model made a claim despite a correct review record.

The model report is
[agent-benchmark-long-books-2026-08-25-model.json](agent-benchmark-long-books-2026-08-25-model.json).
Both reports state that calibration is not applicable because the runner emits
categorical outcomes and no confidence-like probabilities.

## Numeric-ingestion extension

`tests/numeric_agent_questions.json` adds six Fischer v6 tasks that exercise the
structured table interface rather than general document lookup:

- retrieve one exact cell;
- join records from two continuation blocks;
- calculate a ratio from two cells;
- select a value using atomic number, term, and configuration metadata;
- refuse a low-confidence `reader_refused` value without opening its crop;
- follow cell provenance to a source crop and confirm the value there.

The benchmark builds a bounded JSON evidence packet for each task. It resolves the
latest bundle by source hash, requires the pinned v6 provenance hash, and verifies the
SHA-256 digest of each CSV and crop before inference. Numeric tasks return a third
response field, `evidence_fields_used`. A task passes only when its answer, artifact
and page citation, task-specific evidence field, and required source asset all pass.
This makes a guessed value insufficient when the record exposes a refusal state.

```console
uv run --extra describe python scripts/agent_benchmark.py \
  --questions tests/numeric_agent_questions.json \
  --mode bundle \
  --model qwen3.6:35b-mlx \
  --reasoning-effort none \
  --seed 0 \
  --max-tokens 2048 \
  --output out/agent-benchmark/qwen3.6-35b-mlx-numeric-v1.json
```

The 2026-08-15 run passes 5/6 tasks. It answers the exact lookup, continuation join,
metadata lookup, and crop-confirmation tasks correctly, and it correctly refuses the
unverified value. All six responses cite the expected artifact and page, all six use
the minimum task-specific evidence field, and the crop task opens its required image.
The run uses 4,559 input tokens.

The one release blocker is arithmetic. The model reports 2.589076 for
`4.753337 / 1.835914`; decimal arithmetic gives 2.589085 at six places. This separates
table extraction quality from agent calculation quality and argues for a calculator
or deterministic expression evaluator in any ingestion workflow. The pinned result
is in `docs/agent-benchmark-numeric-2026-08-15.json`.

The follow-up calculator experiment freezes that ratio as a separate one-question
control. `--calculator` reads only the two query-selected, hash-pinned `best_value`
fields, verifies them against the declared operands, divides with `Decimal`, and adds
the rounded result to the evidence packet. Operand selection and arithmetic are
recorded as deterministic gates rather than inferred from the model's answer.

With the same Qwen3-VL 4B model and prompt, the control selects and cites the correct
fields but answers `2.587225`. The assisted condition verifies operands `4.753337` and
`1.835914`, computes `2.589085`, and returns that exact answer. It removes the release
blocker for 79 additional input tokens, a 20.2 percent increase on this small packet.
The frozen paired result is
`docs/agent-benchmark-calculator-2026-08-15.json`.

```console
uv run --extra describe python scripts/agent_benchmark.py \
  --questions tests/numeric_calculator_questions.json \
  --mode bundle \
  --model mlx-community/Qwen3-VL-4B-Instruct-4bit \
  --base-url http://127.0.0.1:18081 \
  --api-mode responses \
  --calculator \
  --max-tokens 256 \
  --output out/agent-benchmark/qwen3-vl-4b-calculator-assisted-v1.json
```

The installed `qwen3-vl:32b` is a Thinking checkpoint. In this environment its Ollama
renderer ignores both OpenAI-compatible `reasoning_effort=none` and native
`think=false`, then consumes the completion budget without producing content. The
numeric snapshot records failed 2,048- and 4,096-token attempts so future runs do not
misclassify this as a bundle error.

## Equation and figure extension

`tests/equation_figure_agent_questions.json` adds three exact equation-component
questions and three scientific-figure questions. Every question pins the source PDF,
the exact conversion provenance, the labelled page, and one required crop. Bundle
retrieval is page-bounded for this experiment because it measures representation
quality rather than document navigation. The required crop is opened first, with a
one-asset limit; PDF mode receives the corresponding rendered source page.

The 2026-08-15 run used `mlx-community/Qwen3-VL-4B-Instruct-4bit` through
mlx-vlm 0.3.9's Responses API, temperature 0, one chunk, one image, and a 256-token
completion cap:

```console
uv run --extra describe python scripts/agent_benchmark.py \
  --questions tests/equation_figure_agent_questions.json \
  --manifest tests/equation_figure_agent_manifest.json \
  --mode bundle \
  --mode pdf \
  --model mlx-community/Qwen3-VL-4B-Instruct-4bit \
  --base-url http://127.0.0.1:18081 \
  --api-mode responses \
  --reasoning-effort none \
  --seed 0 \
  --max-chunks 1 \
  --max-assets 1 \
  --max-tokens 256 \
  --output out/agent-benchmark/qwen3-vl-4b-equation-figure-v1.json
```

| Mode | Correct | Page citations | Required assets | Input tokens |
|---|---:|---:|---:|---:|
| Bundle | 5/6 | 6/6 | 6/6 | 13,215 |
| PDF page | 5/6 | 6/6 | 6/6 | 17,206 |

Bundle mode uses 23.2 percent fewer input tokens and matches PDF-page accuracy, so the
paired context and accuracy criteria pass. The figure tasks pass 3/3 in both modes.
Both modes fail the scanned Slater subscript: the printed equation is `rho_nu`, bundle
mode reports the known erroneous transcription `rho_0`, and PDF-page mode reports only
`rho`. The bundle failure remains a release blocker and prevents promotion of that
equation transcription. The orbitals answer `1/2` is mathematically equal to the
labelled `0.5`; numeric scoring now accepts a response consisting solely of a simple
fraction.

The frozen result is
`docs/agent-benchmark-equation-figure-2026-08-15.json`. mlx-vlm's chat-completions
handler was not used: it passes multimodal content, including base64 image URLs, into
the text prompt formatter and stalls during MLX evaluation. The Responses handler
separates image bytes from prompt text and reports token usage correctly.
