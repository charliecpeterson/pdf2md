"""Measure --figure-labels accuracy against hand-labelled ground truth.

    uv run python scripts/eval_figure_labels.py OUT_DIR [--labels FILE]

qa.py catches when output silently regresses; it can't say the printed labels a figure
carries were read *right*. This does, for a small hand-checked set of figures: it scores
each figure's `labels.text` against the known-correct transcription two ways — an overall
character similarity (0-1) and, because a plot's load-bearing content is its numbers, a
numeric-token accuracy (how many of the true axis/peak values were recovered). Reads
outputs only; no reconversion.

Run it against several output dirs to compare the model (`--vlm-ocr-model`) and
vote count (`--ocr-consensus`) in separate `--out` dirs, then watch
whether accuracy moves. Figure block ids (`#/pictures/N`) are stable across those runs,
so the same labels apply. The last column is the flag-don't-fabricate check: a wrong read
that stayed high-confidence is the dangerous failure; a wrong one the block flagged
(`reads disagreed`, low confidence) is the system working as designed.

The seed labels in tests/figure_labels_labels.json are keyed to the figures of two local,
not-committed sources — `image.pdf` (one busy rotated multi-panel plot) and
`GRASP2018-manual.pdf` (a flowchart with exact text, a clean numeric plot). Ground truth
was read off the crops by hand, not from model output. Convert with e.g.

    pdf2md convert GRASP2018-manual.pdf --figure-labels --ocr-consensus 3 \
        --vlm-ocr-model glm-ocr:bf16

then point this at the `out/` dir. Re-label more number-rich figures to make the numeric
metric bite harder.
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
from pathlib import Path

from pdf2md.consensus import numeric_tokens

_WS = re.compile(r"\s+")


def _norm(s: str | None) -> str:
    return _WS.sub(" ", (s or "").strip()).lower()


def _similarity(candidate: str | None, truth: str) -> float:
    return round(difflib.SequenceMatcher(None, _norm(candidate), _norm(truth)).ratio(), 3)


def _numeric_hits(candidate: str | None, truth: str) -> tuple[int, int]:
    """(numbers recovered, numbers in truth), order-insensitive over the multiset."""
    want = numeric_tokens(truth)
    got = list(numeric_tokens(candidate or ""))
    hits = 0
    for tok in want:
        if tok in got:
            got.remove(tok)
            hits += 1
    return hits, len(want)


def _latest_provenances(out_dir: Path) -> dict[str, dict]:
    """source filename -> its newest complete version's provenance dict."""
    by_source: dict[str, dict] = {}
    for doc_dir in (p for p in out_dir.iterdir() if p.is_dir()):
        versions = sorted(doc_dir.glob("v*/provenance.json"),
                          key=lambda p: int(p.parent.name[1:]) if p.parent.name[1:].isdigit() else -1,
                          reverse=True)
        for prov in versions:
            d = json.loads(prov.read_text())
            src = Path(d.get("source_path", "")).name
            if src and src not in by_source:
                by_source[src] = d
            break
    return by_source


def main() -> None:
    ap = argparse.ArgumentParser(description="Score figure-label text against ground truth.")
    ap.add_argument("out_dir", help="Output root (an `out/` directory).")
    ap.add_argument("--labels", default="tests/figure_labels_labels.json")
    args = ap.parse_args()

    labels = json.loads(Path(args.labels).read_text())
    provs = _latest_provenances(Path(args.out_dir))

    sims, num_hits, num_total, missing, confident_wrong = [], 0, 0, 0, 0
    print(f"{'FIGURE':30} {'SIM':>6} {'NUMBERS':>9} {'CONF':>6}  FLAG")
    print("-" * 78)
    for lab in labels:
        prov = provs.get(lab["source"])
        fig = next((f for f in prov.get("figures", []) if f["block_id"] == lab["block_id"]),
                   None) if prov else None
        got = (fig.get("labels") or {}).get("text") if fig else None
        if not got:
            print(f"{lab['note'][:30]:30} {'—':>6} {'—':>9} {'—':>6}  no labels in outputs")
            missing += 1
            continue
        sim = _similarity(got, lab["text"])
        hit, tot = _numeric_hits(got, lab["text"])
        sims.append(sim)
        num_hits += hit
        num_total += tot
        lb = fig["labels"]
        conf = lb.get("confidence", 0.0)
        flagged = "disagreed" in (lb.get("note") or "")
        # flag-don't-fabricate: a weak read that stayed confident is the dangerous case.
        # Numbers are a plot's load-bearing content, so judge a numeric figure on numeric
        # recovery (char-sim runs low on scattered labels even for a good read); fall back
        # to char-sim only for a text figure (a flowchart, a legend) that carries few numbers.
        weak = hit / tot < 0.5 if tot >= 3 else sim < 0.5
        if weak and not flagged:
            confident_wrong += 1
        note = "flagged" if flagged else ("CONFIDENT-WEAK" if weak else "")
        print(f"{lab['note'][:30]:30} {sim:6.3f} {f'{hit}/{tot}':>9} {conf:>6.2f}  {note}")

    print("-" * 78)
    if sims:
        print(f"mean char similarity: {sum(sims) / len(sims):.3f}  (n={len(sims)})")
    if num_total:
        print(f"numeric accuracy:     {num_hits}/{num_total} = {num_hits / num_total:.3f}")
    if confident_wrong:
        print(f"confident-but-weak:   {confident_wrong} (weak read that did not flag — the bad case)")
    if missing:
        print(f"{missing} labelled figure(s) not found in outputs (reconvert with --figure-labels?)")


if __name__ == "__main__":
    main()
