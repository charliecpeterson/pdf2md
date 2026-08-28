"""Run document parsers natively and preserve their outputs for comparison.

    uv run python scripts/engine_bakeoff.py --list
    uv run python scripts/engine_bakeoff.py --engine docling-standard \
        --document vector-plot

The runner owns process isolation and provenance only. It deliberately leaves each
engine's files unchanged so later scoring cannot be distorted by an adapter.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_ROOT = Path(__file__).parent.parent
_DEFAULT_MANIFEST = _ROOT / "tests" / "bakeoff_manifest.json"
_DEFAULT_OUTPUT = _ROOT / "out" / "bakeoff"
_LAYOUT_CANDIDATES = _ROOT / "tests" / "docling_layout_candidates.json"
_LAYOUT_RUNNER = _ROOT / "scripts" / "docling_layout_candidate.py"
_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")

DOCLING_LAYOUT_ENGINE_IDS = (
    "docling-heron",
    "docling-heron-101",
    "docling-egret-medium",
    "docling-egret-large",
    "docling-egret-xlarge",
)
ENGINE_IDS = (
    "pdf2md-current",
    "pdf2md-page-vlm",
    "docling-standard",
    "docling-vlm",
    *DOCLING_LAYOUT_ENGINE_IDS,
    "paddleocr-vl",
    "mineru",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_manifest(path: Path) -> tuple[Path, list[dict[str, Any]]]:
    manifest = json.loads(path.read_text())
    if manifest.get("schema_version") != 1:
        raise ValueError(f"{path}: unsupported schema_version")
    source_root = (path.parent / manifest.get("source_root", ".")).resolve()
    documents = manifest.get("documents")
    if not isinstance(documents, list) or not documents:
        raise ValueError(f"{path}: documents must be a non-empty list")
    ids = [document.get("id") for document in documents]
    if any(not isinstance(doc_id, str) or not doc_id for doc_id in ids):
        raise ValueError(f"{path}: every document needs a non-empty id")
    if len(ids) != len(set(ids)):
        raise ValueError(f"{path}: document ids must be unique")
    invalid_pages = [
        document["id"]
        for document in documents
        if "page" in document
        and (not isinstance(document["page"], int) or document["page"] < 1)
    ]
    if invalid_pages:
        raise ValueError(f"{path}: page must be a positive integer for {', '.join(invalid_pages)}")
    return source_root, documents


def _source(document: dict[str, Any], source_root: Path) -> Path:
    path = (source_root / document["path"]).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"{document['id']}: source not found: {path}")
    expected = document.get("sha256")
    actual = _sha256(path)
    if not expected:
        raise ValueError(f"{document['id']}: manifest has no sha256")
    if actual != expected:
        raise ValueError(f"{document['id']}: sha256 {actual} != {expected}")
    return path


def _default_executable(engine_id: str) -> str | None:
    if engine_id in DOCLING_LAYOUT_ENGINE_IDS:
        return sys.executable
    name = {
        "pdf2md-current": "pdf2md",
        "pdf2md-page-vlm": "pdf2md",
        "docling-standard": "docling",
        "docling-vlm": "docling",
        "paddleocr-vl": "paddleocr",
        "mineru": "mineru",
    }[engine_id]
    beside_python = Path(sys.executable).parent / name
    if beside_python.is_file():
        return str(beside_python)
    return shutil.which(name)


def _resolve_executable(engine_id: str, overrides: dict[str, str]) -> str | None:
    raw = overrides.get(engine_id)
    if raw is None:
        return _default_executable(engine_id)
    if os.sep in raw or (os.altsep and os.altsep in raw):
        path = Path(raw).expanduser().resolve()
        return str(path) if path.is_file() else None
    return shutil.which(raw)


def _probe(executable: str, args: list[str], timeout: float = 30.0) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            [executable, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"command": [executable, *args], "error": str(exc)}
    return {
        "command": [executable, *args],
        "exit_code": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def _docling_uses_convert(help_text: str) -> bool:
    clean = _ANSI.sub("", help_text)
    return "COMMAND [ARGS]" in clean


def _build_command(
    engine_id: str,
    executable: str,
    source: Path,
    native_dir: Path,
    *,
    docling_help: str | None = None,
) -> tuple[list[str], dict[str, Any]]:
    details: dict[str, Any] = {}
    if engine_id.startswith("pdf2md-"):
        command = [
            executable,
            "convert",
            str(source),
            "--out",
            str(native_dir),
            "--force",
            "--figure-svg",
        ]
        if engine_id == "pdf2md-page-vlm":
            command.extend([
                "--ocr-page-vlm",
                "--vlm-ocr-model", "glm-ocr:q8_0",
            ])
        return command, details

    if engine_id in DOCLING_LAYOUT_ENGINE_IDS:
        candidate = _layout_candidate(engine_id)
        details["layout_candidate"] = candidate
        return [
            executable,
            str(_LAYOUT_RUNNER),
            "--candidate", engine_id,
            "--candidates", str(_LAYOUT_CANDIDATES),
            "--output", str(native_dir),
            str(source),
        ], details

    if engine_id in {"docling-standard", "docling-vlm"}:
        if docling_help is None:
            help_probe = _probe(executable, ["--help"])
            docling_help = help_probe.get("stdout", "") + help_probe.get("stderr", "")
            details["cli_help_probe"] = {
                "command": help_probe["command"],
                "exit_code": help_probe.get("exit_code"),
                "stdout_sha256": hashlib.sha256(
                    help_probe.get("stdout", "").encode()
                ).hexdigest(),
                "stderr": help_probe.get("stderr", ""),
                **({"error": help_probe["error"]} if "error" in help_probe else {}),
            }
        uses_convert = _docling_uses_convert(docling_help)
        details["cli_generation"] = "convert-subcommand" if uses_convert else "single-command"
        command = [executable]
        if uses_convert:
            command.append("convert")
        if engine_id == "docling-standard":
            command.extend([
                "--pipeline", "standard",
                "--enrich-formula",
                "--enrich-picture-classes",
                "--enrich-picture-description",
                "--enrich-chart-extraction",
            ])
        else:
            command.extend(["--pipeline", "vlm", "--vlm-model", "granite_docling"])
        command.extend([
            "--to", "md",
            "--to", "json",
            "--image-export-mode", "referenced",
            "--abort-on-error",
            "--output", str(native_dir),
            str(source),
        ])
        return command, details

    if engine_id == "paddleocr-vl":
        return [
            executable,
            "doc_parser",
            "-i", str(source),
            "--device", "cpu",
            "--save_path", str(native_dir),
        ], details

    if engine_id == "mineru":
        return [
            executable,
            "-p", str(source),
            "-o", str(native_dir),
            "-b", "hybrid-engine",
            "--effort", "high",
            "--image-analysis", "true",
        ], details

    raise ValueError(f"unknown engine: {engine_id}")


def _version_probe(engine_id: str, executable: str) -> dict[str, Any]:
    if engine_id in DOCLING_LAYOUT_ENGINE_IDS:
        args = ["--version"]
    else:
        args = ["version"] if engine_id.startswith("pdf2md-") else ["--version"]
    return _probe(executable, args)


def _layout_candidate(engine_id: str) -> dict[str, Any]:
    registry = json.loads(_LAYOUT_CANDIDATES.read_text())
    if registry.get("schema_version") != 1:
        raise ValueError(f"{_LAYOUT_CANDIDATES}: unsupported schema_version")
    candidates = registry.get("candidates")
    if not isinstance(candidates, list):
        raise ValueError(f"{_LAYOUT_CANDIDATES}: candidates must be a list")
    by_id = {candidate.get("id"): candidate for candidate in candidates}
    candidate = by_id.get(engine_id)
    if candidate is None:
        raise ValueError(f"{_LAYOUT_CANDIDATES}: no candidate named {engine_id}")
    revision = candidate.get("revision")
    weight_sha256 = candidate.get("weight_sha256")
    if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError(f"{_LAYOUT_CANDIDATES}: {engine_id} revision is not a commit hash")
    if not isinstance(weight_sha256, str) or not re.fullmatch(
        r"[0-9a-f]{64}", weight_sha256
    ):
        raise ValueError(f"{_LAYOUT_CANDIDATES}: {engine_id} weight_sha256 is invalid")
    return candidate


def _inventory(run_dir: Path, native_dir: Path) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    for path in sorted(p for p in native_dir.rglob("*") if p.is_file()):
        files.append({
            "path": str(path.relative_to(run_dir)),
            "bytes": path.stat().st_size,
            "sha256": _sha256(path),
        })
    return files


def _materialize_input(
    document: dict[str, Any], source: Path, input_root: Path, pdfseparate: str | None = None
) -> tuple[Path, dict[str, Any]]:
    page = document.get("page")
    if page is None:
        return source, {
            "path": str(source),
            "sha256": document["sha256"],
            "selection": "full document",
        }

    executable = pdfseparate or shutil.which("pdfseparate")
    if executable is None:
        raise RuntimeError("pdfseparate is required for sampled-page corpus entries")
    input_root.mkdir(parents=True, exist_ok=True)
    stem = f"{document['sha256'][:16]}-p{page}"
    selected = input_root / f"{stem}.pdf"
    reused = selected.is_file()
    producer = [
        executable,
        "-f", str(page),
        "-l", str(page),
        str(source),
        str(input_root / f".{stem}-%d-{os.getpid()}.pdf"),
    ]
    if not reused:
        probe = _probe(executable, producer[1:], timeout=120.0)
        if probe.get("exit_code") != 0:
            message = probe.get("stderr") or probe.get("error") or "unknown pdfseparate failure"
            raise RuntimeError(message.strip())
        generated = input_root / f".{stem}-{page}-{os.getpid()}.pdf"
        if not generated.is_file():
            raise RuntimeError(f"pdfseparate did not create {generated}")
        generated.replace(selected)
    return selected, {
        "path": str(selected),
        "sha256": _sha256(selected),
        "selection": {"source_page": page},
        "producer": {"command": producer, "reused": reused},
    }


def _stop_process(proc: subprocess.Popen[Any]) -> None:
    if proc.poll() is not None:
        return
    if os.name == "posix":
        os.killpg(proc.pid, signal.SIGTERM)
    else:
        proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        if os.name == "posix":
            os.killpg(proc.pid, signal.SIGKILL)
        else:
            proc.kill()
        proc.wait()


def _run_process(
    command: list[str], stdout_path: Path, stderr_path: Path, timeout: float | None
) -> tuple[int | None, bool, dict[str, Any]]:
    with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
        proc = subprocess.Popen(
            command,
            stdout=stdout,
            stderr=stderr,
            start_new_session=os.name == "posix",
        )
        try:
            if os.name == "posix" and hasattr(os, "wait4"):
                deadline = time.monotonic() + timeout if timeout is not None else None
                while True:
                    waited_pid, status, usage = os.wait4(proc.pid, os.WNOHANG)
                    if waited_pid:
                        exit_code = os.waitstatus_to_exitcode(status)
                        proc.returncode = exit_code
                        rss_scale = 1024 if sys.platform.startswith("linux") else 1
                        return exit_code, False, {
                            "peak_rss_bytes": int(usage.ru_maxrss * rss_scale),
                            "user_cpu_seconds": round(usage.ru_utime, 3),
                            "system_cpu_seconds": round(usage.ru_stime, 3),
                        }
                    if deadline is not None and time.monotonic() >= deadline:
                        raise subprocess.TimeoutExpired(command, timeout)
                    time.sleep(0.05)
            return proc.wait(timeout=timeout), False, {}
        except subprocess.TimeoutExpired:
            _stop_process(proc)
            return proc.returncode, True, {}
        except BaseException:
            _stop_process(proc)
            raise


def _run_one(
    engine_id: str,
    document: dict[str, Any],
    source: Path,
    output_root: Path,
    executable: str | None,
    timeout: float | None,
) -> tuple[Path, dict[str, Any]]:
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    run_dir = output_root / document["id"] / engine_id / run_id
    native_dir = run_dir / "native"
    native_dir.mkdir(parents=True)
    started = datetime.now(timezone.utc)
    record: dict[str, Any] = {
        "schema_version": 1,
        "run_id": run_id,
        "status": "unavailable",
        "started_at": started.isoformat(),
        "engine": {"id": engine_id, "executable": executable},
        "source": {
            "id": document["id"],
            "path": str(source),
            "bytes": source.stat().st_size,
            "sha256": document["sha256"],
            "archetypes": document.get("archetypes", []),
        },
        "host": {
            "node": platform.node(),
            "platform": platform.platform(),
            "machine": platform.machine(),
        },
    }
    stdout_path = run_dir / "stdout.log"
    stderr_path = run_dir / "stderr.log"
    stdout_path.touch()
    stderr_path.touch()
    if executable is None:
        record["error"] = "executable not found"
    else:
        version = _version_probe(engine_id, executable)
        attempt_started = time.perf_counter()
        try:
            effective_source, input_record = _materialize_input(
                document, source, output_root / "_inputs"
            )
            command, details = _build_command(
                engine_id, executable, effective_source, native_dir
            )
            record["input"] = input_record
            record["engine"].update({"version_probe": version, **details})
            record["command"] = command
            exit_code, timed_out, resources = _run_process(
                command, stdout_path, stderr_path, timeout
            )
        except (OSError, RuntimeError) as exc:
            record["duration_seconds"] = round(time.perf_counter() - attempt_started, 3)
            record["status"] = "failed"
            record["error"] = str(exc)
        else:
            record["duration_seconds"] = round(time.perf_counter() - attempt_started, 3)
            record["exit_code"] = exit_code
            record["resources"] = resources
            record["status"] = (
                "timed_out" if timed_out else ("ok" if exit_code == 0 else "failed")
            )

    finished = datetime.now(timezone.utc)
    record["finished_at"] = finished.isoformat()
    record["outputs"] = _inventory(run_dir, native_dir)
    (run_dir / "run.json").write_text(json.dumps(record, indent=2) + "\n")
    return run_dir, record


def _parse_overrides(values: list[str]) -> dict[str, str]:
    overrides: dict[str, str] = {}
    for value in values:
        engine_id, separator, executable = value.partition("=")
        if not separator or engine_id not in ENGINE_IDS or not executable:
            choices = ", ".join(ENGINE_IDS)
            raise ValueError(f"--executable must be ENGINE=PATH; ENGINE is one of: {choices}")
        overrides[engine_id] = executable
    return overrides


def _print_inventory(documents: list[dict[str, Any]], overrides: dict[str, str]) -> None:
    print("Engines:")
    for engine_id in ENGINE_IDS:
        executable = _resolve_executable(engine_id, overrides)
        print(f"  {engine_id:18} {executable or 'unavailable'}")
    print("Documents:")
    for document in documents:
        kinds = ", ".join(document.get("archetypes", []))
        print(f"  {document['id']:18} {document['path']} ({kinds})")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run parser candidates without adapting their output."
    )
    parser.add_argument("--manifest", type=Path, default=_DEFAULT_MANIFEST)
    parser.add_argument("--engine", action="append", choices=ENGINE_IDS, default=[])
    parser.add_argument("--document", action="append", default=[])
    parser.add_argument("--output", type=Path, default=_DEFAULT_OUTPUT)
    parser.add_argument(
        "--executable",
        action="append",
        default=[],
        metavar="ENGINE=PATH",
        help="Use an engine executable from a separate environment.",
    )
    parser.add_argument("--timeout", type=float, help="Stop one engine run after N seconds.")
    parser.add_argument("--list", action="store_true", help="List candidates and corpus entries.")
    parser.add_argument("--dry-run", action="store_true", help="Resolve and print commands only.")
    args = parser.parse_args()

    try:
        source_root, documents = _load_manifest(args.manifest.resolve())
        overrides = _parse_overrides(args.executable)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))

    if args.list:
        _print_inventory(documents, overrides)
        return
    if not args.engine or not args.document:
        parser.error("select at least one --engine and one --document (use --list)")
    if args.timeout is not None and args.timeout <= 0:
        parser.error("--timeout must be positive")

    by_id = {document["id"]: document for document in documents}
    unknown = sorted(set(args.document) - by_id.keys())
    if unknown:
        parser.error(f"unknown document id(s): {', '.join(unknown)}")

    failures = 0
    for doc_id in args.document:
        document = by_id[doc_id]
        try:
            source = _source(document, source_root)
        except (OSError, ValueError) as exc:
            print(f"FAIL {exc}", file=sys.stderr)
            failures += len(args.engine)
            continue
        for engine_id in args.engine:
            executable = _resolve_executable(engine_id, overrides)
            if args.dry_run:
                if executable is None:
                    print(f"{doc_id}/{engine_id}: unavailable")
                    failures += 1
                    continue
                command_source = (
                    Path(f"SOURCE_PAGE_{document['page']}.pdf")
                    if "page" in document
                    else source
                )
                command, _ = _build_command(
                    engine_id, executable, command_source, Path("NATIVE_OUTPUT")
                )
                print(f"{doc_id}/{engine_id}: {shlex.join(command)}")
                continue
            print(f"RUN  {doc_id}/{engine_id}")
            run_dir, record = _run_one(
                engine_id,
                document,
                source,
                args.output.resolve(),
                executable,
                args.timeout,
            )
            print(f"{record['status'].upper():4} {run_dir}")
            failures += record["status"] != "ok"
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
