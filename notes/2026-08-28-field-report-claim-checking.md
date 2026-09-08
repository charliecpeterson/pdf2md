# Field report: using pdf2md to check claims in a paper under review

Written 2026-08-28 by an agent (Claude) that spent a working session using
pdf2md to verify citations in four physics papers in progress. Everything
below is grounded in a specific thing that happened, with paths. It is a
usage report, not a design review — treat the suggestions as evidence
about one workload, not as a spec.

**The workload.** I was hardening claims in a paper (`~/projects/ECPgen/
papers/p1-qecp`) that compares our pseudopotentials against three other
groups' families. Comparative claims about other people's methods are the
highest-risk sentences in the paper, so each one had to be checked against
the primary source. 28 PDFs in `~/projects/ECPgen/notes/ecp_papers/`,
converted to `~/scratch/ecp-papers-md/`.

---

## 1. The thing that made pdf2md worth using

`pdftotext` **silently truncated a sentence at exactly the operative
words** and I nearly published a wrong correction because of it.

The claim under test: "BFD's Wood–Boring references are quasirelativistic".
`pdftotext` on `dolg-ecp.pdf` (Burkatzki, Filippi & Dolg, JCP 126, 234105)
gave me:

> "the all-electron reference energies ... are calculated at the
>  scalar-relativistic, i.e., spin-orbit interaction free,"

and stopped — the two-column layout put the continuation elsewhere. Worse,
the interleaved text placed *"Trail and Needs have generated nondivergent
**Dirac-Fock** spin-orbit averaged relativistic potentials"* where it read
as a statement about BFD's own references. On that basis I concluded the
Wood–Boring attribution was unverifiable and **weakened a correct claim**.

pdf2md's output (`out/dolg-ecp-24f433c4/v1/document.md:132`) had the whole
sentence:

> "... at the scalar-relativistic, i.e., spin-orbit interaction free,
>  **Wood-Boring Hartree-Fock level of theory** within the LS coupling
>  scheme"

Same failure mode hit a second paper: `cr2001383.pdf` (Dolg & Cao, Chem.
Rev. 112, 403) interleaves a download watermark into the columns, and a
fragmented read had me about to write "WB vs DHF agree to 0.01–0.05 eV"
when the actual text says "**WB and CG** approaches" — two quasirelativistic
schemes agreeing with each other, which supports nothing about DHF.

**Two near-miss wrong claims in one session, both from column interleaving,
both caught by pdf2md or by a page-scoped re-extract.** That is the value
proposition, and it is worth stating in the README in exactly those terms:
not "cleaner markdown" but "the naive tool corrupts sentences in ways that
read as fluent text."

---

## 2. The biggest usability gap: I never found `passages.jsonl`

I did all my searching with `grep` over `document.md` and cited **line
numbers in a derived file**, which are meaningless to anyone else and
change on reconversion.

`passages.jsonl` is exactly what I needed and I did not know it existed
until I went looking at the end. One record carries:

```
section_breadcrumb : [{title: "Correlation consistent basis sets ..."}]
sources            : [{page: 2, bbox: {...}, source_page: "../source.pdf#page=2"}]
authority          : "text"
review             : {"needs_review": true, "dispositions": ["action_required"]}
```

Page number, bounding box, a link that opens the source at the right page,
section context, and whether the passage is trustworthy. That is a
citation, ready to paste.

**Suggestion — a `cite` / `find` subcommand.** The single highest-value
addition for this workload:

```
pdf2md find <doc-or-corpus> "systematic convergence"
  -> p2  §II. Basis sets  authority=text  review=ACTION_REQUIRED
     "Systematic convergence of both Hartree-Fock and correlation
      energies towards their respective CBS limits are observed."
     source.pdf#page=2
```

Without it the artifacts are excellent and undiscovered. The README does
say "For an agent, start with `manifest.json`, `metadata.json`, and
`outline.json`" — on **line 694**. I never got there. Consider moving a
three-line "checking a claim? do this" block to the top.

---

## 3. The review queue is a real safety net and I bypassed it

All three sentences I quoted from the Peterson lanthanide paper are
flagged `needs_review: true, action_required` — content-conservation
losses on those pages. I quoted them anyway, because I was reading
`document.md` and never opened `review.md`.

I got lucky: re-extracting each quote from the source PDF page-scoped
confirmed all three verbatim. But the process was unsound, and the tool
had already told me so in a file I did not read.

**Suggestions:**

- **Mark it inline.** If a passage is `action_required`, say so where the
  text is, e.g. an HTML comment in `document.md`
  `<!-- pdf2md: action_required, unexplained loss 8 words -->`. A reader
  quoting that paragraph then cannot miss it.
