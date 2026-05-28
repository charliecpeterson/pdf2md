from __future__ import annotations

import json
import logging
import re
import sqlite3
from pathlib import Path
from typing import Any

_log = logging.getLogger("docsmcp.index")


def _safe_json(
    value: str | None,
    default: Any,
    *,
    context: str = "",
) -> Any:
    """Parse JSON safely. Returns `default` on missing or malformed input.

    Corrupted cells (truncated, null-byte-injected, malformed quote) would otherwise
    crash whatever called us. Log once, return the default, keep the row in results.
    """
    if not value:
        return default
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError, ValueError) as e:
        _log.warning(
            "malformed JSON column (%s): %s — returning default", context or "?", e
        )
        return default

import numpy as np
import sqlite_vec

from docsmcp.chunk import Chunk, chunk_blocks
from docsmcp.embed import embedder as _embed
from docsmcp.embed import reranker as _rerank
from docsmcp.postprocess.equations import parse_equations
from docsmcp.postprocess.figures import parse_figures
from docsmcp.postprocess.tables import parse_tables
from docsmcp.postprocess.xref import extract_refs
from docsmcp.postprocess.normalize import normalize_query, normalize_text
from docsmcp.store.cache import doc_dir as get_doc_dir, latest_version, out_root

_SCHEMA = """
CREATE TABLE IF NOT EXISTS docs (
    doc_id TEXT PRIMARY KEY,
    source_path TEXT NOT NULL,
    source_sha256 TEXT NOT NULL,
    version INTEGER NOT NULL,
    page_count INTEGER NOT NULL,
    profile TEXT,
    transcribed_at TEXT,
    out_dir TEXT NOT NULL,
    kind TEXT DEFAULT 'document',
    title TEXT,
    authors TEXT,
    year INTEGER,
    tags TEXT
);

CREATE TABLE IF NOT EXISTS blocks (
    block_id TEXT NOT NULL,
    doc_id TEXT NOT NULL,
    rowid_local INTEGER NOT NULL,
    page INTEGER NOT NULL,
    block_type TEXT NOT NULL,
    text TEXT NOT NULL,
    engine TEXT,
    verify_status TEXT,
    bbox_json TEXT,
    PRIMARY KEY (doc_id, rowid_local),
    FOREIGN KEY (doc_id) REFERENCES docs(doc_id)
);

CREATE VIRTUAL TABLE IF NOT EXISTS blocks_fts USING fts5(
    text,
    content='blocks',
    content_rowid='rowid',
    tokenize='unicode61 remove_diacritics 2'
);

CREATE TRIGGER IF NOT EXISTS blocks_ai AFTER INSERT ON blocks BEGIN
    INSERT INTO blocks_fts(rowid, text) VALUES (new.rowid, new.text);
END;
CREATE TRIGGER IF NOT EXISTS blocks_ad AFTER DELETE ON blocks BEGIN
    INSERT INTO blocks_fts(blocks_fts, rowid, text) VALUES('delete', old.rowid, old.text);
END;

CREATE TABLE IF NOT EXISTS chunks (
    chunk_id TEXT PRIMARY KEY,
    doc_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    text TEXT NOT NULL,
    token_count INTEGER NOT NULL,
    page_first INTEGER NOT NULL,
    page_last INTEGER NOT NULL,
    block_ids_json TEXT NOT NULL,
    section TEXT,
    prev_chunk_id TEXT,
    next_chunk_id TEXT,
    embed_model TEXT,
    refs_json TEXT,
    FOREIGN KEY (doc_id) REFERENCES docs(doc_id)
);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    text,
    content='chunks',
    content_rowid='rowid',
    tokenize='unicode61 remove_diacritics 2'
);

CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
    INSERT INTO chunks_fts(rowid, text) VALUES (new.rowid, new.text);
END;
CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES('delete', old.rowid, old.text);
END;

CREATE INDEX IF NOT EXISTS idx_blocks_doc_page ON blocks(doc_id, page);
CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_id);
CREATE INDEX IF NOT EXISTS idx_chunks_seq ON chunks(doc_id, seq);

CREATE TABLE IF NOT EXISTS equations (
    eq_id TEXT PRIMARY KEY,
    doc_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    number TEXT,
    inferred INTEGER DEFAULT 0,
    latex_raw TEXT NOT NULL,
    latex_clean TEXT NOT NULL,
    page INTEGER NOT NULL,
    block_id TEXT NOT NULL,
    bbox_json TEXT,
    context_before TEXT,
    context_after TEXT,
    FOREIGN KEY (doc_id) REFERENCES docs(doc_id)
);
CREATE INDEX IF NOT EXISTS idx_eq_doc ON equations(doc_id);
CREATE INDEX IF NOT EXISTS idx_eq_doc_number ON equations(doc_id, number);

CREATE TABLE IF NOT EXISTS figures (
    figure_id TEXT PRIMARY KEY,
    doc_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    number TEXT,
    inferred INTEGER DEFAULT 0,
    flags TEXT,
    caption TEXT,
    panels_json TEXT,
    page INTEGER NOT NULL,
    block_id TEXT NOT NULL,
    bbox_json TEXT,
    context_before TEXT,
    context_after TEXT,
    FOREIGN KEY (doc_id) REFERENCES docs(doc_id)
);
CREATE INDEX IF NOT EXISTS idx_fig_doc ON figures(doc_id);
CREATE INDEX IF NOT EXISTS idx_fig_doc_number ON figures(doc_id, number);

CREATE TABLE IF NOT EXISTS doc_tables (
    table_id TEXT PRIMARY KEY,
    doc_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    number TEXT,
    inferred INTEGER DEFAULT 0,
    flags TEXT,
    caption TEXT,
    markdown TEXT NOT NULL,
    n_rows INTEGER,
    n_cols INTEGER,
    page INTEGER NOT NULL,
    block_id TEXT NOT NULL,
    bbox_json TEXT,
    context_before TEXT,
    context_after TEXT,
    footnotes_raw TEXT,
    footnotes_json TEXT,
    FOREIGN KEY (doc_id) REFERENCES docs(doc_id)
);
CREATE INDEX IF NOT EXISTS idx_tab_doc ON doc_tables(doc_id);
CREATE INDEX IF NOT EXISTS idx_tab_doc_number ON doc_tables(doc_id, number);

CREATE TABLE IF NOT EXISTS table_cell_verifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id TEXT NOT NULL,
    table_id TEXT NOT NULL,
    row_idx INTEGER NOT NULL,
    col_idx INTEGER NOT NULL,
    expected_text TEXT,
    vlm_text TEXT,
    vlm_confidence TEXT,
    vlm_raw TEXT,
    match INTEGER,
    verified_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(table_id, row_idx, col_idx)
);
CREATE INDEX IF NOT EXISTS idx_cellv_table ON table_cell_verifications(table_id);
CREATE INDEX IF NOT EXISTS idx_cellv_doc ON table_cell_verifications(doc_id);
"""


def _db_path() -> Path:
    return out_root() / "library.sqlite"


def _connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.executescript(_SCHEMA)
    _ensure_vec_table(conn)
    return conn


