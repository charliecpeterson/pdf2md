# Field report: pdf2md as ECPgen's numeric source, 2026-09-05

Second field report from the ECPgen side. The [first
one](2026-08-28-field-report-claim-checking.md) was about checking claims in
prose. This one is about the harder job: taking **numbers** out of a paper and
putting them into a fit. On 2026-09-05 that path produced a defect that reached
a shipped artifact, and the same day it repaired one. Both are worth writing
down.

Run at `068a3fb`, docling default, RTX 4090. Eight documents converted from a
clean checkout; a 545-page book and a 50-page scan were still running when this
was written.

| document | pp | layout | OCR dep. | tables verified | severity | local authors |
|---|---|---|---|---|---|---|
| dolg-ecp | 9 | excellent | none | full (4/4) | high | none found |
| 054111 (Lu & Peterson) | 14 | good | none | partial (5/7) | high | none found |
| ptuk-arabic | 18 | excellent | none | partial (1/2) | high | none found |
| PhysRev.37.1025 | 19 | good | none | partial (6/10) | high | found |
| cjk-sample | 20 | excellent | none | partial (8/9) | **medium** | found |
| 1972 compilation | 99 | fair | **full (99/99)** | **none (0/82)** | high | none found |
| GRASP2018 manual | 327 | good | none | partial (46/54) | high | none found |
| Lanthanides-SI | 346 | fair | none | partial (26/118) | high | found |

Speed is not a constraint. 346 pages parsed in 2m22s, 327 in about two minutes,
a 14-page paper in 25 seconds. Whatever this report asks for, it is not asking
for less work per page.

## The case that matters

ECPgen seeds its basis-set optimizer from exponents published in the
Lanthanides SI. Those numbers were extracted once, months ago, and stored in
`probes/peterson_si_declared.json`. A gate checks each shell's delivered count
against the count the SI itself declares, which makes the extraction
self-verifying — and it passed, 64 of 64.

Europium was wrong anyway. Its QZ *i* exponent read `98757.81` where the page
says `1.8867100`, and its QZ *g* row had dropped two exponents and picked up two
of its own *h* row's. The count gate passed all of it, because Eu declares one
*i* function and delivers exactly one. **Count right, value garbage.**

Re-extracting today from the same PDF, 62 of 64 shells agree exactly and the
only two disagreements are those Eu rows. The correct value also lands where the
family trend predicts, between Sm 1.747718 and Gd 2.062092 — a check that costs
nothing and would have caught it years earlier than the count gate ever could.

The lesson is not that pdf2md was wrong. It is that **a numeric extraction that
passes every structural check can still be wrong in the only way that matters**,
and neither side of this — not the converter, not the consumer — had a way to
notice.

## What would make the numeric path trustworthy

### 1. Report where the two grids disagree

This is the one I would build first, and it is cheap because both halves already
exist.

Every table ships an engine grid (`tables_N.md`) and a glyph grid
(`tables_N.glyph.md`). Neither is authoritative. Measured across the whole SI,
the engine grid holds **566 distinct numbers the glyph grid lacks**, and the
glyph grid holds **268 the engine grid lacks**. They fail differently: the
engine grid merges the page footer into 165 of 8,640 data cells, producing
values like `Q. Lu and K.A. 5.5680440E+00`; the glyph grid splits numbers across
column boundaries, turning `2.1999000E-01 1.6203900E-06` into
`2.1999000E-01 1` and `.6203900E-06`.

A split number is the worse failure, because it parses cleanly as a wrong
number. A contaminated cell at least fails loudly.

I argued earlier today that the glyph layer was cleaner and should be preferred.
That was wrong, and rejecting it was right: I had measured one failure mode
(cells mixing text and numbers, where glyph scores 0.00% against the engine's
1.91%) and generalised it into a direction the rest of the evidence does not
support.

