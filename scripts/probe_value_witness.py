"""Compare the engine's table values against the glyph-truth reading of the same region.

PROBE. Its result is negative and is recorded in docs/accuracy-improvement-notes.md:
the two grids are not independent witnesses about *characters*. Both read the same
glyph layer, so where that layer is corrupt they are corrupt identically -- on the
Kyte & Doolittle table whose middle-dot decimals are lost, both read `45`. The
glyph grid is an independent witness about *arrangement*, which is what it was
built for, and this check cannot borrow that independence for a different question.

Kept because the reasoning is easy to have again, and because the guards it needed
are the interesting part.

pdf2md already writes both -- `tables_N.md` from the engine's cells and
`tables_N.glyph.md` rebuilt from the page's own glyphs -- and nothing compares
them numerically. Where two independent readings of one region disagree about a
number, that is a candidate value error with both witnesses already on disk.

Reports only value disagreements. A cell present in one reading and absent from
the other is a structural difference the row/grid audit already owns; this asks a
narrower question, which is whether the numbers that appear in both agree.
"""
import argparse
import re
from collections import Counter
from pathlib import Path

NUMBER = re.compile(r"[−‑–-]?\d+(?:[.,]\d+)?")
# `4·5` read as `45` is the case that motivated this: same digits, different value.
DIGITS = re.compile(r"\d")


LONE = re.compile(r"\A[−‑–-]?\s?\d+(?:[.,]\d+)?\Z")


def values(markdown: str) -> list[str]:
    """Values whose cell holds nothing but the value.

    A value lifted out of `SLWe+ .1,6-T9-rIphl8,7,` -- a glyph reading whose columns
    merged -- is not evidence about anything, and comparing against it manufactures
    disagreements. Requiring a lone number on both sides is what makes the two
    readings comparable rather than merely both present.
    """
    out = []
    for line in markdown.split("\n"):
        if not line.strip().startswith("|") or set(line.strip()) <= set("|- :"):
            continue
        for cell in line.strip().strip("|").split("|"):
            text = " ".join(cell.split())
            if LONE.match(text):
                out.append(text.replace(" ", "").replace("−", "-")
                           .replace("–", "-").replace("‑", "-"))
    return out


def digits_only(value: str) -> str:
    return "".join(DIGITS.findall(value))


def grid_shape(markdown: str) -> tuple[int, int]:
    widths = [
        len(line.strip().strip("|").split("|"))
        for line in markdown.split("\n")
        if line.strip().startswith("|") and not set(line.strip()) <= set("|- :")
    ]
    if not widths:
        return 0, 0
    modal = Counter(widths).most_common(1)[0][0]
    return modal, sum(1 for w in widths if w == modal) / len(widths)


def usable_witness(engine: str, glyph: str) -> bool:
    """Refuse where the glyph reading is itself malformed.

    A glyph grid whose columns merged reads `Ile 169-Trp 187` as
    `SLWe+ .1,6-T9-rIphl8,7,`, and a value pulled out of that is not evidence of
    anything. Requiring the two grids to agree on their column count is a cheap
    proxy for the witness having resolved the same columns the engine did.
    """
    ew, eshare = grid_shape(engine)
    gw, gshare = grid_shape(glyph)
    return ew > 1 and ew == gw and eshare >= 0.8 and gshare >= 0.8


def compare(engine: str, glyph: str):
    a, b = values(engine), values(glyph)
    ca, cb = Counter(a), Counter(b)
    only_engine = ca - cb
    only_glyph = cb - ca
    # A value the two readings spell differently while agreeing on the digits is
    # a separator or sign difference, which is the class that changes magnitude
    # without changing any character a word-level check can see.
    by_digits = Counter(digits_only(v) for v in only_glyph.elements())
    same_digits = []
    for value in only_engine.elements():
        key = digits_only(value)
        if key and by_digits.get(key):
            by_digits[key] -= 1
            partner = next(v for v in only_glyph.elements() if digits_only(v) == key)
            same_digits.append((value, partner))
    return only_engine, only_glyph, same_digits


ap = argparse.ArgumentParser()
ap.add_argument("root", type=Path)
args = ap.parse_args()

totals = Counter()
report = []
for doc in sorted(d for d in args.root.iterdir() if d.is_dir()):
    versions = [v for v in doc.glob("v*") if (v / "data" / "tables").is_dir()]
    if not versions:
        continue
    tables_dir = max(versions, key=lambda v: int(v.name[1:])) / "data" / "tables"
    for engine_path in sorted(tables_dir.glob("*.md")):
        if engine_path.name.endswith(".glyph.md"):
            continue
        glyph_path = engine_path.with_suffix("").with_suffix(".glyph.md")
        if not glyph_path.is_file():
            continue
        engine_md = engine_path.read_text(errors="replace")
        glyph_md = glyph_path.read_text(errors="replace")
        if not usable_witness(engine_md, glyph_md):
            totals["skipped: glyph reading not a usable witness"] += 1
            continue
        totals["tables compared"] += 1
        only_engine, only_glyph, same_digits = compare(
            engine_md, glyph_md)
        if same_digits:
            totals["tables with a same-digits disagreement"] += 1
            report.append((doc.name, engine_path.name, same_digits[:4]))
        if only_engine or only_glyph:
            totals["tables differing at all"] += 1

for k, n in totals.most_common():
    print(f"  {k:44s}: {n}")
print(f"\nsame-digit disagreements (engine value | glyph value):")
for doc, name, pairs in report[:20]:
    shown = ", ".join(f"{a!r}|{b!r}" for a, b in pairs)
    print(f"  {doc[:38]:38s} {name:16s} {shown}")
