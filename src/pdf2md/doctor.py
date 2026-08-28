"""Environment diagnostics for conversion engines and optional features."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from importlib import metadata
import json
from pathlib import Path
import platform
import shutil
import sys
from urllib import error, request

from pdf2md.config import Config


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    detail: str
    fix: str | None = None


def _package(name: str, *, required: bool, fix: str) -> Check:
    try:
        installed = metadata.version(name)
    except metadata.PackageNotFoundError:
        return Check(
            name,
            "error" if required else "optional",
            "not installed",
            fix,
        )
    return Check(name, "ok", installed)


def _executable(name: str, command: str, *, required: bool, fix: str) -> Check:
    path = shutil.which(command)
    if path:
        return Check(name, "ok", path)
    return Check(
        name,
        "error" if required else "optional",
        f"not found on PATH: {command}",
        fix,
    )


def _configured_file(name: str, value: str | None, *, kind: str) -> Check:
    if value is None:
        return Check(name, "skipped", "not configured")
    path = Path(value).expanduser()
    if path.exists():
        return Check(name, "ok", str(path.resolve()))
    return Check(
        name,
        "error",
        f"configured {kind} does not exist: {path}",
        f"Correct or remove {name} in the configuration file.",
    )


def _vision_enabled(config: Config) -> bool:
    return any((
        config.describe_figures,
        config.ocr_page_vlm,
        config.digitize_vlm,
        config.figure_labels,
    ))


def _formula_headers(config: Config) -> Check:
    if config.engine != "docling" or not config.do_formula_enrichment:
        return Check("CUDA formula headers", "skipped", "formula enrichment inactive")
    from pdf2md.engines.docling import missing_cuda_python_headers

    missing = missing_cuda_python_headers(config.device)
    if missing is None:
        return Check("CUDA formula headers", "ok", "available or not required")
    return Check(
        "CUDA formula headers",
        "error",
        f"missing {missing}",
        "Install the matching python-devel package or convert with --no-formula.",
    )


def _probe_grobid(config: Config, timeout: float) -> Check:
    from pdf2md.grobid import is_alive

    alive = is_alive(config.grobid_url, timeout=timeout)
    return Check(
        "GROBID service",
        "ok" if alive else "warning",
        (f"alive at {config.grobid_url}" if alive
         else f"unreachable at {config.grobid_url}"),
        None if alive else "Start the GROBID service or correct grobid_url; "
                           "conversions fall back to heuristic metadata meanwhile.",
    )


def _probe_vlm(config: Config, timeout: float) -> Check:
    endpoint = config.vlm_base_url.rstrip("/") + "/models"
    headers = {}
    if config.vlm_api_key:
        headers["Authorization"] = f"Bearer {config.vlm_api_key}"
    try:
        with request.urlopen(
            request.Request(endpoint, headers=headers), timeout=timeout
        ) as response:
            payload = json.loads(response.read())
    except (OSError, ValueError, error.HTTPError) as exc:
        return Check(
            "vision endpoint",
            "error" if _vision_enabled(config) else "warning",
            f"unreachable at {endpoint}: {exc}",
            "Start the configured service or correct vlm_base_url and vlm_api_key.",
        )
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        return Check(
            "vision endpoint",
            "error" if _vision_enabled(config) else "warning",
            f"reachable at {endpoint}, but /models returned an invalid response",
            "Use an OpenAI-compatible /models endpoint.",
        )
    model_ids = {
        item["id"]
        for item in payload["data"]
        if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"]
    }
    configured = {config.vlm_model}
    if config.vlm_ocr_model:
        configured.add(config.vlm_ocr_model)
    missing = sorted(configured - model_ids)
    if missing:
        return Check(
            "vision endpoint",
            "error" if _vision_enabled(config) else "warning",
            f"reachable, but configured model(s) absent: {', '.join(missing)}",
            "Serve the configured model names or update vlm_model and vlm_ocr_model.",
        )
    return Check("vision endpoint", "ok", f"reachable at {endpoint}")


def inspect_environment(
    config: Config,
    *,
    probe_vlm: bool = False,
    endpoint_timeout: float = 3.0,
) -> dict:
    vision_enabled = _vision_enabled(config)
    checks = [
        Check(
            "python",
            "ok" if sys.version_info >= (3, 11) else "error",
            f"{platform.python_version()} at {sys.executable}",
            None if sys.version_info >= (3, 11) else "Install Python 3.11 or newer.",
        ),
        _package("docling", required=config.engine == "docling", fix="Run `uv sync`."),
        _package("pypdfium2", required=True, fix="Run `uv sync`."),
        _package("rapidocr", required=True, fix="Run `uv sync`."),
        _package("onnxruntime", required=True, fix="Run `uv sync`."),
        _formula_headers(config),
        _executable(
            "MinerU",
            config.mineru_executable,
            required=config.engine == "mineru",
            fix="Install MinerU separately and set mineru_executable to its CLI path.",
        ),
        _executable(
            "Tesseract",
            config.table_ocr_executable or "tesseract",
            required=config.table_ocr_executable is not None,
            fix="Install Tesseract or remove table_ocr_executable from the configuration.",
        ),
        _executable(
            "pdftocairo",
            "pdftocairo",
            required=config.figure_svg,
            fix="Install Poppler or disable figure_svg.",
        ),
        _package(
            "surya-ocr",
            required=config.transcribe_equations,
            fix="Run `uv sync --extra transcribe` or disable transcribe_equations.",
        ),
        _package(
            "openai",
            required=vision_enabled,
            fix="Run `uv sync --extra describe` or disable vision features.",
        ),
        _configured_file(
            "local_model_dir", config.local_model_dir, kind="model directory"
        ),
        _configured_file(
            "table_reference_path", config.table_reference_path, kind="reference CSV"
        ),
    ]
    if probe_vlm:
        checks.append(_probe_vlm(config, endpoint_timeout))
        if config.grobid_url:
            checks.append(_probe_grobid(config, endpoint_timeout))
    else:
        checks.append(Check(
            "vision endpoint",
            "skipped",
            "use --probe-vlm to test the configured endpoint",
        ))
    return {
        "schema_version": 1,
        "ready": not any(check.status == "error" for check in checks),
        "engine": config.engine,
        "active_optional_features": {
            "equation_transcription": config.transcribe_equations,
            "formula_enrichment": config.do_formula_enrichment,
            "vision": vision_enabled,
            "figure_svg": config.figure_svg,
            "table_reader": config.table_ocr_executable is not None,
            "external_table_reference": config.table_reference_path is not None,
        },
        "checks": [asdict(check) for check in checks],
    }
