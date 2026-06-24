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


# Control chars a symbol font maps glyphs to (a list bullet -> U+0015, say); pdfium
# surfaces them literally in a text-region reading. Excludes the whitespace controls
# handled by the collapse that follows.
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")

# A TeX text font (Computer Modern / OT1 encoding) puts its f-ligatures and the
# discretionary hyphen in the C0 control range with no ToUnicode entry, so pdfium
# surfaces them as raw control bytes. Without this they'd be stripped to spaces by
# _CONTROL, manufacturing "rst"/"con guration"/"di erence" from first/configuration/
# difference. Expand them to letters BEFORE the strip — deterministic, and these bytes
# never carry real text. (OT1 ligature slots 0x0B-0x0F, here offset into 0x1B-0x1F.)
_GLYPH_LIGATURES = {
    0x02: "",  # discretionary hyphen at a line break -> join the word (practi-cal)
    0x1B: "ff", 0x1C: "fi", 0x1D: "fl", 0x1E: "ffi", 0x1F: "ffl",
}


def expand_ligature_glyphs(text: str) -> str:
    """Map a broken TeX font's control-byte f-ligatures back to letters."""
    return text.translate(_GLYPH_LIGATURES)


def clean_reading(text: str) -> str:
    """Flatten a pdfium text-region reading into single-line prose: drop the raw
    line breaks and stray control-mapped glyphs a multi-line block carries, collapse
    runs of whitespace. Used when refilling a block whose engine text was symbol-font
    garbage from the (clean) pdfium glyph layer."""
    return re.sub(r"\s+", " ", _CONTROL.sub(" ", expand_ligature_glyphs(text))).strip()


def clean_preformatted(text: str) -> str:
    """Like `clean_reading` but keep the line breaks — for console/ASCII blocks whose
    meaning is the layout. Strips control-mapped glyphs and trailing space per line."""
    lines = [_CONTROL.sub("", expand_ligature_glyphs(unglyph(ln))).rstrip()
             for ln in text.splitlines()]
    return "\n".join(lines).strip("\n")


# A ligature cluster left stranded between two word-fragments by a stray space.
_LIG_SPLIT = re.compile(r"(\w+) (ff|ffi|ffl|fi|fl) (\w+)")


def has_split_ligature(text: str) -> bool:
    return bool(_LIG_SPLIT.search(text))


def vocabulary(reference: str) -> set[str]:
    """Alphabetic words in a reference reading, ligatures folded (ﬀ -> ff) so a
    rejoined ASCII word matches."""
    return set(re.findall(r"[A-Za-z]+", unicodedata.normalize("NFKC", reference)))


def religature(text: str, words: set[str]) -> str:
    """Rejoin a ligature Docling split with a stray space ('di ff erent').

    Some publishers (ACS) encode ﬀ/ﬁ/ﬂ and Docling emits the decomposed letters
    flanked by spaces. `words` is the vocabulary of pdfium's reading of the
    document, which keeps the word intact. A join is applied only when it
    reconstructs a word the document actually contains, so a real boundary ('off
    the', 'cutoff value') is never fused — the validation, not a heuristic, is what
    makes this safe."""
    if not _LIG_SPLIT.search(text):
        return text

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


# A word broken by a stray space that no ligature explains — the other way the
# text layer fractures a word, e.g. a dropped diacritic ('Löwdin' -> 'Lo wdin').
# Right piece is lowercase so a sentence boundary or proper noun ('New York')
# never matches.
_WORD_SPLIT = re.compile(r"\b([A-Za-z]{2,}) +([a-z]{2,})\b")


def has_split_word(text: str) -> bool:
    return bool(_WORD_SPLIT.search(text))


def rejoin_split_word(text: str, words: set[str]) -> str:
    """Rejoin a word the text layer split with a stray space, validated the same
    way `religature` is: join only when the LEFT piece (the stem before the break)
    is not a word the document uses on its own, yet the joined form is. So
    'Lo wdin' -> 'Lowdin' ('Lo' is a stem fragment), but 'of the', 'data set' and
    'non linear' (left piece is a real word) are left untouched. The stem is the
    reliable signal: a consistent split puts the broken *tail* ('wdin') into the
    vocabulary, so guarding on the tail would silently stop firing."""
    def merge(m: re.Match) -> str:
        left, right = m.groups()
        joined = left + right
        return joined if left not in words and joined in words else m.group(0)

    return _WORD_SPLIT.sub(merge, text)
