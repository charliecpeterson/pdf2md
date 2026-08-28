"""Runtime configuration: a frozen dataclass loaded from TOML, no Pydantic."""

from __future__ import annotations

import hashlib
import tomllib
from dataclasses import asdict, dataclass, replace
from pathlib import Path


@dataclass(frozen=True)
class Config:
    # Parser backend. Docling remains the default for born-digital documents;
    # MinerU is the measured high-accuracy path for scans and difficult tables or
    # equations, installed in its own environment.
    engine: str = "docling"        # docling | mineru
    mineru_executable: str = "mineru"
    # Fine-deskew textless raster pages before MinerU layout detection. The gate
    # refuses weak, sub-degree, and out-of-range angles; original page geometry is
    # restored afterward so crops remain tied to the source PDF.
    deskew_scans: bool = True
    device: str = "auto"          # auto | mps | cuda | cpu
    # Formula→LaTeX enrichment is accurate but slow (minutes for equation-heavy
    # papers); turn off for speed or for large/scanned books.
    do_formula_enrichment: bool = True
    # Re-OCR the page images instead of trusting the embedded text layer, for a PDF whose
    # "text" is itself degraded OCR ("?3astman" for "Eastman") — indistinguishable from good
    # born-digital text, so it's opt-in (`--force-ocr`). The engine OCRs full pages and the
    # doc is treated as a scan (page rasters, word-split). Add --ocr-page-vlm for a
    # full-page vision transcription.
    force_ocr: bool = False
    # Recover inline sub/superscripts from glyph geometry (born-digital pages).
    detect_scripts: bool = True
    # Re-transcribe flagged (image-backed) equation crops with a local math-OCR
    # model (Surya). Opt-in: needs `surya-ocr` installed and is slow per crop, but
    # turns an OCR/garbled equation's hint into a real transcription. The crop image
    # stays the authoritative source either way.
    transcribe_equations: bool = False
    # Describe image crops (figures, image-fallback tables, image-backed equations)
    # with a vision model over an OpenAI-compatible API. Opt-in: needs the `openai`
    # extra and a reachable `vlm_base_url`, and adds latency/cost per crop. The crop
    # stays the authoritative source; the description rides below it as a labelled aid.
    # `vlm_base_url` points at a local server (ollama/vLLM/LM Studio) or a remote
    # endpoint; `vlm_model` must be a model that endpoint serves.
    describe_figures: bool = False
    # Transcribe each scanned page whole with the vision model. Page-level replacement
    # preserves Markdown layout but collapses element structure; use the MinerU engine when
    # equation/table blocks and their individual crops matter. Needs a describer + endpoint;
    # slow, so opt-in (`--ocr-page-vlm`). Born-digital pages are untouched.
    ocr_page_vlm: bool = False
    vlm_base_url: str = "http://localhost:11434/v1"
    vlm_model: str = "qwen3-vl:8b"
    # Optional OCR-tuned model for table crops; an OCR model reads dense grids more
    # faithfully than a general VLM. Equations/figures stay on vlm_model (VLMs give
    # cleaner LaTeX and plot descriptions). None → use vlm_model for every crop.
    vlm_ocr_model: str | None = None
    vlm_api_key: str | None = None
    # Per-call timeout (s) for the vision endpoint. A long whole-document run hammers a
    # local server; the client retries transient connection errors with backoff so they
    # don't silently degrade the output. Raise for a slow model, lower for a fast remote.
    vlm_timeout: float = 180.0
    # Cap generation per vision call so a model on a large context can't run away forever.
    # A reasoning VLM (qwen3-vl) spends tokens thinking before it answers, so a tight cap
    # can starve the reply; but too high just lets a model that never concludes stall for
    # many minutes (measured: 32k -> an 18-minute empty reply on a hard plot). 8k balances
    # room for reasoning + a data-dense answer against a fast-ish failure on pathological
    # input. The real fix for the stall is a no-think mode, which is endpoint-specific.
    vlm_max_tokens: int = 8192
    # Optional tighter generation cap for --figure-labels reads specifically. A figure's
    # printed text is short, so a labels read running long is a model loop (glm-ocr counts
    # or repeats until the cap). Lowering this (e.g. 1024) bounds that waste — but a
    # reasoning VLM (qwen3-vl) spends tokens thinking before it answers and returns EMPTY if
    # the cap starves the reply, so the tight cap is opt-in. None → use vlm_max_tokens.
    figure_labels_max_tokens: int | None = None
    crop_dpi: int = 220
    crop_padding_pts: float = 6.0
    # Figure crops are re-rendered on their own to hit this long-side pixel budget, the same
    # adaptive path the model-bound crops use. A figure cut from the crop_dpi page raster caps
    # its detail at whatever crop_dpi sampled, which blurs a small vector figure's atom/axis
    # labels; re-rendering the region climbs toward 600 dpi for the small ones and stays at
    # crop_dpi for the large ones (self-bounding, so no bloat on full-width figures).
    figure_crop_target_px: int = 1600
    # Model-bound crops (equations and image-fallback tables) are
    # re-rendered on their own to hit this long-side pixel budget, so a small dense
    # region's sub/superscripts are sharp for the OCR/VLM instead of capped at the
    # crop_dpi page raster. Self-bounding: DPI is clamped to [crop_dpi, 600], so big
    # regions stay at crop_dpi and only small ones climb. ~1600px matches what most
    # vision models ingest before downsampling; raising it past that only costs tokens.
    vlm_crop_target_px: int = 1600
    # Full-width regions on scanned pages otherwise stop at crop_dpi (220 by
    # default), which discards detail from a common 300-dpi source scan before
    # table OCR sees it. This is a floor, not a claim that upsampling creates detail.
    scan_crop_dpi: int = 300
    # Optional independent table reader. Its cells are compared to the engine's OCR
    # without changing either reading; agreement is evidence, not ground truth.
    table_ocr_executable: str | None = None
    # Optional semantic CSV reference: atomic_number,row_key,column,value. The file
    # content hash joins the run fingerprint so cached outputs cannot outlive it.
    table_reference_path: str | None = None
    # Optional GROBID service for bibliographic metadata (header fields, reference
    # strings, DOIs). Opt-in via --grobid-url; an unreachable service degrades to
    # the embedded-metadata heuristics with a warning, never a failed conversion.
    grobid_url: str | None = None
    grobid_timeout: float = 60.0
    # Resolve a locally observed DOI to CSL-JSON and retain the raw registry record.
    # Networked and time-varying, so conversion remains local-only unless explicitly enabled.
    doi_metadata: bool = False
    doi_metadata_timeout: float = 20.0
    # Render each image-backed equation's LaTeX and compare ink layout against its
    # source crop (needs the `eqrender` extra: matplotlib). Evidence tiers on the
    # block — similar/unclear/dissimilar/unrenderable — never rewrite anything.
    check_equation_render: bool = False
    # Re-read each figure under --figure-labels this many times and keep the modal
    # reading. Disagreement lowers confidence instead of exposing uncertain numbers as
    # clean text. Extra votes sample at ocr_consensus_temperature; vote 0 uses the
    # endpoint default.
    ocr_consensus_votes: int = 1
    ocr_consensus_temperature: float = 0.4
    # Recover plotted data from born-digital vector charts by reading the drawn path
    # coordinates (near-lossless; digitize.py). On by default: it's model-free and the
    # reader declines (returns nothing) unless it finds a framed plot box with numeric
    # ticks, so a scheme/structure/raster figure stays a crop untouched. Making the
    # reconstructable case ship reconstructed is the point — a vector chart becomes
    # md-sufficient instead of a bare crop. `--no-digitize` opts out. Carries a confidence.
    # The native bake-off passed all five vector fixtures; Docling emitted no data for
    # either scatter fixture, so this default has a measured reason to remain on.
    digitize_figures: bool = True
    # Tier 2: for figures the vector reader can't handle (raster/scanned plots), estimate
    # the data with the vision model. Needs a describer; approximate, so it rides at low
    # confidence with the image authoritative. Off by default.
    digitize_vlm: bool = False
    # Sample --digitize-vlm this many times per raster figure and keep the per-bin
    # median curve (the 2026 self-ensembling result): a single VLM decode is one
    # noisy draw; the median of N lands closer, and the across-draw dispersion rides
    # on the Digitization to scale confidence. 1 = single read (byte-identical to
    # before). Extra votes sample at digitize_consensus_temperature.
    digitize_consensus_votes: int = 1
    digitize_consensus_temperature: float = 0.4
    # Export each born-digital figure's region as SVG beside the PNG crop (render.svg_crop):
    # the lossless text form of a vector figure (diagrams/schemes, not just charts). Needs
    # pdftocairo (poppler) on PATH; degrades to PNG-only with a log line when absent.
    # Scanned pages are skipped — their SVG would just wrap the raster. Off by default.
    figure_svg: bool = False
    # Read the printed labels off each figure (axis titles, peak/data labels, legend) with
    # the vision model. Reliable for printed text where curve digitization can't be, so it
    # suits raster figures (published plots, spectra). Needs a describer; off by default.
    figure_labels: bool = False
    # Re-OCR each scanned figure's crop upright (model-free): the engine reads a sideways
    # scan's small text (axis ticks, titles) as garbage, and a derotated re-read recovers it
    # clean. On by default, no endpoint needed; born-digital figures aren't scanned and keep
    # their exact text-layer labels. `--no-figure-ocr` opts out (saves 4x OCR/figure on a
    # scanned book). Superseded by --figure-labels' vision read when that's requested.
    ocr_figures: bool = True
    # Re-split words RapidOCR ran together in a scanned line ('wherethefirst' -> 'where the
    # first'), for OCR'd prose only — born-digital text is untouched. English word frequencies
    # (wordninja); `--no-word-split` for a scanned non-English doc, where the split is wrong.
    # Comma/semicolon spacing ('ramp,toward') is fixed regardless — it's language-agnostic.
    resegment_ocr: bool = True
    # Retain a full-page raster for each scanned (OCR) page, linked from its page anchor,
    # so "verify against the image" works for prose — not just for the element crops. Only
    # scanned pages (born-digital pages have an authoritative text layer). Lower DPI than
    # crops: these are for reading the page, not pixel-faithful reproduction.
    page_images: bool = True
    page_image_dpi: int = 150
    # Render page rasters for EVERY page, not just scanned ones ("page-faithful
    # capture"): downstream readers can then check any answer against the source
    # image. Costs real disk — roughly 100-300 KB per page at page_image_dpi — so
    # it's opt-in; scanned pages are always captured when page_images is on.
    page_images_all_pages: bool = False
    # Directory of pre-downloaded Docling models (see `pdf2md models pull
    # --local-dir`). Set it to run fully offline and reproducibly — the local
    # snapshot is the pin. None = Docling's default Hugging Face cache.
    local_model_dir: str | None = None
    # Retrieval passages use an offline lexical counter by default. Set this to
    # hf:<model-or-local-path> when passage sizes must match an embedding model.
    passage_tokenizer: str = "lexical"
    passage_max_tokens: int = 512

    def __post_init__(self) -> None:
        if self.engine not in {"docling", "mineru"}:
            raise ValueError("engine must be 'docling' or 'mineru'")
        if self.engine == "mineru" and self.ocr_page_vlm:
            raise ValueError(
                "--ocr-page-vlm cannot be combined with the MinerU engine; "
                "page replacement would discard MinerU's equation and table structure"
            )
        if self.ocr_consensus_votes < 1:
            raise ValueError("ocr_consensus_votes must be at least 1")
        if self.digitize_consensus_votes < 1:
            raise ValueError("digitize_consensus_votes must be at least 1")
        if self.passage_max_tokens < 1:
            raise ValueError("passage_max_tokens must be positive")
        if self.passage_tokenizer != "lexical" and not self.passage_tokenizer.startswith("hf:"):
            raise ValueError(
                "passage_tokenizer must be 'lexical' or 'hf:<model-or-local-path>'"
            )

    def effective_dict(self) -> dict:
        """Serializable run inputs, excluding credentials that do not affect output."""
        values = asdict(self)
        values.pop("vlm_api_key")
        if values["local_model_dir"]:
            values["local_model_dir"] = str(
                Path(values["local_model_dir"]).expanduser().resolve()
            )
        if values["table_reference_path"]:
            reference = Path(values["table_reference_path"]).expanduser().resolve()
            values["table_reference_path"] = str(reference)
            values["table_reference_sha256"] = hashlib.sha256(reference.read_bytes()).hexdigest()
        return values

    @classmethod
    def load(cls, path: Path | None = None, *, base: "Config | None" = None) -> "Config":
        cfg = base or cls()
        if path is None:
            return cfg
        data = tomllib.loads(path.read_text())
        known = {f for f in cls.__dataclass_fields__}
        unknown = sorted(set(data) - known)
        if unknown:
            names = ", ".join(unknown)
            raise ValueError(f"unknown configuration key(s): {names}")
        return replace(cfg, **data)
