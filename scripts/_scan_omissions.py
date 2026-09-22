"""Experimental second-reader line comparison; no extraction or repair decisions."""

from __future__ import annotations

import csv
import html
import io
import math
import re
import unicodedata
from collections import defaultdict

from _missing_content import bounds, contains

POLICY = {
    "dpi": 300, "psm": 3, "language": "eng", "timeout_seconds": 120,
    "minimum_characters": 20, "minimum_mean_reader_confidence": 70,
    "maximum_normalized_distance": 0.20,
}


def normalized(text: str) -> str:
    text = html.unescape(re.sub(r"</?(?:sub|sup)>", "", text))
    return "".join(c for c in unicodedata.normalize("NFKC", text).casefold() if c.isalnum())


def substring_distance(needle: str, text: str) -> int:
    """Edit distance to any contiguous substring, with free text prefix/suffix."""
    if needle in text:
        return 0
    previous = [0] * (len(text) + 1)
    for i, char in enumerate(needle, 1):
        row = [i]
        for j, other in enumerate(text, 1):
            row.append(min(row[-1] + 1, previous[j] + 1,
                           previous[j - 1] + (char != other)))
        previous = row
    return min(previous)


def read_lines(tsv: str, width: int, height: int, page_box: tuple) -> list[dict]:
    """Tesseract word TSV -> source-addressed lines in absolute PDF user space."""
    reader = csv.DictReader(io.StringIO(tsv), delimiter="\t", quoting=csv.QUOTE_NONE)
    required = {"level", "block_num", "par_num", "line_num", "text", "conf",
                "left", "top", "width", "height"}
    if not required <= set(reader.fieldnames or []):
        raise ValueError("reader TSV lacks required columns")
    left, bottom, right, top = bounds(page_box)
    if width <= 0 or height <= 0 or right <= left or top <= bottom:
        raise ValueError("invalid page dimensions")
    groups = defaultdict(list)
    for raw in reader:
        if raw["level"] != "5" or not raw["text"].strip():
            continue
        x, y, w, h = (int(raw[k]) for k in ("left", "top", "width", "height"))
        confidence = float(raw["conf"])
        if (not math.isfinite(confidence) or not -1 <= confidence <= 100
                or x < 0 or y < 0 or w <= 0 or h <= 0 or x + w > width or y + h > height):
            raise ValueError("invalid reader word geometry or confidence")
        key = (raw["block_num"], raw["par_num"], raw["line_num"])
        groups[key].append({
            "text": raw["text"].strip(), "confidence": confidence,
            "bbox": [left + x / width * (right - left),
                     top - (y + h) / height * (top - bottom),
                     left + (x + w) / width * (right - left),
                     top - y / height * (top - bottom)],
        })
    result = []
    for key, words in groups.items():
        result.append({
            "line_id": ":".join(key), "text": " ".join(w["text"] for w in words),
            "words": words,
            "bbox": [min(w["bbox"][0] for w in words), min(w["bbox"][1] for w in words),
                     max(w["bbox"][2] for w in words), max(w["bbox"][3] for w in words)],
            "mean_reader_confidence": sum(w["confidence"] for w in words) / len(words),
        })
    return result


def _covers_words(box: tuple, line: dict) -> bool:
    return all(contains(box, word["bbox"][0], word["bbox"][1])
               and contains(box, word["bbox"][2], word["bbox"][3]) for word in line["words"])


def compare_lines(lines: list[dict], blocks: list[dict]) -> list[dict]:
    """Compare with local text, never treating a paragraph's box as proof of recall."""
    represented = [b for b in blocks if b["coverage_status"] in {"emitted", "cropped", "flagged"}]
    unlocated = [b["id"] for b in represented if b["bbox"] is None]
    located = [(b, bounds(b["bbox"])) for b in represented if b["bbox"] is not None]
    records = []
    for line in lines:
        record = {**line, "status": "unassessed", "block_ids": [], "distance": None}
        records.append(record)
        crops = [b["id"] for b, box in located
                 if b.get("content_crop") and _covers_words(box, line)]
        furniture = [b["id"] for b, box in located
                     if b["type"] in {"page_header", "page_footer"} and _covers_words(box, line)]
        if crops or furniture:
            record.update(status="content_crop" if crops else "declared_furniture",
                          block_ids=crops or furniture)
            continue
        if unlocated:
            record.update(status="unlocated_representation", block_ids=unlocated)
            continue
        source = normalized(line["text"])
        if len(source) < POLICY["minimum_characters"]:
            record["status"] = "short_line"
            continue
        if line["mean_reader_confidence"] < POLICY["minimum_mean_reader_confidence"]:
            record["status"] = "low_reader_confidence"
            continue
        x0, y0, x1, y1 = bounds(line["bbox"])
        local = [b for b, box in located
                 if b["coverage_status"] == "emitted" and not b.get("content_crop")
                 and b["type"] not in {"page_header", "page_footer", "figure", "table"}
                 and min(x1, box[2]) > max(x0, box[0])
                 and min(y1, box[3]) > max(y0, box[1])]
        text = normalized("\n".join(b["text"] for b in local))
        distance = substring_distance(source, text) / len(source)
        record.update(
            status="reader_disagreement" if distance > POLICY["maximum_normalized_distance"]
            else "reader_agreement",
            block_ids=[b["id"] for b in local], distance=round(distance, 6),
        )
    return records
