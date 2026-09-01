"""Classify source-to-Markdown token conservation by block and representation.

The document-level numeric totals remain compatible with older profiles. The
block report separates intentional image dependence from unexplained text drift.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

from pdf2md.schema import Block, BlockType, CoverageFlag, CoverageStatus, Document
from pdf2md.tables import render_table

_SCRIPT_TAGS = re.compile(r"</?(?:sub|sup)>")
_WORD = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)*", re.UNICODE)
_NUMBER = re.compile(r"[−‑–-]?(?:\d[\d,]*(?:\.\d+)?|\.\d+)")
_MINUS_CHARS = str.maketrans({"−": "-", "‑": "-", "–": "-"})
_SPACED_DECIMAL = re.compile(r"(?<=\d)[ \t\r\n]+\.[ \t\r\n]+(?=\d)")
_MARKDOWN_DESTINATION = re.compile(r"(?<=\])\([^\n)]*\)")
_HTML_TAG = re.compile(r"<[^>]+>")
_TEX_COMMAND = re.compile(r"\\[A-Za-z]+")
# A marker and every blockquote line continuing it: the detail lines quote the
# source rows a finding is about, and counting those quotes as emitted content
# would report a table's own losses back as additions.
_PDF2MD_MARKER = re.compile(r"^> \*\*\[pdf2md:.*(?:\n>.*)*$", re.MULTILINE)
# Navigation pdf2md emits beside content (links into the bundle's own
# artifacts). Its labels are not words the source page printed.
_EMITTED_NAV = re.compile(r"^\*\[pdf2md\][^\n]*$", re.MULTILINE)
_INTRAWORD_HYPHEN = re.compile(r"([^\W\d_])[-‐‑]\s*([^\W\d_])", re.UNICODE)
_EXAMPLE_LIMIT = 200


def _canon_number(token: str) -> str:
    token = token.translate(_MINUS_CHARS).replace(",", "")
    sign = ""
    if token.endswith("."):
        token = token[:-1]
    if token.startswith("-"):
        sign, token = "-", token[1:]
    if token.startswith("."):
        token = f"0{token}"
    return sign + token


def _raw_numbers(text: str) -> list[str]:
    normalized = _SPACED_DECIMAL.sub(".", unicodedata.normalize("NFKC", text))
    return _NUMBER.findall(normalized)


def _numeric_tokens(text: str) -> Counter[str]:
    return Counter(_canon_number(token) for token in _raw_numbers(text))


def _raw_words(text: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", text)
    while (joined := _INTRAWORD_HYPHEN.sub(r"\1\2", normalized)) != normalized:
        normalized = joined
    return _WORD.findall(normalized)


def _semantic_output(text: str) -> str:
    """Remove syntax introduced by emission while retaining visible content."""
    text = _PDF2MD_MARKER.sub("", text)
    text = _EMITTED_NAV.sub("", text)
    text = _MARKDOWN_DESTINATION.sub("", text)
    text = _SCRIPT_TAGS.sub("", text)
    text = _HTML_TAG.sub(" ", text)
    text = _TEX_COMMAND.sub(" ", text)
    return text


def _token_delta(
    source_tokens: list[str],
    output_tokens: list[str],
    canonicalize,
) -> dict[str, Any]:
    source_by_value: dict[str, Counter[str]] = defaultdict(Counter)
    output_by_value: dict[str, Counter[str]] = defaultdict(Counter)
    for token in source_tokens:
        source_by_value[canonicalize(token)][token] += 1
    for token in output_tokens:
        output_by_value[canonicalize(token)][token] += 1

    source = Counter({value: sum(variants.values()) for value, variants in source_by_value.items()})
    output = Counter({value: sum(variants.values()) for value, variants in output_by_value.items()})
    common = source & output
    exact = sum(
        sum((source_by_value[value] & output_by_value[value]).values())
        for value in common
    )
    losses = source - output
    additions = output - source
    return {
        "source": sum(source.values()),
        "output": sum(output.values()),
        "conserved": sum(common.values()),
        "exact": exact,
        "expected_normalization": sum(common.values()) - exact,
        "losses": losses,
        "additions": additions,
    }


def token_accounting(source_text: str, output_text: str) -> dict[str, dict[str, Any]]:
    """Compare word and number multisets, preserving exact difference counts.

    Both sides get the same treatment. The source of a table is its own rendered
    markup, so an HTML table's `td`, `tr` and `tbody` are tokens on the source
    side and stripped on the output side -- which charged one 29-row table with
    losing 471 words that were never content. Whatever counts as emitted syntax
    has to count as syntax in both readings or the difference is the
    normalization, not the document."""
    source_text = _semantic_output(source_text)
    output_text = _semantic_output(output_text)
    return {
        "words": _token_delta(
            _raw_words(source_text),
            _raw_words(output_text),
            lambda token: token.casefold(),
        ),
        "numbers": _token_delta(
            _raw_numbers(source_text),
            _raw_numbers(output_text),
            _canon_number,
        ),
    }


def numeric_accounting(source_text: str, output_text: str) -> dict[str, Any]:
    """Compatibility whole-document numeric multiset comparison."""
    source = _numeric_tokens(source_text)
    output = _numeric_tokens(output_text)
    missing = source - output
    examples = [
        {"value": value, "count": count}
        for value, count in sorted(missing.items(), key=lambda item: (-item[1], item[0]))[:12]
    ]
    return {
        "source_values": sum(source.values()),
        "distinct_source_values": len(source),
        "conserved_values": sum((source & output).values()),
        "missing_values": sum(missing.values()),
        "missing_examples": examples,
    }


def _bbox_record(block: Block) -> dict[str, float] | None:
    if block.bbox is None:
        return None
    return {
        "x0": block.bbox.x0,
        "y0": block.bbox.y0,
        "x1": block.bbox.x1,
        "y1": block.bbox.y1,
    }


def _source_dependent(block: Block) -> bool:
    return bool(block.extra.get("crop_path")) or block.type is BlockType.FIGURE


def _intentional_formatting(block: Block, emission: dict[str, Any]) -> bool:
    return (
        block.type in {BlockType.PAGE_HEADER, BlockType.PAGE_FOOTER}
        or bool(emission.get("intentional_omission"))
    )


def _empty_counts() -> dict[str, int]:
    return {"words": 0, "numbers": 0}


def _record_examples(
    examples: list[dict[str, Any]],
    *,
    block: Block,
    artifact: str | None,
    kind: str,
    direction: str,
    values: Counter[str],
) -> int:
    truncated = 0
    for value, count in sorted(values.items(), key=lambda item: (-item[1], item[0])):
        if len(examples) >= _EXAMPLE_LIMIT:
            truncated += 1
            continue
        examples.append({
            "kind": kind,
            "direction": direction,
            "value": value,
            "count": count,
            "block_id": block.id,
            "page": block.page,
            "bbox": _bbox_record(block),
            "source_page": f"../source.pdf#page={block.page}",
            "emitted_artifact": artifact,
        })
    return truncated


def representation_accounting(
    doc: Document,
    glyphs,
    emission_index: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    categories = {
        "conserved": _empty_counts(),
        "expected_source_dependent": _empty_counts(),
        "expected_normalization_or_formatting": _empty_counts(),
        "unexplained_loss": _empty_counts(),
        "unexplained_addition": _empty_counts(),
    }
    examples: list[dict[str, Any]] = []
    block_findings: list[dict[str, Any]] = []
    unmeasured_blocks = 0
    preexisting_action_blocks = 0
    truncated = 0
    tables = {table.block_id: table for table in doc.tables}

    for block in doc.blocks:
        if block.coverage_status in {CoverageStatus.FLAGGED, CoverageStatus.DROPPED}:
            preexisting_action_blocks += 1
            continue
        if block.bbox is None or block.id not in emission_index:
            unmeasured_blocks += 1
            continue
        emission = emission_index.get(block.id, {})
        artifact = emission.get("markdown")
        if _source_dependent(block):
            page_chars = glyphs.page_chars(block.page) if block.bbox is not None else None
            if page_chars is None:
                unmeasured_blocks += 1
                continue
            source_text = page_chars.text_region(block.bbox)
            source_counts = {
                "words": len(_raw_words(source_text)),
                "numbers": len(_raw_numbers(source_text)),
            }
            for kind, count in source_counts.items():
                categories["expected_source_dependent"][kind] += count
            continue
        table = tables.get(block.id)
        source_text = table.preformatted or render_table(table) if table is not None else block.text
        source_counts = {
            "words": len(_raw_words(source_text)),
            "numbers": len(_raw_numbers(source_text)),
        }
        if _intentional_formatting(block, emission):
            for kind, count in source_counts.items():
                categories["expected_normalization_or_formatting"][kind] += count
            continue

        accounting = token_accounting(source_text, emission.get("text", ""))
        if block.type is BlockType.EQUATION:
            categories["expected_normalization_or_formatting"]["words"] += source_counts[
                "words"
            ]
            accounting["words"]["exact"] = 0
            accounting["words"]["expected_normalization"] = 0
            accounting["words"]["losses"] = Counter()
            accounting["words"]["additions"] = Counter()
        finding = {
            "block_id": block.id,
            "page": block.page,
            "bbox": _bbox_record(block),
            "source_page": f"../source.pdf#page={block.page}",
            "emitted_artifact": artifact,
            "unexplained_loss": _empty_counts(),
            "unexplained_addition": _empty_counts(),
        }
        for kind, delta in accounting.items():
            categories["conserved"][kind] += delta["exact"]
            categories["expected_normalization_or_formatting"][kind] += delta[
                "expected_normalization"
            ]
            loss_count = sum(delta["losses"].values())
            addition_count = sum(delta["additions"].values())
            categories["unexplained_loss"][kind] += loss_count
            categories["unexplained_addition"][kind] += addition_count
            finding["unexplained_loss"][kind] = loss_count
            finding["unexplained_addition"][kind] = addition_count
            truncated += _record_examples(
                examples,
                block=block,
                artifact=artifact,
                kind=kind,
                direction="loss",
                values=delta["losses"],
            )
            truncated += _record_examples(
                examples,
                block=block,
                artifact=artifact,
                kind=kind,
                direction="addition",
                values=delta["additions"],
            )
        if any(sum(finding[key].values()) for key in ("unexplained_loss", "unexplained_addition")):
            block_findings.append(finding)

    return {
        "schema_version": 1,
        "scope": "enriched logical blocks to emitted Markdown",
        "source_layer_note": (
            "PDF-to-block word recall and whole-document numeric conservation remain "
            "separate signals because PDF region text is not a stable reading-order oracle."
        ),
        "categories": categories,
        "blocks_with_unexplained_changes": len(block_findings),
        "unmeasured_blocks": unmeasured_blocks,
        "preexisting_action_blocks": preexisting_action_blocks,
        "block_findings": block_findings,
        "examples": examples,
        "examples_truncated": truncated,
    }


def _conservation_priority(loss: dict, addition: dict) -> tuple[str, str]:
    if loss["numbers"] or loss["words"] >= 20:
        return "high", "high"
    if loss["words"] or addition["numbers"] or addition["words"] >= 20:
        return "medium", "medium"
    return "low", "low"


def conservation_review_flags(report: dict[str, Any]) -> list[CoverageFlag]:
    """Turn unexplained block-level changes into one action per affected block."""
    flags = []
    for finding in report.get("representation_aware", {}).get("block_findings", []):
        loss = finding["unexplained_loss"]
        addition = finding["unexplained_addition"]
        parts = []
        if sum(loss.values()):
            parts.append(f"loss: {loss['words']} word(s), {loss['numbers']} number(s)")
        if sum(addition.values()):
            parts.append(
                f"addition: {addition['words']} word(s), {addition['numbers']} number(s)"
            )
        reason = "content conservation: unexplained " + "; ".join(parts)
        severity, content_impact = _conservation_priority(loss, addition)
        page = finding["page"]
        flags.append(CoverageFlag(
            finding["block_id"],
            page,
            reason,
            f"> **[pdf2md: action required ({severity}): {reason}; verify against "
            f"[source page {page}](../source.pdf#page={page})]**",
            disposition="action_required",
            severity=severity,
            content_impact=content_impact,
        ))
    return flags


def annotate_conservation_warnings(
    version_dir: Path,
    flags: list[CoverageFlag],
    emission_index: dict[str, dict[str, Any]],
) -> int:
    """Place post-audit warnings immediately before their emitted source blocks."""
    insertions: dict[str, list[tuple[int, str, str]]] = defaultdict(list)
    for flag in flags:
        emission = emission_index.get(flag.block_id)
        if emission is None or emission.get("start") is None or not emission.get("markdown"):
            raise ValueError(f"no emitted Markdown span for conservation flag {flag.block_id}")
        insertions[emission["markdown"]].append(
            (int(emission["start"]), flag.marker_text, flag.block_id)
        )

    for markdown, records in insertions.items():
        path = version_dir / markdown
        text = path.read_text()
        for start, marker, block_id in sorted(records, reverse=True):
            if not 0 <= start <= len(text):
                raise ValueError(f"invalid emitted Markdown span for {block_id}: {start}")
            text = f"{text[:start]}{marker}\n\n{text[start:]}"
        path.write_text(text)
    return sum(len(records) for records in insertions.values())


def numeric_conservation(
    pdf_path: Path,
    md_texts: Iterable[str],
    *,
    force_ocr: bool = False,
    document: Document | None = None,
    emission_index: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Compare the embedded layer with Markdown, optionally classifying each block."""
    from pdf2md.enrich import GlyphIndex

    source_text: list[str] = []
    pages_with_layer = scan_pages = 0
    with GlyphIndex(pdf_path, force_ocr=force_ocr) as glyphs:
        for page_no in range(1, glyphs.page_count + 1):
            page_chars = glyphs.page_chars(page_no)
            if page_chars is None:
                scan_pages += 1
            else:
                pages_with_layer += 1
                source_text.append(page_chars.text_scriptsplit())
        representation = (
            representation_accounting(document, glyphs, emission_index or {})
            if document is not None else None
        )
    if force_ocr or not pages_with_layer:
        reason = (
            "embedded text layer distrusted (--force-ocr)" if force_ocr
            else "no embedded text layer (scanned document)"
        )
        result = {"available": False, "reason": reason}
        if representation is not None:
            result["representation_aware"] = representation
        return result
    result = numeric_accounting("\n".join(source_text), "\n".join(md_texts))
    result["available"] = True
    result["pages_with_text_layer"] = pages_with_layer
    result["scan_pages_skipped"] = scan_pages
    if representation is not None:
        result["representation_aware"] = representation
    return result
