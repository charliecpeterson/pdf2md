"""Measure equation accuracy against hand-labelled ground truth.

    uv run python scripts/eval_equations.py OUT_DIR [--labels FILE] [--check] [--strict]

The labels-free qa.py catches when output silently gets *worse*; it can't say the
LaTeX is *right*. This does, for a small hand-checked set: for each labelled
equation it normalises and scores the engine's LaTeX and the math-OCR
transcription against the known-correct LaTeX (a 0-1 similarity), so you can see
whether the transcription is actually closer to truth than the engine — and
whether a render/engine change helps. Reads outputs only; no reconversion.

The score is a guide, not a grade: LaTeX has many equivalent forms, so normalise
what we can (whitespace, \\text wrappers, \\tag numbers, text-op backslashes) and
read the printed pair, don't just trust the ratio.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

_TAG = re.compile(r"\\tag\s*\{[^}]*\}")
_WRAP = re.compile(r"\\(?:text|operatorname|mathrm|mathbf|mathit|mathbb|boldsymbol|rm)\s*\{?([^{}]*)\}?")
_COMPONENT_WRAP = re.compile(r"\\(?:text|operatorname|rm)\s*\{?([^{}]*)\}?")
_FUNC = re.compile(r"\\(sin|cos|tan|max|min|exp|log|ln)\b")
_SPACE = re.compile(r"\\(?:quad|qquad|[,!;:> ])|\s+")
_DISPLAY = re.compile(r"\$\$(.*?)\$\$", re.DOTALL)
_TRAILING_NUMBER = re.compile(r"(?:\\quad)?\s*\(\d+(?:[-.]\d+)*\)\s*$")
_SCRIPT_GROUP = re.compile(r"([_^])\{([^{}]+)\}")
_COMPONENT_KINDS = (
    "symbol",
    "sign",
    "fraction",
    "delimiter",
    "subscript",
    "superscript",
)
_ROOT = Path(__file__).parent.parent
_DEFAULT_CORPUS = _ROOT / "tests" / "equation_component_corpus.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _report_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(_ROOT.resolve()).as_posix()
    except ValueError:
        return resolved.as_posix()


def _norm(s: str | None) -> str:
    if not s:
        return ""
    s = _TRAILING_NUMBER.sub("", s)
    s = _TAG.sub("", s)
    for _ in range(3):  # unwrap nested \text{...}
        s = _WRAP.sub(r"\1", s)
    s = _FUNC.sub(r"\1", s)                       # \sin -> sin
    s = s.replace(r"\left", "").replace(r"\right", "")
    s = _SPACE.sub("", s)
    return s.lower()


def _ratio(candidate: str | None, truth: str) -> float:
    return round(difflib.SequenceMatcher(None, _norm(candidate), _norm(truth)).ratio(), 3)


def _component_norm(value: str | None) -> str:
    if not value:
        return ""
    normalized = _TRAILING_NUMBER.sub("", value)
    normalized = _TAG.sub("", normalized)
    for _ in range(3):
        normalized = _COMPONENT_WRAP.sub(r"\1", normalized)
    normalized = _FUNC.sub(r"\1", normalized)
    normalized = normalized.replace(r"\left", "").replace(r"\right", "")
    normalized = _SPACE.sub("", normalized).lower()
    normalized = normalized.replace(r"\_", "_").replace("&", "")
    normalized = re.sub(r"\^\{?\\prime\}?", "'", normalized)
    for _ in range(4):
        updated = _SCRIPT_GROUP.sub(r"\1\2", normalized)
        if updated == normalized:
            break
        normalized = updated
    return normalized


def _page_transcription(blocks: dict[str, dict], page: int, truth: str) -> str | None:
    candidates = []
    for block in blocks.values():
        if block.get("page") != page or (block.get("extra") or {}).get("text_source") != "vlm-page":
            continue
        candidates.extend(_DISPLAY.findall(block.get("text", "")))
    return max(candidates, key=lambda candidate: _ratio(candidate, truth), default=None)


def _labelled_equation(blocks: dict[str, dict], label: dict) -> dict | None:
    block = blocks.get(label["block_id"])
    if block is not None and block.get("type") == "equation":
        return {**block, "_label_resolution": "block_id"}
    page = label.get("page")
    if page is None:
        return None
    candidates = [
        candidate
        for candidate in blocks.values()
        if candidate.get("page") == page and candidate.get("type") == "equation"
    ]
    if candidates:
        selected = max(
            candidates,
            key=lambda candidate: _ratio(candidate.get("text"), label["latex"]),
        )
        return {**selected, "_label_resolution": "page_equation"}
    transcription = _page_transcription(blocks, page, label["latex"])
    if transcription is not None:
        return {
            "id": None,
            "type": "page_transcription",
            "page": page,
            "text": "",
            "extra": {"transcribed": transcription},
            "_label_resolution": "page_transcription",
        }
    return None


def _latest_provenances(out_dir: Path) -> dict[str, dict]:
    """Source filename to hash and blocks from its newest complete version."""
    by_source: dict[str, dict] = {}
    for doc_dir in (p for p in out_dir.iterdir() if p.is_dir()):
        versions = sorted(doc_dir.glob("v*/provenance.json"),
                          key=lambda p: int(p.parent.name[1:]) if p.parent.name[1:].isdigit() else -1,
                          reverse=True)
        for prov in versions:
            d = json.loads(prov.read_text())
            src = Path(d.get("source_path", "")).name
            if src and src not in by_source:
                by_source[src] = {
                    "source_sha256": d.get("source_sha256"),
                    "provenance_path": _report_path(prov),
                    "provenance_sha256": _sha256(prov),
                    "blocks": {block["id"]: block for block in d.get("blocks", [])},
                }
            break
    return by_source


def _fact_specification(fact: str | dict) -> tuple[str, int | None]:
    if isinstance(fact, str):
        return fact, None
    return fact["latex"], int(fact.get("count", 1))


def _candidate_metrics(candidate: str | None, label: dict) -> dict:
    facts = []
    component_counts: dict[str, Counter] = {
        kind: Counter() for kind in _COMPONENT_KINDS
    }
    normalized = _component_norm(candidate)
    for kind in _COMPONENT_KINDS:
        for fact in label.get("components", {}).get(kind, []):
            latex, expected_count = _fact_specification(fact)
            fact_normalized = _component_norm(latex)
            actual_count = normalized.count(fact_normalized) if normalized else 0
            exact = actual_count > 0 if expected_count is None else actual_count == expected_count
            component_counts[kind]["total"] += 1
            component_counts[kind]["correct"] += exact
            facts.append({
                "kind": kind,
                "latex": latex,
                "match_rule": "present" if expected_count is None else "exact_count",
                "expected_count": expected_count,
                "actual_count": actual_count,
                "exact": exact,
            })
    available = candidate is not None and bool(str(candidate).strip())
    return {
        "available": available,
        "latex": candidate,
        "similarity": _ratio(candidate, label["latex"]) if available else None,
        "full_exact": available and normalized == _component_norm(label["latex"]),
        "components": {
            kind: {
                "correct": component_counts[kind]["correct"],
                "total": component_counts[kind]["total"],
            }
            for kind in _COMPONENT_KINDS
        },
        "facts": facts,
    }


def _aggregate(records: list[dict], channel: str) -> dict:
    available = [
        record["candidates"][channel]
        for record in records
        if record["status"] == "checked"
        and record["candidates"][channel]["available"]
    ]
    components = {}
    for kind in _COMPONENT_KINDS:
        correct = sum(item["components"][kind]["correct"] for item in available)
        total = sum(item["components"][kind]["total"] for item in available)
        components[kind] = {
            "correct": correct,
            "total": total,
            "accuracy": round(correct / total, 6) if total else None,
        }
    return {
        "labelled": sum(record["status"] == "checked" for record in records),
        "available": len(available),
        "full_exact": sum(item["full_exact"] for item in available),
        "mean_similarity": round(
            sum(item["similarity"] for item in available) / len(available), 6
        ) if available else None,
        "components": components,
    }


def evaluate(out_dir: Path, labels_path: Path) -> dict:
    labels = json.loads(labels_path.read_text())
    documents = _latest_provenances(out_dir)
    records = []
    # A bundle records the path it was converted from, so a document converted
    # from a renamed or staged copy is filed under that name instead of its own.
    # The source hash identifies it whatever the file was called, and the labels
    # already carry it, so fall back to that before declaring a source missing.
    by_hash = {d["source_sha256"]: d for d in documents.values() if d.get("source_sha256")}
    for label in labels:
        document = documents.get(label["source"]) or by_hash.get(label.get("source_sha256"))
        if document is None:
            records.append({**label, "status": "source_missing"})
            continue
        if document.get("source_sha256") != label.get("source_sha256"):
            records.append({**label, "status": "source_hash_mismatch"})
            continue
        block = _labelled_equation(document["blocks"], label)
        if block is None:
            records.append({**label, "status": "equation_missing"})
            continue
        engine = block.get("text") or None
        transcription = (block.get("extra") or {}).get("transcribed")
        selected = transcription if transcription is not None else engine
        records.append({
            "source": label["source"],
            "source_sha256": label["source_sha256"],
            "kind": label.get("kind", "unspecified"),
            "page": label.get("page"),
            "note": label["note"],
            "labelled_block_id": label["block_id"],
            "resolved_block_id": block.get("id"),
            "resolution": block["_label_resolution"],
            "status": "checked",
            "truth": label["latex"],
            "truth_sha256": hashlib.sha256(label["latex"].encode()).hexdigest(),
            "selected_channel": "transcription" if transcription is not None else "engine",
            "candidates": {
                "engine": _candidate_metrics(engine, label),
                "transcription": _candidate_metrics(transcription, label),
                "selected": _candidate_metrics(selected, label),
            },
        })
    checked = [record for record in records if record["status"] == "checked"]
    by_kind = {}
    for kind in sorted({record["kind"] for record in checked}):
        by_kind[kind] = _aggregate(
            [record for record in checked if record["kind"] == kind], "selected"
        )
    return {
        "schema_version": 1,
        "method": "source_labelled_equation_component_scoring",
        "labels_path": labels_path.as_posix(),
        "labels_sha256": _sha256(labels_path),
        "provenance": {
            source: {
                key: document[key]
                for key in ("source_sha256", "provenance_path", "provenance_sha256")
            }
            for source, document in sorted(documents.items())
            if source in {label["source"] for label in labels}
        },
        "labelled": len(labels),
        "checked": len(checked),
        "failures": Counter(
            record["status"] for record in records if record["status"] != "checked"
        ),
        "summary": {
            channel: _aggregate(records, channel)
            for channel in ("engine", "transcription", "selected")
        },
        "selected_by_kind": by_kind,
        "records": records,
    }


def _checked_result(report: dict) -> dict:
    records = []
    for record in report["records"]:
        checked = {
            key: record[key]
            for key in (
                "source",
                "kind",
                "page",
                "note",
                "labelled_block_id",
                "resolved_block_id",
                "resolution",
                "status",
                "truth_sha256",
                "selected_channel",
            )
        }
        selected = record["candidates"]["selected"]
        checked["selected"] = {
            key: selected[key]
            for key in ("available", "similarity", "full_exact")
        }
        checked["selected_failed_facts"] = [
            {
                key: fact[key]
                for key in (
                    "kind",
                    "latex",
                    "match_rule",
                    "expected_count",
                    "actual_count",
                )
            }
            for fact in record["candidates"]["selected"]["facts"]
            if not fact["exact"]
        ]
        records.append(checked)
    return {
        key: report[key]
        for key in (
            "labels_sha256",
            "provenance",
            "labelled",
            "checked",
            "failures",
            "summary",
            "selected_by_kind",
        )
    } | {"records": records}


def check_corpus(root: Path, corpus_path: Path, report: dict) -> bool:
    corpus = json.loads(corpus_path.read_text())
    if corpus.get("schema_version") != 1:
        raise ValueError("unsupported equation component corpus schema_version")
    for name, artifact in corpus["artifacts"].items():
        if _sha256(root / artifact["path"]) != artifact["sha256"]:
            raise ValueError(f"equation component artifact hash mismatch: {name}")
    return _checked_result(report) == corpus["expected"]


def main() -> None:
    ap = argparse.ArgumentParser(description="Score equation transcription vs engine against ground truth.")
    ap.add_argument("out_dir", help="Output root (the `out/` directory).")
    ap.add_argument("--labels", default="tests/equation_labels.json")
    ap.add_argument("--check", action="store_true", help="Check the pinned component corpus.")
    ap.add_argument("--strict", action="store_true", help="Fail on unavailable labels.")
    ap.add_argument("--report", type=Path)
    ap.add_argument("--frozen", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--corpus", type=Path, default=_DEFAULT_CORPUS)
    args = ap.parse_args()
    report = evaluate(Path(args.out_dir), Path(args.labels))
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n")

    eng_scores, tr_scores, helped = [], [], 0
    print(f"{'EQUATION':40} {'ENGINE':>7} {'TRANSCR':>7}  WINNER")
    print("-" * 70)
    for record in report["records"]:
        if record["status"] != "checked":
            print(f"{record['note'][:40]:40} {'—':>7} {'—':>7}  {record['status']}")
            continue
        engine = record["candidates"]["engine"]
        transcription = record["candidates"]["transcription"]
        eng = engine["similarity"]
        tr = transcription["similarity"]
        if eng is not None:
            eng_scores.append(eng)
        winner = "—"
        if tr is not None:
            tr_scores.append(tr)
            if eng is None or tr > eng:
                helped += 1
                winner = "transcription"
            elif eng > tr:
                winner = "engine"
            else:
                winner = "tie"
        engine_text = "—" if eng is None else f"{eng:.3f}"
        transcription_text = "—" if tr is None else f"{tr:.3f}"
        print(
            f"{record['note'][:40]:40} {engine_text:>7} "
            f"{transcription_text:>7}  {winner}"
        )

    n = len(eng_scores)
    print("-" * 70)
    if n:
        em = sum(eng_scores) / n
        print(f"engine mean similarity:        {em:.3f}  (n={n})")
        if tr_scores:
            tm = sum(tr_scores) / len(tr_scores)
            print(f"transcription mean similarity: {tm:.3f}  (n={len(tr_scores)})")
            print(f"transcription closer to truth: {helped}/{len(tr_scores)} equations")
    failures = sum(report["failures"].values())
    if failures:
        print(f"{failures} labelled equation(s) unavailable or source-mismatched")
    if (args.check or args.frozen) and not check_corpus(_ROOT, args.corpus, report):
        raise SystemExit("equation component corpus differs from expected results")
    if args.strict and (failures or report["checked"] == 0):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
