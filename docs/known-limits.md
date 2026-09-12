# Known limits

What this tool does not do well, stated so a reader finds it before a
conversion does. The [approach map](../README.md#approach-map) says which
of these are deliberate boundaries and which are open work.

## Known limits (v1)

- **Equation enrichment is slow** (minutes for equation-heavy papers). `--no-formula`
  trades LaTeX for speed.
- **Suspect equations are image-backed.** When the engine's LaTeX disagrees with the
  page's text layer (or there's no text layer — a scan), the equation is cropped to a
  faithful image that becomes the authoritative source, with the text as a flagged
  hint. `--transcribe` upgrades that hint via local math-OCR. The recognizer receives
  blank context around the crop to avoid dropping symbols at its edges; the saved
  authoritative crop is unchanged.
- **Sub/superscripts** are recovered from glyph geometry on born-digital pages, on by
  default. A residual ceiling remains where the engine renders an exponent unlike the
  raw glyphs.
- **Dropped symbols are reported, not repaired.** Where the engine loses a Greek letter
  or math operator from prose, the block carries a marker naming the characters. That
  one is missing is certain; where to reinsert it is not, so the text is left as the
  engine produced it and the source page stays the reference. A dropped **comparison**
  (`≥ ≤ ≈ ∼ ≠ ≃ ≅ ≪ ≫`) is the same finding at high severity, because it is the one
  symbol loss that reads as ordinary text afterwards: `to be ≥1.6` emitted as `to be
  1.6` turns a floor into an exact value with nothing for a reader to notice, where a
  lost `±` leaves the visible `9.3 0.2`. ASCII `<` and `>` are not detected — the
  emitted side carries `<sup>` markup, which would supply phantom angle brackets — so
  the check is a floor on comparison loss, not a count of it.
- **A block of one or two characters that will not decode is a marginal mark, not lost
  prose.** A journal's decorative footer glyph is flagged as an `undecodable fragment` at
  informational severity rather than as an illegible paragraph, so `illegible_blocks`
  counts text a reader actually lost.
- **Book splitting depends on structural evidence.** Chapter bookmarks are preferred;
  numbered heading fallback is limited to Part containers to avoid turning references,
  index entries, or incidental “Chapter N” text into files. PDFs with neither signal
  remain split at their top-level bookmarks. Crops can still include journal furniture
  such as logos and banners.
- **`--describe` is an AI aid, not ground truth.** The description is labelled and the
  crop stays authoritative — verify specifics against the image.
- **Whole-page VLM transcription (`--ocr-page-vlm`) trades structure for a clean
  Markdown page.** It can recover prose and equations well, but table, equation, and
  caption elements are no longer separately addressable. A failed or looping read gets
  a visible marker and the page raster. Use the MinerU engine when element structure and
  equation-level crops matter.
- **Chart data is withheld often, and what does ship is right about six times in ten.**
  Design rule 4 says a structured value ships only when its gate passes; for figures
  that trade is measured, on two small hand-labelled sets. Of figures whose printed axes
  carry enough scale to recover numbers at all, about half are extracted: 23 of 50
  labelled figures were recoverable and 11 shipped. Nothing in that set invents data on
  a figure that was never recoverable.
  Of the numbers that do ship, **45 of 73 anchor points (62%)** read by hand off the
  printed charts are matched, and 18 of 19 extractions land on at least one. That
  average hides the thing worth knowing, so here is the split by the confidence printed
  beside every extraction:

  | confidence | figures | anchor points matched |
  |---|---|---|
  | below 0.60 | 10 | 18/41 (44%) |
  | 0.60 and above | 9 | 27/32 (84%) |

  So below 0.60, rather more than half the shipped numbers are not on the printed
  chart, usually because the figure emits one curve where the page draws three or four.
  Treat a low-confidence chart extraction as a hint and read the crop. The gaps are
  known and named rather than mysterious: bar and category-axis charts, figures embedded
  as bitmaps, dual-axis figures whose tick text is outlined, and second panels whose
  range differs from the first. If you need a figure's numbers, the crop is beside the
  data and is the authority. `scripts/eval_figure_axes.py` and
  `scripts/eval_figure_values.py` reproduce both measurements.
- **Reader agreement is not ground truth.** OCR candidates can contain a plausible
  wrong digit that two readers share. The crop remains authoritative unless a cell
  matches a pinned external reference or a human verifies it.

The `Unresolved error severity` row reports its mass, not only its worst item:
`high (1 high, 3 medium, 8 low; 12 action items)`. A severity alone saturates — a
paper whose single high finding is one reading-order glitch reads exactly like a
346-page supplement with contaminated data cells — and the breakdown is what makes a
stack of documents rankable.
