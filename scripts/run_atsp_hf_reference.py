"""Run independent term-specific Hartree-Fock cases for table verification.

The runner records every input and raw log plus executable and case-file hashes.
It refuses existing case directories so a prior calculation is never overwritten.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import time
from pathlib import Path


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_manifest(path: Path, manifest: dict) -> None:
    pending = path.with_suffix(".json.pending")
    pending.write_text(json.dumps(manifest, indent=2) + "\n")
    pending.replace(path)


def _input(case: dict) -> str:
    closed = (
        "  " + "  ".join(case["closed_shells"])
        if case["closed_shells"]
        else ""
    )
    return "\n".join([
        f"{case['symbol']},{case['term']},{case['atomic_number']}",
        closed,
        case["open_configuration"],
        "all",
        "y",
        "y",
        "n",
        "n",
        "",
    ])


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run pinned ATSP Hartree-Fock reference cases."
    )
    parser.add_argument("cases", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--executable", type=Path, required=True)
    parser.add_argument("--tool-version", required=True)
    args = parser.parse_args()

    cases_path = args.cases.resolve()
    executable = args.executable.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    source = json.loads(cases_path.read_text())
    if source.get("schema_version") != 1:
        raise ValueError("unsupported ATSP reference cases schema_version")

    manifest = {
        "schema_version": 1,
        "method": "term_specific_numerical_hartree_fock",
        "cases": str(cases_path),
        "cases_sha256": _sha256(cases_path),
        "executable": str(executable),
        "executable_sha256": _sha256(executable),
        "tool_version": args.tool_version,
        "runs": [],
    }
    manifest_path = output_dir / "manifest.json"
    for index, case in enumerate(source["cases"], start=1):
        case_id = case["id"]
        case_dir = output_dir / case_id
        if case_dir.exists():
            raise FileExistsError(f"ATSP case directory already exists: {case_dir}")
        case_dir.mkdir()
        input_path = case_dir / "stdin.txt"
        stdout_path = case_dir / "stdout.log"
        input_path.write_text(_input(case))
        started = time.monotonic()
        with input_path.open("rb") as stdin, stdout_path.open("wb") as stdout:
            completed = subprocess.run(
                [executable], cwd=case_dir, stdin=stdin, stdout=stdout, stderr=subprocess.STDOUT
            )
        elapsed = time.monotonic() - started
        hf_log = case_dir / "hf.log"
        completed_result = (
            hf_log.is_file() and "TOTAL ENERGY (a.u.)" in hf_log.read_text()
        )
        run = {
            "id": case_id,
            "atomic_number": case["atomic_number"],
            "symbol": case["symbol"],
            "term": case["term"],
            "input": str(input_path.relative_to(output_dir)),
            "input_sha256": _sha256(input_path),
            "stdout": str(stdout_path.relative_to(output_dir)),
            "stdout_sha256": _sha256(stdout_path),
            "hf_log": str(hf_log.relative_to(output_dir)) if hf_log.is_file() else None,
            "hf_log_sha256": _sha256(hf_log) if hf_log.is_file() else None,
            "exit_code": completed.returncode,
            "completed_result": completed_result,
            "elapsed_seconds": round(elapsed, 6),
        }
        manifest["runs"].append(run)
        _write_manifest(manifest_path, manifest)
        print(
            f"[{index}/{len(source['cases'])}] {case_id}: "
            f"exit {completed.returncode}, {elapsed:.2f}s",
            flush=True,
        )
        if completed.returncode or not completed_result:
            raise SystemExit(f"ATSP reference case failed: {case_id}")


if __name__ == "__main__":
    main()
