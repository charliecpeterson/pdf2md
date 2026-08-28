"""Read a figure's printed text — the labels tiers: exact text-layer read for a
born-digital figure, model-free upright re-OCR of a scanned crop (best of four
rotations), and the opt-in vision-model read (--figure-labels) with consensus voting.
extract_caption splits a 'Fig N.' line out of the recovered text. Split from
digitize.py, which keeps the data side (curves/scatter/bars)."""

from __future__ import annotations

import re
from pathlib import Path

from pdf2md.schema import FigureLabels

_LABELS_CONFIDENCE = 0.6  # printed-text OCR is reliable-ish, but a model can still misread a digit
_NARRATION_MIN_WORDS = 8  # a printed label is a short fragment; 8+ prose words is a describing sentence


def _is_narration(line: str) -> bool:
    """A describing sentence rather than a printed label. Two shapes: a long prose run (the
    'The graph shows...' paragraph, or a truncated loop with no end punctuation), and a
    shorter sentence that ends in terminal punctuation — the model narrating its own work
    ('Let's re-read the image.', 'No text is present in the image.'). A printed label is a
    fragment (axis title, m/z value, legend entry) and rarely ends in a period; a title or
    unit like 'Scatterplot of dT' or 'A (s^{-1})' has no terminal punctuation, so it stays."""
    words = re.findall(r"[A-Za-z]{2,}", line)
    if len(words) >= _NARRATION_MIN_WORDS:
        return True
    return len(words) >= 3 and line.strip().rstrip("\"')").endswith((".", "!", "?"))


def _strip_narration(text: str) -> tuple[str, bool]:
    """Drop describing sentences, keeping the printed labels. Narration belongs to --describe,
    and a hallucinated value inside a sentence is the real hazard since it reads as a peak.
    Returns (kept text, stripped) so the drop is noted, not silent."""
    kept, stripped = [], False
    for ln in text.splitlines():
        if _is_narration(ln):
            stripped = True
            continue
        kept.append(ln)
    return "\n".join(kept).strip(), stripped


_TEXTLAYER_CONFIDENCE = 0.9  # the PDF's own text is exact; < 1 for region-edge/rotation/font risk


def figure_labels_textlayer(page_chars, bbox) -> FigureLabels | None:
    """Tier 1: read a born-digital figure's printed labels straight from the PDF text layer
    inside its bbox — exact characters, no OCR and no model, so no digit slips, loops, or
    hallucination. Line breaks are kept (labels are a list, not a paragraph). Returns None
    when there's no usable text — a scanned/raster figure, or one whose labels are baked into
    the image — so the caller falls back to the vision read. The same clean_reading pass the
    prose refill uses fixes the text layer's f-ligature control bytes; a region that's still
    symbol-font garbage (a broken ToUnicode CMap) fails the legibility gate and defers too."""
    from pdf2md.legibility import is_garbage
    from pdf2md.normalize import clean_reading, normalize_text

    if page_chars is None or bbox is None:
        return None
    lines = [clean_reading(normalize_text(ln)) for ln in page_chars.text_lines(bbox).splitlines()]
    text = "\n".join(ln for ln in lines if ln.strip())
    if len(re.sub(r"\s", "", text)) < 3 or is_garbage(text):
        return None
    note = "printed text from the PDF text layer (born-digital) — exact characters, not OCR"
    return FigureLabels(text, _TEXTLAYER_CONFIDENCE, note)


_FIG_OCR_CONFIDENCE = 0.55  # a derotated scan OCR: cleaner than the raw engine read, still a scan
_FIG_OCR_ANGLES = (0, 90, 180, 270)


def load_figure_ocr():
    """RapidOCR reader for the model-free scanned-figure tier (`figure_labels_ocr`). Built once
    and reused across figures; returns None if RapidOCR isn't importable so the tier degrades to
    skipped rather than raising. RapidOCR ships with the engine, so it's normally present."""
    try:
        from rapidocr import RapidOCR
    except ImportError:
        return None
    return RapidOCR(params={"Global.log_level": "WARNING"})


