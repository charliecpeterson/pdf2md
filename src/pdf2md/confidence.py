"""Cross-check Docling's equation LaTeX against the PDF's embedded text layer.

Docling's formula model re-derives an equation's characters from its rendered
image and sometimes misreads (AQCC -> AQC/CC, pVTZ -> pVTEZ, a dropped equation
number). We score how much of the text layer's alphanumeric content survives in
the LaTeX; a low score means the extraction is untrustworthy. The caller then
crops the equation image as the faithful source and shows the text only as a
labelled hint, so this score gates *uncertainty*, not correctness.
"""

from __future__ import annotations

import io
import re
from collections import Counter
from pathlib import Path

import numpy as np
from PIL import Image

from pdf2md.logging import get_logger
from pdf2md.schema import BlockType, Digitization

log = get_logger("confidence")

RECOVER_BELOW = 0.9     # two-way text-layer agreement below this means the extraction is suspect
SCRAMBLED_ABOVE = 0.12  # reading_disorder above this means the text layer is unfit to show
HINT_MIN_CONF = 0.5     # below this the text layer shares too little with the LaTeX to trust as a hint
PLOT_DATA_MIN_CONFIDENCE = 0.5

# Render-back verification bands: measured ONLY for renderer-vs-renderer
# comparison (see the measured limit above). On real crops these tiers carry no
# correctness meaning.
RENDER_SIMILAR_ABOVE = 0.70
RENDER_DISSIMILAR_BELOW = 0.45

_TOKEN = re.compile(r"[A-Za-z0-9]{2,}")
# A printed equation number: page furniture the engine includes only
# sometimes. Shared by the cross-check and the render-back pass.
_TRAILING_EQ_NO = re.compile(r"\s*\((?:\d+|[ivx]+)\)\s*$")
# `\text{cc-pVTZ}` / `\mathrm{...}` wrap real visible text — keep the content.
_TEXT_WRAPPER = re.compile(r"\\(?:text|mathrm|mathbf|mathit|operatorname)\s*\{([^{}]*)\}")
# Operators whose name *is* the visible text (\exp, \max), not a symbol — keep it.
_TEXT_OP = re.compile(r"\\(max|min|exp|log|ln|sin|cos|tan|det|lim|sup|inf|deg|arg|gcd)(?![a-zA-Z])")
# An environment declaration and its column spec (`\begin{array}{rlr}`) is markup
# that was never visible text; leaving it in put `array` and `rlr` in the token
# set, where they can only count as content the text layer appears to be missing.
_ENVIRONMENT = re.compile(r"\\(?:begin|end)\s*\{[^{}]*\}(?:\s*\{[^{}]*\})?")
# The same declaration with a column spec that never closes. Docling can run
# away inside one: a GRASP2018 equation came back as `\begin{array} { c c c ...`
# for 4075 characters with no closing brace, and the spec then reached the token
# set as a single 1000-character `cccc...`, counted as content the text layer was
# missing. Nothing after an unterminated environment spec is visible text.
_UNTERMINATED_ENVIRONMENT = re.compile(r"\\(?:begin|end)\s*\{[^{}]*\}\s*\{[^{}]*$")
# A script group attaches to its base the way the text layer draws it, so `E_{0}`
# reads as `E0`. Only after an alphanumeric: a limit hanging off a command
# (`\sum_{bends}`) is set apart on the page, not glued to the operator.
_SCRIPT_GROUP = re.compile(r"(?<=[A-Za-z0-9])[_^]\{([^{}]*)\}")
_SCRIPT_CHAR = re.compile(r"(?<=[A-Za-z0-9])[_^]([A-Za-z0-9])")
# A command marks a token boundary, held as a sentinel because whitespace is
# about to be removed and so cannot mark it. Docling spaces out every glyph
# (`M R - c c C A`), and that strip is what rejoins the word.
_COMMAND = re.compile(r"\\[a-zA-Z]+")
_BOUNDARY = "\x00"
_STRUCT = re.compile(r"[\x00_^{}]")


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
    """Visible tokens of an equation's LaTeX, as the text layer would spell them.

    Order is load-bearing. Commands become boundaries *before* whitespace is
    stripped, because the strip is what rejoins Docling's per-glyph spacing and
    would otherwise weld separate symbols into one token. Deleting the structure
    outright, as this did, produced tokens no layer could ever match:
    `\\frac{1}{2} k^{BEND}` came out as `12kBEND` against a layer holding `1`,
    `2`, `k` and `BEND`, and `\\sum_{\\text{bends}} U^{BEND}` as `bendsUBEND`.
    Those welds were the largest single source of apparent disagreement between
    correct LaTeX and the layer.
    """
    s = _UNTERMINATED_ENVIRONMENT.sub(" ", latex)
    s = _ENVIRONMENT.sub(" ", s)
    s = _TEXT_WRAPPER.sub(r"\1", s)
    s = _TEXT_OP.sub(r"\1", s)
    s = _COMMAND.sub(_BOUNDARY, s)
    s = re.sub(r"\s+", "", s)
    while (joined := _SCRIPT_GROUP.sub(r"\1", s)) != s:  # nested scripts
        s = joined
    s = _SCRIPT_CHAR.sub(r"\1", s)
    return set(_TOKEN.findall(_STRUCT.sub(" ", s)))


