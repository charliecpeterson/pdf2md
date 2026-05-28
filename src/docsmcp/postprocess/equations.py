from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any


# Equation numbering patterns.
#  - \tag{N}                            — explicit LaTeX command
#  - & ( N )                            — alignment env separator (amsmath align)
#  - \quad ( N )                        — spacing then number
#  - trailing ( N ) or { ( N ) }        — at end of equation
#  - ( N N ) anywhere                   — digits separated by spaces (OCR artifact)
# Numbers accept dotted form (2.46 / 3.5a) and digit-spaced form (1 2 → 12).
_NUM_RE = r"([0-9]+(?:\.[0-9]+)*[a-z]?)"
_DIGIT_SPACED = r"([0-9](?:\s+[0-9]){0,3}[a-z]?)"

_TAG = re.compile(r"\\tag\s*\{\s*" + _NUM_RE + r"\s*\}")
_ALIGN_NUM = re.compile(r"\s+&\s*\(\s*" + _NUM_RE + r"\s*\)\s*")
_QUAD_NUM = re.compile(r"\\quad\s*\(\s*" + _NUM_RE + r"\s*\)\s*")
_TRAILING_PAREN = re.compile(
    r"[\s.,]*\{?\(\s*" + _NUM_RE + r"\s*\)\s*\}?\s*\$*\s*$"
)
_TRAILING_BRACE_PAREN = re.compile(
    r"\{\s*\(\s*" + _NUM_RE + r"\s*\)\s*\}\s*\$*\s*$"
)
# Internally-spaced number must have at least one whitespace between digits:
# `( 1 2 )` qualifies, `( 1 )` does not (avoids picking up subscript markers like p_(1)).
_PAREN_DIGIT_SPACED = re.compile(r"\(\s*([0-9](?:\s+[0-9]){1,3}[a-z]?)\s*\)")

# Column-bleed signal: in a two-column journal layout, body text can leak into
# equation bboxes as `& &` followed by text fragments. Strip from the first
# `& &` to end, but salvage any (N) numbers we find inside.
_COLUMN_BLEED_SPLIT = re.compile(r"\s+&\s+&\s+")


def _strip_math_delims(s: str) -> str:
    """Strip $$ ... $$ or $ ... $ wrapping (single-pass, both ends)."""
    s = s.strip()
    if s.startswith("$$") and s.endswith("$$"):
        s = s[2:-2].strip()
    elif s.startswith("$") and s.endswith("$"):
        s = s[1:-1].strip()
    return s


def _normalize_number(s: str) -> str:
    """Collapse internal whitespace in an OCR-mangled number ('1 2' → '12')."""
    return re.sub(r"\s+", "", s)


def strip_column_bleed(latex: str) -> tuple[str, list[str]]:
    """Split off everything after the first `& &` (column-bleed boundary).

    Returns (body_without_bleed, list_of_numbers_found_in_bleed). The bleed often
    contains the actual equation number plus body-text fragments from the next column.
    """
    m = _COLUMN_BLEED_SPLIT.search(latex)
    if not m:
        return latex, []
    body = latex[: m.start()].rstrip()
    tail = latex[m.start() :]
    nums: list[str] = []
    for nm in _PAREN_DIGIT_SPACED.finditer(tail):
        nums.append(_normalize_number(nm.group(1)))
    return body, nums


_HARD_LATEX_CAP = 1500


