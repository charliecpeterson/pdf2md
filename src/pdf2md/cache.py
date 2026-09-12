"""On-disk identity and versioning.

`doc_id` is the SHA-256 of the source bytes. New output directories combine a
readable source-name slug with a short hash (`out/paper-a1b2c3d4/v<n>/`), while
legacy hash-only directories remain discoverable. New runs never overwrite old
ones; `latest_version()` is what readers use. A version directory is allocated by
creating it, so concurrent converts of one document cannot collide.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import socket
from datetime import UTC, datetime
from pathlib import Path

from pdf2md.run_metrics import failed_optional_calls

_DOCUMENT_HASH_LENGTHS = {8, 12, 16, 64}
# Written into a version directory at allocation and removed when it completes.
_CLAIM_FILE = "claim.json"


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


def document_slug(source_path: Path) -> str:
    """A readable, filesystem-safe name derived from the source filename."""
    slug = re.sub(r"[\W_]+", "-", Path(source_path).stem.casefold()).strip("-")
    return slug[:64].rstrip("-") or "document"


def _matches_document(path: Path, doc_id: str) -> bool:
    source = path / "source.pdf"
    try:
        return source.is_file() and content_hash(source) == doc_id
    except OSError:
        return False


def is_document_dir(path: Path) -> bool:
    """Whether a directory name and stored source agree on document identity."""
    path = Path(path)
    source = path / "source.pdf"
    if not source.is_file():
        return False
    suffix = path.name.rsplit("-", 1)[-1]
    if (
        len(suffix) not in _DOCUMENT_HASH_LENGTHS
        or re.fullmatch(r"[0-9a-f]+", suffix) is None
    ):
        return False
    try:
        return content_hash(source).startswith(suffix)
    except OSError:
        return False


def document_dirs(root: Path | None = None, *, recursive: bool = False) -> list[Path]:
    """Verified document roots below an output library."""
    output = Path(root).expanduser().resolve() if root else out_root()
    if not output.is_dir():
        return []
    candidates = (
        (source.parent for source in output.rglob("source.pdf"))
        if recursive else
        (path for path in output.iterdir() if path.is_dir())
    )
    return sorted({path for path in candidates if is_document_dir(path)})


def doc_dir(
    doc_id: str,
    source_path: Path | None = None,
    *,
    root: Path | None = None,
) -> Path:
    """Resolve a document directory, preserving legacy hash-only libraries."""
    output = Path(root).expanduser().resolve() if root else out_root()
    legacy = output / doc_id[:16]
    if legacy.exists() and _matches_document(legacy, doc_id):
        return legacy
    if source_path is None:
        return legacy

    # Reuse the same content after a source rename. Only plausible hash-suffix
    # matches are opened, so a large output library does not require a full scan.
    for length in (8, 12, 16, 64):
        for candidate in sorted(output.glob(f"*-{doc_id[:length]}")):
            if _matches_document(candidate, doc_id):
                return candidate

    slug = document_slug(source_path)
    for length in (8, 12, 16, 64):
        candidate = output / f"{slug}-{doc_id[:length]}"
        if not candidate.exists() or _matches_document(candidate, doc_id):
            return candidate
    raise RuntimeError(f"could not allocate output directory for {source_path}")


def _versions(doc_dir_path: Path) -> list[int]:
    """All `v<n>` dirs on disk (including crashed/partial ones) — used by prune to
    clean everything up."""
    if not doc_dir_path.exists():
        return []
    return [
        int(p.name[1:])
        for p in doc_dir_path.iterdir()
        if p.is_dir() and p.name.startswith("v") and p.name[1:].isdigit()
    ]


def _complete_versions(doc_dir_path: Path) -> list[int]:
    """Versions with a provenance.json — written last, so its absence marks a crashed
    run. The cache reasons over these so a partial run never counts as cached output
    (otherwise an interrupted run blocks every later one)."""
    return [v for v in _versions(doc_dir_path)
            if (doc_dir_path / f"v{v}" / "provenance.json").exists()]


def next_version(doc_dir_path: Path) -> int:
    versions = _complete_versions(doc_dir_path)
    return (max(versions) + 1) if versions else 1


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:  # another user's process, but a process
        return True
    return True


def live_claim(version_dir: Path) -> dict | None:
    """The claim of a run still working in this version directory, if there is one.

    A version directory without `provenance.json` is either a crashed run's
    leftovers or a conversion happening right now, and from outside the two look
    identical -- which is why allocation used to delete a concurrent run's work.
    The claim written at allocation names the host and pid that made it, which
    separates them on the machine that wrote it; a claim from another host counts
    as live, because its pids mean nothing here. A directory with no claim at all
    predates this file and is treated as abandoned, as it always was.
    """
    if (version_dir / "provenance.json").exists():
        return None
    try:
        claim = json.loads((version_dir / _CLAIM_FILE).read_text())
    except (OSError, ValueError):
        return None
    if claim.get("host") != socket.gethostname():
        return claim
    return claim if _pid_alive(int(claim.get("pid", 0))) else None


def claim_version(doc_dir_path: Path) -> tuple[int, Path]:
    """Allocate this run's `v<n>` directory, atomically.

    Creating the directory *is* the allocation: `mkdir` either succeeds or reports
    that somebody else got there first, so two converts of the same document running
    at once cannot both write into one version. A crashed run's number is reused (its
    claim names a pid that is gone, so its leftovers are cleared first); a completed
    or live one is stepped over.
    """
    version = next_version(doc_dir_path)
    while True:
        vdir = doc_dir_path / f"v{version}"
        try:
            vdir.mkdir(parents=True)
        except FileExistsError:
            if (vdir / "provenance.json").exists() or live_claim(vdir) is not None:
                version += 1
                continue
            try:
                shutil.rmtree(vdir)
            except FileNotFoundError:  # someone else cleared it first
                pass
            continue
        vdir.joinpath(_CLAIM_FILE).write_text(json.dumps({
            "host": socket.gethostname(),
            "pid": os.getpid(),
            "claimed_at": datetime.now(UTC).isoformat(),
        }))
        return version, vdir


def release_claim(version_dir: Path) -> None:
    """Drop the claim once `provenance.json` has marked the version complete."""
    (version_dir / _CLAIM_FILE).unlink(missing_ok=True)


def latest_version(doc_dir_path: Path) -> int | None:
    versions = _complete_versions(doc_dir_path)
    return max(versions) if versions else None


def run_fingerprint(run_inputs: dict) -> str:
    """Hash the canonical inputs that can change a conversion's output."""
    encoded = json.dumps(run_inputs, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def matching_version(
    doc_dir_path: Path,
    fingerprint: str,
    *,
    include_partial: bool = False,
) -> int | None:
    """Newest reusable version produced from the same effective run inputs."""
    for version in sorted(_complete_versions(doc_dir_path), reverse=True):
        provenance_path = doc_dir_path / f"v{version}" / "provenance.json"
        try:
            document = json.loads(provenance_path.read_text())
        except (OSError, ValueError):
            continue
        provenance = document.get("provenance") or {}
        if (
            provenance.get("run_fingerprint") == fingerprint
            and (
                include_partial
                or failed_optional_calls(provenance.get("run_metrics")) == 0
            )
        ):
            return version
    return None


def deduplicate_assets(version_dir: Path) -> tuple[int, int]:
    """Hard-link assets identical to files in earlier completed versions."""
    assets = version_dir / "assets"
    if not assets.is_dir():
        return 0, 0

    candidates: dict[tuple[int, str], Path] = {}
    hashes: dict[tuple[int, int], str] = {}
    for version in sorted(_complete_versions(version_dir.parent), reverse=True):
        previous = version_dir.parent / f"v{version}"
        if previous == version_dir:
            continue
        previous_assets = previous / "assets"
        if not previous_assets.is_dir():
            continue
        for path in previous_assets.rglob("*"):
            try:
                if not path.is_file() or path.is_symlink():
                    continue
                stat = path.stat()
                inode = (stat.st_dev, stat.st_ino)
                digest = hashes.get(inode)
                if digest is None:
                    digest = content_hash(path)
                    hashes[inode] = digest
            except OSError:
                continue
            candidates.setdefault((stat.st_size, digest), path)

    linked = 0
    saved = 0
    for path in assets.rglob("*"):
        try:
            if not path.is_file() or path.is_symlink():
                continue
            stat = path.stat()
            candidate = candidates.get((stat.st_size, content_hash(path)))
            if candidate is None or path.samefile(candidate):
                continue
        except OSError:
            continue
        pending = path.with_name(f".{path.name}.dedup")
        try:
            pending.unlink(missing_ok=True)
            os.link(candidate, pending)
            pending.replace(path)
        except OSError:
            pending.unlink(missing_ok=True)
            continue
        linked += 1
        saved += stat.st_size
    return linked, saved


def prune(*, keep: int = 1, dry_run: bool = False) -> list[Path]:
    """Remove old `v<n>` dirs across the output root, keeping the newest `keep`
    per document. Returns the version dirs removed (or that would be, if dry-run)."""
    root = out_root()
    removed: list[Path] = []
    if not root.exists():
        return removed
    for dd in document_dirs(root):
        versions = sorted(_versions(dd))
        doomed = versions if keep == 0 else versions[:-keep]
        for v in doomed:
            vdir = dd / f"v{v}"
            if live_claim(vdir) is not None:
                continue  # a conversion is writing here right now
            removed.append(vdir)
            if not dry_run:
                shutil.rmtree(vdir)
    return removed
