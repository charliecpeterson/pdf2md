"""Describe an image crop with a vision model over an OpenAI-compatible API.

The pipeline crops every figure, image-fallback table, and image-backed equation to
a faithful PNG; those crops are opaque to a text consumer (a screen reader or an
LLM). This re-reads each crop with a vision model and stores a short description — or
a transcription, for tables/equations — as a labelled aid below the image. The crop
stays the authoritative source, so a wrong description is never the source of truth.

`Describer` is the seam — anything with `describe(image_path, kind, context)`.
`OpenAIVisionDescriber` speaks the OpenAI `/v1/chat/completions` vision protocol, so
the one client points at a local server (ollama, vLLM, LM Studio) or a remote
endpoint purely via `base_url`/`model`. It is lazy-imported and optional: with the
`openai` client absent, or the pass off, the pipeline simply skips it.
"""

from __future__ import annotations

import base64
import difflib
import hashlib
import json
import re
from pathlib import Path
from typing import Protocol, runtime_checkable

from pdf2md.logging import get_logger

log = get_logger("describe")

# Per-crop-kind instruction. The crop is authoritative, so the prompt asks for a
# faithful reading and forbids the exact-value invention a vision model is prone to.
_PROMPTS = {
    "figure": (
        "Describe this figure from a scientific document: the kind of figure (plot, "
        "diagram, scheme, flowchart, ...), what it shows, and the key variables or "
        "relationships. Transcribe embedded text — axis labels, legends, and any program "
        "or command names — exactly as written, character for character. If there are "
        "several curves or data series, describe each distinct one, not just the most "
        "prominent. Be specific and factual but do not invent exact numerical values you "
        "cannot read clearly. Write a few sentences of plain prose — no bullet lists, no "
        "repetition, and no asides about typos or image quality."
    ),
    "table": (
        "Transcribe this table to GitHub-flavored Markdown. If its structure is too "
        "complex for a clean grid, instead describe its columns and what it contains. "
        "Do not invent values."
    ),
    "equation": (
        "Transcribe this equation to LaTeX. Output only the LaTeX, with no surrounding text."
    ),
    "digitize": (
        "This is a data plot. Read the plotted data and return ONLY JSON, no prose:\n"
        '{"x_axis": "label", "y_axis": "label", "series": '
        '[{"label": "...", "points": [[x, y], ...]}]}\n'
        "Read each point's x and y from its position against the numbered axis ticks, as "
        "accurately as the pixels allow. Do not invent points that aren't plotted. If you "
        'cannot read the axes or there is no data plot, return {"series": []}.'
    ),
    "labels": (
        "Transcribe every piece of printed text on this figure exactly as written — axis "
        "titles and units, numeric peak/data labels, legend entries, and callouts. Output "
        "the text only, one item per line — no description, no commentary, no repetition. "
        "Do not write sentences about what the figure shows. Copy only text actually printed "
        "on the image; if a value is not clearly legible, omit it rather than guessing."
    ),
    "page": (
        "Transcribe this scanned document page to clean GitHub-flavored Markdown, exactly as "
        "written. Preserve the reading order, headings, paragraphs, and lists, and render any "
        "table as a Markdown table. Transcribe printed text character for character; do not "
        "describe, summarize, or caption the images, and do not invent values you cannot read. "
        "Output only the transcription — no commentary, and no code fence around the whole page."
    ),
}

VISION_CACHE_SCHEMA_VERSION = 2
VISION_PROMPT_SHA256 = hashlib.sha256(
    json.dumps(_PROMPTS, sort_keys=True, separators=(",", ":")).encode()
).hexdigest()


def _prompt(kind: str, context: str = "") -> str:
    base = _PROMPTS.get(kind, _PROMPTS["figure"])
    return f"{base}\n\nContext: {context.strip()}" if context.strip() else base