# Named, versioned migrations. Each entry is (id, sql). Once applied, the id is
# recorded in `schema_migrations` and never re-run. Add new migrations only at the
# end of this list — id is the stable key, not order in the file.
_MIGRATIONS: tuple[tuple[str, str], ...] = (
    ("equations.inferred", "ALTER TABLE equations ADD COLUMN inferred INTEGER DEFAULT 0"),
    ("equations.flags", "ALTER TABLE equations ADD COLUMN flags TEXT"),
    ("docs.doi", "ALTER TABLE docs ADD COLUMN doi TEXT"),
    ("docs.journal", "ALTER TABLE docs ADD COLUMN journal TEXT"),
    ("docs.volume", "ALTER TABLE docs ADD COLUMN volume TEXT"),
    ("docs.pages", "ALTER TABLE docs ADD COLUMN pages TEXT"),
    ("chunks.refs_json", "ALTER TABLE chunks ADD COLUMN refs_json TEXT"),
    ("figures.panels_json", "ALTER TABLE figures ADD COLUMN panels_json TEXT"),
    ("doc_tables.footnotes_raw", "ALTER TABLE doc_tables ADD COLUMN footnotes_raw TEXT"),
    ("doc_tables.footnotes_json", "ALTER TABLE doc_tables ADD COLUMN footnotes_json TEXT"),
    ("equations.vlm_verified", "ALTER TABLE equations ADD COLUMN vlm_verified INTEGER DEFAULT 0"),
    ("equations.vlm_number", "ALTER TABLE equations ADD COLUMN vlm_number TEXT"),
    ("equations.vlm_confidence", "ALTER TABLE equations ADD COLUMN vlm_confidence TEXT"),
    ("equations.vlm_raw", "ALTER TABLE equations ADD COLUMN vlm_raw TEXT"),
    ("doc_tables.vlm_caption_match", "ALTER TABLE doc_tables ADD COLUMN vlm_caption_match INTEGER DEFAULT NULL"),
    ("doc_tables.vlm_caption_confidence", "ALTER TABLE doc_tables ADD COLUMN vlm_caption_confidence TEXT"),
    ("doc_tables.vlm_caption_raw", "ALTER TABLE doc_tables ADD COLUMN vlm_caption_raw TEXT"),
    ("figures.vlm_caption_match", "ALTER TABLE figures ADD COLUMN vlm_caption_match INTEGER DEFAULT NULL"),
    ("figures.vlm_caption_confidence", "ALTER TABLE figures ADD COLUMN vlm_caption_confidence TEXT"),
    ("figures.vlm_caption_raw", "ALTER TABLE figures ADD COLUMN vlm_caption_raw TEXT"),
    ("equations.vlm_latex_match", "ALTER TABLE equations ADD COLUMN vlm_latex_match INTEGER DEFAULT NULL"),
    ("equations.vlm_latex_confidence", "ALTER TABLE equations ADD COLUMN vlm_latex_confidence TEXT"),
    ("equations.vlm_latex_raw", "ALTER TABLE equations ADD COLUMN vlm_latex_raw TEXT"),
)


def _ensure_vec_table(conn: sqlite3.Connection) -> None:
    model = _embed.MODELS[_embed.DEFAULT_MODEL]
    conn.execute(
        f"CREATE VIRTUAL TABLE IF NOT EXISTS vec_chunks USING vec0(embedding float[{model.dim}])"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "  id TEXT PRIMARY KEY, "
        "  applied_at TEXT NOT NULL DEFAULT (datetime('now'))"
        ")"
    )
    applied = {
        r[0] for r in conn.execute("SELECT id FROM schema_migrations").fetchall()
    }
    new_applied = False
    for mid, sql in _MIGRATIONS:
        if mid in applied:
            continue
        try:
            conn.execute(sql)
            conn.execute("INSERT INTO schema_migrations(id) VALUES (?)", (mid,))
            new_applied = True
        except sqlite3.OperationalError as e:
            # "duplicate column name" means the column was added in a pre-versioned
            # run; record the migration so we don't try again.
            msg = str(e).lower()
            if "duplicate column" in msg:
                conn.execute("INSERT INTO schema_migrations(id) VALUES (?)", (mid,))
                new_applied = True
            else:
                _log.error("migration %s failed: %s", mid, e)
                raise
    # Commit migrations immediately so other connections see them and don't
    # try to re-apply (which would dead-lock on the duplicate-column path).
    if new_applied:
        conn.commit()


def _bytes_to_vec(b: bytes, dim: int) -> np.ndarray:
    return np.frombuffer(b, dtype=np.float32).reshape(dim)


def reindex_doc(provenance: dict[str, Any], out_dir: Path) -> dict[str, int]:
    """Index blocks and chunks for one doc. Embeds chunks if not yet embedded."""
    conn = _connect()
    try:
        doc_id = provenance["doc_id"]
        # Preserve user-set metadata (kind, tags, title, authors, year) across reindex.
        existing = conn.execute(
            "SELECT kind, tags, title, authors, year FROM docs WHERE doc_id = ?",
            (doc_id,),
        ).fetchone()
        prev_kind = existing["kind"] if existing else "document"
        prev_tags = existing["tags"] if existing else None
        prev_title = existing["title"] if existing else None
        prev_authors = existing["authors"] if existing else None
        prev_year = existing["year"] if existing else None

        _delete_doc_rows(conn, doc_id)

        conn.execute(
            "INSERT INTO docs(doc_id, source_path, source_sha256, version, page_count, profile, "
            "transcribed_at, out_dir, kind, tags, title, authors, year) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                doc_id,
                provenance.get("source_path", ""),
                provenance.get("source_sha256", doc_id),
                provenance["version"],
                provenance["page_count"],
                provenance.get("build", {}).get("profile"),
                provenance.get("build", {}).get("finished_at"),
                str(out_dir),
                prev_kind,
                prev_tags,
                prev_title,
                prev_authors,
                prev_year,
            ),
        )

        n_blocks = 0
        normalized_blocks = []
        for i, b in enumerate(provenance.get("blocks", [])):
            raw = (b.get("text") or "").strip()
            btype = (b.get("type") or "other").lower()
            # Include figure/table blocks even without text so their bbox is queryable
            # (get_block_image etc. depend on a row existing in the blocks table).
            if not raw and btype not in ("figure", "table"):
                continue
            text = normalize_text(raw) if raw else ""
            bbox = b.get("bbox")
            conn.execute(
                "INSERT INTO blocks(block_id, doc_id, rowid_local, page, block_type, text, engine, verify_status, bbox_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    b.get("id", f"{doc_id[:8]}-{i}"),
                    doc_id,
                    i,
                    int(b.get("page", 0)),
                    btype,
                    text,
                    b.get("engine", "unknown"),
                    b.get("verify", "unverified"),
                    json.dumps(bbox) if bbox else None,
                ),
            )
            n_blocks += 1
            if raw:
                normalized_blocks.append({**b, "text": text})

        chunks = chunk_blocks(doc_id, normalized_blocks)
        n_chunks = 0
        if chunks:
            embedder = _embed.get_default()
            texts = [c.text for c in chunks]
            vecs = embedder.embed_passages(texts)
            for c, v in zip(chunks, vecs):
                refs = extract_refs(c.text)
                refs_payload = (
                    json.dumps(refs)
                    if (refs["equations"] or refs["tables"] or refs["figures"])
                    else None
                )
                conn.execute(
                    "INSERT INTO chunks(chunk_id, doc_id, seq, text, token_count, page_first, page_last, "
                    "block_ids_json, section, prev_chunk_id, next_chunk_id, embed_model, refs_json) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        c.chunk_id,
                        c.doc_id,
                        c.seq,
                        c.text,
                        c.token_count,
                        c.page_first,
                        c.page_last,
                        json.dumps(c.block_ids),
                        c.section,
                        c.prev_chunk_id,
                        c.next_chunk_id,
                        embedder.model.name,
                        refs_payload,
                    ),
                )
                rowid = conn.execute(
                    "SELECT rowid FROM chunks WHERE chunk_id = ?", (c.chunk_id,)
                ).fetchone()[0]
                conn.execute(
                    "INSERT INTO vec_chunks(rowid, embedding) VALUES (?, ?)",
                    (rowid, _embed.vector_to_bytes(v)),
                )
                n_chunks += 1

        equations = parse_equations(doc_id, provenance.get("blocks", []))
        for eq in equations:
            conn.execute(
                "INSERT INTO equations(eq_id, doc_id, seq, number, inferred, flags, latex_raw, latex_clean, "
                "page, block_id, bbox_json, context_before, context_after) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    eq.eq_id,
                    eq.doc_id,
                    eq.seq,
                    eq.number,
                    1 if eq.inferred else 0,
                    json.dumps(eq.flags) if eq.flags else None,
                    eq.latex_raw,
                    eq.latex_clean,
                    eq.page,
                    eq.block_id,
                    eq.bbox_json,
                    eq.context_before,
                    eq.context_after,
                ),
            )

        tables = parse_tables(doc_id, provenance.get("blocks", []))
        for t in tables:
            conn.execute(
                "INSERT INTO doc_tables(table_id, doc_id, seq, number, inferred, flags, caption, markdown, "
                "n_rows, n_cols, page, block_id, bbox_json, context_before, context_after, "
                "footnotes_raw, footnotes_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    t.table_id,
                    t.doc_id,
                    t.seq,
                    t.number,
                    1 if t.inferred else 0,
                    json.dumps(t.flags) if t.flags else None,
                    t.caption,
                    t.markdown,
                    t.n_rows,
                    t.n_cols,
                    t.page,
                    t.block_id,
                    t.bbox_json,
                    t.context_before,
                    t.context_after,
                    t.footnotes_raw,
                    json.dumps(t.footnotes) if t.footnotes else None,
                ),
            )

        figures = parse_figures(doc_id, provenance.get("blocks", []))
        for f in figures:
            conn.execute(
                "INSERT INTO figures(figure_id, doc_id, seq, number, inferred, flags, caption, panels_json, "
                "page, block_id, bbox_json, context_before, context_after) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    f.figure_id,
                    f.doc_id,
                    f.seq,
                    f.number,
                    1 if f.inferred else 0,
                    json.dumps(f.flags) if f.flags else None,
                    f.caption,
                    json.dumps(f.panels) if f.panels else None,
                    f.page,
                    f.block_id,
                    f.bbox_json,
                    f.context_before,
                    f.context_after,
                ),
            )

        conn.commit()
        return {
            "blocks": n_blocks,
            "chunks": n_chunks,
            "equations": len(equations),
            "tables": len(tables),
            "figures": len(figures),
        }
    finally:
        conn.close()