def assess_equation(latex: str, text_layer: str) -> tuple[float, str | None] | None:
    """Return (confidence, reading), or None when the text layer is too sparse to
    judge. `reading` is the cleaned text-layer string when the extraction is
    suspect (confidence below RECOVER_BELOW), else None.

    Confidence is the *two-way* agreement between the LaTeX and the text layer of
    the equation's (single-column) bbox: recall catches missing content, precision
    catches content the LaTeX has but the bbox doesn't — adjacent-column prose
    Docling's formula model bled in. A one-directional score misses bleed entirely
    (the bled tokens just inflate the LaTeX).

    A printed equation number leaves both sides. It is page furniture, not part
    of the equation, and the engine includes it only sometimes -- so whichever
    way it falls it scores as a disagreement about content. 57 of the 108
    equation regions measured here end in one, and two of the nine equations
    whose disagreement survived every other correction differed by nothing
    else: `V_0 \\subset V_1 \\subset V_2 \\subset \\cdots` against a layer
    reading the same thing plus `(13)`."""
    flat = " ".join(text_layer.split())
    numbered = _TRAILING_EQ_NO.search(flat)
    toks = _TOKEN.findall(_TRAILING_EQ_NO.sub("", flat))
    if len(toks) < 3:
        return None
    text_set, latex_set = set(toks), _latex_tokens(latex)
    if numbered:
        latex_set.discard(numbered.group().strip(" ()"))
    recall = sum(1 for t in text_set if t in latex_set) / len(text_set)
    precision = sum(1 for t in latex_set if t in text_set) / len(latex_set) if latex_set else 1.0
    conf = min(recall, precision)
    if conf >= RECOVER_BELOW:
        return conf, None
    return conf, re.sub(r"\s+", " ", text_layer).strip()


def plot_data_accepted(digitization: Digitization | None) -> bool:
    """Whether recovered chart numbers are safe to expose as structured data."""
    return bool(
        digitization is not None
        and digitization.series
        and digitization.confidence >= PLOT_DATA_MIN_CONFIDENCE
    )


# ---------------------------------------------------------------------------
# Render-back verification: render the equation's LaTeX and compare ink layout
# against its source crop.
#
# MEASURED LIMIT (scripts/eval_render_bands.py ->
# docs/render-band-calibration.json): on real display-equation crops even
# GROUND-TRUTH LaTeX scores 0.113-0.373 — below any useful threshold and fully
# overlapping deliberately-corrupted variants (0.114-0.397). Cross-font,
# cross-layout differences (equation numbers, stacked displays, journal fonts)
# dominate the dense-mask IoU. Discrimination is demonstrated ONLY when both
# sides come from this renderer (self 1.0 vs corrupted topology 0.11-0.35).
#
# Verdicts therefore rank LAYOUT AGREEMENT, nothing more: "dissimilar" on a
# real crop is NOT evidence of a wrong equation, and no band here may gate
# anything. The pass stays opt-in and scoped to scans, where it adds a
# review-ranking hint beside the authoritative crop and nothing else.

