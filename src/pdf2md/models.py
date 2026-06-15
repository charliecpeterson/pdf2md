"""Model management. Docling fetches its weights from Hugging Face on first use.

`pull()` either warms the default cache or, with a directory, downloads a local
snapshot you can point `local_model_dir` / `--local-dir` at to run fully offline
and reproducibly (the snapshot is the pin — Docling exposes no per-model revision
pinning, so an immutable local copy is the mechanism). Useful for air-gapped HPC.
"""

from __future__ import annotations

from pathlib import Path

from pdf2md.logging import get_logger

log = get_logger("models")


def pull(local_dir: str | Path | None = None) -> None:
    if local_dir is None:
        from pdf2md.engines.docling import DoclingEngine

        log.info("warming Docling models in the default cache (first run downloads)...")
        DoclingEngine()
        log.info("models ready")
        return

    from docling.utils.model_downloader import download_models

    out = Path(local_dir).expanduser()
    log.info("downloading Docling model snapshot to %s ...", out)
    download_models(output_dir=out, progress=True)
    log.info("done. set local_model_dir = %s (or --local-dir) to run offline", out)
