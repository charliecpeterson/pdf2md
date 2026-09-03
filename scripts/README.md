# scripts/

Development harnesses. None of this ships: `pyproject.toml` packages `src/pdf2md`
only, and nothing here is imported by the library.

There are 72 of them, 22.9k lines — more code than `src/` holds. That is not
sprawl, it is the project's actual asset. Every claim in `CLAUDE.md` and `docs/`
that carries a number was produced by something in here, and the 84 label and
corpus files in `tests/` are what those numbers are measured against. A finding
with no harness behind it is an opinion.

## What is here

| prefix | count | what it does |
|---|---|---|
| `eval_*` | 55 | scores one behaviour against labels, an independent reader, or a second engine |
| `benchmark_*`, `benchmark.py` | 3 | timing and end-use question answering, not correctness |
| `build_*` | 2 | generates a synthetic corpus with known ground truth |
| `mine_*` | 2 | finds candidate cases worth labelling by hand |
| `run_*` | 3 | drives an external tool (a reference implementation, a separate OCR env) |
| the rest | 7 | `qa.py` (the regression gate), bakeoff scoring, one-off preparation |

## The distinction that matters

**Gates** are re-run and must keep passing. `qa.py` over the corpus,
`eval_table_audit.py --check`, `eval_figure_axes.py --check`,
`eval_figure_values.py --check`. These have a `--check` flag or a committed
baseline, and a regression in them is a bug.

**Probes** answered a question once. Their finding is written down in `docs/` or
in the `CLAUDE.md` gotchas, and the harness is kept so the finding can be
re-derived rather than re-argued. A probe is not expected to be re-run, and it may
need a corpus that no longer exists on this machine.

If you are adding one, say which it is in the module docstring's first paragraph,
and if it is a probe, name the document that records what it found. A harness whose
finding is nowhere written down is the one thing here with no reason to exist.

## Before deleting

Check `git log --oneline -- docs/ CLAUDE.md` for the commit that cites it. If the
number it produced is still quoted anywhere, keep the harness: a quoted number
whose derivation has been deleted is worse than no number.