_TAG = re.compile(r"\\tag\{[^{}]*\}")
_LABEL = re.compile(r"\\label\{[^{}]*\}")
_ALIGN_AMP = re.compile(r"\s*&\s*")
# An aligned pair's line break: draw the segments as one row rather than refuse
# (the stacked-vs-flat distortion lands in the topology score, honestly).
_LINEBREAK = re.compile(r"\\\\(?:\*|\[\d+(?:pt|em)?\])?")
_UNICODE_MATH = str.maketrans({"−": "-", "–": "-", "×": r"\times ", "·": r"\cdot ",
                               "≤": r"\leq ", "≥": r"\geq ", "≈": r"\approx ",
                               "∈": r"\in ", "∞": r"\infty "})
_PROFILE_BINS = 64


def _render_ready_latex(latex: str) -> str | None:
    """LaTeX matplotlib's mathtext can plausibly draw, or None for constructs it
    cannot (environments, macros). \\text becomes \\mathrm; tags, labels, alignment
    amps, and a trailing equation number are presentation, not content; an
    aligned pair's line break joins its segments into one row."""
    s = _TAG.sub("", latex)
    s = _LABEL.sub("", s)
    s = _TRAILING_EQ_NO.sub("", s).strip()
    s = s.replace(r"\text", r"\mathrm").translate(_UNICODE_MATH)
    s = _ALIGN_AMP.sub(" ", _LINEBREAK.sub(r" \; ", s))
    if any(tok in s for tok in (r"\begin{", r"\end{", r"\substack", r"\cases")):
        return None
    return f"${s}$" or None


def _ink_profile(img: Image.Image) -> np.ndarray:
    """Where the ink is: row and column mass distributions over fixed bins, each
    L1-normalized. Layout topology — a fraction bar spikes the row profile,
    subscripts shift mass down — survives resampling; absolute size does not."""
    arr = np.asarray(img.convert("L"))
    mask = (arr < 140).astype(float)
    rows, cols = np.where(mask.any(axis=1))[0], np.where(mask.any(axis=0))[0]
    if not len(rows):
        return np.zeros(_PROFILE_BINS * 2)
    mask = mask[rows[0]:rows[-1] + 1, cols[0]:cols[-1] + 1]
    def bins(signal):
        x_old = np.linspace(0.0, 1.0, len(signal))
        return np.interp(np.linspace(0.0, 1.0, _PROFILE_BINS), x_old, signal)
    row_p, col_p = bins(mask.sum(axis=1)), bins(mask.sum(axis=0))
    vec = np.concatenate([row_p, col_p])
    total = vec.sum()
    return vec / total if total else vec


def _ink_mask(img: Image.Image) -> np.ndarray | None:
    """Binarized ink, trimmed to its bounding box; None when there is no ink."""
    mask = np.asarray(img.convert("L")) < 140
    rows, cols = np.where(mask.any(axis=1))[0], np.where(mask.any(axis=0))[0]
    if not len(rows) or not len(cols):
        return None
    return mask[rows[0]:rows[-1] + 1, cols[0]:cols[-1] + 1]


def _topology_grid(mask: np.ndarray) -> np.ndarray:
    """Both masks stretched onto one fixed grid: absolute size and position drop
    out, layout topology stays. A fraction bar survives as its own dense band;
    a single-line sum cannot produce one."""
    image = Image.fromarray((mask * 255).astype(np.uint8))
    grid = image.resize((_GRID_COLS, _GRID_ROWS), Image.BOX)
    return np.asarray(grid).astype(float) / 255.0


_GRID_ROWS, _GRID_COLS = 48, 160


def render_latex(latex: str) -> Image.Image | None:
    """Draw sanitized LaTeX with mathtext; None when mathtext can't parse it."""
    math = _render_ready_latex(latex)
    if not math:
        return None
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        fig = plt.figure(figsize=(6, 1.5), dpi=160)
        fig.text(0.02, 0.5, math, fontsize=16, va="center")
        buf = io.BytesIO()
        fig.savefig(buf, format="png", bbox_inches="tight", pad_inches=0.05)
        plt.close(fig)
        return Image.open(buf).convert("L")
    except Exception as exc:  # noqa: BLE001 - unrenderable is a verdict, not a crash
        log.debug("mathtext render failed for %.40s: %s", latex, exc)
        return None


