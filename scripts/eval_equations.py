"""Measure equation accuracy against hand-labelled ground truth.

    uv run python scripts/eval_equations.py OUT_DIR [--labels FILE]

The labels-free qa.py catches when output silently gets *worse*; it can't say the
LaTeX is *right*. This does, for a small hand-checked set: for each labelled
equation it normalises and scores the engine's LaTeX and the math-OCR
transcription against the known-correct LaTeX (a 0-1 similarity), so you can see
whether the transcription is actually closer to truth than the engine — and
whether a render/engine change helps. Reads outputs only; no reconversion.

The score is a guide, not a grade: LaTeX has many equivalent forms, so normalise
what we can (whitespace, \\text wrappers, \\tag numbers, text-op backslashes) and
read the printed pair, don't just trust the ratio.
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
from pathlib import Path

_TAG = re.compile(r"\\tag\{[^}]*\}")
_WRAP = re.compile(r"\\(?:text|operatorname|mathrm|mathbf|mathit|mathbb|boldsymbol|rm)\s*\{?([^{}]*)\}?")
_FUNC = re.compile(r"\\(sin|cos|tan|max|min|exp|log|ln)\b")
_SPACE = re.compile(r"\\[,!;:> ]|\s+")


def _norm(s: str | None) -> str:
    if not s:
        return ""
    s = _TAG.sub("", s)
    for _ in range(3):  # unwrap nested \text{...}
        s = _WRAP.sub(r"\1", s)
    s = _FUNC.sub(r"\1", s)                       # \sin -> sin
    s = s.replace(r"\left", "").replace(r"\right", "")
    s = _SPACE.sub("", s)
    return s.lower()


def _ratio(candidate: str | None, truth: str) -> float:
    return round(difflib.SequenceMatcher(None, _norm(candidate), _norm(truth)).ratio(), 3)


def _latest_provenances(out_dir: Path) -> dict[str, dict]:
    """source filename -> blocks of its newest complete version."""
    by_source: dict[str, dict] = {}
    for doc_dir in (p for p in out_dir.iterdir() if p.is_dir()):
        versions = sorted(doc_dir.glob("v*/provenance.json"),
                          key=lambda p: int(p.parent.name[1:]) if p.parent.name[1:].isdigit() else -1,
                          reverse=True)
        for prov in versions:
            d = json.loads(prov.read_text())
            src = Path(d.get("source_path", "")).name
            if src and src not in by_source:
                by_source[src] = {b["id"]: b for b in d.get("blocks", [])}
            break
    return by_source


def main() -> None:
    ap = argparse.ArgumentParser(description="Score equation transcription vs engine against ground truth.")
    ap.add_argument("out_dir", help="Output root (the `out/` directory).")
    ap.add_argument("--labels", default="tests/equation_labels.json")
    args = ap.parse_args()

    labels = json.loads(Path(args.labels).read_text())
    blocks_by_source = _latest_provenances(Path(args.out_dir))

    eng_scores, tr_scores, helped, missing = [], [], 0, 0
    print(f"{'EQUATION':40} {'ENGINE':>7} {'TRANSCR':>7}  WINNER")
    print("-" * 70)
    for lab in labels:
        blocks = blocks_by_source.get(lab["source"])
        b = blocks.get(lab["block_id"]) if blocks else None
        if b is None:
            print(f"{lab['note'][:40]:40} {'—':>7} {'—':>7}  not in outputs")
            missing += 1
            continue
        eng = _ratio(b.get("text"), lab["latex"])
        tr_raw = (b.get("extra") or {}).get("transcribed")
        tr = _ratio(tr_raw, lab["latex"]) if tr_raw is not None else None
        eng_scores.append(eng)
        winner = "—"
        if tr is not None:
            tr_scores.append(tr)
            if tr > eng:
                helped += 1
                winner = "transcription"
            elif eng > tr:
                winner = "engine"
            else:
                winner = "tie"
        print(f"{lab['note'][:40]:40} {eng:7.3f} {('—' if tr is None else f'{tr:.3f}'):>7}  {winner}")

    n = len(eng_scores)
    print("-" * 70)
    if n:
        em = sum(eng_scores) / n
        print(f"engine mean similarity:        {em:.3f}  (n={n})")
        if tr_scores:
            tm = sum(tr_scores) / len(tr_scores)
            print(f"transcription mean similarity: {tm:.3f}  (n={len(tr_scores)})")
            print(f"transcription closer to truth: {helped}/{len(tr_scores)} equations")
    if missing:
        print(f"{missing} labelled equation(s) not found in outputs (reconvert?)")


if __name__ == "__main__":
    main()
