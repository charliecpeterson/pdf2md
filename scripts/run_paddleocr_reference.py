"""Run preserved image crops through a PaddleOCR-VL HTTP service.

The runner keeps each raw response and updates a hash-pinned machine-readable run
record after every request so an interrupted comparison can resume safely.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from pathlib import Path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _request(url: str, image_path: Path, timeout: float) -> tuple[int, bytes]:
    payload = json.dumps({
        "file": base64.b64encode(image_path.read_bytes()).decode("ascii"),
        "fileType": 1,
        "visualize": False,
    }).encode()
    request = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def _write_run(path: Path, run: dict) -> None:
    pending = path.with_suffix(path.suffix + ".pending")
    pending.write_text(json.dumps(run, indent=2) + "\n")
    pending.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run image crops through a PaddleOCR-VL layout-parsing service."
    )
    parser.add_argument("manifest", type=Path)
    parser.add_argument(
        "--url", default="http://127.0.0.1:8080/layout-parsing"
    )
    parser.add_argument("--timeout", type=float, default=600)
    parser.add_argument("--heartbeat", type=float, default=15)
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--tool-metadata",
        type=Path,
        help="JSON object describing the pinned model, images, host, and settings.",
    )
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text())
    crops = manifest.get("crops") or []
    run_path = args.manifest.parent / "paddle-run.json"
    existing = {}
    prior = {}
    if run_path.is_file() and not args.force:
        prior = json.loads(run_path.read_text())
        existing = {item["input"]: item for item in prior.get("results", [])}
    tool = (
        json.loads(args.tool_metadata.read_text())
        if args.tool_metadata
        else prior.get("tool")
    )
    run = {
        "schema_version": 1,
        "url": args.url,
        "source_manifest": args.manifest.name,
        "results": [],
    }
    if tool:
        run["tool"] = tool

    with ThreadPoolExecutor(max_workers=1) as executor:
        for index, crop in enumerate(crops, start=1):
            image_path = args.manifest.parent / crop["path"]
            input_sha256 = _sha256(image_path)
            prior = existing.get(crop["path"])
            if (
                prior
                and prior.get("input_sha256") == input_sha256
                and (args.manifest.parent / prior.get("response", "")).is_file()
            ):
                print(f"[{index}/{len(crops)}] {crop['path']}: cached", flush=True)
                run["results"].append(prior)
                _write_run(run_path, run)
                continue

            print(f"[{index}/{len(crops)}] {crop['path']}: running", flush=True)
            started = time.monotonic()
            future = executor.submit(_request, args.url, image_path, args.timeout)
            while True:
                try:
                    status, body = future.result(timeout=args.heartbeat)
                    break
                except FutureTimeout:
                    elapsed = time.monotonic() - started
                    print(
                        f"[{index}/{len(crops)}] {crop['path']}: {elapsed:.0f}s elapsed",
                        flush=True,
                    )
            elapsed = time.monotonic() - started
            response_path = args.manifest.parent / f"{image_path.stem}.paddle.json"
            response_path.write_bytes(body)
            try:
                response = json.loads(body)
                error_code = response.get("errorCode")
                error_message = response.get("errorMsg")
            except json.JSONDecodeError:
                error_code = None
                error_message = "non_json_response"
            result = {
                **crop,
                "input": crop["path"],
                "input_sha256": input_sha256,
                "response": response_path.name,
                "response_sha256": _sha256(response_path),
                "http_status": status,
                "error_code": error_code,
                "error_message": error_message,
                "elapsed_seconds": round(elapsed, 6),
            }
            run["results"].append(result)
            _write_run(run_path, run)
            print(
                f"[{index}/{len(crops)}] {crop['path']}: HTTP {status}, "
                f"error {error_code}, {elapsed:.2f}s",
                flush=True,
            )


if __name__ == "__main__":
    main()