def vision_cache_key(image_path: Path, describer, kind: str, *, context: str = "",
                     temperature: float | None = None, max_tokens: int | None = None,
                     endpoint: str = "") -> str:
    """Identity for one vision inference, including every output-shaping input."""
    payload = {
        "schema": VISION_CACHE_SCHEMA_VERSION,
        "endpoint": endpoint,
        "model": describer.model_for(kind),
        "kind": kind,
        "prompt": _prompt(kind, context),
        "image_sha256": hashlib.sha256(Path(image_path).read_bytes()).hexdigest(),
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return f"vision-v{VISION_CACHE_SCHEMA_VERSION}:{digest}"


# Kinds that route to `ocr_model` (when configured): table grids, figure labels, and a
# whole-page transcription (`page`) read far more faithfully on an OCR-tuned model — glm-ocr
# transcribed a full page in 7s exactly, where qwen3-vl:32b took minutes per page and 8b
# escaped/emptied. Equations do NOT — a general VLM transcribes them to cleaner LaTeX (OCR
# models add the equation-number \tag and CJK punctuation), and --transcribe (Surya) is the
# dedicated math path if you want one. `digitize` routes to the OCR model too: the 2026-07
# bake-off had qwen3-vl (8b AND 32b) think past the whole token budget on the JSON-digitize
# prompt and return nothing, while glm-ocr answered in format.
_OCR_KINDS = {"table", "labels", "page", "digitize"}


@runtime_checkable
class Describer(Protocol):
    def describe(self, image_path: Path, kind: str, context: str = "",
                 temperature: float | None = None,
                 max_tokens: int | None = None) -> str | None: ...
    def model_for(self, kind: str) -> str: ...


def _data_uri(image_path: Path) -> str:
    return f"data:image/png;base64,{base64.b64encode(image_path.read_bytes()).decode()}"


_REPEAT_SIM = 0.97   # near-exact: a loop re-emits the SAME line; distinct table/TOC rows differ more
_LOOP_MIN = 5        # a line must recur near-identically this many times to count as a loop
_BLOCK_SEED_LINES = 3
_BLOCK_SEED_CHARS = 120
_FENCE = re.compile(r"^\s*```.*$", re.MULTILINE)


def _decode_escaped_lines(text: str) -> str:
    """Some vision models (seen on qwen3-vl:8b) return the whole answer as one escaped line —
    '# Title\\n\\nBody' with literal backslash-n instead of real newlines — which renders as
    one broken line. When that happens (literal \\n present, no real newline), turn the
    newline/tab escapes back into whitespace so the markdown renders. Normal multi-line output
    is left untouched, and unicode is not decoded (so Greek/accented text survives)."""
    if "\\n" in text and "\n" not in text:
        return text.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\t", "\t")
    return text


def clean_vlm_text(text: str) -> tuple[str, bool]:
    """Scrub the ways an OCR/vision model ignores 'plain text only': a stray markdown code
    fence wrapping the answer (glm-ocr emits a dangling ```markdown), escaped newlines (some
    models return one \\n-joined line), and a generation loop. Returns (cleaned text,
    loop_truncated) — the flag lets the caller mark a block whose tail was dropped, so a loop
    is never a *silent* loss. Compliant output is unchanged (returns it verbatim, flag False)."""
    cleaned, truncated = collapse_repeats(_FENCE.sub("", _decode_escaped_lines(text)))
    return cleaned.strip(), truncated


def collapse_repeats(text: str) -> tuple[str, bool]:
    """Truncate a generation loop, conservatively. A loop re-emits essentially the *same*
    line many times until the token limit; the danger is mistaking that for legitimately
    similar-but-distinct lines (a scanned data table, TOC, or bibliography), which must not
    be dropped. So a line only counts as a loop if a near-identical copy of it recurs
    `_LOOP_MIN`+ times close by; then keep that first clean copy and drop the loop, and
    report it. No such line -> text returned verbatim. Returns (text, truncated)."""
    lines = text.splitlines()
    nonblank = [(i, ln.strip()) for i, ln in enumerate(lines) if ln.strip()]
    for a, (idx, s) in enumerate(nonblank):
        window = nonblank[a + 1:a + 61]  # a loop repeats densely; no need to scan the whole block
        copies = sum(1 for _, t in window
                     if difflib.SequenceMatcher(None, s, t).ratio() >= _REPEAT_SIM)
        if copies >= _LOOP_MIN:
            return "\n".join(lines[:idx + 1]), True  # keep the first clean copy; drop the loop
    for start in range(len(nonblank) - _BLOCK_SEED_LINES + 1):
        seed = tuple(text for _, text in nonblank[start:start + _BLOCK_SEED_LINES])
        if sum(map(len, seed)) < _BLOCK_SEED_CHARS:
            continue
        matches = [
            candidate
            for candidate in range(start + _BLOCK_SEED_LINES, len(nonblank) - _BLOCK_SEED_LINES + 1)
            if tuple(text for _, text in nonblank[candidate:candidate + _BLOCK_SEED_LINES]) == seed
        ]
        if len(matches) >= 2:
            second_copy = nonblank[matches[0]][0]
            return "\n".join(lines[:second_copy]).rstrip(), True
    return text, False


class OpenAIVisionDescriber:
    """Vision description over any OpenAI-compatible endpoint. The only version- or
    server-specific surface is `_run` (the chat-completions call); pointing at a
    different host or model is pure config (`base_url`, `model`, `api_key`)."""

    def __init__(self, base_url: str, model: str, api_key: str | None = None,
                 ocr_model: str | None = None, timeout: float = 180.0,
                 max_retries: int = 5, max_tokens: int = 8192) -> None:
        try:
            from openai import OpenAI
        except ImportError as exc:  # openai is an optional extra
            raise RuntimeError(
                "describe needs the openai client — add the `describe` extra to the env "
                "pdf2md runs from: `uv run --extra describe pdf2md ...` from the project, "
                'or `uv tool install --force -e ".[describe]"` for the global tool.'
            ) from exc
        # Local servers (ollama, vLLM) ignore the key, but the client requires one set.
        # A whole-document run fires one call per crop/block (thousands on a long scan),
        # which makes a local endpoint drop connections under load; max_retries backs off
        # and retries transient errors so those failures don't silently degrade the output.
        self._client = OpenAI(base_url=base_url, api_key=api_key or "not-needed",
                              timeout=timeout, max_retries=max_retries)
        self._model = model
        self._ocr_model = ocr_model or model  # OCR kinds fall back to the main model
        self._max_tokens = max_tokens
        self.calls = 0
        self.failures = 0
        self.last_truncated = False  # did the most recent call hit the token cap (finish_reason=length)

    def model_for(self, kind: str) -> str:
        return self._ocr_model if kind in _OCR_KINDS else self._model

    def _run(self, model: str, prompt: str, data_uri: str,
             temperature: float | None = None, max_tokens: int | None = None) -> str:
        # temperature is passed only when set, so a single-read call stays byte-identical
        # to the endpoint default; consensus votes raise it for independent samples.
        extra = {} if temperature is None else {"temperature": temperature}
        cap = max_tokens or self._max_tokens  # a caller can bound tighter than the doc default
        resp = self._client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": data_uri}},
            ]}],
            max_tokens=cap,  # bound generation: a loop on a big context runs for minutes
            **extra,
        )
        choice = resp.choices[0]
        if getattr(choice, "finish_reason", None) == "length":  # hit the cap: may be cut mid-text
            self.last_truncated = True
            log.warning("vision output hit the %d-token cap and may be truncated", cap)
        return (choice.message.content or "").strip()

    def describe(self, image_path: Path, kind: str, context: str = "",
                 temperature: float | None = None,
                 max_tokens: int | None = None) -> str | None:
        self.calls += 1
        self.last_truncated = False
        try:
            # Raw model text; the repetition guard runs at consumption in the pipeline so
            # it also cleans values served from an older cache (which bypass this method).
            return self._run(self.model_for(kind), _prompt(kind, context),
                             _data_uri(image_path), temperature, max_tokens) or None
        except Exception as exc:  # noqa: BLE001 - best-effort; the crop is the source
            self.failures += 1
            log.warning("description failed for %s: %s", image_path.name, exc)
            return None


def get_describer(config) -> Describer | None:
    """Build the configured describer, or None when both vision passes are off."""
    if not (getattr(config, "describe_figures", False)
            or getattr(config, "ocr_page_vlm", False) or getattr(config, "digitize_vlm", False)
            or getattr(config, "figure_labels", False)):
        return None
    return OpenAIVisionDescriber(
        base_url=getattr(config, "vlm_base_url", "http://localhost:11434/v1"),
        model=getattr(config, "vlm_model", ""),
        api_key=getattr(config, "vlm_api_key", None),
        ocr_model=getattr(config, "vlm_ocr_model", None),
        timeout=getattr(config, "vlm_timeout", 180.0),
        max_tokens=getattr(config, "vlm_max_tokens", 8192),
    )
