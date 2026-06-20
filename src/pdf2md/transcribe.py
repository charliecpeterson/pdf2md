"""Multi-pass equation transcription: re-read a flagged equation crop with a local
math-OCR model to upgrade its text hint.

The pipeline already crops every uncertain or OCR-sourced equation to a faithful
image; this re-transcribes that image and stores the result as a better hint than
the engine's (often wrong) LaTeX. The image stays the authoritative source, so a
bad transcription is never worse than what we had.

`Transcriber` is the seam — anything with `transcribe(image_path) -> str | None`.
`SuryaTranscriber` is the one model adapter; all of its version-specific surface is
in `_run`, so a Surya API change is a one-method fix. It is lazy-imported and
optional: with `surya-ocr` absent the pipeline simply skips the pass.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Protocol, runtime_checkable

from pdf2md.logging import get_logger

log = get_logger("transcribe")

_MATH = re.compile(r"<math[^>]*>(.*?)</math>", re.DOTALL)
_TAG = re.compile(r"<[^>]+>")


@runtime_checkable
class Transcriber(Protocol):
    def transcribe(self, image_path: Path) -> str | None: ...


def _latex_from_html(html: str) -> str | None:
    """Surya returns recognized math as `<math>…</math>` (KaTeX LaTeX) inside the
    page HTML. Pull the math out; fall back to the stripped text if there's none."""
    math = [m.strip() for m in _MATH.findall(html) if m.strip()]
    if math:
        return " ".join(math)
    text = _TAG.sub("", html).strip()
    return text or None


class SuryaTranscriber:
    """Local math OCR via Surya (the maintained successor to texify). Loads the
    model once; safe to reuse across a batch."""

    def __init__(self) -> None:
        try:
            from surya.inference import SuryaInferenceManager
            from surya.recognition import RecognitionPredictor
        except ImportError as exc:  # surya is an optional extra
            raise RuntimeError(
                "transcribe_equations needs surya-ocr (`uv pip install surya-ocr`)"
            ) from exc
        self._predictor = RecognitionPredictor(SuryaInferenceManager())

    def _run(self, image) -> str:
        # The one Surya-version-specific call. Surya 2 does math inline as part of
        # full-page recognition, returning HTML with `<math>` tags.
        prediction = self._predictor([image])[0]
        return getattr(prediction, "html", "") or ""

    def transcribe(self, image_path: Path) -> str | None:
        try:
            from PIL import Image

            with Image.open(image_path) as img:
                return _latex_from_html(self._run(img.convert("RGB")))
        except Exception as exc:  # noqa: BLE001 - best-effort; the crop is the source
            log.warning("transcription failed for %s: %s", image_path.name, exc)
            return None


def get_transcriber(config) -> Transcriber | None:
    """Build the configured transcriber, or None when the pass is off."""
    if not getattr(config, "transcribe_equations", False):
        return None
    return SuryaTranscriber()
