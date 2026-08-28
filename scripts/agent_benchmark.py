"""Compare question answering from pdf2md retrieval records and source-PDF pages.

    uv run --extra describe python scripts/agent_benchmark.py \
        --mode bundle --mode pdf --model qwen3-vl:8b --output results.json

Bundle mode ranks page chunks or stable passages and opens only assets attached
to uncertain or image-only records. PDF mode renders the pinned source pages.
Every run records answers, citations, opened assets, token use, and release gates.
"""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import json
import mimetypes
import re
import tempfile
import time
from dataclasses import fields
from decimal import Decimal
from pathlib import Path
from typing import Any

from pdf2md.cache import content_hash
from pdf2md.passages import build_passages
from pdf2md.render import CropRenderer
from pdf2md.schema import (
    BBox,
    Block,
    BlockType,
    CoverageFlag,
    CoverageReport,
    CoverageStatus,
    Digitization,
    Document,
    FigureLabels,
    FigureRef,
    Section,
    SectionKind,
    TableData,
)


_ROOT = Path(__file__).parent.parent
_QUESTIONS = _ROOT / "tests" / "agent_questions.json"
_MANIFEST = _ROOT / "tests" / "bakeoff_manifest.json"
_WORD = re.compile(r"[A-Za-z0-9]+")
_NUMBER = re.compile(r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?")
_FRACTION = re.compile(r"\s*([-+]?\d+(?:\.\d+)?)\s*/\s*([-+]?\d+(?:\.\d+)?)\s*")
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
_MATERIAL_TOKEN_REDUCTION = 0.20


def _normalized(text: str) -> str:
    return " ".join(_WORD.findall(text.casefold()))


def score_answer(text: str, expected: dict[str, Any]) -> bool:
    kind = expected["kind"]
    if kind == "refusal":
        return _normalized(text) == "insufficient evidence"
    if kind == "exact":
        answer = _normalized(text)
        return any(answer == _normalized(candidate) for candidate in expected["accepted"])
    if kind == "contains":
        answer_words = _normalized(text).split()
        for candidate in expected["accepted"]:
            candidate_words = _normalized(candidate).split()
            width = len(candidate_words)
            if any(
                answer_words[index:index + width] == candidate_words
                for index in range(len(answer_words) - width + 1)
            ):
                return True
        return False
    if kind == "contains_all":
        answer = _normalized(text)
        return all(
            any(_normalized(candidate) in answer for candidate in group)
            for group in expected["accepted_groups"]
        )
    fraction = _FRACTION.fullmatch(text)
    if fraction and float(fraction.group(2)) != 0:
        numbers = [float(fraction.group(1)) / float(fraction.group(2))]
    else:
        numbers = [float(value) for value in _NUMBER.findall(text)]
    tolerance = float(expected.get("tolerance", 0))
    targets = [float(expected["value"])] if kind == "number" else [
        float(value) for value in expected["values"]
    ]
    return len(numbers) == len(targets) and all(
        abs(actual - target) <= tolerance for actual, target in zip(numbers, targets)
    )


def classify_answer(text: str, expected: dict[str, Any], finish_reason: str | None) -> str:
    if score_answer(text, expected):
        return "correct"
    if not text or _normalized(text) == "insufficient evidence" or finish_reason == "length":
        return "refused"
    return "incorrect"


def _question_terms(question: str) -> set[str]:
    stop = {"a", "an", "and", "at", "do", "does", "in", "is", "of", "the", "to", "what", "which"}
    return {word for word in _WORD.findall(question.casefold()) if word not in stop}


def rank_chunks(question: str, chunks: list[dict[str, Any]], limit: int = 3) -> list[dict[str, Any]]:
    terms = _question_terms(question)

    def score(chunk: dict[str, Any]) -> tuple[int, int, int]:
        words = _WORD.findall(
            f"{chunk.get('section', {}).get('title', '')} "
            f"{chunk.get('search_text', chunk.get('text', ''))}"
            .casefold()
        )
        counts = {word: words.count(word) for word in terms}
        return sum(bool(count) for count in counts.values()), sum(counts.values()), -len(words)

    ranked = sorted(chunks, key=score, reverse=True)
    return [chunk for chunk in ranked[:limit] if score(chunk)[0] > 0] or ranked[:1]


def _bbox(raw: dict[str, Any] | None) -> BBox | None:
    return BBox(**raw) if raw else None


def _known_fields(cls, raw: dict[str, Any]) -> dict[str, Any]:
    names = {item.name for item in fields(cls)}
    return {key: value for key, value in raw.items() if key in names}


def _section(raw: dict[str, Any]) -> Section:
    return Section(
        id=raw["id"],
        title=raw["title"],
        depth=raw["depth"],
        kind=SectionKind(raw["kind"]),
        page_start=raw["page_start"],
        block_ids=raw.get("block_ids", []),
        children=[_section(child) for child in raw.get("children", [])],
    )


def _document_from_provenance(raw: dict[str, Any]) -> Document:
    blocks = []
    for value in raw.get("blocks", []):
        block = _known_fields(Block, value)
        block["type"] = BlockType(block["type"])
        block["coverage_status"] = CoverageStatus(block["coverage_status"])
        block["bbox"] = _bbox(block.get("bbox"))
        blocks.append(Block(**block))

    tables = []
    for value in raw.get("tables", []):
        table = _known_fields(TableData, value)
        table["bbox"] = _bbox(table.get("bbox"))
        tables.append(TableData(**table))

    figures = []
    for value in raw.get("figures", []):
        figure = _known_fields(FigureRef, value)
        figure["bbox"] = _bbox(figure.get("bbox"))
        figure["caption_bbox"] = _bbox(figure.get("caption_bbox"))
        if figure.get("digitization"):
            figure["digitization"] = Digitization(**_known_fields(
                Digitization, figure["digitization"]
            ))
        if figure.get("labels"):
            figure["labels"] = FigureLabels(**_known_fields(
                FigureLabels, figure["labels"]
            ))
        figures.append(FigureRef(**figure))

    coverage_raw = raw.get("coverage")
    coverage = None
    if coverage_raw:
        coverage_values = _known_fields(CoverageReport, coverage_raw)
        coverage_values["flags"] = [
            CoverageFlag(**_known_fields(CoverageFlag, flag))
            for flag in coverage_raw.get("flags", [])
        ]
        coverage = CoverageReport(**coverage_values)

    return Document(
        doc_id=raw["doc_id"],
        source_path=raw["source_path"],
        source_sha256=raw["source_sha256"],
        version=raw["version"],
        page_count=raw["page_count"],
        sections=_section(raw["sections"]),
        blocks=blocks,
        tables=tables,
        figures=figures,
        coverage=coverage,
    )


def _passages_from_provenance(version_dir: Path) -> list[dict[str, Any]]:
    provenance = json.loads((version_dir / "provenance.json").read_text())
    manifest = json.loads((version_dir / "manifest.json").read_text())
    document = _document_from_provenance(provenance)
    markdown = [version_dir / name for name in manifest["read"].get("markdown", [])]
    page_rasters = {
        int(record["page"]): record["path"]
        for record in manifest.get("representations", {}).get("page_images", [])
    }
    return build_passages(
        document,
        {"title": manifest["document"].get("title") or Path(document.source_path).stem},
        markdown,
        page_rasters,
    )


def _passage_record(passage: dict[str, Any]) -> dict[str, Any]:
    pages = list(dict.fromkeys(source["page"] for source in passage["sources"]))
    source_pages = list(dict.fromkeys(
        source["source_page"] for source in passage["sources"]
    ))
    breadcrumb = passage.get("section_breadcrumb", [])
    return {
        "id": passage["id"],
        "markdown": passage["markdown"],
        "pages": pages,
        "source_pages": source_pages,
        "text": passage["display_text"],
        "search_text": passage["retrieval_text"],
        "assets": [asset["path"] for asset in passage.get("assets", [])],
        "needs_review": passage.get("review", {}).get("needs_review", False),
        "section": {"title": breadcrumb[-1]["title"] if breadcrumb else ""},
    }


def _retrieval_records(version_dir: Path, retrieval: str) -> list[dict[str, Any]]:
    if retrieval == "chunks":
        return [
            json.loads(line)
            for line in (version_dir / "chunks.jsonl").read_text().splitlines()
            if line.strip()
        ]
    passage_path = version_dir / "passages.jsonl"
    passages = (
        [json.loads(line) for line in passage_path.read_text().splitlines() if line.strip()]
        if passage_path.is_file()
        else _passages_from_provenance(version_dir)
    )
    return [_passage_record(passage) for passage in passages]


def _review_queue_metrics(results: list[dict[str, Any]]) -> dict[str, Any]:
    labelled = [result for result in results if result.get("review_queue_scored")]
    true_positives = sum(
        result["expected_action_required"]
        and result.get("queue_flagged", result.get("review_flagged", False))
        for result in labelled
    )
    false_positives = sum(
        not result["expected_action_required"]
        and result.get("queue_flagged", result.get("review_flagged", False))
        for result in labelled
    )
    false_negatives = sum(
        result["expected_action_required"]
        and not result.get("queue_flagged", result.get("review_flagged", False))
        for result in labelled
    )
    return {
        "questions": len(labelled),
        "true_positives": true_positives,
        "false_positives": false_positives,
        "false_negatives": false_negatives,
        "precision": round(
            true_positives / (true_positives + false_positives), 4
        ) if true_positives + false_positives else None,
        "recall": round(
            true_positives / (true_positives + false_negatives), 4
        ) if true_positives + false_negatives else None,
    }


def _retrieval_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    page_evaluable = [result for result in results if result["has_expected_pages"]]
    return {
        "questions": len(results),
        "answerable_questions": sum(result["answerable"] for result in results),
        "page_evaluable_questions": len(page_evaluable),
        "page_hits": sum(bool(result["page_hit"]) for result in page_evaluable),
        "mean_page_recall": round(
            sum(result["page_recall"] for result in page_evaluable)
            / len(page_evaluable), 4
        ) if page_evaluable else None,
        "mean_page_precision": round(
            sum(result["page_precision"] for result in page_evaluable)
            / len(page_evaluable), 4
        ) if page_evaluable else None,
        "retrieval_duration_s": round(
            sum(result["retrieval_duration_s"] for result in results), 6
        ),
        "review_queue": _review_queue_metrics(results),
    }


def _group_retrieval_results(
    results: list[dict[str, Any]], key: str
) -> dict[str, dict[str, dict[str, dict[str, Any]]]]:
    values = sorted({result[key] for result in results})
    return {
        value: {
            retrieval: {
                str(budget): _retrieval_summary([
                    result for result in results
                    if result[key] == value
                    and result["retrieval"] == retrieval
                    and result["budget"] == budget
                ])
                for budget in sorted({result["budget"] for result in results})
            }
            for retrieval in ("chunks", "passages")
        }
        for value in values
    }


def audit_retrieval(
    questions: list[dict[str, Any]],
    roots: list[Path],
    limits: int | list[int],
) -> dict[str, Any]:
    budgets = [limits] if isinstance(limits, int) else sorted(set(limits))
    if not budgets or any(budget < 1 for budget in budgets):
        raise ValueError("retrieval budgets must be positive integers")
    results = []
    documents: dict[str, dict[str, Any]] = {}
    records_by_bundle: dict[tuple[Path, str], list[dict[str, Any]]] = {}
    action_blocks_by_bundle: dict[Path, set[str]] = {}
    for question in questions:
        if question.get("numeric_context"):
            continue
        version_dir = _latest_bundle(
            roots,
            question["bundle_source_sha256"],
            question.get("bundle_provenance_sha256"),
        )
        if version_dir is None:
            raise FileNotFoundError(
                f"no current bundle for {question['document_id']} "
                f"({question['bundle_source_sha256'][:16]})"
            )
        expected_pages = set(question["bundle_pages"])
        provenance = json.loads((version_dir / "provenance.json").read_text())
        run_metrics = provenance.get("provenance", {}).get("run_metrics", {})
        documents[question["document_id"]] = {
            "source_sha256": question["bundle_source_sha256"],
            "provenance_sha256": _sha256(version_dir / "provenance.json"),
            "page_count": provenance.get("page_count"),
            "conversion_duration_s": run_metrics.get("duration_s"),
        }
        if version_dir not in action_blocks_by_bundle:
            review_path = version_dir / "review.json"
            review = json.loads(review_path.read_text()) if review_path.is_file() else {}
            action_blocks_by_bundle[version_dir] = {
                item["block_id"] for item in review.get("items", [])
                if item.get("disposition") == "action_required"
            }
        evidence_blocks = set(question.get("evidence_block_ids", []))
        queue_flagged = bool(
            evidence_blocks.intersection(action_blocks_by_bundle[version_dir])
        )
        for retrieval in ("chunks", "passages"):
            cache_key = (version_dir, retrieval)
            if cache_key not in records_by_bundle:
                records_by_bundle[cache_key] = _retrieval_records(version_dir, retrieval)
            records = records_by_bundle[cache_key]
            for budget in budgets:
                started = time.perf_counter()
                selected = rank_chunks(question["question"], records, budget)
                duration_s = time.perf_counter() - started
                selected_pages = sorted({
                    page for record in selected for page in record.get("pages", [])
                })
                matched_pages = expected_pages.intersection(selected_pages)
                has_expected_pages = bool(expected_pages)
                answerable = question.get("answer", {}).get("kind") != "refusal"
                expected_disposition = question.get("expected_review_disposition")
                results.append({
                    "question_id": question["id"],
                    "document_id": question["document_id"],
                    "document_class": question.get("document_class", "unspecified"),
                    "representation": question.get("representation", "unspecified"),
                    "retrieval": retrieval,
                    "budget": budget,
                    "selected_ids": [record["id"] for record in selected],
                    "selected_pages": selected_pages,
                    "expected_pages": sorted(expected_pages),
                    "answerable": answerable,
                    "has_expected_pages": has_expected_pages,
                    "page_hit": bool(matched_pages) if has_expected_pages else None,
                    "page_recall": (
                        len(matched_pages) / len(expected_pages)
                        if has_expected_pages else None
                    ),
                    "page_precision": (
                        len(matched_pages) / len(selected_pages)
                        if has_expected_pages and selected_pages
                        else 0.0 if has_expected_pages else None
                    ),
                    "retrieval_duration_s": round(duration_s, 6),
                    "review_queue_scored": expected_disposition is not None,
                    "expected_action_required": expected_disposition == "action_required",
                    "queue_flagged": queue_flagged,
                    "review_flagged": any(
                        bool(record.get("needs_review")) for record in selected
                    ),
                })

    by_retrieval: dict[str, dict[str, Any]] = {}
    for retrieval in ("chunks", "passages"):
        by_retrieval[retrieval] = {
            str(budget): _retrieval_summary([
                result for result in results
                if result["retrieval"] == retrieval and result["budget"] == budget
            ])
            for budget in budgets
        }
    comparisons = {}
    for budget in budgets:
        chunks = by_retrieval["chunks"][str(budget)]
        passages = by_retrieval["passages"][str(budget)]
        comparison = {
            "page_hit_delta": passages["page_hits"] - chunks["page_hits"],
            "mean_page_recall_delta": round(
                (passages["mean_page_recall"] or 0) - (chunks["mean_page_recall"] or 0), 4
            ),
            "mean_page_precision_delta": round(
                (passages["mean_page_precision"] or 0)
                - (chunks["mean_page_precision"] or 0), 4
            ),
        }
        comparison["passed"] = (
            comparison["page_hit_delta"] >= 0
            and comparison["mean_page_recall_delta"] >= 0
            and comparison["mean_page_precision_delta"] >= 0
        )
        comparisons[str(budget)] = comparison
    return {
        "schema_version": 2,
        "settings": {"passage_budgets": budgets},
        "documents": documents,
        "results": results,
        "summary": by_retrieval,
        "by_representation": _group_retrieval_results(results, "representation"),
        "by_document_class": _group_retrieval_results(results, "document_class"),
        "comparison": {
            "budgets": comparisons,
            "passed": all(value["passed"] for value in comparisons.values()),
        },
        "calibration": {
            "status": "not_applicable",
            "reason": "The benchmark emits categorical outcomes, not confidence-like probabilities.",
        },
    }


def _latest_bundle(
    roots: list[Path], source_sha256: str, provenance_sha256: str | None = None
) -> Path | None:
    candidates = []
    for root in roots:
        for provenance_path in root.rglob("provenance.json"):
            version_dir = provenance_path.parent
            if not (version_dir / "manifest.json").is_file() or not (
                version_dir / "chunks.jsonl"
            ).is_file():
                continue
            try:
                provenance = json.loads(provenance_path.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            if (
                provenance.get("source_sha256") == source_sha256
                and (
                    provenance_sha256 is None
                    or _sha256(provenance_path) == provenance_sha256
                )
            ):
                candidates.append(version_dir)
    return max(candidates, key=lambda path: path.stat().st_mtime) if candidates else None


def _bundle_context(
    question: dict[str, Any], roots: list[Path], max_chunks: int, max_assets: int,
    calculator: bool = False,
    retrieval: str = "chunks",
) -> tuple[str, list[Path], bool, Path, dict[str, Any] | None]:
    version_dir = _latest_bundle(
        roots,
        question["bundle_source_sha256"],
        question.get("bundle_provenance_sha256"),
    )
    if version_dir is None:
        raise FileNotFoundError(
            f"no current bundle for {question['document_id']} "
            f"({question['bundle_source_sha256'][:16]})"
        )
    if question.get("numeric_context"):
        return _numeric_bundle_context(question, version_dir, calculator)
    chunks = _retrieval_records(version_dir, retrieval)
    if question.get("bundle_page_context"):
        pages = set(question["bundle_pages"])
        chunks = [chunk for chunk in chunks if pages.intersection(chunk.get("pages", []))]
        if not chunks:
            raise ValueError(f"no bundle chunks on labelled pages for {question['id']}")
    selected = rank_chunks(question["question"], chunks, max_chunks)
    context_parts = []
    assets: list[Path] = []
    review_flagged = False
    required_assets = set(question.get("required_asset_paths", []))
    for chunk in selected:
        context_parts.append(
            f"[{chunk['id']}; {chunk['markdown']}; pages {chunk['pages']}; "
            f"source {', '.join(chunk['source_pages'])}]\n{chunk['text']}"
        )
        review_flagged = review_flagged or bool(chunk.get("needs_review"))
        if chunk.get("needs_review") or "image-only" in chunk.get("text", "").casefold():
            relatives = sorted(
                chunk.get("assets", []), key=lambda relative: relative not in required_assets
            )
            for relative in relatives:
                if len(assets) == max_assets:
                    break
                path = version_dir / relative
                if path.suffix.casefold() in _IMAGE_SUFFIXES and path.is_file() and path not in assets:
                    assets.append(path)
    return "\n\n".join(context_parts), assets, review_flagged, version_dir, None


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _report_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(_ROOT.resolve()).as_posix()
    except ValueError:
        return path.name


def _numeric_bundle_context(
    question: dict[str, Any], version_dir: Path, calculator: bool = False
) -> tuple[str, list[Path], bool, Path, dict[str, Any] | None]:
    expected_provenance = question.get("bundle_provenance_sha256")
    if expected_provenance and _sha256(version_dir / "provenance.json") != expected_provenance:
        raise ValueError(f"bundle provenance does not match question {question['id']}")

    context_parts = []
    query_rows = []
    review_flagged = False
    for query in question["numeric_context"]["queries"]:
        relative = Path(query["path"])
        path = version_dir / relative
        if _sha256(path) != query["sha256"]:
            raise ValueError(f"numeric artifact does not match question {question['id']}: {relative}")
        with path.open(newline="") as stream:
            rows = [
                row for row in csv.DictReader(stream)
                if all(row.get(field) == value for field, value in query["filters"].items())
            ]
        expected_rows = int(query.get("expected_rows", 1))
        if len(rows) != expected_rows:
            raise ValueError(
                f"numeric query {question['id']} matched {len(rows)} rows, expected {expected_rows}"
            )
        fields = query.get("fields")
        selected = [
            {field: row.get(field, "") for field in fields} if fields else row
            for row in rows
        ]
        query_rows.append(selected)
        page = query["page"]
        context_parts.append(
            f"[{relative.as_posix()}; page {page}; sha256 {query['sha256']}]\n"
            f"{json.dumps(selected, ensure_ascii=False)}"
        )
        review_flagged = review_flagged or any(
            row.get("confidence") in {"low", "medium"}
            or row.get("verification_status") not in {"reader_agreement", "externally_verified"}
            for row in rows
        )

    calculation = None
    if calculator and question.get("calculation"):
        spec = question["calculation"]
        operands = []
        for operand in spec["operands"]:
            value = query_rows[operand["query_index"]][0][operand["field"]]
            if value != operand["expected"]:
                raise ValueError(
                    f"calculator operand does not match question {question['id']}: {value}"
                )
            operands.append(value)
        if spec["operation"] != "divide" or len(operands) != 2:
            raise ValueError(f"unsupported calculator operation for {question['id']}")
        places = int(spec["round_decimal_places"])
        value = (Decimal(operands[0]) / Decimal(operands[1])).quantize(
            Decimal(1).scaleb(-places)
        )
        calculation = {
            "operation": "divide",
            "operands": operands,
            "round_decimal_places": places,
            "calculator_result": format(value, "f"),
            "operand_selection_valid": True,
            "arithmetic_matches_label": score_answer(format(value, "f"), question["answer"]),
        }
        context_parts.append(
            "[deterministic calculator; operands selected from the pinned queries above]\n"
            f"{json.dumps(calculation, ensure_ascii=False)}"
        )

    assets = []
    for artifact in question["numeric_context"].get("assets", []):
        relative = Path(artifact["path"])
        path = version_dir / relative
        if _sha256(path) != artifact["sha256"]:
            raise ValueError(f"numeric asset does not match question {question['id']}: {relative}")
        assets.append(path)
        context_parts.append(
            f"[{relative.as_posix()}; page {artifact['page']}; attached source crop; "
            f"sha256 {artifact['sha256']}]"
        )
    return "\n\n".join(context_parts), assets, review_flagged, version_dir, calculation


def _source_for(question: dict[str, Any], manifest_path: Path) -> Path:
    manifest = json.loads(manifest_path.read_text())
    source_root = (manifest_path.parent / manifest.get("source_root", ".")).resolve()
    entry = next(
        document for document in manifest["documents"] if document["id"] == question["document_id"]
    )
    source = (source_root / entry["path"]).resolve()
    if not source.is_file():
        raise FileNotFoundError(f"source not found: {source}")
    if content_hash(source) != question["source_sha256"]:
        raise ValueError(f"source hash does not match question {question['id']}")
    return source


def _pdf_context(question: dict[str, Any], manifest_path: Path, temp_dir: Path) -> list[Path]:
    source = _source_for(question, manifest_path)
    pages = []
    with CropRenderer(source, dpi=180) as renderer:
        for page in question["pdf_pages"]:
            path = temp_dir / f"{question['id']}-page-{page}.png"
            renderer.full_page(page, path)
            pages.append(path)
    return pages


def _data_uri(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode()}"


def _parse_reply(text: str) -> tuple[str, list[dict[str, Any]], list[str]]:
    cleaned = _FENCE.sub("", text.strip())
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        return text.strip(), [], []
    return (
        str(value.get("answer", "")).strip(),
        value.get("citations") or [],
        [str(field) for field in value.get("evidence_fields_used") or []],
    )


def _responses_text(response) -> str:
    if response.output_text:
        return response.output_text
    for item in response.output or []:
        for part in getattr(item, "content", []) or []:
            text = getattr(part, "text", None)
            if text:
                return text
    return ""


def _ask(
    client,
    api_mode: str,
    model: str,
    question: str,
    context: str,
    images: list[Path],
    max_tokens: int,
    reasoning_effort: str,
    seed: int,
    report_evidence_fields: bool = False,
):
    response_contract = (
        'Return JSON with exactly three keys: "answer" (a short direct answer), '
        '"citations" (a list of objects containing "path" and "page"), and '
        '"evidence_fields_used" (the exact structured field names used).'
        if report_evidence_fields else
        'Return JSON with exactly two keys: "answer" (a short direct answer) and '
        '"citations" (a list of objects containing "path" and "page").'
    )
    instruction = (
        "Answer only from the provided document evidence. "
        f"{response_contract} If the evidence is insufficient, answer "
        '"insufficient evidence".\n\n'
        "For digitized chart rows, use the point nearest the requested coordinate when the "
        "difference is ordinary extraction rounding.\n\n"
        f"Question: {question}\n\nEvidence:\n{context or '[source PDF page images attached]'}"
    )
    if api_mode == "responses":
        content: list[dict[str, Any]] = [{"type": "input_text", "text": instruction}]
        content.extend(
            {"type": "input_image", "image_url": _data_uri(path)} for path in images
        )
        response = client.responses.create(
            model=model,
            input=[{"role": "user", "content": content}],
            max_output_tokens=max_tokens,
            temperature=0,
            extra_body={"reasoning_effort": reasoning_effort, "seed": seed},
        )
        raw = _responses_text(response)
        usage = getattr(response, "usage", None)
        return raw, {
            "finish_reason": "length" if response.status == "incomplete" else "stop",
            "input_tokens": getattr(usage, "input_tokens", None),
            "output_tokens": getattr(usage, "output_tokens", None),
        }

    content = [{"type": "text", "text": instruction}]
    content.extend(
        {"type": "image_url", "image_url": {"url": _data_uri(path)}} for path in images
    )
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": content}],
        max_tokens=max_tokens,
        reasoning_effort=reasoning_effort,
        seed=seed,
        temperature=0,
    )
    raw = response.choices[0].message.content or ""
    finish_reason = getattr(response.choices[0], "finish_reason", None)
    usage = getattr(response, "usage", None)
    return raw, {
        "finish_reason": finish_reason,
        "input_tokens": getattr(usage, "prompt_tokens", None),
        "output_tokens": getattr(usage, "completion_tokens", None),
    }


def _citation_has_page(citations: list[dict[str, Any]], pages: list[int]) -> bool:
    expected = {str(page) for page in pages}
    return any(str(citation.get("page")) in expected for citation in citations)


def _citation_valid(
    citations: list[dict[str, Any]], pages: list[int], required_paths: list[str]
) -> bool:
    if not _citation_has_page(citations, pages):
        return False
    cited_paths = {str(citation.get("path") or "") for citation in citations}
    return all(
        any(cited == required or cited.endswith(f"/{required}") for cited in cited_paths)
        for required in required_paths
    )


def _required_fields_used(reported: list[str], required: list[str]) -> bool:
    return set(required) <= set(reported)


def _required_assets_opened(
    mode: str, version_dir: Path | None, required: list[str], images: list[Path]
) -> bool:
    if mode == "pdf":
        return True
    if version_dir is None:
        return False
    opened = {str(path.resolve()) for path in images}
    return all(
        str((version_dir / relative).resolve()) in opened for relative in required
    )


def _queue_flags_evidence(question: dict[str, Any], version_dir: Path) -> bool:
    review_path = version_dir / "review.json"
    if not review_path.is_file():
        return False
    review = json.loads(review_path.read_text())
    action_blocks = {
        item["block_id"] for item in review.get("items", [])
        if item.get("disposition") == "action_required"
    }
    return bool(action_blocks.intersection(question.get("evidence_block_ids", [])))


def _result_counts(results: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "runs": len(results),
        "correct": sum(bool(result.get("correct")) for result in results),
        "passed": sum(
            bool(result.get("benchmark_pass", result.get("correct")))
            for result in results
        ),
        "correct_refusals": sum(
            result.get("expected_kind") == "refusal" and bool(result.get("correct"))
            for result in results
        ),
        "incorrect": sum(result.get("outcome") == "incorrect" for result in results),
        "refused": sum(result.get("outcome") == "refused" for result in results),
        "valid_citations": sum(bool(result.get("citation_page_valid")) for result in results),
        "evidence_fields_scored": sum(
            bool(result.get("evidence_fields_scored")) for result in results
        ),
        "valid_evidence_fields": sum(
            bool(
                result.get("evidence_fields_scored")
                and result.get("evidence_fields_valid")
            )
            for result in results
        ),
        "required_assets_scored": sum(
            bool(result.get("required_assets_scored")) for result in results
        ),
        "required_assets_opened": sum(
            bool(
                result.get("required_assets_scored")
                and result.get("required_assets_opened")
            )
            for result in results
        ),
        "calculator_scored": sum(bool(result.get("calculator_scored")) for result in results),
        "calculator_operand_selection_valid": sum(
            bool(result.get("calculator_operand_selection_valid")) for result in results
        ),
        "calculator_arithmetic_valid": sum(
            bool(result.get("calculator_arithmetic_valid")) for result in results
        ),
        "release_blockers": sum(bool(result.get("release_blocker")) for result in results),
        "errors": sum("error" in result for result in results),
        "input_tokens": sum(result.get("input_tokens") or 0 for result in results),
        "output_tokens": sum(result.get("output_tokens") or 0 for result in results),
        "evidence_duration_s": round(
            sum(result.get("evidence_duration_s") or 0 for result in results), 6
        ),
        "inference_duration_s": round(
            sum(result.get("inference_duration_s") or 0 for result in results), 6
        ),
    }


def _group_result_counts(
    results: list[dict[str, Any]], key: str
) -> dict[str, dict[str, Any]]:
    values = sorted({result.get(key, "unspecified") for result in results})
    return {
        value: _result_counts([
            result for result in results if result.get(key, "unspecified") == value
        ])
        for value in values
    }


def summarize_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    summary = _result_counts(results)
    by_mode: dict[str, dict[str, Any]] = {}
    for mode in ("bundle", "pdf"):
        selected = [result for result in results if result.get("mode") == mode]
        if not selected:
            continue
        by_mode[mode] = _result_counts(selected)
        by_mode[mode]["assets_opened"] = sum(
            len(result.get("assets_opened") or []) for result in selected
        )
    summary["by_mode"] = by_mode
    summary["by_representation"] = _group_result_counts(results, "representation")
    summary["by_document_class"] = _group_result_counts(results, "document_class")
    summary["review_queue"] = _review_queue_metrics(results)
    if {"bundle", "pdf"} <= by_mode.keys() and by_mode["pdf"]["input_tokens"]:
        bundle = by_mode["bundle"]
        pdf = by_mode["pdf"]
        summary["comparison"] = {
            "bundle_input_token_reduction": round(
                1 - bundle["input_tokens"] / pdf["input_tokens"], 4
            ),
            "bundle_accuracy_minus_pdf": round(
                bundle["correct"] / bundle["runs"] - pdf["correct"] / pdf["runs"], 4
            ),
        }
    return summary


def evaluate_exit_criteria(summary: dict[str, Any]) -> dict[str, Any] | None:
    by_mode = summary.get("by_mode", {})
    comparison = summary.get("comparison")
    if not comparison or not {"bundle", "pdf"} <= by_mode.keys():
        return None
    accuracy_pass = by_mode["bundle"]["correct"] >= by_mode["pdf"]["correct"]
    context_pass = comparison["bundle_input_token_reduction"] >= _MATERIAL_TOKEN_REDUCTION
    return {
        "minimum_bundle_input_token_reduction": _MATERIAL_TOKEN_REDUCTION,
        "accuracy_pass": accuracy_pass,
        "context_pass": context_pass,
        "passed": accuracy_pass and context_pass,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare bundle and source-PDF question answering.")
    parser.add_argument("--questions", type=Path, default=_QUESTIONS)
    parser.add_argument("--manifest", type=Path, default=_MANIFEST)
    parser.add_argument("--bundle-root", type=Path, action="append", default=[_ROOT / "out"])
    parser.add_argument("--mode", action="append", choices=("bundle", "pdf"), default=[])
    parser.add_argument("--document", action="append", default=[])
    parser.add_argument("--model")
    parser.add_argument("--base-url", default="http://localhost:11434/v1")
    parser.add_argument("--api-key", default="not-needed")
    parser.add_argument(
        "--api-mode",
        choices=("chat", "responses"),
        default="chat",
        help="OpenAI-compatible endpoint shape (default: chat).",
    )
    parser.add_argument("--max-chunks", type=int, default=3)
    parser.add_argument("--max-assets", type=int, default=3)
    parser.add_argument("--max-tokens", type=int, default=2048)
    parser.add_argument(
        "--retrieval",
        choices=("chunks", "passages"),
        default="chunks",
        help="Bundle retrieval records to rank (default: chunks).",
    )
    parser.add_argument(
        "--retrieval-audit",
        action="store_true",
        help="Compare chunk and passage top-k page selection without model inference.",
    )
    parser.add_argument(
        "--retrieval-budget",
        action="append",
        type=int,
        default=[],
        help="Top-k budget for retrieval audit; repeat for several fixed budgets.",
    )
    parser.add_argument(
        "--calculator",
        action="store_true",
        help="Add deterministic results for questions with a pinned calculation spec.",
    )
    parser.add_argument(
        "--reasoning-effort",
        choices=("none", "low", "medium", "high", "max"),
        default="none",
        help="Thinking level sent to the OpenAI-compatible endpoint (default: none).",
    )
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit nonzero on a task failure or failed benchmark exit criterion.",
    )
    parser.add_argument("--check", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()

    question_file = json.loads(args.questions.read_text())
    questions = question_file["questions"]
    if args.document:
        selected = set(args.document)
        questions = [question for question in questions if question["document_id"] in selected]
    roots = [path.resolve() for path in args.bundle_root]
    if args.retrieval_audit:
        budgets = args.retrieval_budget or [args.max_chunks]
        report = audit_retrieval(questions, roots, budgets)
        report["questions_sha256"] = _sha256(args.questions)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n")
        if (args.strict or args.check) and not report["comparison"]["passed"]:
            raise SystemExit(1)
        return
    if not args.mode:
        parser.error("--mode is required unless --retrieval-audit is used")
    if not args.model:
        parser.error("--model is required unless --retrieval-audit is used")

    from openai import OpenAI

    client = OpenAI(base_url=args.base_url, api_key=args.api_key, timeout=300, max_retries=2)
    results = []
    failures = 0
    with tempfile.TemporaryDirectory(prefix="pdf2md-agent-benchmark-") as temp:
        temp_dir = Path(temp)
        for question in questions:
            for mode in args.mode:
                if mode not in question.get("modes", ["bundle", "pdf"]):
                    continue
                try:
                    evidence_started = time.perf_counter()
                    if mode == "bundle":
                        context, images, review_flagged, version_dir, calculation = _bundle_context(
                            question,
                            roots,
                            args.max_chunks,
                            args.max_assets,
                            args.calculator,
                            args.retrieval,
                        )
                        evidence = _report_path(version_dir)
                        citation_pages = question["bundle_pages"]
                        queue_flagged = _queue_flags_evidence(question, version_dir)
                    else:
                        version_dir = None
                        calculation = None
                        context = "\n".join(
                            f"[source.pdf#page={page} is attached as an image]"
                            for page in question["pdf_pages"]
                        )
                        images = _pdf_context(question, args.manifest.resolve(), temp_dir)
                        review_flagged = False
                        evidence = _report_path(
                            _source_for(question, args.manifest.resolve())
                        )
                        citation_pages = question["pdf_pages"]
                        queue_flagged = False
                    evidence_duration_s = time.perf_counter() - evidence_started
                    inference_started = time.perf_counter()
                    raw, usage = _ask(
                        client,
                        args.api_mode,
                        args.model,
                        question["question"],
                        context,
                        images,
                        args.max_tokens,
                        args.reasoning_effort,
                        args.seed,
                        report_evidence_fields=bool(question.get("required_evidence_fields")),
                    )
                    inference_duration_s = time.perf_counter() - inference_started
                    answer, citations, evidence_fields = _parse_reply(raw)
                    outcome = classify_answer(
                        answer, question["answer"], usage["finish_reason"]
                    )
                    correct = outcome == "correct"
                    required_paths = (
                        question.get("required_citation_paths", [])
                        if mode == "bundle" else []
                    )
                    citation_required = question.get("citation_required", True)
                    cited = (
                        _citation_valid(citations, citation_pages, required_paths)
                        if citation_required else True
                    )
                    required_fields = question.get("required_evidence_fields", [])
                    evidence_fields_valid = _required_fields_used(
                        evidence_fields, required_fields
                    )
                    required_assets = question.get("required_asset_paths", [])
                    required_assets_opened = _required_assets_opened(
                        mode, version_dir, required_assets, images
                    )
                    calculation_valid = not calculation or (
                        calculation["operand_selection_valid"]
                        and calculation["arithmetic_matches_label"]
                    )
                    benchmark_pass = (
                        correct
                        and cited
                        and evidence_fields_valid
                        and required_assets_opened
                        and calculation_valid
                    )
                    mandatory_evidence = bool(
                        question.get("numeric_context")
                        or required_paths
                        or required_fields
                        or required_assets
                    )
                    release_blocker = mode == "bundle" and (
                        not benchmark_pass
                        if mandatory_evidence else outcome == "incorrect" and not review_flagged
                    )
                    result = {
                        "question_id": question["id"],
                        "document_id": question["document_id"],
                        "document_class": question.get("document_class", "unspecified"),
                        "representation": question.get("representation", "unspecified"),
                        "mode": mode,
                        "retrieval": args.retrieval if mode == "bundle" else None,
                        "model": args.model,
                        "api_mode": args.api_mode,
                        "reasoning_effort": args.reasoning_effort,
                        "seed": args.seed,
                        "answer": answer,
                        "raw_response": raw,
                        "correct": correct,
                        "benchmark_pass": benchmark_pass,
                        "expected_kind": question["answer"]["kind"],
                        "outcome": outcome,
                        "citations": citations,
                        "citation_page_valid": cited,
                        "evidence_fields_used": evidence_fields,
                        "evidence_fields_scored": bool(required_fields),
                        "evidence_fields_valid": evidence_fields_valid,
                        "required_assets_scored": bool(required_assets),
                        "required_assets_opened": required_assets_opened,
                        "calculator_scored": bool(calculation),
                        "calculator_operand_selection_valid": bool(
                            calculation and calculation["operand_selection_valid"]
                        ),
                        "calculator_arithmetic_valid": bool(
                            calculation and calculation["arithmetic_matches_label"]
                        ),
                        "assets_opened": [_report_path(path) for path in images],
                        "review_flagged": review_flagged,
                        "review_queue_scored": (
                            mode == "bundle" and "expected_review_disposition" in question
                        ),
                        "expected_action_required": (
                            question.get("expected_review_disposition") == "action_required"
                        ),
                        "queue_flagged": queue_flagged,
                        "release_blocker": release_blocker,
                        "evidence": evidence,
                        "evidence_duration_s": round(evidence_duration_s, 6),
                        "inference_duration_s": round(inference_duration_s, 6),
                        **usage,
                    }
                except Exception as exc:  # noqa: BLE001 - record one task failure and continue
                    result = {
                        "question_id": question["id"],
                        "document_id": question["document_id"],
                        "document_class": question.get("document_class", "unspecified"),
                        "representation": question.get("representation", "unspecified"),
                        "mode": mode,
                        "retrieval": args.retrieval if mode == "bundle" else None,
                        "model": args.model,
                        "api_mode": args.api_mode,
                        "reasoning_effort": args.reasoning_effort,
                        "seed": args.seed,
                        "error": str(exc),
                        "correct": False,
                        "benchmark_pass": False,
                        "expected_kind": question["answer"]["kind"],
                        "outcome": "error",
                        "citation_page_valid": False,
                        "assets_opened": [],
                        "review_flagged": False,
                        "review_queue_scored": (
                            mode == "bundle" and "expected_review_disposition" in question
                        ),
                        "expected_action_required": (
                            question.get("expected_review_disposition") == "action_required"
                        ),
                        "calculator_scored": False,
                        "calculator_operand_selection_valid": False,
                        "calculator_arithmetic_valid": False,
                        "release_blocker": mode == "bundle",
                    }
                results.append(result)
                failures += bool(result.get("error") or result.get("release_blocker"))
                mark = "ok" if result.get("benchmark_pass") else "FAIL"
                print(f"[{mark}] {question['id']}/{mode}: {result.get('answer') or result.get('error')}")

    result_summary = summarize_results(results)
    exit_criteria = evaluate_exit_criteria(result_summary)
    summary = {
        "schema_version": 2,
        "questions_sha256": _sha256(args.questions),
        "model": args.model,
        "api_mode": args.api_mode,
        "reasoning_effort": args.reasoning_effort,
        "seed": args.seed,
        "settings": {
            "max_chunks": args.max_chunks,
            "max_assets": args.max_assets,
            "max_tokens": args.max_tokens,
            "temperature": 0,
            "api_mode": args.api_mode,
            "calculator": args.calculator,
            "retrieval": args.retrieval,
        },
        "results": results,
        "summary": result_summary,
        "exit_criteria": exit_criteria,
        "calibration": {
            "status": "not_applicable",
            "reason": "The benchmark emits categorical outcomes, not confidence-like probabilities.",
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, indent=2) + "\n")
    if (args.strict or args.check) and (
        failures or (exit_criteria is not None and not exit_criteria["passed"])
    ):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