def _search_caption(
    table: str,
    query: str,
    *,
    doc_id: str | None,
    top_k: int,
    extra_cols: tuple[str, ...] = (),
) -> list[dict[str, Any]]:
    """Generic substring search over a caption-bearing table (figures / doc_tables / equations).

    Captions/context are short, so LIKE %query% is plenty.
    """
    if not query.strip():
        return []
    conn = _connect()
    try:
        if doc_id:
            doc_id = _resolve_doc_id(conn, doc_id)
        # Build searchable text: caption + context_before + context_after (figures/tables)
        # or latex_clean + context_before + context_after (equations).
        if table == "equations":
            text_expr = "COALESCE(latex_clean, '') || ' ' || COALESCE(context_before, '') || ' ' || COALESCE(context_after, '')"
        else:
            text_expr = "COALESCE(caption, '') || ' ' || COALESCE(context_before, '') || ' ' || COALESCE(context_after, '')"
        sql = f"SELECT * FROM {table} WHERE LOWER({text_expr}) LIKE LOWER(?)"
        params: list[Any] = [f"%{query.strip()}%"]
        if doc_id:
            sql += " AND doc_id = ?"
            params.append(doc_id)
        sql += " ORDER BY seq LIMIT ?"
        params.append(top_k)
        rows = conn.execute(sql, params).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            entry = {"doc_id": r["doc_id"], "number": r["number"], "page": r["page"]}
            for k in extra_cols:
                entry[k] = r[k]
            out.append(entry)
        return out
    finally:
        conn.close()


def search_figures(query: str, *, doc_id: str | None = None, top_k: int = 10) -> list[dict[str, Any]]:
    return _search_caption(
        "figures", query, doc_id=doc_id, top_k=top_k,
        extra_cols=("figure_id", "caption"),
    )


def search_tables(query: str, *, doc_id: str | None = None, top_k: int = 10) -> list[dict[str, Any]]:
    return _search_caption(
        "doc_tables", query, doc_id=doc_id, top_k=top_k,
        extra_cols=("table_id", "caption", "n_rows", "n_cols"),
    )


def search_equations(query: str, *, doc_id: str | None = None, top_k: int = 10) -> list[dict[str, Any]]:
    return _search_caption(
        "equations", query, doc_id=doc_id, top_k=top_k,
        extra_cols=("eq_id", "latex_clean"),
    )


def get_chunks_by_pages(doc_id: str, page_first: int, page_last: int) -> list[dict[str, Any]]:
    """Return all chunks whose page_first falls in [page_first, page_last], in seq order."""
    conn = _connect()
    try:
        doc_id = _resolve_doc_id(conn, doc_id)
        rows = conn.execute(
            "SELECT c.*, d.title, d.kind FROM chunks c JOIN docs d ON d.doc_id = c.doc_id "
            "WHERE c.doc_id = ? AND c.page_first >= ? AND c.page_first <= ? ORDER BY c.seq",
            (doc_id, int(page_first), int(page_last)),
        ).fetchall()
        return [
            {
                "chunk_id": r["chunk_id"],
                "doc_id": r["doc_id"],
                "title": r["title"],
                "kind": r["kind"],
                "page_first": r["page_first"],
                "page_last": r["page_last"],
                "section": r["section"],
                "text": r["text"],
                "token_count": r["token_count"],
            }
            for r in rows
        ]
    finally:
        conn.close()


def get_outline(doc_id: str) -> list[dict[str, Any]]:
    """Return a list of heading entries: {text, page, depth, kind, block_id}.

    Headings are classified by pattern (part / chapter / section / subsection /
    exercise / problem / appendix / front_matter / figure_caption / heading).
    Depth is inferred from numbering when available; chapters nest under parts,
    sections under chapters. Sentence-fragment 'headings' (paragraph openers,
    long prose ending in punctuation) are filtered out.
    """
    from docsmcp.postprocess.outline import filter_outline

    conn = _connect()
    try:
        doc_id = _resolve_doc_id(conn, doc_id)
        rows = conn.execute(
            "SELECT block_id, text, page, rowid_local FROM blocks "
            "WHERE doc_id = ? AND block_type = 'heading' ORDER BY rowid_local",
            (doc_id,),
        ).fetchall()
        return filter_outline([dict(r) for r in rows])
    finally:
        conn.close()


def list_figures(
    doc_id: str, *, page: int | None = None, summary: bool = False
) -> list[dict[str, Any]]:
    conn = _connect()
    try:
        doc_id = _resolve_doc_id(conn, doc_id)
        sql = "SELECT * FROM figures WHERE doc_id = ?"
        params: list[Any] = [doc_id]
        if page is not None:
            sql += " AND page = ?"
            params.append(page)
        sql += " ORDER BY seq"
        rows = conn.execute(sql, params).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            entry = {
                "figure_id": r["figure_id"],
                "number": r["number"],
                "inferred": bool(r["inferred"]),
                "flags": _safe_json(r["flags"], [], context="flags"),
                "caption": r["caption"],
                "panels": _safe_json(r["panels_json"], [], context="panels"),
                "page": r["page"],
            }
            keys = r.keys()
            if "vlm_caption_match" in keys and r["vlm_caption_match"] is not None:
                entry["vlm_caption"] = {
                    "match": bool(r["vlm_caption_match"]),
                    "confidence": r["vlm_caption_confidence"] if "vlm_caption_confidence" in keys else None,
                }
            if not summary:
                entry["block_id"] = r["block_id"]
                entry["bbox"] = _safe_json(r["bbox_json"], None, context="bbox")
                entry["context_before"] = r["context_before"]
                entry["context_after"] = r["context_after"]
            out.append(entry)
        return out
    finally:
        conn.close()


