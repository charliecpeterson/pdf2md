"""Runtime configuration: a frozen dataclass loaded from TOML, no Pydantic."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, replace
from pathlib import Path


@dataclass(frozen=True)
class Config:
    device: str = "auto"          # auto | mps | cuda | cpu
    # Formula→LaTeX enrichment is accurate but slow (minutes for equation-heavy
    # papers); turn off for speed or for large/scanned books.
    do_formula_enrichment: bool = True
    # Recover inline sub/superscripts from glyph geometry (born-digital pages).
    detect_scripts: bool = True
    # Re-transcribe flagged (image-backed) equation crops with a local math-OCR
    # model (Surya). Opt-in: needs `surya-ocr` installed and is slow per crop, but
    # turns an OCR/garbled equation's hint into a real transcription. The crop image
    # stays the authoritative source either way.
    transcribe_equations: bool = False
    # Describe image crops (figures, image-fallback tables, image-backed equations)
    # with a vision model over an OpenAI-compatible API. Opt-in: needs the `openai`
    # extra and a reachable `vlm_base_url`, and adds latency/cost per crop. The crop
    # stays the authoritative source; the description rides below it as a labelled aid.
    # `vlm_base_url` points at a local server (ollama/vLLM/LM Studio) or a remote
    # endpoint; `vlm_model` must be a model that endpoint serves.
    describe_figures: bool = False
    # Re-OCR scanned prose blocks with the vision model instead of the engine's
    # RapidOCR (substantially more accurate on degraded scans). Opt-in: same `describe`
    # extra + endpoint, slow (one call per block). Replaces the text; the page image
    # stays the source of truth. It is still OCR, so it can misread — verify.
    ocr_vlm: bool = False
    vlm_base_url: str = "http://localhost:11434/v1"
    vlm_model: str = "qwen3-vl:8b"
    # Optional OCR-tuned model for table crops; an OCR model reads dense grids more
    # faithfully than a general VLM. Equations/figures stay on vlm_model (VLMs give
    # cleaner LaTeX and plot descriptions). None → use vlm_model for every crop.
    vlm_ocr_model: str | None = None
    vlm_api_key: str | None = None
    crop_dpi: int = 220
    crop_padding_pts: float = 6.0
    # Directory of pre-downloaded Docling models (see `pdf2md models pull
    # --local-dir`). Set it to run fully offline and reproducibly — the local
    # snapshot is the pin. None = Docling's default Hugging Face cache.
    local_model_dir: str | None = None

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        cfg = cls()
        if path is None:
            return cfg
        data = tomllib.loads(path.read_text())
        known = {f for f in cls.__dataclass_fields__}
        return replace(cfg, **{k: v for k, v in data.items() if k in known})
