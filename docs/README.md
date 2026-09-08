# docs/

## Current

| | |
|---|---|
| [options.md](options.md) | every `convert` flag, which PDF wants which, and worked recipes |
| [output-format.md](output-format.md) | what a bundle contains — the `FORMAT_VERSION` contract |
| [methods.md](methods.md) | how each behaviour is measured, and against what |
| [known-limits.md](known-limits.md) | what this does not do well |
| [qa-corpus.md](qa-corpus.md) | the labelled corpus and where to keep it |
| [accuracy-improvement-notes.md](accuracy-improvement-notes.md) | candidate accuracy work, and what has been measured and rejected |
| [figure-to-text.md](figure-to-text.md) | how a chart becomes data, tier by tier |
| [dense-numeric-tables.md](dense-numeric-tables.md) | the hardest table case, and what is known about it |
| [public-release-plan.md](public-release-plan.md) | what has to happen before this repo is public, and what has been done |

## results/

The JSON a measurement produced, kept beside the document that cites it. Each is
named for its harness and its date, because a number without a run behind it
cannot be re-derived. `scripts/README.md` says which harness writes which.

## archive/

Completed workstreams: the record of a measurement that is finished, kept because
the conclusion is still cited and the method is how it was reached.

| | |
|---|---|
| [archive/PROJECT_PLAN.md](archive/PROJECT_PLAN.md) | the original rationale and decision log |
| [archive/quality-and-ingestion-plan.md](archive/quality-and-ingestion-plan.md) | the 2026 quality, performance and ingestion workstream |
| [archive/engine-bakeoff.md](archive/engine-bakeoff.md), [archive/bakeoff-results.md](archive/bakeoff-results.md) | how Docling was chosen and MinerU scoped |
| [archive/olmocr-bench-predictions.md](archive/olmocr-bench-predictions.md) | the benchmark that put Marker behind an opt-in flag |
| [archive/blind-v3-result.md](archive/blind-v3-result.md) | the blind corpus run |
| [archive/scan-degradation-benchmark.md](archive/scan-degradation-benchmark.md) | how table reading degrades as a scan gets worse |
| [archive/agent-benchmark.md](archive/agent-benchmark.md) | whether an agent can answer questions from a bundle |

Nothing here is deleted when it is finished. A rejected approach is the most
expensive kind of knowledge this project has, and `CLAUDE.md` is full of rules
that only make sense next to the measurement that produced them.