def get_figure(
    doc_id: str, number: str | None = None, *, figure_id: str | None = None
) -> dict[str, Any] | None:
    conn = _connect()
    try:
        doc_id = _resolve_doc_id(conn, doc_id)
        if figure_id:
            row = conn.execute("SELECT * FROM figures WHERE figure_id = ?", (figure_id,)).fetchone()
        elif number is not None:
            row = conn.execute(
                "SELECT * FROM figures WHERE doc_id = ? AND number = ? ORDER BY seq LIMIT 1",
                (doc_id, str(number)),
            ).fetchone()
        else:
            return None
        if not row:
            return None
        return {
            "figure_id": row["figure_id"],
            "doc_id": row["doc_id"],
            "number": row["number"],
            "inferred": bool(row["inferred"]),
            "flags": _safe_json(row["flags"], [], context="flags"),
            "caption": row["caption"],
            "panels": _safe_json(row["panels_json"], [], context="panels"),
            "page": row["page"],
            "block_id": row["block_id"],
            "bbox": _safe_json(row["bbox_json"], None, context="bbox"),
            "context_before": row["context_before"],
            "context_after": row["context_after"],
        }
    finally:
        conn.close()


def list_tables(
    doc_id: str, *, page: int | None = None, summary: bool = False
) -> list[dict[str, Any]]:
    conn = _connect()
    try:
        doc_id = _resolve_doc_id(conn, doc_id)
        sql = "SELECT * FROM doc_tables WHERE doc_id = ?"
        params: list[Any] = [doc_id]
        if page is not None:
            sql += " AND page = ?"
            params.append(page)
        sql += " ORDER BY seq"
        rows = conn.execute(sql, params).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            entry = {
                "table_id": r["table_id"],
                "number": r["number"],
                "inferred": bool(r["inferred"]),
                "flags": _safe_json(r["flags"], [], context="flags"),
                "caption": r["caption"],
                "n_rows": r["n_rows"],
                "n_cols": r["n_cols"],
                "page": r["page"],
            }
            keys = r.keys()
            if "vlm_caption_match" in keys and r["vlm_caption_match"] is not None:
                entry["vlm_caption"] = {
                    "match": bool(r["vlm_caption_match"]),
                    "confidence": r["vlm_caption_confidence"] if "vlm_caption_confidence" in keys else None,
                }
            footnotes_payload = (
                r["footnotes_json"] if "footnotes_json" in keys else None
            )
            if footnotes_payload:
                entry["footnotes"] = _safe_json(footnotes_payload, [], context="footnotes")
            if not summary:
                entry["markdown"] = r["markdown"]
                entry["block_id"] = r["block_id"]
                entry["bbox"] = _safe_json(r["bbox_json"], None, context="bbox")
                entry["context_before"] = r["context_before"]
                entry["context_after"] = r["context_after"]
                if "footnotes_raw" in r.keys() and r["footnotes_raw"]:
                    entry["footnotes_raw"] = r["footnotes_raw"]
            out.append(entry)
        return out
    finally:
        conn.close()


def get_table(
    doc_id: str, number: str | None = None, *, table_id: str | None = None
) -> dict[str, Any] | None:
    conn = _connect()
    try:
        doc_id = _resolve_doc_id(conn, doc_id)
        if table_id:
            row = conn.execute("SELECT * FROM doc_tables WHERE table_id = ?", (table_id,)).fetchone()
        elif number is not None:
            row = conn.execute(
                "SELECT * FROM doc_tables WHERE doc_id = ? AND number = ? ORDER BY seq LIMIT 1",
                (doc_id, str(number)),
            ).fetchone()
        else:
            return None
        if not row:
            return None
        out = {
            "table_id": row["table_id"],
            "doc_id": row["doc_id"],
            "number": row["number"],
            "inferred": bool(row["inferred"]),
            "flags": _safe_json(row["flags"], [], context="flags"),
            "caption": row["caption"],
            "markdown": row["markdown"],
            "n_rows": row["n_rows"],
            "n_cols": row["n_cols"],
            "page": row["page"],
            "block_id": row["block_id"],
            "bbox": _safe_json(row["bbox_json"], None, context="bbox"),
            "context_before": row["context_before"],
            "context_after": row["context_after"],
        }
        if "footnotes_json" in row.keys() and row["footnotes_json"]:
            out["footnotes"] = _safe_json(row["footnotes_json"], [], context="footnotes")
        if "footnotes_raw" in row.keys() and row["footnotes_raw"]:
            out["footnotes_raw"] = row["footnotes_raw"]
        return out
    finally:
        conn.close()


def verify_metadata(doc_id: str, *, force: bool = False) -> dict[str, Any]:
    """Use a VLM to read page 1 of the doc and extract title/authors/year.

    Useful for non-paper docs (textbooks, arXiv preprints, reports) where CrossRef
    can't help. Skips docs that already have a DOI (CrossRef metadata is more authoritative).
    """
    from docsmcp.image import render_page
    from docsmcp.verify import vlm as _vlm

    conn = _connect()
    try:
        doc_id = _resolve_doc_id(conn, doc_id)
        row = conn.execute(
            "SELECT source_path, title, authors, year, doi FROM docs WHERE doc_id = ?",
            (doc_id,),
        ).fetchone()
        if not row:
            return {"error": f"doc not found: {doc_id}"}
        if row["doi"] and not force:
            return {
                "skipped": True,
                "reason": "doc has a CrossRef DOI; not overriding",
                "title": row["title"],
                "authors": row["authors"],
                "year": row["year"],
                "doi": row["doi"],
            }
        src = Path(row["source_path"])
        if not src.exists():
            return {"error": f"source PDF missing on disk: {src}"}
        page_path = render_page(src, doc_id, 1, dpi=180)
        verifier = _vlm.get_default()
        result = verifier.read_paper_metadata(page_path)

        updates: dict[str, Any] = {}
        for k in ("title", "authors"):
            if result.get(k):
                updates[k] = result[k]
        if result.get("year"):
            try:
                updates["year"] = int(result["year"])
            except (TypeError, ValueError):
                pass

        for k, v in updates.items():
            conn.execute(f"UPDATE docs SET {k} = ? WHERE doc_id = ?", (v, doc_id))
        conn.commit()
        return {
            "doc_id": doc_id,
            "updated": list(updates.keys()),
            "title": updates.get("title", row["title"]),
            "authors": updates.get("authors", row["authors"]),
            "year": updates.get("year", row["year"]),
            "vlm_raw": result.get("raw"),
        }
    finally:
        conn.close()


