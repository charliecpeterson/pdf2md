"""Making an engine's LaTeX safe to put in a Markdown file.

None of this judges whether the LaTeX is *right* — `confidence.assess_equation`
does that against the page's text layer, and `--render-check` against the crop.
This is the narrower job of emitting it without breaking the document around it:
closing what the engine left open, dropping the walls of spacing commands a
trailing-whitespace run becomes, and carrying the printed equation number back in
as a `\tag`.

The number is the page's own, recovered in `enrich`. It rides here rather than in
the LaTeX the engine produced, which is why `conservation` counts it on the source
side: it is printed on the page, not invented by the emitter.
"""

from __future__ import annotations

import re

# Docling encodes trailing PDF whitespace and lost alignment columns as long runs
# of LaTeX spacing commands (\quad, control-spaces) or empty `& \quad` cells, which
# render as a wall of gaps. The (?<!\\) guard keeps `\\` line breaks intact.
_MATH_SPACE = r"(?:(?<!\\)\\(?:qquad|quad|[,;:! ])|~)"
_MATH_RUN = re.compile(rf"{_MATH_SPACE}(?:\s*{_MATH_SPACE})+")
_MATH_TAIL = re.compile(rf"(?:{_MATH_SPACE}|\s|\\|&)+$")
_MATH_EMPTY_CELLS = re.compile(rf"(?:&\s*{_MATH_SPACE}\s*){{2,}}")


def _tidy_math(body: str) -> str:
    body = _MATH_EMPTY_CELLS.sub(" & ", body)
    body = _MATH_TAIL.sub("", body)
    body = _MATH_RUN.sub(r" \\quad ", body).strip()
    return _balance_braces(body)


def _equation_latex(text: str, number: str | None = None) -> str:
    body = _balance_delims(_tidy_math(text.strip("$").strip()))
    # Alignment markers (&, \\) are only valid inside an environment; bare $$ makes
    # KaTeX/MathJax throw. Wrap multi-line equations in `aligned`.
    if "&" in body or r"\\" in body:
        body = f"\\begin{{aligned}}\n{body}\n\\end{{aligned}}"
    # The printed number, recovered in enrich from the layer reading of the
    # equation's own region. \tag is how LaTeX carries it, so it stays attached to
    # the equation rather than floating beside it, and the prose's "substituting
    # into (2)" resolves again.
    if number and "\\tag" not in body:
        body = f"{body} \\tag{{{number}}}"
    return f"$$\n{body}\n$$"


def _balance_delims(body: str) -> str:
    """KaTeX throws on a `\\left` without a matching `\\right` (Docling sometimes
    emits `\\left⟨ … \\right| … \\right⟩`, two `\\right` for one `\\left`). When the
    pair is unbalanced, drop the auto-sizing commands; the bare delimiters still
    render, just without stretching."""
    if len(re.findall(r"\\left(?![a-zA-Z])", body)) != len(re.findall(r"\\right(?![a-zA-Z])", body)):
        body = re.sub(r"\\left(?![a-zA-Z])|\\right(?![a-zA-Z])", "", body)
    return body


def _balance_braces(body: str) -> str:
    """KaTeX dumps the raw source for an unbalanced `{`/`}`, which happens when
    Docling garbles an equation (a misread `}` as `)`, say). Pad the missing
    side so the expression still renders instead of showing as literal TeX."""
    opens = len(re.findall(r"(?<!\\)\{", body))
    closes = len(re.findall(r"(?<!\\)\}", body))
    if opens > closes:
        return body + "}" * (opens - closes)
    if closes > opens:
        return "{" * (closes - opens) + body
    return body
