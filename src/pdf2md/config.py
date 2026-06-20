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
    crop_dpi: int = 220
    crop_padding_pts: float = 6.0
    # Blocks below this confidence become a visible marker rather than silent text.
    coverage_confidence_floor: float = 0.0
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
