"""Score the local title and author extraction against the DOI registry.

    uv run python scripts/eval_metadata_precision.py OUT_DIR [--json OUT]
                                                     [--limit N] [--delay S]

Titles and authors are the one extraction here with no adjudicator. The glyph
checks have poppler, the tables have a labelled set, the charts have labelled
axes and values; metadata has had a person looking at it and agreeing, which is
the standard every other check in this project is held above.

The registry is that adjudicator where a paper prints its DOI. `doi_metadata`
already speaks CSL-JSON content negotiation, so this reuses it: the request
carries the DOI and nothing else -- no local text, no identity.

It is a partial oracle and says so. Only some documents print a DOI, a
supplementary file carries its parent article's, and a registry title is the
publisher's record rather than the page's ink, so a legitimate subtitle or a
bilingual title can differ from both. `mismatch` therefore means "worth reading",
not "wrong": the report prints both strings so the caller can judge.

  match       registry title and the local title agree after normalization
  contains    one is a prefix or substring of the other (subtitle, SI parent)
  mismatch    neither -- both strings are printed
  unavailable no DOI printed, or the registry did not answer
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from pdf2md.doi_metadata import fetch_doi_metadata

# A printed DOI, minus the punctuation a sentence puts after it.
_DOI = re.compile(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+")
_TRAILING = ".,;)]}>"
_HEAD_CHARS = 6000


def find_doi(version_dir: Path) -> str | None:
    meta = version_dir / "metadata.json"
    if meta.is_file():
        fields = json.loads(meta.read_text())["document"].get("fields", {})
        value = (fields.get("doi") or {}).get("value")
        if value:
            return str(value)
    markdown = sorted(version_dir.glob("*.md"))
    for path in markdown:
        match = _DOI.search(path.read_text(errors="replace")[:_HEAD_CHARS])
        if match:
            return match.group(0).rstrip(_TRAILING)
    return None


def _norm(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text or "")
    stripped = "".join(c for c in folded if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9 ]+", " ", stripped.casefold()).strip()


def _surnames(names) -> set[str]:
    out = set()
    for name in names or []:
        parts = _norm(str(name)).split()
        if parts:
            out.add(parts[-1])
    return out


def _title_verdict(local: str | None, registry: str | None) -> str:
    if not local or not registry:
        return "unavailable"
    a, b = " ".join(_norm(local).split()), " ".join(_norm(registry).split())
    if not a or not b:
        return "unavailable"
    if a == b:
        return "match"
    return "contains" if a in b or b in a else "mismatch"


def score(version_dir: Path, delay: float) -> dict | None:
    metadata = version_dir / "metadata.json"
    if not metadata.is_file():   # a bundle written before metadata.json existed
        return None
    doi = find_doi(version_dir)
    row = {"document": version_dir.parent.name, "doi": doi}
    meta = json.loads(metadata.read_text())["document"]["fields"]
    row["local_title"] = (meta.get("title") or {}).get("value")
    row["local_authors"] = (meta.get("authors") or {}).get("value") or []
    if not doi:
        row["verdict"] = "unavailable"
        return row
    time.sleep(delay)
    record = fetch_doi_metadata(doi)
    if not record:
        row["verdict"] = "unavailable"
        return row
    row["registry_title"] = record.get("title")
    if isinstance(row["registry_title"], list):
        row["registry_title"] = row["registry_title"][0] if row["registry_title"] else None
    registry_authors = [
        " ".join(filter(None, (a.get("given"), a.get("family"))))
        for a in record.get("author") or []
    ]
    row["registry_authors"] = registry_authors
    row["verdict"] = _title_verdict(row["local_title"], row["registry_title"])
    local, remote = _surnames(row["local_authors"]), _surnames(registry_authors)
    row["author_verdict"] = (
        "unavailable" if not local or not remote
        else "match" if local == remote
        else "subset" if local <= remote or remote <= local
        else "overlap" if local & remote
        else "mismatch"
    )
    return row


def report(rows: list[dict]) -> None:
    def tally(key):
        out = {}
        for r in rows:
            out[r.get(key, "unavailable")] = out.get(r.get(key, "unavailable"), 0) + 1
        return out

    judged = [r for r in rows if r["verdict"] != "unavailable"]
    print(f"{len(rows)} documents, {len(judged)} with a registry record")
    print(f"  title:   {tally('verdict')}")
    print(f"  authors: {tally('author_verdict')}")
    for r in judged:
        if r["verdict"] in ("mismatch", "contains") or r.get("author_verdict") in (
            "mismatch", "overlap"
        ):
            print(f"\n  {r['document'][:52]}  [{r['verdict']}/{r.get('author_verdict')}]")
            print(f"    local    {str(r['local_title'])[:88]!r}")
            print(f"    registry {str(r.get('registry_title'))[:88]!r}")
            if r.get("author_verdict") not in ("match", "unavailable", None):
                print(f"    local    {r['local_authors']}")
                print(f"    registry {r.get('registry_authors')}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("out_dir", type=Path)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--delay", type=float, default=1.0,
                        help="seconds between registry requests")
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args()

    rows = []
    for doc in sorted(args.out_dir.iterdir()):
        versions = sorted(doc.glob("v*/provenance.json"))
        if not versions:
            continue
        row = score(versions[-1].parent, args.delay)
        if row:
            rows.append(row)
        if args.limit and len(rows) >= args.limit:
            break
    report(rows)
    if args.json:
        args.json.write_text(json.dumps(rows, indent=2, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