**The useful artifact is not a winner, it is the diff.** The set of cells where
the two grids disagree is small, is computable without judgment, and is exactly
where a human should look. For a consumer like ECPgen it converts an unbounded
"re-read the PDF" into a bounded list. Nothing else in this report would have
caught Europium; a cross-layer diff might have, and would have cost one pass
over artifacts already on disk.

### 2. Page furniture keeps being promoted to content

The same root cause turns up in two places, and `b42a539` fixed only one.

In tables, the SI's running footer became a `colspan=5` row inside the basis-set
tables. That is fixed: `running_text_findings` requires the same string on three
distinct pages, keeps the 34 SI footer rows, and leaves all 88 Atkins section
titles alone.

In metadata, the same class is unfixed and lands harder:

- **1972 compilation** — `Title: CHARLOTTE FROESE FISCHER`, evidence quality
  **high**, sources `front_heading, repeated_heading`. That is the author's
  name, taken from the journal running head, which appears on 11 of the first 20
  pages. The real title is on page 1 and pdf2md emitted it correctly, as line 28
  of its own `document.md`: `# AVERAGE-ENERGY-OF-CONFIGURATION HARTREE-FOCK
  RESULTS FOR THE ATOMS HELIUM TO RADON`.
- **ptuk-arabic** — `Title: Palestine Technical University Research Journal,
  2026, 14(02), 159-176`. The journal citation line, same shape.

`repeated_heading` is being counted as *support*. For a journal running head,
repetition is the evidence that a candidate is **not** the title. The detector
that already knows this exists; it is scoped to tables. Wiring it in as a veto
on title candidates would flip both documents, and the vetoed candidate is then
a strong author candidate — with `Department of Applied Analysis` and
`University of Waterloo` on the next two lines, about as clear an affiliation
marker as there is.

Related: **five of eight documents report `Authors: no local candidate
selected`**, including `054111`, whose author line is in the emitted markdown
twice (`Qing Lu ; Kirk A. Peterson iD`, and `Qing Lu and Kirk A. Peterson`).
`cjk-sample` found its five authors locally at high confidence, so the path
works; it just rarely fires.

Worth separating: a wrong title at **high** confidence is worse than no title.
`front_heading` and `repeated_heading` are not independent evidence — a running
head *is* a front heading — so two correlated sources should not compound.

### 3. The remedy is known and not surfaced

For the 1972 compilation pdf2md gets the diagnosis exactly right, in
`review.md`: *"the embedded text layer is unfit to read... Re-run with
--force-ocr for a fresh transcription, or --engine mineru where available."*
`docs/accuracy-improvement-notes.md` already quantifies the payoff on this very
document — MinerU recovers 99% of the printed grid against docling's 21%, 144
tables against 82, in 176s against 560s.

`README.md`, the 73-line human summary carrying the scorecard, mentions none of
it. Zero hits for `mineru` or `force-ocr`. A reader sees
`Table verification coverage: none (0/82)` and gets no pointer to the fix that
is already measured to work. The line belongs next to the scorecard row it
explains.

## What ECPgen has to do on its own side

Not pdf2md's problem, recorded here because the two halves only work together.

**No number reaches training data on one extraction.** ECPgen's gates checked
that Eu's exponents were descending and did not duplicate across shells — both
exact, both good, both blind to a single wrong value in a one-element shell.
What caught it was a second extractor. That has to be policy, not diligence.

**A family trend is a cheap advisory.** Not a gate — three of the SI's published
series dip slightly with Z, so strict monotonicity would fire on correct data.
But `98757.81` among siblings spanning 1.12 to 3.69 is not a close call, and an
order-of-magnitude outlier flag would have caught it on the day it was
extracted.

## Limits of this assessment

Seven of the eight documents are born-digital; only the 1972 compilation
exercised OCR at all, so most of that path is untested here. Everything ran on
docling — I did not re-derive the MinerU comparison because
`accuracy-improvement-notes.md` already settles it. One corpus, one domain, and
the numeric claims all come from a single document family. And I got one call
wrong today, described above, which is the honest calibration on the rest.
