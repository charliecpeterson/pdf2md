"""Cross-check Docling's equation LaTeX against the PDF's embedded text layer.

Docling's formula model re-derives an equation's characters from its rendered
image and sometimes misreads (AQCC -> AQC/CC, pVTZ -> pVTEZ, a dropped equation
number). We score how much of the text layer's alphanumeric content survives in
the LaTeX; a low score means the extraction is untrustworthy. The caller then
crops the equation image as the faithful source and shows the text only as a
labelled hint, so this score gates *uncertainty*, not correctness.
"""

from __future__ import annotations

import re

RECOVER_BELOW = 0.85    # text-layer agreement below this means the extraction is suspect
SCRAMBLED_ABOVE = 0.12  # reading_disorder above this means the text layer is unfit to show
HINT_MIN_CONF = 0.5     # below this the text layer shares too little with the LaTeX to trust as a hint

_TOKEN = re.compile(r"[A-Za-z0-9]{2,}")
# `\text{cc-pVTZ}` / `\mathrm{...}` wrap real visible text — keep the content.
_TEXT_WRAPPER = re.compile(r"\\(?:text|mathrm|mathbf|mathit|operatorname)\s*\{([^{}]*)\}")
# Operators whose name *is* the visible text (\exp, \max), not a symbol — keep it.
_TEXT_OP = re.compile(r"\\(max|min|exp|log|ln|sin|cos|tan|det|lim|sup|inf|deg|arg|gcd)(?![a-zA-Z])")
# Docling spaces out every glyph (`M R - c c C A`) and wraps scripts in `_{}`/`^{}`;
# drop the remaining command words and that structure so words rejoin.
_LATEX_STRUCT = re.compile(r"\\[a-zA-Z]+|[_^{}\s]")


def is_clean(text: str) -> bool:
    """No unmapped symbol-font glyph (a C0/C1 control char or U+FFFD) that would
    make the text-layer reading an ugly, hole-ridden hint."""
    for c in text:
        if c in "\t\r\n":
            continue
        o = ord(c)
        if o < 0x20 or 0x7F <= o <= 0x9F or c == "�":
            return False
    return True


def _latex_tokens(latex: str) -> set[str]:
    s = _TEXT_WRAPPER.sub(r"\1", latex)
    s = _TEXT_OP.sub(r"\1", s)
    return set(_TOKEN.findall(_LATEX_STRUCT.sub("", s)))


def assess_equation(latex: str, text_layer: str) -> tuple[float, str | None] | None:
    """Return (confidence, reading), or None when the text layer is too sparse to
    judge. `reading` is the cleaned text-layer string when the extraction is
    suspect (confidence below RECOVER_BELOW), else None."""
    toks = _TOKEN.findall(text_layer)
    if len(toks) < 3:
        return None
    latex_toks = _latex_tokens(latex)
    conf = sum(1 for t in toks if t in latex_toks) / len(toks)
    if conf >= RECOVER_BELOW:
        return conf, None
    return conf, re.sub(r"\s+", " ", text_layer).strip()
