"""Cross-check Docling's equation LaTeX against the PDF's embedded text layer.

For born-digital PDFs the text layer holds the correct characters, while Docling's
formula model re-derives them from the rendered equation image and sometimes
misreads (AQCC -> AQC/CC, pVTZ -> pVTEZ, a dropped equation number). We score how
much of the text layer's alphanumeric content survives in the LaTeX; a low score
means the LaTeX is untrustworthy. When the text layer is itself clean (no
symbol-font holes) we hand its reading back as recovered content; otherwise the
caller keeps the LaTeX but marks it low-confidence.
"""

from __future__ import annotations

import re

RECOVER_BELOW = 0.85  # text-layer agreement below this means the LaTeX is suspect

_TOKEN = re.compile(r"[A-Za-z0-9]{2,}")
# `\text{cc-pVTZ}` / `\mathrm{...}` wrap real visible text — keep the content.
_TEXT_WRAPPER = re.compile(r"\\(?:text|mathrm|mathbf|mathit|operatorname)\s*\{([^{}]*)\}")
# Operators whose name *is* the visible text (\exp, \max), not a symbol — keep it.
_TEXT_OP = re.compile(r"\\(max|min|exp|log|ln|sin|cos|tan|det|lim|sup|inf|deg|arg|gcd)(?![a-zA-Z])")
# Docling spaces out every glyph (`M R - c c C A`) and wraps scripts in `_{}`/`^{}`;
# drop the remaining command words and that structure so words rejoin.
_LATEX_STRUCT = re.compile(r"\\[a-zA-Z]+|[_^{}\s]")

_GREEK = ("alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu nu xi "
          "omicron pi rho sigma tau upsilon phi chi psi omega").split()
# `\Delta`/`\sigma` style commands; Greek is the symbol-font glyph the text layer
# most often drops, so we compare Greek-to-Greek (a minus surviving in the text
# must not mask a dropped Δ, which a total-symbol count would let through).
_GREEK_CMD = re.compile(
    r"\\(?:" + "|".join([g.capitalize() for g in _GREEK] + _GREEK) + r")(?![a-zA-Z])"
)


def _is_clean(text: str) -> bool:
    """A symbol-font glyph the text layer couldn't map comes through as a C0/C1
    control char or U+FFFD; recovering such a string would silently drop it."""
    for c in text:
        if c in "\t\r\n":
            continue
        o = ord(c)
        if o < 0x20 or 0x7F <= o <= 0x9F or c == "�":
            return False
    return True


def _greek_preserved(latex: str, text_layer: str) -> bool:
    """Don't recover if the LaTeX has more Greek letters than the text layer:
    pdfium silently drops unmapped symbol-font glyphs, so recovering would lose
    them (a ΔE term collapsing to E). Text-layer Greek arrives in the U+0370–03FF
    block."""
    n_latex = len(_GREEK_CMD.findall(latex))
    n_text = sum(1 for c in text_layer if 0x370 <= ord(c) <= 0x3FF)
    return n_text >= n_latex


def _latex_tokens(latex: str) -> set[str]:
    s = _TEXT_WRAPPER.sub(r"\1", latex)
    s = _TEXT_OP.sub(r"\1", s)
    return set(_TOKEN.findall(_LATEX_STRUCT.sub("", s)))


def assess_equation(latex: str, text_layer: str) -> tuple[float, str | None, bool] | None:
    """Return (confidence, reading, recoverable), or None when the text layer is
    too sparse to judge. `reading` is the cleaned text-layer string (None when the
    LaTeX is trusted); `recoverable` says it can stand in for the LaTeX as content
    (clean, no dropped Greek) rather than just be shown as a cross-reference."""
    toks = _TOKEN.findall(text_layer)
    if len(toks) < 3:
        return None
    latex_toks = _latex_tokens(latex)
    conf = sum(1 for t in toks if t in latex_toks) / len(toks)
    if conf >= RECOVER_BELOW:
        return conf, None, False
    reading = re.sub(r"\s+", " ", text_layer).strip()
    recoverable = _is_clean(text_layer) and _greek_preserved(latex, text_layer)
    return conf, reading, recoverable
