from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher


_WS = re.compile(r"\s+")
_MATH_DELIMS = re.compile(r"\${1,2}")
_MD_BOLD = re.compile(r"\*\*([^*]+)\*\*")
_MD_ITALIC = re.compile(r"(?<!\*)\*([^*]+)\*(?!\*)")
_HTML_SUPSUB = re.compile(r"</?su[bp]>")
_LATEX_SUBSUP_BRACE = re.compile(r"\s*([_^])\s*\{\s*([^{}]*?)\s*\}")
_LATEX_SUBSUP_SIMPLE = re.compile(r"\s*([_^])\s*(\w)")
_LATEX_TAGS = re.compile(r"\\(?:tag|label|ref|eqref)\s*\{[^}]*\}")
_LATEX_TRAILING_REF = re.compile(r"\s*\(?\d+\.\d+\)?\s*$")
_NUMBER = re.compile(r"-?\d+(?:[.,]\d+)?")
_WORD_CHARS = re.compile(r"[A-Za-zα-ωΑ-Ω]+")
_EQ_REF = re.compile(r"\(?\d+\.\d+[a-z]?\)?")
_EQ_REF_ONLY = re.compile(r"^\s*\(?\d+\.\d+[a-z]?\)?\s*$")


def _strip_markdown(s: str) -> str:
    s = _MD_BOLD.sub(r"\1", s)
    s = _MD_ITALIC.sub(r"\1", s)
    s = _HTML_SUPSUB.sub("", s)
    return s


def _strip_math(s: str) -> str:
    s = _MATH_DELIMS.sub("", s)
    s = _LATEX_TAGS.sub("", s)
    s = _LATEX_SUBSUP_BRACE.sub(r"\1{\2}", s)
    s = _LATEX_SUBSUP_SIMPLE.sub(r"\1\2", s)
    return s


def _normalize(s: str) -> str:
    s = s.strip()
    s = _strip_markdown(s)
    s = _strip_math(s)
    s = s.replace("—", "-").replace("–", "-").replace(" ", " ").replace(" ", " ")
    s = _WS.sub(" ", s)
    return s.strip()


def _tokens(s: str) -> list[str]:
    return _normalize(s).split()


def _numbers(s: str) -> list[str]:
    return _NUMBER.findall(_normalize(s))


def _words(s: str) -> list[str]:
    return _WORD_CHARS.findall(_normalize(s).lower())


@dataclass
class LineDisagreement:
    a: str
    b: str
    similarity: float
    kind: str  # "missing_in_a" | "missing_in_b" | "diff"
    severity: str = "low"  # "low" | "medium" | "high"
    classification: str = "content"  # "formatting" | "math_format" | "numeric" | "content"
    numbers_a: list[str] = field(default_factory=list)
    numbers_b: list[str] = field(default_factory=list)


def _strip_eq_refs(s: str) -> str:
    """Remove equation reference markers like (2.54), {(2.46)}, tag{2.55}."""
    s = re.sub(r"\\tag\s*\{[^}]*\}", "", s)
    s = re.sub(r"\{?\(\d+\.\d+[a-z]?\)\}?", "", s)
    return s.strip()


def classify(a: str, b: str) -> tuple[str, str]:
    """Return (classification, severity) for a disagreement between two lines."""
    a_n = _normalize(a)
    b_n = _normalize(b)
    if a_n == b_n:
        return ("formatting", "low")

    if _EQ_REF_ONLY.match(a_n) and not b_n:
        return ("equation_ref", "low")
    if _EQ_REF_ONLY.match(b_n) and not a_n:
        return ("equation_ref", "low")

    a_no_ref = _normalize(_strip_eq_refs(a))
    b_no_ref = _normalize(_strip_eq_refs(b))
    if a_no_ref == b_no_ref:
        return ("equation_ref", "low")
    nums_a_clean = _NUMBER.findall(a_no_ref)
    nums_b_clean = _NUMBER.findall(b_no_ref)

    if nums_a_clean != nums_b_clean:
        return ("numeric", "high")

    words_a = _words(a)
    words_b = _words(b)
    if words_a == words_b:
        return ("math_format", "low")

    if a_n.startswith("$") or b_n.startswith("$") or "\\" in a or "\\" in b:
        return ("math_format", "medium")

    return ("content", "medium")