def _legible_token(token: str) -> bool:
    """A token that reads as real figure text: a clean number (a tick value) or a word with a
    letter run. Filters the punctuation salad a wrong orientation produces ('2'0', '`0-', '口')."""
    t = token.strip()
    return bool(re.fullmatch(r"-?\d+(?:\.\d+)?", t) or re.search(r"[A-Za-z]{2,}", t))


def best_orientation(reader, image):
    """OCR the image at each 90-degree rotation and return (tokens, angle) for the orientation
    that reads most legibly. Small text OCRs as garbage when it's sideways, so the legible-token
    score picks the upright read even for a page scanned rotated. Tokens are ordered top-to-
    bottom, left-to-right by their boxes so the caption and axis titles come out in reading order."""
    import numpy as np

    best = None
    for angle in _FIG_OCR_ANGLES:
        rot = image if angle == 0 else image.rotate(-angle, expand=True)
        res = reader(np.array(rot))
        if res is None or not res.txts:
            continue
        toks = list(zip(res.txts, res.scores, res.boxes))
        score = sum(s for t, s, _ in toks if _legible_token(t))
        if best is None or score > best[0]:
            best = (score, angle, toks)
    if best is None:
        return [], 0
    _, angle, toks = best
    heights = [max(p[1] for p in b) - min(p[1] for p in b) for _, _, b in toks]
    line = sorted(heights)[len(heights) // 2] * 1.2 if heights else 1.0
    toks.sort(key=lambda tsb: (round(min(p[1] for p in tsb[2]) / line), min(p[0] for p in tsb[2])))
    return toks, angle


def figure_labels_ocr(crop_path: Path, reader) -> FigureLabels | None:
    """Tier 1.5 (model-free) for a scanned figure: the engine OCRs a scan in the page's own
    orientation, so a sideways plot's small text (ticks, axis titles) comes back garbled. Re-OCR
    the rendered crop upright — trying all four 90-degree rotations and keeping the most legible —
    so those labels come back clean, no vision model and no endpoint. Returns None when nothing
    legible is found, so the crop stays the only record."""
    from PIL import Image

    from pdf2md.normalize import normalize_text

    toks, angle = best_orientation(reader, Image.open(crop_path).convert("RGB"))
    kept = [n for t, s, _ in toks if s >= 0.5 and _legible_token(t) and (n := normalize_text(t))]
    if len(kept) < 2:
        return None
    turned = f", {angle}° rotation detected and corrected" if angle else ""
    note = f"re-OCR'd from the crop{turned} — verify against the image; the crop is authoritative"
    return FigureLabels("\n".join(kept), _FIG_OCR_CONFIDENCE, note)


_CAPTION_START = re.compile(r"^\s*fig(?:ure)?\b\.?\s*\w", re.IGNORECASE)
_CAPTION_PREFIX = re.compile(r"^\s*fig(?:ure)?\b\.?\s*[\w.]*\s*", re.IGNORECASE)


def _is_prose(line: str) -> bool:
    """A caption continues on lines that read as prose, not on the tick values and short labels
    around them: at least three letters and not a bare number."""
    return len(re.findall(r"[A-Za-z]", line)) >= 3 and not re.fullmatch(r"-?\d+(?:\.\d+)?", line.strip())


def extract_caption(text: str) -> tuple[str | None, str]:
    """Find a figure caption in recovered label text and split it out. A caption is the line that
    opens with 'Fig'/'Figure' + a number ('FIG. 2a.', 'Figure 1:'). When that line is only the
    label (OCR breaks a caption across lines), the sentence continues on the following prose lines
    until one ends in terminal punctuation. Returns (caption, remaining label text) so the caption
    becomes the figure's own field instead of being duplicated in the labels; (None, text) when no
    caption line is present. Heuristic and model-free — the crop stays authoritative."""
    lines = text.splitlines()
    start = next((i for i, ln in enumerate(lines) if _CAPTION_START.match(ln)), None)
    if start is None:
        return None, text
    end = start + 1
    # A Fig line that already carries the sentence (>= 2 words past the label) is the whole
    # caption; a bare 'FIG. 2a.' pulls its sentence off the prose lines that follow.
    if len(re.findall(r"[A-Za-z]{3,}", _CAPTION_PREFIX.sub("", lines[start]))) < 2:
        while end < len(lines) and end - start < 6 and _is_prose(lines[end]):
            end += 1
            if lines[end - 1].rstrip().endswith((".", "!", "?")):
                break
    caption = re.sub(r"\s+", " ", " ".join(ln.strip() for ln in lines[start:end])).strip()
    remaining = "\n".join(lines[:start] + lines[end:]).strip()
    return caption, remaining


def figure_labels(crop_path: Path, describer, votes: int = 1, temperature: float = 0.4,
                  cache: dict | None = None, max_tokens: int | None = None,
                  endpoint: str = "") -> FigureLabels | None:
    """Transcribe the printed labels off a figure via the OCR model: axis titles, peak/data
    labels, legend, numeric markers. Unlike curve digitization this reads what's *written*
    on the plot, so it's reliable for the printed values a raster figure carries. The OCR
    model can loop, so the same guard the OCR path uses trims it; a model read, emitted at
    medium confidence with the image authoritative.

    With `votes > 1` the crop is read repeatedly and the reads are unioned (`merge_reads`):
    a value only one vote saw is kept, and a digit slip on a label two votes both read — the
    hazard on the printed peak values of a figure like an ESI-MS trace — is flagged. When a
    slip is flagged the block drops to low confidence so a human checks it against the image.

    `cache` is the doc-level describe cache, shared with the OCR pass. Its key includes
    the model, endpoint, prompt, crop, vote temperature, and token cap.
    `max_tokens` bounds generation per read (None → the vision default); set it low only for
    a model that loops, since a reasoning VLM needs room to think before it answers."""
    from pdf2md.consensus import merge_reads
    from pdf2md.describe import clean_vlm_text, vision_cache_key

    reads: list[str] = []
    truncated = narrated = False
    for i in range(max(1, votes)):
        # vote 0 rides the endpoint default so a single read stays byte-identical; extra
        # votes sample at `temperature` for the independent draws consensus needs.
        sampled_temperature = None if i == 0 else temperature
        key = ""
        if cache is not None:
            key = vision_cache_key(
                Path(crop_path), describer, "labels", temperature=sampled_temperature,
                max_tokens=max_tokens, endpoint=endpoint,
            ) + f":vote-{i}"
        raw = cache.get(key) if cache is not None else None
        if raw is None:
            # max_tokens (from config) can bound a looping model; left None a reasoning model
            # gets the full vision budget it needs to think before answering (see config).
            raw = describer.describe(Path(crop_path), "labels",
                                     temperature=sampled_temperature,
                                     max_tokens=max_tokens)
            if raw and cache is not None:
                cache[key] = raw
        if not raw:
            continue
        text, cut = clean_vlm_text(raw)
        # strip narration per-read, before consensus, so a hallucinated value inside a
        # describing sentence can't skew the numeric signatures the votes are compared on.
        text, narr = _strip_narration(text)
        if text:
            reads.append(text)
            truncated = truncated or cut
            narrated = narrated or narr
    if not reads:
        return None
    chosen, conflict = merge_reads(reads)
    note = "printed text read off the figure — OCR, verify against the image"
    if truncated:
        note += "; a repetition loop was trimmed"
    if narrated:
        note += "; describing sentences were removed (the crop is authoritative)"
    confidence = _LABELS_CONFIDENCE
    if conflict:
        note += "; reads disagreed on some values — treat the numbers as uncertain"
        confidence = round(confidence * 0.5, 2)
    return FigureLabels(chosen, confidence, note)
