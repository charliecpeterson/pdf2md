"""Run a pinned PaddleOCR recognition model over a crop manifest."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path

def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _model_hash(model_dir: Path) -> str | None:
    files = [
        model_dir / name
        for name in ("inference.json", "inference.pdiparams", "inference.yml")
    ]
    if not all(path.is_file() for path in files):
        return None
    digest = hashlib.sha256()
    for path in files:
        digest.update(path.relative_to(model_dir).as_posix().encode())
        digest.update(bytes.fromhex(_sha256(path)))
    return digest.hexdigest()


def run(inputs_path: Path, output_path: Path, model_name: str, device: str) -> dict:
    from paddleocr import TextRecognition

    inputs = json.loads(inputs_path.read_text())
    model = TextRecognition(model_name=model_name, device=device)
    records = []
    total = len(inputs["records"])
    for index, record in enumerate(inputs["records"], start=1):
        crop_path = inputs_path.parent / record["crop"]
        result = {
            "id": record["id"],
            "input_sha256": _sha256(crop_path),
            "text": None,
            "score": None,
            "error": None,
        }
        if result["input_sha256"] != record["crop_sha256"]:
            result["error"] = "input_hash_mismatch"
        else:
            try:
                prediction = next(iter(model.predict(input=str(crop_path), batch_size=1)))
                payload = prediction.json["res"]
                result["text"] = payload.get("rec_text")
                result["score"] = payload.get("rec_score")
            except Exception as error:
                result["error"] = f"{type(error).__name__}: {error}"
        records.append(result)
        print(
            f"[{index}/{total}] {record['id']}: "
            f"{result['text']!r} ({result['score']})",
            flush=True,
        )

    model_dir = Path.home() / ".paddlex" / "official_models" / model_name
    report = {
        "schema_version": 1,
        "reader": {
            "model_name": model_name,
            "model_sha256": _model_hash(model_dir),
            "device": device,
            "paddleocr_version": importlib.metadata.version("paddleocr"),
            "paddlex_version": importlib.metadata.version("paddlex"),
            "paddlepaddle_gpu_version": importlib.metadata.version("paddlepaddle-gpu"),
        },
        "records": records,
    }
    output_path.write_text(json.dumps(report, indent=2) + "\n")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Run recognition-only PaddleOCR.")
    parser.add_argument("inputs", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--model", default="PP-OCRv6_medium_rec")
    parser.add_argument("--device", default="gpu:0")
    args = parser.parse_args()
    run(args.inputs, args.output, args.model, args.device)


if __name__ == "__main__":
    main()
