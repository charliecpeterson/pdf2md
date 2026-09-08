"""Orchestration: PDF → engine → structure → render ∥ emit → coverage → disk.

`convert_file` is the list of stages in `stages.py`, in order, over one `_Run`;
`finalize.py` is the last of them. It is idempotent (content-hash identity,
readable versioned output, no-op unless `force`), and `convert_dir` isolates
failures per document so one bad PDF never aborts a batch.
"""

from __future__ import annotations

from pathlib import Path

from pdf2md.cache import is_document_dir, out_root
from pdf2md.config import Config
from pdf2md.describe import Describer, get_describer
from pdf2md.engines.base import Engine
from pdf2md.engines.select import select_engine
from pdf2md.finalize import _finalize_bundle
from pdf2md.logging import Progress, get_logger
from pdf2md.schema import ConvertResult
from pdf2md.stages import (
    _describe_document,
    _emit_bundle,
    _equation_evidence,
    _figure_evidence,
    _open_run,
    _render_assets,
    _verify_text,
)
from pdf2md.transcribe import Transcriber, get_transcriber

log = get_logger("pipeline")


def convert_file(
    pdf_path: Path,
    *,
    engine: Engine | None = None,
    transcriber: Transcriber | None = None,
    describer: Describer | None = None,
    config: Config | None = None,
    force: bool = False,
    output_root: Path | None = None,
) -> ConvertResult:
    run = _open_run(
        Path(pdf_path),
        engine=engine,
        transcriber=transcriber,
        describer=describer,
        config=config or Config(),
        force=force,
        output_root=output_root,
        progress=Progress(log),
    )
    if isinstance(run, ConvertResult):
        return run
    _verify_text(run)
    _describe_document(run)
    _render_assets(run)
    _equation_evidence(run)
    _figure_evidence(run)
    _emit_bundle(run)
    # Per-doc profile, surfaced for an AI (profile.json) and a human (README.md).
    # The numeric-conservation pass reads the embedded layer once more (read-only)
    # against the markdown just written; word recall was recorded during enrichment.
    return _finalize_bundle(run)


def convert_dir(
    root: Path,
    *,
    engine: Engine | None = None,
    config: Config | None = None,
    force: bool = False,
) -> list[ConvertResult]:
    root = Path(root).expanduser().resolve()
    output = out_root()
    nested_output = output != root and output.is_relative_to(root)
    pdfs = sorted(
        pdf for pdf in root.rglob("*.pdf")
        if not (nested_output and pdf.resolve().is_relative_to(output))
        and not (pdf.name == "source.pdf" and is_document_dir(pdf.parent))
    )
    if not pdfs:
        log.warning("no PDFs under %s", root)
        return []
    config = config or Config()
    try:
        # `auto` decides per document, so the batch cannot share one engine.
        engine = (
            None if config.engine == "auto" and engine is None
            else select_engine(engine, config)
        )
        transcriber = get_transcriber(config)  # loads the math-OCR model once, if enabled
        describer = get_describer(config)      # one vision client, reused across the batch
    except Exception as exc:  # noqa: BLE001 - report setup failures for every input
        log.error("batch setup failed under %s: %s", root, exc)
        return [
            ConvertResult(pdf.name, 0, root, [], failed=True, error=str(exc))
            for pdf in pdfs
        ]
    results: list[ConvertResult] = []
    progress = Progress(log)
    progress.count("converting PDFs", 0, len(pdfs), unit="PDFs", force=True)
    for completed, pdf in enumerate(pdfs, start=1):
        try:
            results.append(convert_file(
                pdf, engine=engine, transcriber=transcriber, describer=describer,
                config=config, force=force))
        except Exception as exc:  # noqa: BLE001 - poison-pill isolation
            log.error("unhandled failure on %s: %s", pdf.name, exc)
            # Don't re-hash here — if the file is unreadable, that throws too and aborts
            # the batch this handler exists to protect. The name is enough to report it.
            results.append(
                ConvertResult(pdf.name, 0, root, [], failed=True, error=str(exc))
            )
        progress.count(
            "converting PDFs", completed, len(pdfs), unit="PDFs", detail=pdf.name,
            force=True,
        )
    return results
