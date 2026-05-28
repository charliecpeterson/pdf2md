"""Visual verification via a local vision-language model (Qwen3-VL by default).

The core primitive is `VerificationTask`: an image + prompt + parser + optional
confidence function. Public methods wrap it for common shapes (number reading,
metadata extraction, yes/no comparison). New verification types only need to
build a `VerificationTask` with the right prompt and parser — no boilerplate.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

DEFAULT_VLM_MODEL = os.environ.get(
    "DOCSMCP_VLM_MODEL", "mlx-community/Qwen3-VL-4B-Instruct-4bit"
)


# --- Result and task primitives ------------------------------------------------


@dataclass
class VLMResult:
    """One verification turn against a cropped image."""

    raw_response: str
    parsed: Any
    confidence: str  # "high" | "medium" | "low"
    notes: str | None = None


@dataclass
class VerificationTask:
    """A single VLM call. The verifier handles image loading + generation; the
    task supplies the prompt, parser, and (optional) confidence calculator."""

    image: Path
    prompt: str
    parser: Callable[[str], Any]
    max_tokens: int = 64
    confidence_fn: Callable[[Any, Any], str] | None = None
    extra: dict[str, Any] = field(default_factory=dict)


# --- Parsers (free functions, reusable across tasks) --------------------------


def parse_number(raw: str) -> str | None:
    """Extract a label-shaped number ('12' / '2.46' / '3a') from a VLM response.
    Returns None for 'NONE' or empty text."""
    text = (raw or "").strip()
    if not text or text.upper().startswith("NONE"):
        return None
    m = re.search(r"([0-9]+(?:\.[0-9]+)*[a-z]?)", text)
    return m.group(1) if m else None


def parse_yes_no(raw: str) -> bool | None:
    """Extract a yes/no answer, returning None if the VLM hedged."""
    text = (raw or "").strip().upper()
    if not text:
        return None
    if text.startswith(("YES", "Y", "TRUE", "MATCH")):
        return True
    if text.startswith(("NO", "N", "FALSE", "MISMATCH")):
        return False
    return None


_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")


def parse_metadata(raw: str) -> dict[str, str | None]:
    """Parse 'TITLE: ...\\nAUTHORS: ...\\nYEAR: ...' shaped response."""
    out: dict[str, str | None] = {"title": None, "authors": None, "year": None}
    for line in (raw or "").splitlines():
        line = line.strip()
        if not line:
            continue
        lower = line.lower()
        for key in ("title", "authors", "year"):
            prefix = key + ":"
            if lower.startswith(prefix):
                value = line[len(prefix):].strip()
                if value and value.upper() != "NONE":
                    out[key] = value
                break
    if out.get("year"):
        m = _YEAR_RE.search(out["year"])
        out["year"] = m.group(0) if m else None
    return out


# --- Confidence rules ---------------------------------------------------------


def number_confidence(parsed: str | None, expected: str | None) -> str:
    if parsed is None:
        return "low"
    if expected is None:
        return "medium"
    if parsed == expected:
        return "high"
    if parsed.lstrip("0").lower() == expected.lstrip("0").lower():
        return "high"
    return "low"


def yes_no_confidence(parsed: bool | None, expected: bool | None) -> str:
    """For comparison-style verifications (does X match Y?), the parsed value IS
    the answer; expected is typically True (we asked "do they match?")."""
    if parsed is None:
        return "low"
    if expected is None:
        return "medium" if parsed else "low"
    return "high" if parsed == expected else "low"


# --- The verifier --------------------------------------------------------------


class VLMVerifier:
    """Pluggable visual verifier. Lazy loads the model on first call."""

    _instance: "VLMVerifier | None" = None

    def __init__(self, model_name: str = DEFAULT_VLM_MODEL):
        self.model_name = model_name
        self._model = None
        self._processor = None
        self._config = None

    # ---- model lifecycle ----

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        from mlx_vlm import load
        from mlx_vlm.utils import load_config

        self._model, self._processor = load(self.model_name)
        self._config = load_config(self.model_name)

    def _generate(self, image_path: Path, prompt: str, max_tokens: int = 64) -> str:
        self._ensure_loaded()
        from mlx_vlm import generate
        from mlx_vlm.prompt_utils import apply_chat_template

        formatted = apply_chat_template(
            self._processor, self._config, prompt, num_images=1
        )
        output = generate(
            self._model,
            self._processor,
            formatted,
            image=[str(image_path)],
            max_tokens=max_tokens,
            verbose=False,
            temp=0.0,
        )
        text = output.text if hasattr(output, "text") else str(output)
        return text.strip()

    # ---- core: one task, one result ----

    def verify(self, task: VerificationTask, *, expected: Any = None) -> VLMResult:
        """Run a single VerificationTask and produce a VLMResult."""
        raw = self._generate(task.image, task.prompt, task.max_tokens)
        parsed = task.parser(raw)
        if task.confidence_fn is not None:
            confidence = task.confidence_fn(parsed, expected)
        elif parsed is None:
            confidence = "low"
        elif expected is None:
            confidence = "medium"
        else:
            confidence = "high" if parsed == expected else "low"
        return VLMResult(raw_response=raw, parsed=parsed, confidence=confidence)

    # ---- prompt templates ----

    _PROMPT_EQUATION_NUMBER = (
        "Look at this equation image. Find the equation number — usually a "
        "number in parentheses like (12) or (2.46) at the right edge.\n"
        "Reply with ONLY that number (digits only, no parentheses). "
        "If no number is visible, reply exactly: NONE\n"
        "Do not add explanation."
    )
    _PROMPT_PAGE_METADATA = (
        "This is page 1 of a document (paper, textbook, report, or article).\n"
        "Read the visible text and return EXACTLY this structure with one line per field:\n"
        "TITLE: <full title, or NONE>\n"
        "AUTHORS: <author list separated by commas, or NONE>\n"
        "YEAR: <4-digit publication year, or NONE>\n"
        "Do NOT include affiliations, abstracts, or commentary. "
        "If a field is genuinely not visible, write NONE."
    )
    _PROMPT_TABLE_CELL = (
        "Look at this image showing a single table cell. Reply with EXACTLY the "
        "text or number visible in the cell, nothing else. If the cell is empty, "
        "reply: NONE\n"
        "Do not add units, explanations, or commentary."
    )
    _PROMPT_TABLE_CELL_AT_POSITION = (
        "Look at this table image. Find the cell at row {row} and column {col} "
        "(both 1-indexed; row 1 is the header row, row 2 is the first data row).\n"
        "Reply with EXACTLY the text or number visible in that cell — no extra "
        "text, no units beyond what's shown, no explanation. If the cell is empty, "
        "reply: NONE\n"
        "If you genuinely cannot locate that cell, reply: UNKNOWN"
    )
    _PROMPT_TABLE_CELL_AT_LABEL = (
        "Look at this table image. Find the cell where the ROW is labeled "
        "'{row_label}' (look in the leftmost column) AND the COLUMN is labeled "
        "'{col_label}' (look in the top header rows; the column label may span "
        "multiple header rows joined by ' / ').\n"
        "Reply with EXACTLY the text or number visible in that cell — no extra "
        "text, no units beyond what's shown, no explanation. If the cell is empty, "
        "reply: NONE\n"
        "If you cannot find that row or column, reply: UNKNOWN"
    )
    _PROMPT_LATEX_MATCH = (
        "You will see an image of a mathematical equation and a LaTeX string that was "
        "extracted from the same paper. Decide whether the LaTeX is a faithful "
        "representation of the equation shown. The LaTeX may use slightly different "
        "formatting from the image (e.g. m_{{0}} vs m_0, \\frac vs /); focus on whether "
        "the same mathematical content is represented — same variables, operators, "
        "structure, signs.\n\n"
        "LaTeX: {latex}\n\n"
        "Reply with EXACTLY one word:\n"
        "- YES if the LaTeX faithfully represents the equation\n"
        "- NO if the LaTeX clearly differs (wrong symbols, missing parts, scrambled order)\n"
        "Do not add explanation."
    )
    _PROMPT_CAPTION_MATCH = (
        "You will see an image of a table or figure, and a caption text that was "
        "extracted from the same paper. Your job is to decide whether the caption "
        "is ABOUT THE SAME SUBJECT as the image. The caption may be abbreviated "
        "or use different wording — focus on whether the topic matches, not on "
        "exact phrasing.\n\n"
        "Caption: {caption}\n\n"
        "Reply with EXACTLY one word:\n"
        "- YES if the caption is plausibly about this image's content\n"
        "- NO only if the caption clearly describes a different subject\n"
        "Do not add explanation."
    )

    # ---- public wrappers ----

    def verify_equation_number(
        self, image_path: Path, expected_number: str | None = None
    ) -> VLMResult:
        """Look at a cropped equation image and return its printed number, if visible."""
        task = VerificationTask(
            image=image_path,
            prompt=self._PROMPT_EQUATION_NUMBER,
            parser=parse_number,
            max_tokens=32,
            confidence_fn=number_confidence,
        )
        return self.verify(task, expected=expected_number)

    def read_paper_metadata(self, page_image: Path) -> dict[str, str | None]:
        """Read title, authors, year from a rendered page-1 image.
        Returns {"title", "authors", "year", "raw"}."""
        task = VerificationTask(
            image=page_image,
            prompt=self._PROMPT_PAGE_METADATA,
            parser=parse_metadata,
            max_tokens=300,
        )
        result = self.verify(task)
        return {**(result.parsed or {}), "raw": result.raw_response}

    def verify_table_cell_by_label(
        self,
        table_image: Path,
        row_label: str,
        col_label: str,
        expected_text: str | None = None,
    ) -> VLMResult:
        """Ask VLM for the cell at (row_label, col_label) in a table image.

        More robust than index-based addressing for tables with multi-row headers:
        the VLM identifies the row by its leftmost-column label and the column
        by its header text, rather than counting positions.
        """
        prompt = self._PROMPT_TABLE_CELL_AT_LABEL.format(
            row_label=row_label, col_label=col_label
        )
        return self._verify_cell_response(table_image, prompt, expected_text)

    def verify_table_cell_at(
        self, table_image: Path, row: int, col: int, expected_text: str | None = None
    ) -> VLMResult:
        """Ask VLM to read the value at (row, col) of a table image (index-based)."""
        prompt = self._PROMPT_TABLE_CELL_AT_POSITION.format(row=row, col=col)
        return self._verify_cell_response(table_image, prompt, expected_text)

    def _verify_cell_response(
        self, table_image: Path, prompt: str, expected_text: str | None
    ) -> VLMResult:
        """Common cell-value response handling: liberal parse, tolerant equality."""

        def _parse(raw: str) -> str | None:
            text = (raw or "").strip()
            if not text or text.upper().startswith(("NONE", "UNKNOWN")):
                return None
            # Strip surrounding quotes the VLM sometimes adds
            return text.strip().strip('"').strip("'")

        def _confidence(parsed: str | None, expected: str | None) -> str:
            if parsed is None:
                return "low"
            if expected is None:
                return "medium"
            # Normalize for tolerant comparison (whitespace, surrounding parens)
            p_norm = parsed.strip().strip("()")
            e_norm = (expected or "").strip().strip("()")
            if p_norm == e_norm:
                return "high"
            # Try numeric equality
            try:
                if float(p_norm) == float(e_norm):
                    return "high"
            except (ValueError, TypeError):
                pass
            # Substring match (one contains the other)
            if p_norm and e_norm and (p_norm in e_norm or e_norm in p_norm):
                return "medium"
            return "low"

        task = VerificationTask(
            image=table_image,
            prompt=prompt,
            parser=_parse,
            max_tokens=32,
            confidence_fn=_confidence,
        )
        return self.verify(task, expected=expected_text)

    def verify_table_cell(
        self, cell_image: Path, expected_text: str | None = None
    ) -> VLMResult:
        """Read the text/number in a single table cell crop.

        Use when the extracted cell value contains suspicious patterns (mixed
        digits and letters like '1OO' for '100', misread superscripts, etc.).
        Compares VLM-read text to the heuristic value if expected_text given.
        """
        task = VerificationTask(
            image=cell_image,
            prompt=self._PROMPT_TABLE_CELL,
            parser=lambda raw: parse_number(raw) or raw.strip() or None,
            max_tokens=32,
        )
        return self.verify(task, expected=expected_text)

    def verify_latex_match(
        self, equation_image: Path, latex: str
    ) -> VLMResult:
        """Yes/no: does this LaTeX faithfully represent the equation in the image?

        Tolerates formatting differences (m_{0} vs m_0, \\frac vs /); focuses on
        whether the same math is represented. Catches OCR damage that
        number-only verification misses (whitespace bombs, column bleed,
        truncated content).
        """
        prompt = self._PROMPT_LATEX_MATCH.format(latex=latex)
        task = VerificationTask(
            image=equation_image,
            prompt=prompt,
            parser=parse_yes_no,
            max_tokens=8,
            confidence_fn=yes_no_confidence,
        )
        return self.verify(task, expected=True)

    def verify_caption_match(
        self, region_image: Path, caption: str
    ) -> VLMResult:
        """Yes/no: does this caption describe this image?

        Use as a low-cost cross-check on caption-pairing (figures, tables).
        Catches the case where Docling pairs a figure with the wrong caption
        due to layout ambiguity.
        """
        prompt = self._PROMPT_CAPTION_MATCH.format(caption=caption)
        task = VerificationTask(
            image=region_image,
            prompt=prompt,
            parser=parse_yes_no,
            max_tokens=8,
            confidence_fn=yes_no_confidence,
        )
        return self.verify(task, expected=True)


def get_default() -> VLMVerifier:
    if VLMVerifier._instance is None:
        VLMVerifier._instance = VLMVerifier()
    return VLMVerifier._instance
