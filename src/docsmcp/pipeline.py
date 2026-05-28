from __future__ import annotations

import json
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dataclasses import asdict as _asdict

from docsmcp import __version__
from docsmcp.engines import docling as docling_engine
from docsmcp.engines import marker as marker_engine
from docsmcp.index.fts import reindex_doc, set_metadata as _set_meta
from docsmcp.metadata import extract_metadata
from docsmcp.postprocess.crossref import enrich_metadata_from_blocks
from docsmcp.store.cache import (
    content_hash,
    doc_dir as get_doc_dir,
    latest_version,
    next_version,
)
from docsmcp.store.schema import BuildInfo, Document, VerifyStatus
from docsmcp.triage import summarize as triage_summarize, triage_pdf
from docsmcp.verify.disagree import (
    overall_agreement,
    per_page_agreement,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


_DEFAULT_SUGGESTED_NEXT = [
    "get_citation",
    "get_outline",
    "list_tables",
    "list_figures",
    "list_equations",
    "search_chunks",
]


def _result_payload(
    target: Path,
    doc: Document,
    verify_summary: dict | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    out = {
        "doc_id": doc.doc_id,
        "doc_id_short": doc.doc_id[:16],
        "version": doc.version,
        "out_dir": str(target),
        "markdown_path": str(target / "document.md"),
        "provenance_path": str(target / "provenance.json"),
        "page_count": doc.page_count,
        "block_count": len(doc.blocks),
        "duration_s": doc.build.duration_s,
        "profile": doc.build.profile,
        "triage": triage_summarize(doc.pages) if doc.pages else None,
        "suggested_next": _DEFAULT_SUGGESTED_NEXT,
    }
    if metadata:
        out["metadata"] = {
            k: v
            for k, v in metadata.items()
            if k in ("title", "authors", "year", "doi", "journal", "volume", "pages")
            and v is not None
        }
    if verify_summary is not None:
        out["verify"] = verify_summary
        out["alt_markdown_path"] = str(target / "document.alt.md")
        out["disagreements_path"] = str(target / "disagreements.json")
    return out


def _load_cached(target: Path) -> dict[str, Any]:
    prov = json.loads((target / "provenance.json").read_text())
    pages = prov.get("pages") or []
    triage = None
    if pages:
        counts: dict[str, int] = {}
        for p in pages:
            c = p.get("classification", "empty")
            counts[c] = counts.get(c, 0) + 1
        triage = counts
    doc_id = prov["doc_id"]
    out: dict[str, Any] = {
        "doc_id": doc_id,
        "doc_id_short": doc_id[:16],
        "version": prov["version"],
        "out_dir": str(target),
        "markdown_path": str(target / "document.md"),
        "provenance_path": str(target / "provenance.json"),
        "page_count": prov["page_count"],
        "block_count": len(prov["blocks"]),
        "duration_s": prov["build"]["duration_s"],
        "profile": prov["build"]["profile"],
        "triage": triage,
        "cached": True,
        "suggested_next": _DEFAULT_SUGGESTED_NEXT,
    }
    # Pull authoritative metadata from index if available
    try:
        from docsmcp.index.fts import list_docs as _list_docs

        for d in _list_docs():
            if d["doc_id"] == doc_id:
                md = {k: d.get(k) for k in ("title", "authors", "year", "doi", "journal", "volume", "pages")}
                md = {k: v for k, v in md.items() if v is not None}
                if md:
                    out["metadata"] = md
                break
    except Exception:
        pass
    dis_path = target / "disagreements.json"
    if dis_path.exists():
        out["alt_markdown_path"] = str(target / "document.alt.md")
        out["disagreements_path"] = str(dis_path)
        out["verify"] = json.loads(dis_path.read_text()).get("summary")
    return out


def transcribe(
    path: str,
    *,
    profile: str = "balanced",
    force: bool = False,
    verify: bool = False,
    verify_visual: bool = False,
    kind: str = "document",
    tags: list[str] | None = None,
    title: str | None = None,
    authors: str | None = None,
    year: int | None = None,
) -> dict[str, Any]:
    src = Path(path).expanduser().resolve()
    if not src.exists():
        raise FileNotFoundError(f"No such file: {src}")
    if not src.is_file():
        raise ValueError(f"Not a file: {src}")

    sha = content_hash(src)
    ddir = get_doc_dir(sha)

    if not force:
        v = latest_version(ddir)
        if v is not None:
            cached_target = ddir / f"v{v}"
            md_ok = (cached_target / "document.md").exists()
            prov_ok = (cached_target / "provenance.json").exists()
            verify_satisfied = (not verify) or (cached_target / "disagreements.json").exists()
            if md_ok and prov_ok and verify_satisfied:
                return _load_cached(cached_target)

    version = next_version(ddir)
    target = ddir / f"v{version}"
    target.mkdir(parents=True, exist_ok=True)

    started = _now_iso()
    t0 = time.monotonic()

    is_pdf = src.suffix.lower() == ".pdf"
    pages_triage = triage_pdf(src) if is_pdf else []

    markdown, blocks, page_count, docling_version = docling_engine.transcribe(src, profile=profile)

    duration = time.monotonic() - t0
    finished = _now_iso()

    if pages_triage and page_count == 0:
        page_count = len(pages_triage)

    doc = Document(
        doc_id=sha,
        source_path=str(src),
        source_sha256=sha,
        version=version,
        page_count=page_count,
        blocks=blocks,
        build=BuildInfo(
            tool_version=__version__,
            engine_versions={"docling": docling_version},
            profile=profile,
            started_at=started,
            finished_at=finished,
            duration_s=duration,
        ),
        pages=pages_triage,
    )

    (target / "document.md").write_text(markdown)
    (target / "provenance.json").write_text(json.dumps(asdict(doc), indent=2, default=str))

    verify_summary = None
    if verify:
        verify_summary = _run_verify(src, doc, markdown, target)

    resolved_meta: dict[str, Any] = {}
    try:
        prov = json.loads((target / "provenance.json").read_text())
        reindex_doc(prov, target)

        blocks = prov.get("blocks", [])
        meta = extract_metadata(blocks)
        crossref = enrich_metadata_from_blocks(blocks)
        if crossref:
            for k in ("title", "authors", "year", "doi", "journal", "volume", "pages"):
                if crossref.get(k):
                    meta[k] = crossref[k]
        if title is not None:
            meta["title"] = title
        if authors is not None:
            meta["authors"] = authors
        if year is not None:
            meta["year"] = year
        _set_meta(
            doc.doc_id,
            title=meta.get("title"),
            authors=meta.get("authors"),
            year=meta.get("year"),
            kind=kind,
            tags=tags,
            doi=meta.get("doi"),
            journal=meta.get("journal"),
            volume=meta.get("volume"),
            pages=meta.get("pages"),
        )
        resolved_meta = meta
    except Exception as e:
        print(f"[docsmcp] WARN: index/metadata update failed: {e}")

    visual_summary: dict[str, Any] | None = None
    if verify_visual:
        try:
            from docsmcp.index.fts import (
                verify_equations as _verify_equations,
                verify_metadata as _verify_metadata,
            )

            visual_summary = {}
            # Metadata first (cheap, no big VLM crops yet — page 1 only)
            md_result = _verify_metadata(doc.doc_id, force=False)
            visual_summary["metadata"] = {
                k: md_result.get(k)
                for k in ("updated", "title", "authors", "year", "skipped", "reason")
                if md_result.get(k) is not None
            }
            eq_result = _verify_equations(doc.doc_id, only_flagged=True)
            visual_summary["equations"] = {
                k: eq_result.get(k)
                for k in ("verified", "confirmed", "mismatch", "missing")
                if eq_result.get(k) is not None
            }
        except Exception as e:
            visual_summary = {"error": str(e)}

    payload = _result_payload(target, doc, verify_summary=verify_summary, metadata=resolved_meta)
    if visual_summary is not None:
        payload["visual_verify"] = visual_summary
    return payload


def ingest_dir(
    path: str,
    *,
    profile: str = "balanced",
    kind: str = "document",
    tags: list[str] | None = None,
    pattern: str = "*.pdf",
    verify: bool = False,
) -> dict[str, Any]:
    """Bulk ingest all files matching `pattern` under `path` (recursive)."""
    root = Path(path).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise ValueError(f"Not a directory: {root}")

    files = sorted(root.rglob(pattern))
    results: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for f in files:
        try:
            r = transcribe(
                str(f), profile=profile, kind=kind, tags=tags, verify=verify
            )
            results.append(
                {
                    "path": str(f),
                    "doc_id": r["doc_id"],
                    "cached": r.get("cached", False),
                    "block_count": r["block_count"],
                    "duration_s": r["duration_s"],
                }
            )
        except Exception as e:
            errors.append({"path": str(f), "error": str(e)})

    return {
        "root": str(root),
        "pattern": pattern,
        "kind": kind,
        "total": len(files),
        "succeeded": len(results),
        "failed": len(errors),
        "results": results,
        "errors": errors,
    }


def _run_verify(src: Path, doc: Document, primary_md: str, target: Path) -> dict[str, Any]:
    """Run Marker as a second engine and compute disagreement vs Docling."""
    alt_md, alt_blocks, _alt_pages, marker_version = marker_engine.transcribe(src, profile=doc.build.profile)

    (target / "document.alt.md").write_text(alt_md)

    a_by_page: dict[int, list[str]] = {}
    for b in doc.blocks:
        a_by_page.setdefault(b.page, []).append(b.text)
    b_by_page: dict[int, list[str]] = {}
    for b in alt_blocks:
        b_by_page.setdefault(b.page, []).append(b.text)

    page_agree = per_page_agreement(a_by_page, b_by_page)
    summary = overall_agreement(page_agree)

    flagged_pages = {p.page for p in page_agree if p.similarity < 0.85}
    for b in doc.blocks:
        if b.page in flagged_pages:
            b.verify = VerifyStatus.DISAGREEMENT

    report = {
        "summary": summary,
        "engines": {"a": "docling", "b": "marker"},
        "engine_versions": {**doc.build.engine_versions, "marker": marker_version},
        "pages": [_asdict(p) for p in page_agree],
    }
    (target / "disagreements.json").write_text(json.dumps(report, indent=2, default=str))

    doc.build.engine_versions["marker"] = marker_version
    (target / "provenance.json").write_text(json.dumps(asdict(doc), indent=2, default=str))

    return summary