@dataclass
class PageAgreement:
    page: int
    similarity: float
    a_tokens: int
    b_tokens: int
    disagreements: list[LineDisagreement] = field(default_factory=list)


def _ratio(a: str, b: str) -> float:
    a_n, b_n = _normalize(a), _normalize(b)
    if not a_n and not b_n:
        return 1.0
    return SequenceMatcher(a=a_n, b=b_n).ratio()


def _significant(line: str) -> bool:
    line = line.strip()
    if not line:
        return False
    if line in {"<!-- image -->", "-----"}:
        return False
    if line.startswith("![") and "](" in line:
        return False
    return True


def _build(a: str, b: str, sim: float, kind: str) -> LineDisagreement:
    cls, sev = classify(a, b)
    return LineDisagreement(
        a=a,
        b=b,
        similarity=sim,
        kind=kind,
        classification=cls,
        severity=sev,
        numbers_a=_numbers(a),
        numbers_b=_numbers(b),
    )


def diff_lines(a_text: str, b_text: str, *, threshold: float = 0.85) -> list[LineDisagreement]:
    """Line-level diff between two markdown texts. Returns suspicious disagreements."""
    a_lines = [ln for ln in a_text.splitlines() if _significant(ln)]
    b_lines = [ln for ln in b_text.splitlines() if _significant(ln)]

    # Match on normalized lines so LaTeX/whitespace noise doesn't fragment alignment.
    a_norm = [_normalize(ln) for ln in a_lines]
    b_norm = [_normalize(ln) for ln in b_lines]

    sm = SequenceMatcher(a=a_norm, b=b_norm, autojunk=False)
    out: list[LineDisagreement] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        if tag == "replace":
            n = min(i2 - i1, j2 - j1)
            for k in range(n):
                la, lb = a_lines[i1 + k], b_lines[j1 + k]
                sim = _ratio(la, lb)
                if sim < threshold or _normalize(la) != _normalize(lb):
                    d = _build(la, lb, sim, "diff")
                    if d.classification != "formatting":
                        out.append(d)
            for k in range(n, i2 - i1):
                out.append(_build(a_lines[i1 + k], "", 0.0, "missing_in_b"))
            for k in range(n, j2 - j1):
                out.append(_build("", b_lines[j1 + k], 0.0, "missing_in_a"))
        elif tag == "delete":
            for ai in range(i1, i2):
                out.append(_build(a_lines[ai], "", 0.0, "missing_in_b"))
        elif tag == "insert":
            for bi in range(j1, j2):
                out.append(_build("", b_lines[bi], 0.0, "missing_in_a"))
    return out


def per_page_agreement(
    a_blocks_by_page: dict[int, list[str]],
    b_blocks_by_page: dict[int, list[str]],
) -> list[PageAgreement]:
    pages = sorted(set(a_blocks_by_page) | set(b_blocks_by_page))
    out: list[PageAgreement] = []
    for p in pages:
        a_text = "\n".join(a_blocks_by_page.get(p, []))
        b_text = "\n".join(b_blocks_by_page.get(p, []))
        sim = _norm_ratio(a_text, b_text)
        out.append(
            PageAgreement(
                page=p,
                similarity=sim,
                a_tokens=len(_tokens(a_text)),
                b_tokens=len(_tokens(b_text)),
                disagreements=diff_lines(a_text, b_text),
            )
        )
    return out


def _norm_ratio(a: str, b: str) -> float:
    a_n, b_n = _normalize(a), _normalize(b)
    if not a_n and not b_n:
        return 1.0
    return SequenceMatcher(a=a_n, b=b_n).ratio()


def overall_agreement(pages: list[PageAgreement]) -> dict[str, float | int]:
    if not pages:
        return {"pages": 0, "mean_similarity": 1.0, "flagged_pages": 0, "high_severity": 0}
    sims = [p.similarity for p in pages]
    flagged = sum(1 for p in pages if p.similarity < 0.85)
    high = sum(1 for p in pages for d in p.disagreements if d.severity == "high")
    medium = sum(1 for p in pages for d in p.disagreements if d.severity == "medium")
    return {
        "pages": len(pages),
        "mean_similarity": sum(sims) / len(sims),
        "min_similarity": min(sims),
        "flagged_pages": flagged,
        "high_severity": high,
        "medium_severity": medium,
    }
