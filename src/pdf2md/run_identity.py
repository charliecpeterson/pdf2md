"""What makes this run this run.

`run_fingerprint` in `cache.py` decides whether a completed version can be
reused; these are the inputs it hashes — the source bytes, the effective
configuration, this implementation, the engine's identity, and the versions of
every dependency whose behaviour a bundle depends on. They live above `cache.py`
because naming the engine means importing engines, and `cache.py` is imported by
almost everything.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from functools import cache
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from pathlib import Path

from pdf2md import __version__
from pdf2md.cache import content_hash
from pdf2md.config import Config
from pdf2md.describe import VISION_CACHE_SCHEMA_VERSION, VISION_PROMPT_SHA256
from pdf2md.engines.base import Engine
from pdf2md.schema import FORMAT_VERSION

_RUN_INPUTS_SCHEMA_VERSION = 1


def _store_source(pdf_path: Path, document_dir: Path, expected_sha256: str) -> Path:
    document_dir.mkdir(parents=True, exist_ok=True)
    stored = document_dir / "source.pdf"
    if stored.is_file() and content_hash(stored) == expected_sha256:
        return stored

    pending = document_dir / "source.pdf.tmp"
    try:
        shutil.copyfile(pdf_path, pending)
        actual_sha256 = content_hash(pending)
        if actual_sha256 != expected_sha256:
            raise OSError(
                f"stored source hash {actual_sha256} != expected {expected_sha256}"
            )
        pending.replace(stored)
    finally:
        pending.unlink(missing_ok=True)
    return stored


def _installed_versions(config: Config, engine: Engine | None) -> dict[str, str]:
    distributions = ["pypdfium2", "rapidocr", "wordninja"]
    engine_name = getattr(engine, "name", config.engine)
    if engine_name == "docling":
        distributions.append("docling")
    if any((config.describe_figures, config.ocr_page_vlm,
            config.digitize_vlm, config.figure_labels)):
        distributions.append("openai")
    if config.transcribe_equations:
        distributions.extend(("surya-ocr", "transformers"))
    versions = {}
    for name in distributions:
        try:
            versions[name] = package_version(name)
        except PackageNotFoundError:
            versions[name] = "not-installed"
    if config.table_ocr_executable:
        executable = shutil.which(config.table_ocr_executable)
        if executable is None:
            versions["tesseract"] = "not-found"
        else:
            completed = subprocess.run(
                [executable, "--version"], capture_output=True, text=True, check=False,
                timeout=10,
            )
            versions["tesseract"] = (
                completed.stdout.splitlines()[0] if completed.returncode == 0 else "unavailable"
            )
    return versions


@cache
def _implementation_sha256() -> str:
    """Fingerprint the installed pdf2md Python implementation, not only its version."""
    package_root = Path(__file__).parent
    digest = hashlib.sha256()
    for path in sorted(package_root.rglob("*.py")):
        digest.update(path.relative_to(package_root).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _engine_identity(engine: Engine | None, config: Config) -> dict[str, str]:
    if engine is None:
        if config.engine == "mineru":
            from pdf2md.engines.mineru import MinerUEngine

            candidate = MinerUEngine(
                config.mineru_executable, deskew_scans=config.deskew_scans
            )
            return {
                "name": candidate.name,
                "implementation": "pdf2md.engines.mineru.MinerUEngine",
                "cache_identity": candidate.cache_identity(),
            }
        identity = {
            "name": "docling",
            "implementation": "pdf2md.engines.docling.DoclingEngine",
        }
    else:
        cls = type(engine)
        identity = {
            "name": getattr(engine, "name", cls.__name__),
            "implementation": f"{cls.__module__}.{cls.__qualname__}",
        }
        custom = getattr(engine, "cache_identity", None)
        if custom is not None:
            identity["cache_identity"] = str(custom() if callable(custom) else custom)
    if identity["name"] == "docling":
        # The resolved device, not the configured one: `auto` is not an answer, and the
        # layout detector makes marginally different calls on CUDA than on CPU or MPS,
        # so a bundle produced on one is not interchangeable with a bundle from another.
        from pdf2md.engines.docling import resolved_device

        identity["device"] = resolved_device(config.device)
    return identity


def _run_inputs(source_sha256: str, config: Config, engine: Engine | None) -> dict:
    vision_enabled = any((config.describe_figures, config.ocr_page_vlm,
                          config.digitize_vlm, config.figure_labels))
    return {
        "schema_version": _RUN_INPUTS_SCHEMA_VERSION,
        "source_sha256": source_sha256,
        "tool_version": __version__,
        "implementation_sha256": _implementation_sha256(),
        "format_version": FORMAT_VERSION,
        "engine": _engine_identity(engine, config),
        "dependency_versions": _installed_versions(config, engine),
        "effective_config": config.effective_dict(),
        "vision_cache_schema_version": VISION_CACHE_SCHEMA_VERSION if vision_enabled else None,
        "vision_prompt_sha256": VISION_PROMPT_SHA256 if vision_enabled else None,
    }
