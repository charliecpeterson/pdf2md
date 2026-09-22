# Omission pilot: frozen evaluation protocol

Frozen on 2026-09-22 before converting or inspecting the three evaluation outputs.
This closes a bounded development experiment; it is not a general parser benchmark.

## Sources and split

Development: the existing ten `out-blind-v3b` documents and the multi-span
regression paper. Evaluation: three public documents selected for different
document families, not because of observed parser errors:

- Born-digital research paper: [Mikolov et al., arXiv 1301.3781v3](https://arxiv.org/abs/1301.3781v3).
- Long technical reference: [NIST SP 811, 2008 edition](https://doi.org/10.6028/NIST.SP.811e2008).
- Historical scanned chemistry paper: [Dibeler, 1952, tetramethyl mass spectra](https://nvlpubs.nist.gov/nistpubs/jres/049/4/V49.N04.A01.pdf).

Pin downloaded source hashes and verify no match against known project bundle
sources and the local QA corpus before conversion. The bibliographic pages and
source PDF text may be inspected to establish suitability. Conversion outputs
must remain uninspected until the policy and review selection are frozen.
This is fresh to this evaluation, not a claim of absence from model training.

Use Docling on MPS, with formula enrichment, chart digitization, and figure OCR
disabled. Force OCR on the historical scan. Preserve all fallback crops.
Do not tune thresholds on these outputs. Keep PDFs and conversion output in
scratch; commit hashes, labels, and aggregate results, not the source PDFs.

## Fixed sampling and labels

Use seed `20260922` and up to **two pages per document per stratum**. Strata are
flagged by either the original action-required queue or the final probe, and
flagged by neither. Retain inclusion probabilities and the full frame. Review
each selected source page visually against all relevant Markdown and content
crops, including neighbouring sections for continued paragraphs. Do not treat
the parser's extracted text or detector output as the label authority.

Labels answer whether meaningful source content is omitted from text and content
fallback crops. Running furniture and general page-evidence images follow the
[pilot labelling rules](missing-content-pilot.md#how-to-use-it). Wrong values and
ordering are outside this particular label. Record source/output evidence for
each definite label. Assistant-reviewed labels are explicitly identified and
must not be presented as independent human ground truth.

Additionally inspect at least one source occurrence of every distinct line the
furniture filter downgrades in the evaluation corpus. Record the supporting
pages and whether the downgrade appears justified. This is targeted safety
review, not a random estimate of suppression precision.

## Decision rules

1. No code promotion if any negative-control test suppresses body content, a
   table heading adjacent to rows, distinct numeric values, or a single-page line.
2. Keep all suppressed candidates and their evidence in the report. No source
   text or conversion output may be removed by this experimental filter.
3. Reject production promotion if source review finds any meaningful suppressed
   content, any unflagged omission, or unsupported input coverage in this pilot.
4. Even a clean small sample supports continued experimentation only, not a
   calibrated accuracy score. Statistical generalization from one document per
   family and assistant labels is not justified.

Report equal-budget yield at **four reviewed pages** per strategy within the
frozen sample, reviewed-page confusion counts, explicit refusals, and all missing
labels. Report deterministic worst-case bounds for the finite set of baseline-
unflagged pages: confirmed omissions divided by population size through one
minus confirmed clean pages divided by population size. These require correct
labels and are not confidence intervals. Do not substitute a degenerate bootstrap
interval when a small sample contains no observed omissions.
