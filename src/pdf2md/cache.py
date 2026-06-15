"""On-disk identity and versioning.

`doc_id` is the SHA-256 of the source bytes; the first 16 chars name the output
directory (`out/<doc_id[:16]>/v<n>/`). New runs never overwrite old ones;
`latest_version()` is what readers use.
"""

from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path


def content_hash(path: Path, *, chunk_size: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(chunk_size):
            h.update(chunk)
    return h.hexdigest()


def out_root() -> Path:
    env = os.environ.get("PDF2MD_OUT")
    if env:
        return Path(env).expanduser().resolve()
    return Path.cwd() / "out"


def doc_dir(doc_id: str) -> Path:
    return out_root() / doc_id[:16]


def _versions(doc_dir_path: Path) -> list[int]:
    if not doc_dir_path.exists():
        return []
    return [
        int(p.name[1:])
        for p in doc_dir_path.iterdir()
        if p.is_dir() and p.name.startswith("v") and p.name[1:].isdigit()
    ]


def next_version(doc_dir_path: Path) -> int:
    versions = _versions(doc_dir_path)
    return (max(versions) + 1) if versions else 1


def latest_version(doc_dir_path: Path) -> int | None:
    versions = _versions(doc_dir_path)
    return max(versions) if versions else None


def prune(*, keep: int = 1, dry_run: bool = False) -> list[Path]:
    """Remove old `v<n>` dirs across the output root, keeping the newest `keep`
    per document. Returns the version dirs removed (or that would be, if dry-run)."""
    root = out_root()
    removed: list[Path] = []
    if not root.exists():
        return removed
    for dd in sorted(root.iterdir()):
        if not dd.is_dir():
            continue
        versions = sorted(_versions(dd))
        doomed = versions if keep == 0 else versions[:-keep]
        for v in doomed:
            vdir = dd / f"v{v}"
            removed.append(vdir)
            if not dry_run:
                shutil.rmtree(vdir)
    return removed