def _verify_caption_for_table(
    table_name: str, id_col: str, doc_id: str, *, limit: int | None, force: bool
) -> dict[str, Any]:
    """Shared implementation for verify_table_captions / verify_figure_captions.

    Walks captioned entities, crops their bbox region, asks the VLM whether the
    caption matches the image, stores the yes/no answer + confidence + raw response.
    """
    from docsmcp.image import render_block_crop
    from docsmcp.verify import vlm as _vlm

    conn = _connect()
    counts = {"checked": 0, "matched": 0, "mismatched": 0, "skipped_no_caption": 0,
              "skipped_no_bbox": 0, "uncertain": 0}
    try:
        doc_id = _resolve_doc_id(conn, doc_id)
        doc_row = conn.execute(
            "SELECT source_path FROM docs WHERE doc_id = ?", (doc_id,)
        ).fetchone()
        if not doc_row or not doc_row["source_path"]:
            return {"error": "doc not found or source path missing", **counts}
        src = Path(doc_row["source_path"])
        if not src.exists():
            return {"error": f"source PDF missing on disk: {src}", **counts}

        sql = f"SELECT * FROM {table_name} WHERE doc_id = ?"
        params: list[Any] = [doc_id]
        if not force:
            sql += " AND vlm_caption_match IS NULL"
        sql += " ORDER BY seq"
        if limit:
            sql += f" LIMIT {int(limit)}"
        rows = conn.execute(sql, params).fetchall()

        verifier = _vlm.get_default()
        for r in rows:
            caption = r["caption"]
            if not caption:
                counts["skipped_no_caption"] += 1
                continue
            if not r["bbox_json"]:
                counts["skipped_no_bbox"] += 1
                continue
            bbox = _safe_json(r["bbox_json"], None, context="bbox")
            if not bbox:
                counts["skipped_no_bbox"] += 1
                continue
            try:
                crop = render_block_crop(
                    src,
                    doc_id,
                    r["page"],
                    bbox,
                    block_id=r[id_col],
                )
            except Exception as e:
                _log.warning("crop failed for %s=%s: %s", id_col, r[id_col], e)
                continue
            result = verifier.verify_caption_match(crop, caption)
            counts["checked"] += 1
            if result.parsed is True:
                counts["matched"] += 1
            elif result.parsed is False:
                counts["mismatched"] += 1
            else:
                counts["uncertain"] += 1
            conn.execute(
                f"UPDATE {table_name} SET vlm_caption_match = ?, "
                f"vlm_caption_confidence = ?, vlm_caption_raw = ? WHERE {id_col} = ?",
                (
                    None if result.parsed is None else (1 if result.parsed else 0),
                    result.confidence,
                    result.raw_response,
                    r[id_col],
                ),
            )
        conn.commit()
        return {"doc_id": doc_id, **counts}
    finally:
        conn.close()


def verify_table_captions(
    doc_id: str, *, limit: int | None = None, force: bool = False
) -> dict[str, Any]:
    """Cross-modal verification: does each table's caption actually describe the table?

    Catches caption-pairing errors (table N paired with caption from table N+1 due
    to layout). Returns yes/no/uncertain counts.
    """
    return _verify_caption_for_table(
        "doc_tables", "table_id", doc_id, limit=limit, force=force
    )


def verify_figure_captions(
    doc_id: str, *, limit: int | None = None, force: bool = False
) -> dict[str, Any]:
    """Cross-modal verification: does each figure's caption actually describe the image?"""
    return _verify_caption_for_table(
        "figures", "figure_id", doc_id, limit=limit, force=force
    )


def verify_table_cells(
    doc_id: str,
    *,
    table_number: str | None = None,
    only_numeric: bool = True,
    max_per_table: int = 8,
    force: bool = False,
) -> dict[str, Any]:
    """Per-cell verification: VLM reads specific (row, col) cells from each table
    and compares to the heuristic-extracted value.

    Catches OCR errors in cell values (1OO → 100, transposed digits, dropped
    decimals). Renders the whole-table crop once per table; the VLM locates each
    queried cell within that image.

    Args:
        doc_id: Doc to verify.
        table_number: If set, verify only this table; otherwise all tables.
        only_numeric: Skip non-numeric cells (typical: focus on data values).
        max_per_table: Cap on cells checked per table (small tables fully covered;
            large tables get a representative sample).
        force: Re-verify cells already in table_cell_verifications.

    Returns: {tables_processed, cells_checked, matched, mismatched, uncertain}.
    """
    from docsmcp.image import render_block_crop
    from docsmcp.postprocess.table_cells import (
        parse_markdown_table,
        select_targets_for_verification,
    )
    from docsmcp.verify import vlm as _vlm

    conn = _connect()
    counts = {
        "tables_processed": 0, "cells_checked": 0,
        "matched": 0, "mismatched": 0, "uncertain": 0,
        "skipped_no_bbox": 0, "skipped_no_markdown": 0,
    }
    try:
        doc_id = _resolve_doc_id(conn, doc_id)
        doc_row = conn.execute(
            "SELECT source_path FROM docs WHERE doc_id = ?", (doc_id,)
        ).fetchone()
        if not doc_row or not doc_row["source_path"]:
            return {"error": "doc not found or source path missing", **counts}
        src = Path(doc_row["source_path"])
        if not src.exists():
            return {"error": f"source PDF missing on disk: {src}", **counts}

        sql = "SELECT * FROM doc_tables WHERE doc_id = ?"
        params: list[Any] = [doc_id]
        if table_number is not None:
            sql += " AND number = ?"
            params.append(str(table_number))
        sql += " ORDER BY seq"
        tables = conn.execute(sql, params).fetchall()
        if not tables:
            return {"warning": "no matching tables", **counts}

        verifier = _vlm.get_default()
        for t in tables:
            if not t["bbox_json"]:
                counts["skipped_no_bbox"] += 1
                continue
            if not t["markdown"]:
                counts["skipped_no_markdown"] += 1
                continue
            bbox = _safe_json(t["bbox_json"], None, context="bbox")
            if not bbox:
                counts["skipped_no_bbox"] += 1
                continue
            try:
                table_image = render_block_crop(
                    src, doc_id, t["page"], bbox, block_id=t["table_id"] + "_cells"
                )
            except Exception as e:
                _log.warning("crop failed for table %s: %s", t["table_id"], e)
                continue

            cells = parse_markdown_table(t["markdown"])
            targets = select_targets_for_verification(
                cells, only_numeric=only_numeric, limit=max_per_table
            )
            counts["tables_processed"] += 1

            if force:
                # Drop any prior verifications for this table so a re-run with
                # different target selection doesn't leave stale entries.
                conn.execute(
                    "DELETE FROM table_cell_verifications WHERE table_id = ?",
                    (t["table_id"],),
                )

            for tgt in targets:
                if not force:
                    existing = conn.execute(
                        "SELECT 1 FROM table_cell_verifications "
                        "WHERE table_id = ? AND row_idx = ? AND col_idx = ?",
                        (t["table_id"], tgt.row_idx, tgt.col_idx),
                    ).fetchone()
                    if existing:
                        continue
                result = verifier.verify_table_cell_by_label(
                    table_image,
                    row_label=tgt.row_label,
                    col_label=tgt.col_label,
                    expected_text=tgt.text,
                )
                counts["cells_checked"] += 1
                match = None
                if result.confidence == "high":
                    counts["matched"] += 1
                    match = 1
                elif result.parsed is None:
                    counts["uncertain"] += 1
                else:
                    counts["mismatched"] += 1
                    match = 0
                conn.execute(
                    "INSERT OR REPLACE INTO table_cell_verifications "
                    "(doc_id, table_id, row_idx, col_idx, expected_text, "
                    "vlm_text, vlm_confidence, vlm_raw, match) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        doc_id, t["table_id"], tgt.row_idx, tgt.col_idx,
                        tgt.text, result.parsed, result.confidence,
                        result.raw_response, match,
                    ),
                )
        conn.commit()
        return {"doc_id": doc_id, **counts}
    finally:
        conn.close()


