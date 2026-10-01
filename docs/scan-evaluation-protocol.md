# Fresh-scan omission evaluation

Freeze this protocol before conversion. This is a bounded evaluation of the
existing read-only probe, not permission to repair OCR or change confidence.

## Sources and exposure

Select three short historical NIST papers for scientific prose, footnotes,
lists, and tables, without inspecting their pdf2md conversions:

- [Purification of gallium by fractional crystallization of the metal](https://nvlpubs.nist.gov/nistpubs/jres/15/jresv15n3p205_A1b.pdf), 1935.
- [Effects of fumigants on paper](https://nvlpubs.nist.gov/nistpubs/jres/15/jresv15n3p271_A1b.pdf), 1935.
- [Infrared spectra of noble gases (12000 to 19000 A)](https://nvlpubs.nist.gov/nistpubs/jres/049/jresv49n2p73_A1b.pdf), 1952.

Source metadata and source-only pages may be inspected for suitability. Pin
downloaded bytes and check against known project source hashes, local QA PDFs,
and previous pdf2md scratch manifests before conversion. Report that scope;
absence from this inventory is not proof of global novelty or training-data
exclusion. Do not silently replace duplicates, unreadable PDFs, or failed runs.
All three papers share an archive; this does not test scanner/publisher diversity.

Use the current Docling adapter on MPS with forced OCR, formula enrichment off,
chart digitization off, and figure OCR off. Keep content fallback crops. Run
documents serially. Pin conversion configuration and complete bundle hashes.
Do not inspect converted content before freezing this protocol and source set.

## Detector and review budget

Keep the existing scan-probe policy unchanged: 300 dpi, Tesseract English PSM 3,
at least 20 alphanumeric characters, mean reader confidence at least 70, and
normalized substring distance greater than 0.20. No threshold or furniture-rule
tuning on these outputs. Pin reader, trained-data, code, protocol, and reports.

Screen every source page. Record failed, empty, rotated, and partly unassessed
pages explicitly; lack of a candidate is not a clean-page verdict.

Sample up to two pages per document per stratum, with seed 20260923. The strata
are flagged by either the existing action-required queue or the scan probe,
and flagged by neither. This gives at most 12 pages. Keep the complete frame
and inclusion probabilities. A page with a reader refusal stays in the frame
and is reported separately; it must not be called detector-verified clean.

Use a detector-masked review sheet: full source page, page-attributed output,
links to complete Markdown and content crops, no detector hints or statuses.
Do not prefill labels from assistant review. The human reviewer should not
inspect the detector report until the labels are saved. Production markers in
the original output remain visible, so this is not fully blinded evaluation.

Label whether meaningful source content is absent from text AND content
fallback crops. Footnotes and list identifiers count; running headers, page
numbers, and decoration do not. General page-evidence images do not preserve
content for this label. Wrong values and reading order are separate defects.
Check adjacent sections for continued paragraphs. Use uncertain when needed.
Every definite label needs a reviewer and source/output evidence note.

## Decision and scoring

Do not promote the probe until independent human labels are available. An
assistant must not claim independent adjudication or fill the human template.
After labels arrive, compare baseline and probe yield at four reviewed pages
within the frozen sample using the existing descriptive scorer. Report missing
labels, confusion counts, and finite-corpus worst-case bounds, not calibrated
confidence or corpus-wide recall. Page-level yield does not measure candidate
localization precision.

Any confirmed unflagged omission, page-reader refusal, or more than one
false-positive page among reviewed probe-flagged pages rejects production
promotion in this pilot. A clean small sample only supports a broader evaluation,
not deployment. Candidate-level precision would require a separate frozen
localization review; do not derive it from these page labels.

Commit the protocol, source identities, sampling record, and test results. Keep
PDFs, bundles, OCR TSVs, and review images in scratch. Leave labels unreviewed
until the user or another independent human supplies them.
