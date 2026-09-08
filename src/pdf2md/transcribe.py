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

import hashlib
import json
import re
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Protocol, runtime_checkable

from pdf2md.cache import content_hash
from pdf2md.logging import get_logger
from pdf2md.run_identity import _implementation_sha256
from pdf2md.schema import BlockType
from pdf2md.vision_cache import CacheStats, load_vision_cache

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
    model once; safe to reuse across a batch.

    The single version-specific surface is `_run`: Surya 0.17 recognizes the crop
    as one region (the whole-image bbox, so no detection model is needed) and, with
    `math_mode=True`, returns the math as LaTeX wrapped in `<math>` tags."""

    def __init__(self, device: str | None = None) -> None:
        try:
            from surya.foundation import FoundationPredictor
            from surya.recognition import RecognitionPredictor
        except ImportError as exc:  # surya is an optional extra
            raise RuntimeError(
                'transcribe_equations needs surya-ocr — install the extra into the '
                'env pdf2md runs from (e.g. `uv tool install --force -e ".[transcribe]"`)'
            ) from exc
        kwargs = {"device": device} if device and device != "auto" else {}
        self._rec = RecognitionPredictor(FoundationPredictor(**kwargs))

    def _run(self, image) -> str:
        w, h = image.size
        result = self._rec(
            [image], task_names=["ocr_with_boxes"], bboxes=[[[0, 0, w, h]]], math_mode=True
        )[0]
        return "\n".join(line.text for line in result.text_lines if line.text)

    def transcribe(self, image_path: Path) -> str | None:
        try:
            from PIL import Image, ImageOps

            with Image.open(image_path) as img:
                rgb = img.convert("RGB")
                # Surya can drop a valid first or last token when ink sits near the
                # detector crop edge. Blank context changes no source pixels and fixed
                # all six source-labelled equation crops in the padding experiment.
                padded = ImageOps.expand(rgb, border=80, fill="white")
                return _latex_from_html(self._run(padded))
        except Exception as exc:  # noqa: BLE001 - best-effort; the crop is the source
            log.warning("transcription failed for %s: %s", image_path.name, exc)
            return None

    def cache_identity(self) -> str:
        try:
            package = version("surya-ocr")
        except PackageNotFoundError:
            package = "not-installed"
        return f"surya-ocr:{package}:math_mode=true:padding=80"


def get_transcriber(config) -> Transcriber | None:
    """Build the configured transcriber, or None when the pass is off."""
    if not getattr(config, "transcribe_equations", False):
        return None
    return SuryaTranscriber(device=getattr(config, "device", None))


def transcribe_equations(
    blocks,
    transcriber,
    vdir: Path,
    document_dir: Path | None = None,
    *,
    cache_stats: CacheStats | None = None,
) -> None:
    """Store a better hint on each image-backed equation from re-OCR'ing its crop."""
    cache = load_vision_cache(document_dir or vdir.parent, cache_stats)
    custom_identity = getattr(transcriber, "cache_identity", None)
    identity = (
        str(custom_identity() if callable(custom_identity) else custom_identity)
        if custom_identity is not None else
        f"{type(transcriber).__module__}.{type(transcriber).__qualname__}"
    )
    for b in blocks:
        crop = b.extra.get("crop_path")
        if b.type is BlockType.EQUATION and crop:
            image_path = vdir / crop
            if not image_path.is_file():
                latex = transcriber.transcribe(image_path)
                if latex:
                    b.extra["transcribed"] = latex
                    b.extra["transcribed_source"] = "math OCR"
                continue
            key_payload = {
                "schema": 1,
                "kind": "equation-transcription",
                "transcriber": identity,
                "image_sha256": content_hash(image_path),
                "implementation_sha256": _implementation_sha256(),
            }
            key = "transcription-v1:" + hashlib.sha256(
                json.dumps(key_payload, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            latex = cache.get(key)
            if latex is None:
                latex = transcriber.transcribe(image_path)
                if latex:
                    cache[key] = latex
            if latex:
                b.extra["transcribed"] = latex
                b.extra["transcribed_source"] = "math OCR"