def get_table_cell_verifications(
    doc_id: str, *, table_number: str | None = None
) -> list[dict[str, Any]]:
    """Fetch stored per-cell verification results for a doc (optionally one table)."""
    conn = _connect()
    try:
        doc_id = _resolve_doc_id(conn, doc_id)
        if table_number is not None:
            sql = (
                "SELECT v.* FROM table_cell_verifications v "
                "JOIN doc_tables t ON t.table_id = v.table_id "
                "WHERE v.doc_id = ? AND t.number = ? "
                "ORDER BY t.seq, v.row_idx, v.col_idx"
            )
            rows = conn.execute(sql, (doc_id, str(table_number))).fetchall()
        else:
            sql = (
                "SELECT v.*, t.number AS table_number FROM table_cell_verifications v "
                "JOIN doc_tables t ON t.table_id = v.table_id "
                "WHERE v.doc_id = ? "
                "ORDER BY t.seq, v.row_idx, v.col_idx"
            )
            rows = conn.execute(sql, (doc_id,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def verify_equation_latex(
    doc_id: str,
    *,
    only_flagged: bool = True,
    limit: int | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Visually verify that each equation's extracted LaTeX matches the source image.

    Catches OCR damage that number-verification misses: whitespace runaways,
    column-bleed content, truncated formulas. Asks the VLM "does this LaTeX
    represent the equation?" with tolerance for formatting differences.

    Args:
        doc_id: Doc to verify.
        only_flagged: If True, skip equations without any flags (assumes flag-free
            extractions are likely correct; focuses cost on suspicious ones).
        limit: Cap on how many to verify.
        force: Re-verify even when vlm_latex_match is already set.

    Returns: {checked, matched, mismatched, uncertain, skipped_no_bbox, skipped_no_latex}.
    Each equation's get_equation / list_equations response now includes
    `vlm_latex: {match, confidence}`.
    """
    from docsmcp.image import render_block_crop
    from docsmcp.verify import vlm as _vlm

    conn = _connect()
    counts = {
        "checked": 0, "matched": 0, "mismatched": 0, "uncertain": 0,
        "skipped_no_bbox": 0, "skipped_no_latex": 0,
    }
    try:
        doc_id = _resolve_doc_id(conn, doc_id)
        doc_row = conn.execute(
            "SELECT source_path FROM docs WHERE doc_id = ?", (doc_id,)
        ).fetchone()
        if not doc_row or not doc_row["source_path"]:
            return {"error": "doc not found or source path missing", **counts}
        src = Path(doc_row["source_path"])
        if not src.exists():
            return {"error": f"source PDF missing on disk: {src}", **counts}

        sql = "SELECT * FROM equations WHERE doc_id = ?"
        params: list[Any] = [doc_id]
        if only_flagged:
            sql += " AND (inferred = 1 OR (flags IS NOT NULL AND flags != '[]'))"
        if not force:
            sql += " AND vlm_latex_match IS NULL"
        sql += " ORDER BY seq"
        if limit:
            sql += f" LIMIT {int(limit)}"
        rows = conn.execute(sql, params).fetchall()

        verifier = _vlm.get_default()
        for r in rows:
            if not r["latex_clean"]:
                counts["skipped_no_latex"] += 1
                continue
            if not r["bbox_json"]:
                counts["skipped_no_bbox"] += 1
                continue
            bbox = _safe_json(r["bbox_json"], None, context="bbox")
            if not bbox:
                counts["skipped_no_bbox"] += 1
                continue
            try:
                crop = render_block_crop(
                    src, doc_id, r["page"], bbox, block_id=r["eq_id"]
                )
            except Exception as e:
                _log.warning("crop failed for eq %s: %s", r["eq_id"], e)
                continue
            result = verifier.verify_latex_match(crop, r["latex_clean"])
            counts["checked"] += 1
            if result.parsed is True:
                counts["matched"] += 1
            elif result.parsed is False:
                counts["mismatched"] += 1
            else:
                counts["uncertain"] += 1
            conn.execute(
                "UPDATE equations SET vlm_latex_match = ?, "
                "vlm_latex_confidence = ?, vlm_latex_raw = ? WHERE eq_id = ?",
                (
                    None if result.parsed is None else (1 if result.parsed else 0),
                    result.confidence,
                    result.raw_response,
                    r["eq_id"],
                ),
            )
        conn.commit()
        return {"doc_id": doc_id, **counts}
    finally:
        conn.close()


def verify_equations(
    doc_id: str,
    *,
    only_flagged: bool = True,
    limit: int | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Run VLM verification on equation crops.

    For each candidate equation: render its bbox crop, ask the VLM "what number is shown?",
    store the result in the equations table. Only verifies "flagged" entries by default
    (inferred numbers, repeat-loop-truncated, column-bleed-stripped, dropped anchors).

    Args:
        doc_id: Doc to verify.
        only_flagged: If True, skip equations with directly-extracted numbers and no flags.
        limit: Cap on how many to verify (None = all).
        force: Re-verify even if vlm_verified=1 already.

    Returns counts: {verified, confirmed, mismatch, missing, unchanged}.
    """
    from docsmcp.image import render_block_crop
    from docsmcp.verify import vlm as _vlm

    conn = _connect()
    counts = {"verified": 0, "confirmed": 0, "mismatch": 0, "missing": 0, "unchanged": 0}
    try:
        doc_id = _resolve_doc_id(conn, doc_id)
        doc_row = conn.execute(
            "SELECT source_path FROM docs WHERE doc_id = ?", (doc_id,)
        ).fetchone()
        if not doc_row or not doc_row["source_path"]:
            return {"error": "doc not found or source path missing", **counts}
        src = Path(doc_row["source_path"])
        if not src.exists():
            return {"error": f"source PDF missing on disk: {src}", **counts}

        sql = "SELECT * FROM equations WHERE doc_id = ?"
        params: list[Any] = [doc_id]
        if only_flagged:
            sql += " AND (inferred = 1 OR (flags IS NOT NULL AND flags != '[]'))"
        if not force:
            sql += " AND COALESCE(vlm_verified, 0) = 0"
        sql += " ORDER BY seq"
        if limit:
            sql += f" LIMIT {int(limit)}"
        rows = conn.execute(sql, params).fetchall()

        verifier = _vlm.get_default()
        for r in rows:
            if not r["bbox_json"]:
                continue
            bbox = _safe_json(r["bbox_json"], None, context="bbox")
            try:
                crop = render_block_crop(
                    src,
                    doc_id,
                    r["page"],
                    bbox,
                    block_id=r["eq_id"],
                )
            except Exception:
                continue
            result = verifier.verify_equation_number(
                crop, expected_number=r["number"]
            )
            counts["verified"] += 1
            new_number = r["number"]
            if result.parsed is None:
                counts["missing"] += 1
            elif r["number"] is None or result.parsed != r["number"]:
                # VLM disagrees with our heuristic; trust VLM
                counts["mismatch"] += 1
                new_number = result.parsed
            else:
                counts["confirmed"] += 1
            conn.execute(
                "UPDATE equations SET vlm_verified = 1, vlm_number = ?, vlm_confidence = ?, "
                "vlm_raw = ?, number = ? WHERE eq_id = ?",
                (
                    result.parsed,
                    result.confidence,
                    result.raw_response,
                    new_number,
                    r["eq_id"],
                ),
            )
        conn.commit()
        return {"doc_id": doc_id, **counts}
    finally:
        conn.close()


def list_equations(
    doc_id: str,
    *,
    page: int | None = None,
    only_numbered: bool = False,
    summary: bool = False,
) -> list[dict[str, Any]]:
    conn = _connect()
    try:
        doc_id = _resolve_doc_id(conn, doc_id)
        sql = "SELECT * FROM equations WHERE doc_id = ?"
        params: list[Any] = [doc_id]
        if page is not None:
            sql += " AND page = ?"
            params.append(page)
        if only_numbered:
            sql += " AND number IS NOT NULL"
        sql += " ORDER BY seq"
        rows = conn.execute(sql, params).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            keys = r.keys()
            entry = {
                "eq_id": r["eq_id"],
                "number": r["number"],
                "inferred": bool(r["inferred"]) if "inferred" in keys else False,
                "flags": _safe_json(r["flags"], [], context="flags"),
                "page": r["page"],
                "latex_clean": r["latex_clean"],
            }
            if "vlm_verified" in keys and r["vlm_verified"]:
                entry["vlm"] = {
                    "verified": True,
                    "number": r["vlm_number"] if "vlm_number" in keys else None,
                    "confidence": r["vlm_confidence"] if "vlm_confidence" in keys else None,
                }
            if "vlm_latex_match" in keys and r["vlm_latex_match"] is not None:
                entry["vlm_latex"] = {
                    "match": bool(r["vlm_latex_match"]),
                    "confidence": r["vlm_latex_confidence"] if "vlm_latex_confidence" in keys else None,
                }
            if not summary:
                entry["latex_raw"] = r["latex_raw"]
                entry["block_id"] = r["block_id"]
                entry["bbox"] = _safe_json(r["bbox_json"], None, context="bbox")
                entry["context_before"] = r["context_before"]
                entry["context_after"] = r["context_after"]
            out.append(entry)
        return out
    finally:
        conn.close()


def get_equation(doc_id: str, number: str | None = None, *, eq_id: str | None = None) -> dict[str, Any] | None:
    conn = _connect()
    try:
        doc_id = _resolve_doc_id(conn, doc_id)
        if eq_id:
            row = conn.execute("SELECT * FROM equations WHERE eq_id = ?", (eq_id,)).fetchone()
        elif number is not None:
            row = conn.execute(
                "SELECT * FROM equations WHERE doc_id = ? AND number = ? ORDER BY seq LIMIT 1",
                (doc_id, str(number)),
            ).fetchone()
        else:
            return None
        if not row:
            return None
        keys = row.keys()
        out: dict[str, Any] = {
            "eq_id": row["eq_id"],
            "doc_id": row["doc_id"],
            "number": row["number"],
            "inferred": bool(row["inferred"]) if "inferred" in keys else False,
            "flags": _safe_json(row["flags"], [], context="flags"),
            "page": row["page"],
            "latex_clean": row["latex_clean"],
            "latex_raw": row["latex_raw"],
            "block_id": row["block_id"],
            "bbox": _safe_json(row["bbox_json"], None, context="bbox"),
            "context_before": row["context_before"],
            "context_after": row["context_after"],
        }
        if "vlm_verified" in keys and row["vlm_verified"]:
            out["vlm"] = {
                "verified": True,
                "number": row["vlm_number"] if "vlm_number" in keys else None,
                "confidence": row["vlm_confidence"] if "vlm_confidence" in keys else None,
            }
        if "vlm_latex_match" in keys and row["vlm_latex_match"] is not None:
            out["vlm_latex"] = {
                "match": bool(row["vlm_latex_match"]),
                "confidence": row["vlm_latex_confidence"] if "vlm_latex_confidence" in keys else None,
            }
        return out
    finally:
        conn.close()


def _delete_doc_rows(conn: sqlite3.Connection, doc_id: str) -> None:
    chunk_rowids = [
        r[0] for r in conn.execute(
            "SELECT rowid FROM chunks WHERE doc_id = ?", (doc_id,)
        ).fetchall()
    ]
    for rid in chunk_rowids:
        conn.execute("DELETE FROM vec_chunks WHERE rowid = ?", (rid,))
    conn.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))
    conn.execute("DELETE FROM blocks WHERE doc_id = ?", (doc_id,))
    conn.execute("DELETE FROM equations WHERE doc_id = ?", (doc_id,))
    conn.execute("DELETE FROM doc_tables WHERE doc_id = ?", (doc_id,))
    conn.execute("DELETE FROM table_cell_verifications WHERE doc_id = ?", (doc_id,))
    conn.execute("DELETE FROM figures WHERE doc_id = ?", (doc_id,))
    conn.execute("DELETE FROM docs WHERE doc_id = ?", (doc_id,))


def _resolve_doc_id(conn: sqlite3.Connection, doc_id: str) -> str:
    """Resolve a short prefix to a full doc_id.

    When multiple docs share a prefix, prefer the most-recently-transcribed one
    (deterministic tiebreak instead of arbitrary insertion order).
    """
    if len(doc_id) >= 64:
        return doc_id
    rows = conn.execute(
        "SELECT doc_id, transcribed_at FROM docs WHERE doc_id LIKE ? "
        "ORDER BY transcribed_at DESC NULLS LAST LIMIT 2",
        (doc_id + "%",),
    ).fetchall()
    if not rows:
        return doc_id
    if len(rows) > 1:
        _log.warning(
            "doc_id prefix %r matched %d docs; using most recent (%s)",
            doc_id, len(rows), rows[0]["doc_id"][:16],
        )
    return rows[0]["doc_id"]




def get_chunk(chunk_id: str) -> dict[str, Any] | None:
    conn = _connect()
    try:
        r = conn.execute(
            "SELECT c.*, d.title, d.authors, d.kind, d.source_path, d.out_dir "
            "FROM chunks c JOIN docs d ON d.doc_id = c.doc_id WHERE c.chunk_id = ?",
            (chunk_id,),
        ).fetchone()
        if not r:
            return None
        return {
            "chunk_id": r["chunk_id"],
            "doc_id": r["doc_id"],
            "seq": r["seq"],
            "section": r["section"],
            "page_first": r["page_first"],
            "page_last": r["page_last"],
            "text": r["text"],
            "token_count": r["token_count"],
            "block_ids": _safe_json(r["block_ids_json"], [], context="block_ids"),
            "prev_chunk_id": r["prev_chunk_id"],
            "next_chunk_id": r["next_chunk_id"],
            "title": r["title"],
            "authors": r["authors"],
            "kind": r["kind"],
            "source_path": r["source_path"],
            "out_dir": r["out_dir"],
        }
    finally:
        conn.close()


def get_block(block_id: str) -> dict[str, Any] | None:
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT b.*, d.source_path, d.out_dir FROM blocks b "
            "JOIN docs d ON d.doc_id = b.doc_id WHERE b.block_id = ?",
            (block_id,),
        ).fetchone()
        if not row:
            return None
        return {
            "block_id": row["block_id"],
            "doc_id": row["doc_id"],
            "page": row["page"],
            "type": row["block_type"],
            "text": row["text"],
            "engine": row["engine"],
            "verify": row["verify_status"],
            "bbox": _safe_json(row["bbox_json"], None, context="bbox"),
            "source_path": row["source_path"],
            "out_dir": row["out_dir"],
        }
    finally:
        conn.close()