def compare_render(crop_path, latex: str) -> dict:
    """Score how well `latex` re-renders into the shape of its own source crop:
    soft-IoU of the stretched ink masks (layout topology) blended with aspect
    agreement (proportions)."""
    rendered = render_latex(latex)
    if rendered is None:
        return {"verdict": "unrenderable"}
    mask_r, mask_c = _ink_mask(rendered), _ink_mask(Image.open(crop_path))
    if mask_r is None or mask_c is None:
        return {"verdict": "no_ink"}
    grid_r, grid_c = _topology_grid(mask_r), _topology_grid(mask_c)
    intersection = float(np.minimum(grid_r, grid_c).sum())
    union = float(np.maximum(grid_r, grid_c).sum())
    topology = intersection / union if union else 0.0
    ar_r = (mask_r.shape[1] / mask_r.shape[0]) if mask_r.size else 1.0
    ar_c = (mask_c.shape[1] / mask_c.shape[0]) if mask_c.size else 1.0
    aspect = min(ar_r, ar_c) / max(ar_r, ar_c) if max(ar_r, ar_c) else 1.0
    score = round(0.7 * topology + 0.3 * aspect, 3)
    verdict = (
        "similar" if score >= RENDER_SIMILAR_ABOVE
        else "dissimilar" if score < RENDER_DISSIMILAR_BELOW
        else "unclear"
    )
    return {
        "verdict": verdict,
        "score": score,
        "topology_iou": round(topology, 3),
        "aspect_agreement": round(aspect, 3),
    }


def check_equation_render_support(blocks) -> dict[str, int]:
    """Count how many equations' LaTeX parses under the bundled math renderer
    (mathtext, after the same sanitizing render-back uses). This is RENDERER
    COVERAGE, not correctness: a construct mathtext lacks (aligned environments,
    custom macros) counts as unsupported even when the LaTeX is perfectly valid
    and renders fine in KaTeX/MathJax. The count tells a downstream reader how
    much of the document's math survives round-trip through limited renderers.
    Returns {"supported": n, "unsupported": n}; empty when matplotlib is absent."""
    try:
        from matplotlib.mathtext import MathTextParser
    except ImportError:
        return {}
    parser = MathTextParser("agg")
    counts: Counter[str] = Counter()
    for b in blocks:
        if b.type is not BlockType.EQUATION or not b.text.strip():
            continue
        math = _render_ready_latex(b.text)
        if not math:
            counts["unsupported"] += 1
            b.extra["render_support"] = "unsupported"
            continue
        try:
            parser.parse(math, dpi=72, prop=None)
        except Exception:  # noqa: BLE001 - any parse failure is the verdict
            counts["unsupported"] += 1
            b.extra["render_support"] = "unsupported"
        else:
            counts["supported"] += 1
            b.extra["render_support"] = "supported"
    return dict(counts)


def check_equation_renders(blocks, version_dir=None) -> int:
    """Attach `extra["render_check"]` to image-backed equations the text layer
    could not judge — scanned pages (`ocr` flag) or equations with no confidence
    from `assess_equation`. Where the text layer DID answer, that signal stands
    and render-back would only add layout noise (equation numbers, stacked
    displays). Returns the number checked. Crop paths are version-dir relative;
    missing matplotlib disables the pass loudly once rather than per block."""
    checked = 0
    warned = False
    for b in blocks:
        if b.type is not BlockType.EQUATION:
            continue
        crop = b.extra.get("crop_path")
        if not crop or not b.text.strip():
            continue
        judged_by_text_layer = b.confidence is not None and not b.extra.get("ocr")
        if judged_by_text_layer:
            continue
        path = Path(crop)
        if not path.is_absolute() and version_dir is not None:
            path = Path(version_dir) / crop
        try:
            b.extra["render_check"] = compare_render(path, b.text)
            checked += 1
        except ImportError as exc:
            if not warned:
                warned = True
                log.warning("render-check needs matplotlib (uv sync --extra eqrender): "
                            "%s — pass skipped", exc)
            break
    return checked
