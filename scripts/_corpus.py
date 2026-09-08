"""Where the labelled corpus lives.

The labelled sources are copyrighted journal PDFs and are not in the repository:
43 of the 47 cannot be redistributed. They are kept in one directory outside the
tree and named by `PDF2MD_CORPUS`, so a clean clone still installs, lints and
runs its fast tests. Everything generated or small enough to publish stays in
the tree and keeps resolving relative to the repository root.

Every harness that reads a labelled source resolves it through here, so there is
one answer to "where is that PDF" rather than one per script.
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).parent.parent


def labelled_source(source: str | Path, root: Path | None = None) -> Path:
    """A labelled source, in the tree if it is there and in the corpus if not."""
    base = root or ROOT
    in_tree = base / source
    if in_tree.exists():
        return in_tree
    return Path(os.environ.get("PDF2MD_CORPUS", base)) / Path(source).name
