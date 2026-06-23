"""Labelled accuracy harness: score converted output against hand-verified facts.

    uv run python scripts/eval_accuracy.py OUT_DIR [--check] [--labels FILE]

`qa.py` catches when output silently gets *worse* (labels-free regression);
`eval_equations.py` scores equation LaTeX. This is the third leg: per-archetype
structural facts a human verified from the source — text that must appear (and
font-decode dingbats that must not), a legibility floor, the expected confidence
grade, scan detection. It turns "is the conversion accurate?" into a number per
document type, and validates the profile.json signals against ground truth.

Reads existing outputs (no reconversion); match is by the `source` in profile.json.
`--check` exits non-zero on any failed fact.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

_LABELS = Path(__file__).parent.parent / "tests" / "accuracy_labels.json"


def _latest_with_profile(doc_dir: Path):
    versions = sorted(doc_dir.glob("v*"),
                      key=lambda p: int(p.name[1:]) if p.name[1:].isdigit() else -1,
                      reverse=True)
    for v in versions:
        if (v / "profile.json").exists():
            return v
    return None


def _find_output(out_dir: Path, source: str):
    """The newest version whose profile.json names `source` (its md text, profile)."""
    for doc_dir in sorted(p for p in out_dir.iterdir() if p.is_dir()):
        v = _latest_with_profile(doc_dir)
        if v is None:
            continue
        profile = json.loads((v / "profile.json").read_text())
        if profile.get("source") == source:
            md = "\n".join(p.read_text() for p in sorted(v.glob("*.md")))
            return md, profile
    return None, None


def check_doc(md: str, profile: dict, label: dict) -> list[tuple[bool, str]]:
    """(passed, description) for each fact in the label."""
    out: list[tuple[bool, str]] = []
    for s in label.get("must_contain", []):
        out.append((s in md, f"contains {s!r}"))
    for s in label.get("must_not_contain", []):
        out.append((s not in md, f"absent {s!r}"))
    if "min_legibility" in label:
        lo = label["min_legibility"]
        out.append((profile.get("prose_legibility", 0) >= lo, f"legibility >= {lo}"))
    if "confidence" in label:
        want = label["confidence"]
        out.append((profile.get("confidence") == want, f"confidence == {want}"))
    if "min_ocr_pages" in label:
        lo = label["min_ocr_pages"]
        out.append((profile.get("ocr_pages", 0) >= lo, f"ocr_pages >= {lo}"))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Score converted output against labelled accuracy facts.")
    ap.add_argument("out_dir", help="Output root (the `out/` directory).")
    ap.add_argument("--labels", default=str(_LABELS))
    ap.add_argument("--check", action="store_true", help="Exit non-zero on any failed fact.")
    args = ap.parse_args()

    labels = json.loads(Path(args.labels).read_text())
    out_dir = Path(args.out_dir)
    total = passed = failures = 0
    for label in labels:
        source = label["source"]
        md, profile = _find_output(out_dir, source)
        if md is None:
            print(f"  SKIP {source} (no output found)")
            continue
        facts = check_doc(md, profile, label)
        ok = sum(1 for p, _ in facts if p)
        total += len(facts)
        passed += ok
        mark = "ok" if ok == len(facts) else "FAIL"
        print(f"[{mark}] {source} ({label.get('archetype','')}): {ok}/{len(facts)} facts")
        for p, desc in facts:
            if not p:
                failures += 1
                print(f"       FAIL: {desc}")

    if total:
        print(f"\naccuracy: {passed}/{total} facts ({100 * passed // total}%)")
    if args.check and failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