def get_page_blocks(doc_id: str, page: int) -> list[dict[str, Any]]:
    conn = _connect()
    try:
        doc_id = _resolve_doc_id(conn, doc_id)
        rows = conn.execute(
            "SELECT block_id, page, block_type, text, engine, verify_status, bbox_json "
            "FROM blocks WHERE doc_id = ? AND page = ? ORDER BY rowid_local",
            (doc_id, page),
        ).fetchall()
        return [
            {
                "block_id": r["block_id"],
                "page": r["page"],
                "type": r["block_type"],
                "text": r["text"],
                "engine": r["engine"],
                "verify": r["verify_status"],
                "bbox": _safe_json(r["bbox_json"], None, context="bbox"),
            }
            for r in rows
        ]
    finally:
        conn.close()


def list_docs(kind: str | None = None) -> list[dict[str, Any]]:
    conn = _connect()
    try:
        if kind:
            rows = conn.execute(
                "SELECT * FROM docs WHERE kind = ? ORDER BY transcribed_at DESC", (kind,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM docs ORDER BY transcribed_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def set_metadata(
    doc_id: str,
    *,
    title: str | None = None,
    authors: str | None = None,
    year: int | None = None,
    kind: str | None = None,
    tags: list[str] | None = None,
    doi: str | None = None,
    journal: str | None = None,
    volume: str | None = None,
    pages: str | None = None,
) -> dict[str, Any]:
    conn = _connect()
    try:
        doc_id = _resolve_doc_id(conn, doc_id)
        updates: dict[str, Any] = {}
        for k, v in (
            ("title", title),
            ("authors", authors),
            ("year", year),
            ("kind", kind),
            ("doi", doi),
            ("journal", journal),
            ("volume", volume),
            ("pages", pages),
        ):
            if v is not None:
                updates[k] = v
        if tags is not None:
            updates["tags"] = json.dumps(tags)
        for k, v in updates.items():
            conn.execute(f"UPDATE docs SET {k} = ? WHERE doc_id = ?", (v, doc_id))
        conn.commit()
        row = conn.execute("SELECT * FROM docs WHERE doc_id = ?", (doc_id,)).fetchone()
        return dict(row) if row else {"error": "doc not found"}
    finally:
        conn.close()


def diagnose_schema(doc_id: str | None = None) -> dict[str, Any]:
    """Scan every JSON column for malformed rows. Returns a report of any corruption.

    Run this when search/list tools start returning unexpected gaps or empty results
    — corrupted JSON cells now degrade gracefully via _safe_json, but the diagnostic
    helps locate which rows need a re-ingest.
    """
    conn = _connect()
    try:
        scope_filter = ""
        params: list[Any] = []
        if doc_id:
            doc_id = _resolve_doc_id(conn, doc_id)
            scope_filter = " WHERE doc_id = ?"
            params = [doc_id]

        columns_to_scan: list[tuple[str, str, str]] = [
            ("blocks", "block_id", "bbox_json"),
            ("chunks", "chunk_id", "block_ids_json"),
            ("chunks", "chunk_id", "refs_json"),
            ("equations", "eq_id", "bbox_json"),
            ("equations", "eq_id", "flags"),
            ("doc_tables", "table_id", "bbox_json"),
            ("doc_tables", "table_id", "flags"),
            ("doc_tables", "table_id", "footnotes_json"),
            ("figures", "figure_id", "bbox_json"),
            ("figures", "figure_id", "flags"),
            ("figures", "figure_id", "panels_json"),
        ]
        problems: list[dict[str, Any]] = []
        totals: dict[str, int] = {}
        for table, pk, col in columns_to_scan:
            tot = 0
            bad = 0
            bad_rows: list[str] = []
            try:
                rows = conn.execute(
                    f"SELECT {pk}, {col} FROM {table}{scope_filter}", params
                ).fetchall()
            except sqlite3.OperationalError:
                continue
            for r in rows:
                v = r[col]
                if not v:
                    continue
                tot += 1
                try:
                    json.loads(v)
                except (json.JSONDecodeError, TypeError, ValueError):
                    bad += 1
                    if len(bad_rows) < 5:
                        bad_rows.append(r[pk])
            totals[f"{table}.{col}"] = tot
            if bad:
                problems.append(
                    {
                        "table": table,
                        "column": col,
                        "bad_rows": bad,
                        "total_non_null": tot,
                        "sample_ids": bad_rows,
                    }
                )

        # Schema version info
        try:
            applied = [
                r[0]
                for r in conn.execute(
                    "SELECT id FROM schema_migrations ORDER BY applied_at"
                ).fetchall()
            ]
        except sqlite3.OperationalError:
            applied = None
        return {
            "scope_doc_id": doc_id,
            "json_column_totals": totals,
            "json_corruption": problems,
            "applied_migrations": applied,
            "all_migrations_count": len(_MIGRATIONS),
            "healthy": len(problems) == 0,
        }
    finally:
        conn.close()


def reindex_all() -> dict[str, int]:
    from docsmcp.metadata import extract_metadata
    from docsmcp.postprocess.crossref import enrich_metadata_from_blocks

    root = out_root()
    if not root.exists():
        return {"docs": 0, "blocks": 0, "chunks": 0}
    docs = 0
    blocks = 0
    chunks = 0
    equations = 0
    tables = 0
    figures = 0
    for doc_path in root.iterdir():
        if not doc_path.is_dir():
            continue
        v = latest_version(doc_path)
        if v is None:
            continue
        target = doc_path / f"v{v}"
        prov_path = target / "provenance.json"
        if not prov_path.exists():
            continue
        try:
            prov = json.loads(prov_path.read_text())
        except Exception:
            continue
        counts = reindex_doc(prov, target)
        doc_blocks = prov.get("blocks", [])
        meta = extract_metadata(doc_blocks)
        crossref = enrich_metadata_from_blocks(doc_blocks)
        if crossref:
            for k in ("title", "authors", "year", "doi", "journal", "volume", "pages"):
                if crossref.get(k):
                    meta[k] = crossref[k]
        if meta:
            set_metadata(
                prov["doc_id"],
                title=meta.get("title"),
                authors=meta.get("authors"),
                year=meta.get("year"),
                doi=meta.get("doi"),
                journal=meta.get("journal"),
                volume=meta.get("volume"),
                pages=meta.get("pages"),
            )
        docs += 1
        blocks += counts["blocks"]
        chunks += counts["chunks"]
        equations += counts.get("equations", 0)
        tables += counts.get("tables", 0)
        figures += counts.get("figures", 0)
    return {
        "docs": docs,
        "blocks": blocks,
        "chunks": chunks,
        "equations": equations,
        "tables": tables,
        "figures": figures,
    }


# Backward compat alias for old callers
def search(query: str, top_k: int = 5, doc_id: str | None = None) -> list[dict[str, Any]]:
    """Legacy block-level FTS search (kept for compatibility)."""
    conn = _connect()
    try:
        where = ""
        params: list[Any] = [query]
        if doc_id:
            where = " AND b.doc_id = ?"
            params.append(doc_id)
        params.append(top_k)

        sql = (
            "SELECT b.block_id, b.doc_id, b.page, b.block_type, b.text, b.engine, "
            "b.verify_status, b.bbox_json, bm25(blocks_fts) AS score "
            "FROM blocks_fts JOIN blocks b ON b.rowid = blocks_fts.rowid "
            "WHERE blocks_fts MATCH ?" + where +
            " ORDER BY score LIMIT ?"
        )
        rows = conn.execute(sql, params).fetchall()
        return [
            {
                "block_id": r["block_id"],
                "doc_id": r["doc_id"],
                "page": r["page"],
                "type": r["block_type"],
                "text": r["text"],
                "engine": r["engine"],
                "verify": r["verify_status"],
                "bbox": _safe_json(r["bbox_json"], None, context="bbox"),
                "score": r["score"],
            }
            for r in rows
        ]
    finally:
        conn.close()

# Re-export search functions from the dedicated module so callers using
# `from docsmcp.index.fts import search_chunks` continue to work.
from docsmcp.index.search import (  # noqa: E402
    search_chunks,
    search_docs,
    _sanitize_fts_query,
    _is_structural_query,
)