- **Make severity actionable.** 159 action-required items on an 11-page
  paper is more than anyone triages. Most were "unexplained loss: 2
  word(s)". Separating "lost 2 words" from "lost 20 words and a number"
  would let a user check the 5 that matter. The `review.md` table has the
  data; it is the ranking that is missing.
- **A `verify` command** that takes a quote and answers "is this string
  inside a passage flagged for review?" would close the loop I had to walk
  by hand.

---

## 4. Figures: the data I most wanted stayed a raster

The single most valuable object in the lanthanide paper for my purposes
was **Fig. 1: incremental correlation energy lowerings per angular
momentum for Gd**. I wanted those numbers to compare against my own
measurements. It came through as
`assets/pictures_8_p4.png` plus a caption.

The README advertises "born-digital charts as their extracted data". Either
this figure is not born-digital (plausible — it may be a raster embedded by
the publisher) or the archetype was not matched. Either way, what I needed
as a user was a one-line statement of **which**:

```
FIG 1 (p4): raster source, no vector data present — digitization not possible
FIG 3 (p7): born-digital, archetype unmatched — 4 series detected, not extracted
```

The second is a bug report I could act on. The first tells me to stop
trying. Right now they look identical from the outside.

**Lower priority but noted:** the same figure caption appears **three
times** in `document.md` (image alt-text, an italic caption line, and a
second italic variant). For grep-based reading that is noise, and it
inflates any word-count-based coverage metric.

---

## 5. Throughput and machine manners

Timings on an M2 Ultra (24 cores, 192 GB), from `convert.log`:

| doc | pages | wall |
|---|---:|---:|
| dolg-ecp.pdf | 9 | 5m 35s |
| 054101_1_online.pdf | 27 | ~12m |
| cr2001383.pdf | 78 | >20m (still running at last check) |

Two practical issues, both about **sharing a machine**:

- **It used ~400% CPU sustained.** I launched a heavy PySCF CI calculation
  alongside the batch and both slowed badly; a 10-minute quantum-chemistry
  job took 32. My fault for launching it, but a `--jobs N` or `--nice`
  flag would let a user leave a corpus converting in the background
  without it competing with the actual research. This is the single
  change that would most improve day-to-day usability for me.
- **No progress inside a document.** `still reading 78-page source with
  docling; per-page progress unavailable` for 20+ minutes is
  indistinguishable from a hang. Even `page 34/78` from a callback would
  fix it.

**Batch mode itself worked well** — 28 PDFs, one command, no aborts, and
per-document output landing incrementally so I could start reading the
early ones while the rest converted. That was genuinely good.

---

## 6. Smaller things

- **Skip-if-converted.** I could not tell from the docs whether re-running
  `convert` over a directory redoes finished documents. For an incrementally
  growing corpus (I add papers as I find them) an explicit `--skip-existing`
  and a printed "24 already converted, 4 new" would remove the doubt.
- **Corpus-level index.** After 28 conversions I had 28 hash-named
  directories (`054111-1-online-28cfccae`) and no way to answer "which of
  these is the actinide basis-set paper" without opening each
  `metadata.json`. `pdf2md list` exists — surfacing title/author/year/DOI
  in one table would have saved me repeatedly.
- **The YAML front matter is excellent** — title, authors, year, DOI, venue,
  volume, `citation_locator`, `pages`, `image_backed` count. That is what
  let me confirm a citation was the right paper before quoting it. Keep it.

---

## Converted documents referenced above

Corpus root: `~/scratch/ecp-papers-md/` (28 papers from
`~/projects/ECPgen/notes/ecp_papers/`). Specific ones cited here:

| document | source PDF | what it settled |
|---|---|---|
| `~/projects/pdf2md/out/dolg-ecp-24f433c4/v1/` | `dolg-ecp.pdf` | BFD Wood–Boring, §II B — the sentence pdftotext truncated |
| `~/scratch/ecp-papers-md/054111-1-online-28cfccae/v1/` | `054111_1_online.pdf` | Lu & Peterson lanthanide cc sets — the correlation-consistent criterion and shell groupings |
| `~/scratch/ecp-papers-md/074105-1-online-1c304960/v1/` | `074105_1_online.pdf` | Peterson actinide cc sets — "functions that contribute similar amounts of correlation energy" |
| `~/scratch/ecp-papers-md/114108-1-5-0285320-6d1cbfee/v1/` | `114108_1_5.0285320.pdf` | ccECP lanthanides — established that their stated validation scope excludes DMC |

`cr2001383.pdf` (Dolg & Cao review, 78 pp.) was still converting; I read it
with page-scoped `pdftotext` after a full-document extract proved unsafe.

---

## If only one thing changes

`pdf2md find <corpus> "<phrase>"` returning **page, section, and review
status**. Every other item on this list is a convenience; that one changes
what the tool is for. It turns a converter into a citation checker, which
is what I was actually using it as, badly, with grep.
