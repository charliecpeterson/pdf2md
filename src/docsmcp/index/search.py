"""Hybrid chunk + doc search (FTS5 + sqlite-vec + RRF + cross-encoder rerank).

Imports infrastructure (`_connect`, `_resolve_doc_id`, `_safe_json`) from
`docsmcp.index.fts`. The umbrella module re-exports the public search functions
so existing callers continue to work without code changes.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from typing import Any

from docsmcp.embed import embedder as _embed
from docsmcp.embed import reranker as _rerank
from docsmcp.postprocess.normalize import normalize_query

_log = logging.getLogger("docsmcp.search")


# --- FTS5 query sanitization ---------------------------------------------------

_FTS_SPECIAL = re.compile(r"[\"'()\[\]{}:.\-+*?/\\,;<>=!@#$%^&|~`]")


def _sanitize_fts_query(query: str) -> str:
    """Make a query safe for FTS5 MATCH.

    FTS5 treats `.`, `-`, `(`, etc. as syntax. The robust fix is to quote any token
    that contains FTS-special punctuation as a phrase. Already-quoted phrases pass
    through. Operators like AND / OR / NOT / NEAR are preserved as-is.
    """
    if not query.strip():
        return query
    if '"' in query:
        return query
    tokens = query.split()
    out: list[str] = []
    operators = {"AND", "OR", "NOT", "NEAR"}
    for tok in tokens:
        if tok.upper() in operators:
            out.append(tok.upper())
            continue
        if _FTS_SPECIAL.search(tok):
            cleaned = tok.replace('"', "").strip(".,;:!?")
            out.append(f'"{cleaned}"' if cleaned else "")
        else:
            out.append(tok)
    return " ".join(x for x in out if x)


# --- Chunk-row helpers --------------------------------------------------------


def _chunk_verify_status(conn: sqlite3.Connection, chunk_id: str) -> str:
    """Worst-status of any block in this chunk: disagreement > flagged > unverified > verified."""
    from docsmcp.index.fts import _safe_json

    row = conn.execute(
        "SELECT block_ids_json FROM chunks WHERE chunk_id = ?", (chunk_id,)
    ).fetchone()
    if not row:
        return "unverified"
    try:
        ids = _safe_json(row["block_ids_json"], [], context="block_ids")
    except Exception:
        return "unverified"
    if not ids:
        return "unverified"
    placeholders = ",".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT DISTINCT verify_status FROM blocks WHERE block_id IN ({placeholders})", ids
    ).fetchall()
    statuses = {r["verify_status"] for r in rows}
    for s in ("disagreement", "flagged", "unverified", "verified"):
        if s in statuses:
            return s
    return "unverified"


def _chunk_refs(conn: sqlite3.Connection, chunk_id: str) -> dict[str, list[str]] | None:
    from docsmcp.index.fts import _safe_json

    row = conn.execute("SELECT refs_json FROM chunks WHERE chunk_id = ?", (chunk_id,)).fetchone()
    if not row or not row["refs_json"]:
        return None
    return _safe_json(row["refs_json"], None, context="refs")


def _chunk_rowid(conn: sqlite3.Connection, chunk_id: str) -> int | None:
    r = conn.execute("SELECT rowid FROM chunks WHERE chunk_id = ?", (chunk_id,)).fetchone()
    return r["rowid"] if r else None


# --- Structural-query helpers -------------------------------------------------


_STRUCTURAL_TERMS = {
    "abstract", "summary",
    "introduction", "intro",
    "background", "motivation",
    "methods", "method", "methodology", "experimental", "computational",
    "results", "findings",
    "discussion", "analysis",
    "conclusion", "conclusions", "perspective",
    "references", "bibliography",
    "appendix",
    "acknowledgments", "acknowledgements",
}

_BOOST_STOPWORDS = {
    "of", "the", "a", "an", "and", "or", "for", "to", "in", "on", "at",
    "by", "with", "from", "as", "is", "are", "was", "were",
}


def _loose_match(a: str, b: str) -> bool:
    """True if two tokens are 'the same word' for boost purposes: exact match or
    prefix relationship where the shorter is at least 4 chars."""
    if a == b:
        return True
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    if len(short) >= 4 and long_.startswith(short):
        return True
    return False


def _section_boost(query: str, section: str | None) -> float:
    """Additive boost (0-0.6) for chunks whose section name matches query tokens."""
    if not section or not query:
        return 0.0
    q_tokens = [
        t.lower()
        for t in re.findall(r"\w+", query)
        if t.lower() not in _BOOST_STOPWORDS and len(t) > 2
    ]
    s_tokens = [
        t.lower()
        for t in re.findall(r"\w+", section)
        if t.lower() not in _BOOST_STOPWORDS and len(t) > 2
    ]
    if not q_tokens or not s_tokens:
        return 0.0
    matched = 0
    for qt in q_tokens:
        for st in s_tokens:
            if _loose_match(qt, st):
                matched += 1
                break
    if matched == 0:
        return 0.0
    coverage = matched / len(q_tokens)
    base = 0.18 * matched
    if coverage >= 0.8:
        base += 0.2
    return min(0.6, base)


def _is_structural_query(query: str) -> bool:
    """True iff every word in the query is a known structural term."""
    if not query:
        return False
    q = query.strip().lower()
    if q in _STRUCTURAL_TERMS:
        return True
    tokens = re.findall(r"\w+", q)
    return bool(tokens) and all(t in _STRUCTURAL_TERMS for t in tokens)


def _rrf_fuse(
    a: dict[int, float], b: dict[int, float], *, k: int = 60
) -> list[tuple[int, float]]:
    """Reciprocal-rank fusion of two ranked lists."""
    a_rank = {rid: i + 1 for i, (rid, _) in enumerate(sorted(a.items(), key=lambda x: -x[1]))}
    b_rank = {rid: i + 1 for i, (rid, _) in enumerate(sorted(b.items(), key=lambda x: -x[1]))}
    keys = set(a) | set(b)
    scored: list[tuple[int, float]] = []
    for rid in keys:
        s = 0.0
        if rid in a_rank:
            s += 1.0 / (k + a_rank[rid])
        if rid in b_rank:
            s += 1.0 / (k + b_rank[rid])
        scored.append((rid, s))
    scored.sort(key=lambda x: -x[1])
    return scored


# --- search_chunks ------------------------------------------------------------


def search_chunks(
    query: str,
    *,
    top_k: int = 5,
    doc_id: str | None = None,
    kind: str | None = None,
    mode: str = "hybrid",
    rerank: bool = True,
    only_verified: bool = False,
    candidates: int = 40,
    compact: bool = False,
) -> list[dict[str, Any]]:
    """Hybrid chunk search: FTS5 + sqlite-vec, RRF fusion, optional cross-encoder rerank.

    mode: "hybrid" (default) | "fts" | "vector"
    """
    from docsmcp.index.fts import _connect, _resolve_doc_id

    conn = _connect()
    try:
        if doc_id:
            doc_id = _resolve_doc_id(conn, doc_id)

        original_query = query
        query = normalize_query(query)
        fts_query = _sanitize_fts_query(query)

        filters = ""
        params: list[Any] = []
        if doc_id:
            filters += " AND c.doc_id = ?"
            params.append(doc_id)
        if kind:
            filters += " AND d.kind = ?"
            params.append(kind)

        fts_hits: dict[int, float] = {}
        vec_hits: dict[int, float] = {}
        fts_degraded = False

        if mode in ("hybrid", "fts"):
            sql = (
                "SELECT c.rowid AS rid, bm25(chunks_fts) AS score "
                "FROM chunks_fts JOIN chunks c ON c.rowid = chunks_fts.rowid "
                "JOIN docs d ON d.doc_id = c.doc_id "
                "WHERE chunks_fts MATCH ?" + filters +
                " ORDER BY score LIMIT ?"
            )
            try:
                for r in conn.execute(sql, [fts_query, *params, candidates]).fetchall():
                    fts_hits[r["rid"]] = -float(r["score"])
            except sqlite3.OperationalError as e:
                fts_degraded = True
                _log.info("FTS5 query failed (%r), falling back to vector-only: %s", fts_query, e)

        if mode in ("hybrid", "vector"):
            embedder = _embed.get_default()
            qv = embedder.embed_query(query)
            sql_v = (
                "SELECT c.rowid AS rid, vec_chunks.distance AS dist "
                "FROM vec_chunks JOIN chunks c ON c.rowid = vec_chunks.rowid "
                "JOIN docs d ON d.doc_id = c.doc_id "
                "WHERE embedding MATCH ? AND k = ?" + filters +
                " ORDER BY dist"
            )
            for r in conn.execute(
                sql_v, [_embed.vector_to_bytes(qv), candidates, *params]
            ).fetchall():
                vec_hits[r["rid"]] = 1.0 - float(r["dist"])

        fused = _rrf_fuse(fts_hits, vec_hits, k=60)
        top_rowids = [rid for rid, _ in fused[: max(candidates, top_k)]]
        if not top_rowids:
            return []

        rows = conn.execute(
            "SELECT c.*, d.title, d.authors, d.year, d.kind, d.source_path "
            f"FROM chunks c JOIN docs d ON d.doc_id = c.doc_id "
            f"WHERE c.rowid IN ({','.join('?' * len(top_rowids))})",
            top_rowids,
        ).fetchall()
        rows_by_rid = {r["rowid"] if "rowid" in r.keys() else None: r for r in rows}
        ordered = [rows_by_rid.get(rid) for rid in top_rowids if rows_by_rid.get(rid)]
        if len(ordered) < len(top_rowids):
            ordered = []
            for rid in top_rowids:
                r = conn.execute(
                    "SELECT c.*, c.rowid AS rowid, d.title, d.authors, d.year, d.kind, d.source_path "
                    "FROM chunks c JOIN docs d ON d.doc_id = c.doc_id WHERE c.rowid = ?",
                    (rid,),
                ).fetchone()
                if r:
                    ordered.append(r)

        results: list[dict[str, Any]] = []
        for r in ordered:
            rid = _chunk_rowid(conn, r["chunk_id"])
            verify = _chunk_verify_status(conn, r["chunk_id"])
            if only_verified and verify in ("disagreement", "flagged"):
                continue
            refs = _chunk_refs(conn, r["chunk_id"])
            if compact:
                snippet = r["text"] or ""
                if len(snippet) > 240:
                    snippet = snippet[:240].rstrip() + "…"
                entry = {
                    "chunk_id": r["chunk_id"],
                    "doc_id": r["doc_id"],
                    "title": r["title"],
                    "page_first": r["page_first"],
                    "page_last": r["page_last"],
                    "section": r["section"],
                    "snippet": snippet,
                    "verify": verify,
                }
                if refs:
                    entry["refs"] = refs
            else:
                entry = {
                    "chunk_id": r["chunk_id"],
                    "doc_id": r["doc_id"],
                    "title": r["title"],
                    "authors": r["authors"],
                    "year": r["year"],
                    "kind": r["kind"],
                    "source_path": r["source_path"],
                    "page_first": r["page_first"],
                    "page_last": r["page_last"],
                    "section": r["section"],
                    "text": r["text"],
                    "token_count": r["token_count"],
                    "verify": verify,
                    "fts_score": fts_hits.get(rid),
                    "vec_score": vec_hits.get(rid),
                }
                if refs:
                    entry["refs"] = refs
            results.append(entry)

        if rerank and results:
            reranker = _rerank.get_default()
            passages = [x.get("text") or x.get("snippet") or "" for x in results]
            ranked = reranker.rerank(original_query, passages)
            new_order: list[dict[str, Any]] = []
            seen: set[int] = set()
            for h in ranked:
                if h.idx in seen:
                    continue
                seen.add(h.idx)
                item = results[h.idx]
                item["rerank_score"] = h.score
                new_order.append(item)
            results = new_order

        boosted_any = False
        for item in results:
            boost = _section_boost(original_query, item.get("section"))
            if boost > 0:
                item["section_boost"] = boost
                base = item.get("rerank_score")
                if base is None:
                    base = item.get("vec_score") or 0.0
                item["adjusted_score"] = float(base) + boost
                boosted_any = True
            else:
                item["adjusted_score"] = float(
                    item.get("rerank_score") if item.get("rerank_score") is not None
                    else (item.get("vec_score") or 0.0)
                )
        if boosted_any:
            results.sort(key=lambda x: -x.get("adjusted_score", 0.0))

        if results and fts_degraded:
            results[0]["_fts_degraded"] = True

        return results[:top_k]
    finally:
        conn.close()


# --- search_docs --------------------------------------------------------------


def search_docs(
    query: str,
    *,
    top_k: int = 5,
    kind: str | None = None,
    rerank: bool = True,
    chunks_per_doc: int = 3,
    candidates: int = 60,
) -> list[dict[str, Any]]:
    """Doc-level search: aggregate chunk hits per document, return top docs."""
    hits = search_chunks(
        query,
        top_k=candidates,
        kind=kind,
        rerank=rerank,
        candidates=candidates,
    )
    by_doc: dict[str, list[dict[str, Any]]] = {}
    for h in hits:
        by_doc.setdefault(h["doc_id"], []).append(h)

    docs: list[dict[str, Any]] = []
    for doc_id, items in by_doc.items():
        primary_score = items[0].get("rerank_score") if items else None
        if primary_score is None:
            primary_score = max((i.get("vec_score") or 0.0) for i in items)
        score = primary_score + 0.1 * len(items)
        docs.append(
            {
                "doc_id": doc_id,
                "title": items[0]["title"],
                "authors": items[0]["authors"],
                "year": items[0]["year"],
                "kind": items[0]["kind"],
                "source_path": items[0]["source_path"],
                "match_count": len(items),
                "score": score,
                "top_chunks": items[:chunks_per_doc],
            }
        )
    docs.sort(key=lambda d: -d["score"])
    return docs[:top_k]
