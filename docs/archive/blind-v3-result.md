# blind-v3: predictions, and what the run gave

Spent 2026-09-02 on build cb310f0, one conversion at default settings, after a
batch of four output-affecting changes all justified on the working corpus. The
predictions below were written and committed to before the run; the result column
was filled in after.

## Predictions

Working-corpus reference: 36 documents, 3,495 pages, 894 tables, build cb310f0.

| quantity | working corpus | prediction for blind-v3 | what would falsify the changes |
|---|---|---|---|
| `accounted_for` | true on all 36 | true on all 10 | any false |
| `dropped` | 0 | 0 | any non-zero |
| split-line pages | 16 (0.46% of pages) | 0-3 over 345 pages | a rate far above ~1%, meaning the printed-line requirement only suppressed documents I had read |
| printed-lines artifacts | 114 of 894 tables (12.8%), but 68 in one basis-set SI and 20 in one scanned textbook | low single digits; arXiv papers carry ordinary numeric tables | a double-digit share, meaning `corroborated` fires on healthy grids |
| equations action vs informational | 20 action / 340 informational | action a small minority | most equations landing as action, meaning the substitution signature does not generalize |
| reading-order precision (proof + poppler) | 0.90 | 0.75+ | a collapse, meaning the geometric proof was fitted to these layouts |
| table-row adjudication | 1.00 on separable convictions | upholds >> contradicts | contradictions appearing at all |

These are aggregates. Per the corpus's own recorded discipline, report them before
drilling into any individual table.

## Result

10 documents, 345 pages, 62 tables. All ten converted `rc=0`.

| quantity | predicted | measured | held |
|---|---|---|---|
| `accounted_for` | true on all 10 | true on all 10 | yes |
| `dropped` | 0 | 0 | yes |
| split-line pages | 0-3 of 345 | 0 (0.00%) | yes |
| printed-lines artifacts | low single digits | 1 (1.6% of 62 tables) | yes |
| equations action vs informational | action a small minority | 18 action / 235 informational (7.1%, against 5.6% on the working corpus) | yes |
| reading-order precision | 0.75+ | 0.91 (working corpus 0.90) | yes |
| table-row adjudication | upholds >> contradicts | 1 upholds, 0 contradicts | too small to carry weight |

Every prediction held. The changes are not fitted to documents already read.

Two things worth recording that were not predicted:

- Poppler alone scores 0.41 here against 0.61 on the working corpus, and the
  geometric proof settles more pages than poppler does (25 against 18). The
  adjudicator is weaker on arXiv two-column layouts; the proof is not.
- 14 `illegible text layer` findings, against 4 across the whole 36-document
  working corpus -- and all 14 are in one paper, 2608.31138v1. A font-specific
  defect concentrated in one document is what that flag exists to catch, not a
  systemic change.

The table-row row is the one genuine gap: 62 tables produced a single row-level
conviction, so that adjudicator is untested here. It needs a table-heavy blind
set, which arXiv preprints are not.

## Status

Spent. This set has now been read and must not be used as a blind measurement
again.
