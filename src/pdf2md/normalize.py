"""Normalize text-extraction artifacts.

Some PDFs use symbol fonts whose glyphs Docling can't map to Unicode, so it emits
the raw Adobe glyph name instead (e.g. `/Delta1`, `/Pi1`). Common in chemistry and
physics papers (Greek term symbols, ΔfH). We map the Greek-letter glyph names back
to their Unicode characters. HTML tags (`</td>`, `</tr>`) are untouched since none
are Greek-letter names.

Docling also occasionally emits an orphaned combining mark (a lone U+0338 long
solidus overlay, say) where a base glyph was struck through or dropped; with
nothing to combine onto it renders as a stray slash. We strip those.
"""

from __future__ import annotations

import re
import unicodedata

_GREEK = {
    "Alpha": "Α", "Beta": "Β", "Gamma": "Γ", "Delta": "Δ", "Epsilon": "Ε",
    "Zeta": "Ζ", "Eta": "Η", "Theta": "Θ", "Iota": "Ι", "Kappa": "Κ",
    "Lambda": "Λ", "Mu": "Μ", "Nu": "Ν", "Xi": "Ξ", "Omicron": "Ο",
    "Pi": "Π", "Rho": "Ρ", "Sigma": "Σ", "Tau": "Τ", "Upsilon": "Υ",
    "Phi": "Φ", "Chi": "Χ", "Psi": "Ψ", "Omega": "Ω",
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε",
    "zeta": "ζ", "eta": "η", "theta": "θ", "iota": "ι", "kappa": "κ",
    "lambda": "λ", "mu": "μ", "nu": "ν", "xi": "ξ", "omicron": "ο",
    "pi": "π", "rho": "ρ", "sigma": "σ", "tau": "τ", "upsilon": "υ",
    "phi": "φ", "chi": "χ", "psi": "ψ", "omega": "ω",
}

# Longest names first so "Sigma" wins over a shorter prefix; the optional trailing
# digits are the font's subset variant marker (Delta1), and the `\b` alternative
# requires a word boundary so "/pictures" (pi + "ctures") never matches.
_GLYPH_RE = re.compile(
    r"/(" + "|".join(sorted(_GREEK, key=len, reverse=True)) + r")(?:\d+|\b)"
)


def unglyph(text: str) -> str:
    if "/" not in text:
        return text
    return _GLYPH_RE.sub(lambda m: _GREEK[m.group(1)], text)


def strip_orphan_combining(text: str) -> str:
    """Drop combining marks with no base char before them (string start or after
    whitespace). A legitimate base+mark pair (≠, an accented letter) is kept."""
    if not any(unicodedata.combining(c) for c in text):
        return text
    out: list[str] = []
    for c in text:
        if unicodedata.combining(c) and (not out or out[-1].isspace()):
            continue
        out.append(c)
    return "".join(out)


def normalize_text(text: str) -> str:
    return strip_orphan_combining(unglyph(text))


# A ligature cluster left stranded between two word-fragments by a stray space.
_LIG_SPLIT = re.compile(r"(\w+) (ff|ffi|ffl|fi|fl) (\w+)")


def has_split_ligature(text: str) -> bool:
    return bool(_LIG_SPLIT.search(text))


def religature(text: str, reference: str) -> str:
    """Rejoin a ligature Docling split with a stray space ('di ff erent').

    Some publishers (ACS) encode ﬀ/ﬁ/ﬂ and Docling emits the decomposed letters
    flanked by spaces. `reference` is pdfium's reading of the same region, which
    keeps the word intact. A join is applied only when it reconstructs a word the
    reference actually contains, so a real boundary ('off the', 'cutoff value') is
    never fused — the validation, not a heuristic, is what makes this safe."""
    if not _LIG_SPLIT.search(text):
        return text
    # NFKC folds any ligature codepoints the reference still carries (ﬀ -> ff) so a
    # rejoined ASCII word matches.
    words = set(re.findall(r"[A-Za-z]+", unicodedata.normalize("NFKC", reference)))

    def merge(m: re.Match) -> str:
        left, lig, right = m.groups()
        if left + lig + right in words:        # ligature sits mid-word
            return left + lig + right
        if left + lig in words:                # ligature ends the left word
            return f"{left}{lig} {right}"
        if lig + right in words:               # ligature starts the right word
            return f"{left} {lig}{right}"
        return m.group(0)                       # unconfirmed: leave it split

    prev = ""
    for _ in range(8):  # fixpoint: a fixed word can be the left of the next split
        prev, text = text, _LIG_SPLIT.sub(merge, text)
        if text == prev:
            break
    return text
