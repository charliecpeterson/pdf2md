"""Describe an image crop with a vision model over an OpenAI-compatible API.

The pipeline crops every figure, image-fallback table, and image-backed equation to
a faithful PNG; those crops are opaque to a text consumer (a screen reader or an
LLM). This re-reads each crop with a vision model and stores a short description — or
a transcription, for tables/equations — as a labelled aid below the image. The crop
stays the authoritative source, so a wrong description is never the source of truth.

`Describer` is the seam — anything with `describe(image_path, kind, context)`.
`OpenAIVisionDescriber` speaks the OpenAI `/v1/chat/completions` vision protocol, so
the one client points at a local server (ollama, vLLM, LM Studio) or a remote
endpoint purely via `base_url`/`model`. It is lazy-imported and optional: with the
`openai` client absent, or the pass off, the pipeline simply skips it.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Protocol, runtime_checkable

from pdf2md.logging import get_logger

log = get_logger("describe")

# Per-crop-kind instruction. The crop is authoritative, so the prompt asks for a
# faithful reading and forbids the exact-value invention a vision model is prone to.
_PROMPTS = {
    "figure": (
        "Describe this figure from a scientific document: the kind of figure (plot, "
        "diagram, scheme, flowchart, ...), what it shows, and the key variables or "
        "relationships. Transcribe embedded text — axis labels, legends, and any program "
        "or command names — exactly as written, character for character. If there are "
        "several curves or data series, describe each distinct one, not just the most "
        "prominent. Be specific and factual but do not invent exact numerical values you "
        "cannot read clearly. Write a few sentences of plain prose — no bullet lists, no "
        "repetition, and no asides about typos or image quality."
    ),
    "table": (
        "Transcribe this table to GitHub-flavored Markdown. If its structure is too "
        "complex for a clean grid, instead describe its columns and what it contains. "
        "Do not invent values."
    ),
    "equation": (
        "Transcribe this equation to LaTeX. Output only the LaTeX, with no surrounding text."
    ),
}


def _prompt(kind: str, context: str = "") -> str:
    base = _PROMPTS.get(kind, _PROMPTS["figure"])
    return f"{base}\n\nContext: {context.strip()}" if context.strip() else base


# Transcription-heavy kinds: an OCR-tuned model reads their text/grids/math more
# faithfully than a general VLM, so they route to `ocr_model` when one is configured.
_OCR_KINDS = {"table", "equation"}


@runtime_checkable
class Describer(Protocol):
    def describe(self, image_path: Path, kind: str, context: str = "") -> str | None: ...
    def model_for(self, kind: str) -> str: ...


def _data_uri(image_path: Path) -> str:
    return f"data:image/png;base64,{base64.b64encode(image_path.read_bytes()).decode()}"


class OpenAIVisionDescriber:
    """Vision description over any OpenAI-compatible endpoint. The only version- or
    server-specific surface is `_run` (the chat-completions call); pointing at a
    different host or model is pure config (`base_url`, `model`, `api_key`)."""

    def __init__(self, base_url: str, model: str, api_key: str | None = None,
                 ocr_model: str | None = None) -> None:
        try:
            from openai import OpenAI
        except ImportError as exc:  # openai is an optional extra
            raise RuntimeError(
                'describe needs the openai client — install the extra into the env '
                'pdf2md runs from (e.g. `uv tool install --force -e ".[describe]"`)'
            ) from exc
        # Local servers (ollama, vLLM) ignore the key, but the client requires one set.
        self._client = OpenAI(base_url=base_url, api_key=api_key or "not-needed")
        self._model = model
        self._ocr_model = ocr_model or model  # OCR kinds fall back to the main model

    def model_for(self, kind: str) -> str:
        return self._ocr_model if kind in _OCR_KINDS else self._model

    def _run(self, model: str, prompt: str, data_uri: str) -> str:
        resp = self._client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": data_uri}},
            ]}],
        )
        return (resp.choices[0].message.content or "").strip()

    def describe(self, image_path: Path, kind: str, context: str = "") -> str | None:
        try:
            return self._run(self.model_for(kind), _prompt(kind, context), _data_uri(image_path)) or None
        except Exception as exc:  # noqa: BLE001 - best-effort; the crop is the source
            log.warning("description failed for %s: %s", image_path.name, exc)
            return None


def get_describer(config) -> Describer | None:
    """Build the configured describer, or None when the pass is off."""
    if not getattr(config, "describe_figures", False):
        return None
    return OpenAIVisionDescriber(
        base_url=getattr(config, "vlm_base_url", "http://localhost:11434/v1"),
        model=getattr(config, "vlm_model", ""),
        api_key=getattr(config, "vlm_api_key", None),
        ocr_model=getattr(config, "vlm_ocr_model", None),
    )