def truncate_repeat_loops(latex: str) -> tuple[str, bool]:
    """Detect & truncate runaway repeats — covers three observed failure modes:

    1. ` & & X` repeated 3+ times (column-bleed loop)
    2. `\\intertext{...}` (or other LaTeX commands) repeated 3+ times consecutively
    3. Equations exceeding a hard length cap (1500 chars) — almost always OCR loops

    Returns (truncated_text, was_truncated).
    """
    truncated = False

    # Mode 1: & & X loop
    parts = latex.split(" & & ")
    if len(parts) >= 4:
        last = parts[-1].strip()
        if len(last) >= 6:
            repeat_count = 1
            for i in range(len(parts) - 2, -1, -1):
                if parts[i].strip() == last:
                    repeat_count += 1
                else:
                    break
            if repeat_count >= 3:
                keep = parts[: len(parts) - repeat_count]
                latex = " & & ".join(keep).rstrip() + f"   /* truncated ×{repeat_count} */"
                truncated = True

    # Mode 2: any \cmd{...} repeated 3+ times — scan for repeated substrings of >= 40 chars
    if not truncated and len(latex) >= 200:
        n = len(latex)
        for chunk_size in (80, 120, 200):
            if chunk_size * 3 > n:
                continue
            for start in range(0, n - chunk_size * 3, max(1, chunk_size // 4)):
                cand = latex[start : start + chunk_size]
                if (
                    latex[start + chunk_size : start + 2 * chunk_size] == cand
                    and latex[start + 2 * chunk_size : start + 3 * chunk_size] == cand
                ):
                    latex = latex[: start + chunk_size] + "   /* loop truncated */"
                    truncated = True
                    break
            if truncated:
                break

    # Mode 3: hard length cap
    if len(latex) > _HARD_LATEX_CAP:
        latex = latex[:_HARD_LATEX_CAP] + "   /* length-capped */"
        truncated = True

    return latex, truncated


def _number_is_plausible_label(number: str, body: str) -> bool:
    """Sanity check on a candidate equation number.

    Rejects obvious data values masquerading as labels: numbers >= 50 with no
    chapter prefix that also appear elsewhere in the equation body (typically
    temperatures like 310, masses like 100, etc.).
    """
    if not number:
        return False
    if "." in number:
        return True
    # Single-digit integers are almost certainly labels
    if number.isdigit() and int(number) < 50:
        return True
    if not number.isdigit():
        return True  # roman numerals or letter-suffixed labels, pass through
    n = int(number)
    if n >= 1000:
        return False
    # Reject if the same number appears as a value elsewhere in the body
    pat = re.compile(rf"(?<!\d)({number})(?!\d)")
    matches = pat.findall(body)
    return len(matches) <= 1


def extract_number(latex: str) -> tuple[str | None, str]:
    """Find an equation number in `latex`. Returns (number_or_None, body_without_number).

    Tries in priority order: \\tag{N}, align `& (N)`, `\\quad (N)`, trailing `(N)`,
    digit-spaced `( N N )` anywhere, then numbers salvaged from column-bleed tail.
    Rejects candidates that look like data values (large integers appearing
    multiple times in the body).
    """
    body = latex

    for pat in (_TAG, _ALIGN_NUM, _QUAD_NUM):
        m = pat.search(body)
        if m:
            number = _normalize_number(m.group(1))
            if _number_is_plausible_label(number, body):
                body = (body[: m.start()] + " " + body[m.end() :]).strip()
                return number, body

    for pat in (_TRAILING_BRACE_PAREN, _TRAILING_PAREN):
        m = pat.search(body)
        if m:
            number = _normalize_number(m.group(1))
            if _number_is_plausible_label(number, body):
                body = body[: m.start()].strip()
                return number, body

    # Column-bleed: strip the tail, salvage any numbers found inside.
    clean_body, bleed_nums = strip_column_bleed(body)
    if bleed_nums:
        # Prefer the last (rightmost) number — typically the actual eq number.
        for cand in reversed(bleed_nums):
            if _number_is_plausible_label(cand, clean_body):
                return cand, clean_body

    # Last resort: digit-spaced `( N N )` anywhere — prefer the LAST occurrence
    # (avoids matching `_{p_{1}}` style fragments earlier in the equation).
    matches = list(_PAREN_DIGIT_SPACED.finditer(clean_body))
    if matches:
        m = matches[-1]
        number = _normalize_number(m.group(1))
        # Multiple identical digit-spaced occurrences = data value, not a label.
        # e.g., temperatures: `(T/K) + ... \times (3 1 0) + ... (3 1 0)^2`
        same_count = sum(
            1 for x in matches if _normalize_number(x.group(1)) == number
        )
        if same_count > 1:
            return None, clean_body
        if _number_is_plausible_label(number, clean_body):
            body = (clean_body[: m.start()] + " " + clean_body[m.end() :]).strip()
            return number, body

    return None, clean_body


# LaTeX cleanup patterns — Docling/Marker emit space-padded scripts; tighten them.
_SUBSUP_BRACED = re.compile(r"\s*([_^])\s*\{\s*([^{}]*?)\s*\}")
_SUBSUP_SINGLE = re.compile(r"\s*([_^])\s*(\w)(?!\w)")
_FRAC = re.compile(r"\\frac\s*\{\s*([^{}]*?)\s*\}\s*\{\s*([^{}]*?)\s*\}")
_INT_BOUNDS = re.compile(r"\\int\s*_\s*\{\s*([^{}]*?)\s*\}\s*\^\s*\{\s*([^{}]*?)\s*\}")
_DOUBLE_SPACE = re.compile(r" {2,}")
_SPACE_BEFORE_PUNCT = re.compile(r"\s+([,.;])")


def clean_latex(latex: str) -> str:
    """Normalize spaced LaTeX. Idempotent."""
    s = latex
    # Repeated apply for nested braces
    for _ in range(3):
        s_new = _SUBSUP_BRACED.sub(r"\1{\2}", s)
        s_new = _FRAC.sub(r"\\frac{\1}{\2}", s_new)
        s_new = _INT_BOUNDS.sub(r"\\int_{\1}^{\2}", s_new)
        if s_new == s:
            break
        s = s_new
    s = _SUBSUP_SINGLE.sub(r"\1\2", s)
    s = _SPACE_BEFORE_PUNCT.sub(r"\1", s)
    s = _DOUBLE_SPACE.sub(" ", s)
    return s.strip()


@dataclass
class ParsedEquation:
    eq_id: str
    doc_id: str
    seq: int
    number: str | None
    latex_raw: str
    latex_clean: str
    page: int
    block_id: str
    bbox_json: str | None
    context_before: str | None = None
    context_after: str | None = None
    inferred: bool = False
    flags: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.flags is None:
            self.flags = []


def _infer_numbers(equations: list[ParsedEquation]) -> None:
    """For equations missing a number, infer from numbered neighbors."""
    from docsmcp.postprocess.inference import infer_numbers

    infer_numbers(equations)


def _eq_id(doc_id: str, seq: int, latex: str) -> str:
    h = hashlib.sha1(f"{doc_id}:{seq}:{latex[:200]}".encode()).hexdigest()
    return f"eq{h[:12]}"


def parse_equations(
    doc_id: str, blocks: list[dict[str, Any]], *, context_chars: int = 240
) -> list[ParsedEquation]:
    """Walk a doc's blocks, return one ParsedEquation per equation block.

    Captures the trailing paragraph before and the paragraph after as context.
    """
    import json as _json

    out: list[ParsedEquation] = []
    seq = 0
    n = len(blocks)
    for i, b in enumerate(blocks):
        if (b.get("type") or "").lower() != "equation":
            continue
        raw = (b.get("text") or "").strip()
        if not raw:
            continue

        body = _strip_math_delims(raw)
        flags: list[str] = []

        body, was_truncated = truncate_repeat_loops(body)
        if was_truncated:
            flags.append("repeat_loop_truncated")

        number, body_no_num = extract_number(body)

        # If the body still has column-bleed text, strip it after number extraction.
        if " & & " in body_no_num:
            cleaned_body, _ = strip_column_bleed(body_no_num)
            if cleaned_body and cleaned_body != body_no_num:
                body_no_num = cleaned_body
                flags.append("column_bleed_stripped")

        cleaned = clean_latex(body_no_num)
        if not cleaned:
            continue

        before = None
        for j in range(i - 1, max(-1, i - 6), -1):
            b2 = blocks[j]
            if (b2.get("type") or "") == "paragraph" and (b2.get("text") or "").strip():
                before = (b2["text"] or "").strip()
                break

        after = None
        for j in range(i + 1, min(n, i + 6)):
            b2 = blocks[j]
            if (b2.get("type") or "") == "paragraph" and (b2.get("text") or "").strip():
                after = (b2["text"] or "").strip()
                break

        if before and context_chars and len(before) > context_chars:
            before = before[-context_chars:]
        if after and context_chars and len(after) > context_chars:
            after = after[:context_chars]

        bbox = b.get("bbox")
        out.append(
            ParsedEquation(
                eq_id=_eq_id(doc_id, seq, cleaned),
                doc_id=doc_id,
                seq=seq,
                number=number,
                latex_raw=raw,
                latex_clean=cleaned,
                page=int(b.get("page", 0)),
                block_id=b.get("id", ""),
                bbox_json=_json.dumps(bbox) if bbox else None,
                context_before=before,
                context_after=after,
                flags=flags,
            )
        )
        seq += 1
    _infer_numbers(out)
    return out
