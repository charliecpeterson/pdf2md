"""How a figure reaches the Markdown: its caption, its labels, and its data.

A figure is the one element pdf2md cannot render as text, so what surrounds the
crop is the whole of what a reader gets: the caption, the printed text the engine
or an OCR pass recovered from inside it, a cross-reference to the table it names,
and — for a born-digital chart — the series read off the drawn paths, as CSV and
as a script that redraws them.

The crop stays authoritative throughout. A digitization below the emission floor
is written to `data/<stem>.withheld.csv` with a header saying so rather than
discarded, because a candidate reading a person can check beats a number nobody
can see.
"""

from __future__ import annotations

import re
from pathlib import Path

from pdf2md.confidence import PLOT_DATA_MIN_CONFIDENCE, plot_data_accepted
from pdf2md.schema import FigureRef

_TABLE_REF = re.compile(r"[Tt]ables?\s*([A-Z]?\d+[a-z]?|[IVXLC]+\b)")


def _table_xref(caption, labels) -> str:
    """A pointer to the printed data table when the figure's own text names one ('Listed
    in Table 5'). For a figure whose plot data couldn't be extracted, the table — which
    the pipeline extracts losslessly — is usually the authoritative text form of the same
    numbers, so say so where a reader (human or LLM) will look."""
    text = " ".join(t for t in (caption, labels.text if labels else None) if t)
    m = _TABLE_REF.search(text)
    if not m:
        return ""
    return (f"\n\n> **[pdf2md: the figure's text points at Table {m.group(1)} — "
            "the printed table is the authoritative data for this figure]**")


def _plot_data(
    dig,
    caption=None,
    labels=None,
    *,
    artifacts=None,
    withheld: str = "",
    status: str = "not_attempted",
    status_note: str = "",
) -> str:
    """Link or inline accepted chart data, and say where a withheld candidate went."""
    if dig is None:
        marker = ""
        if status and status != "not_attempted":
            detail = f" {status_note}" if status_note else ""
            marker = f"\n\n> **[pdf2md: plot data not extracted: {status}]**{detail}"
        return marker + _table_xref(caption, labels)
    if not dig.series:  # a gated read: the pre-scan vetoed digitization, say why
        return (f"\n\n> **[pdf2md: plot data not extracted — {dig.method}]** {dig.note}"
                + _table_xref(caption, labels))
    head = (f"> **[pdf2md: extracted plot data — {dig.method}, "
            f"confidence {dig.confidence:.2f}]** {dig.note}")
    if dig.verify_asset:  # round-trip: original vs reconstruction, for a human eyeball check
        head += f"\n\n![original vs reconstruction]({dig.verify_asset})"
    if not plot_data_accepted(dig):
        where = f" The candidate is in `{withheld}`." if withheld else ""
        return (f"\n\n{head}\n\n> **[pdf2md: data withheld — confidence below "
                f"{PLOT_DATA_MIN_CONFIDENCE:.2f}; read the values off the image above."
                f"{where}]**"
                + _table_xref(caption, labels))
    if artifacts:
        data_path, code_path = artifacts
        links = (
            f"[plot data (CSV)]({data_path}) · "
            f"[reproduction script (Python)]({code_path})"
        )
        return f"\n\n{head}\n\n{links}"
    return f"\n\n{head}\n\n{_plot_data_csv(dig)}\n\n{_plot_script(dig, caption, labels)}"


def _write_plot_artifacts(version_dir: Path, figure: FigureRef) -> tuple[str, str] | None:
    dig = figure.digitization
    figure.data_path = ""
    figure.code_path = ""
    if dig is None or not dig.series:
        return None

    stem = Path(figure.asset_path).stem if figure.asset_path else re.sub(
        r"[^a-zA-Z0-9]+", "_", figure.block_id
    ).strip("_")
    (version_dir / "data").mkdir(exist_ok=True)
    figure.data_path = f"data/{stem}.csv"
    figure.code_path = f"code/{stem}.py"

    csv_text = _plot_data_csv(dig).removeprefix("```csv\n").removesuffix("\n```")
    if not plot_data_accepted(dig):
        # Below the emission floor the candidate used to be computed and thrown
        # away, surviving only as a sentence in the markdown. On a vector figure
        # the curve geometry is read off the drawn paths and is sound; what is
        # uncertain is the axis calibration, so the shape is right and only the
        # scale is in doubt. A reader who can read one tick off the image can
        # calibrate it by hand, which is the difference between usable data and
        # none. It is written under a different name and never linked as data.
        figure.data_path = f"data/{stem}.withheld.csv"
        (version_dir / figure.data_path).write_text(
            f"# withheld: confidence {dig.confidence:.2f} is below the "
            f"{PLOT_DATA_MIN_CONFIDENCE:.2f} emission floor. {dig.note}\n"
            f"# The image beside this file is authoritative; these values are a "
            f"candidate reading, not a measurement.\n" + csv_text + "\n")
        figure.code_path = ""
        return None

    (version_dir / "code").mkdir(exist_ok=True)
    script = _plot_script(dig, figure.caption, figure.labels)
    script_text = script.removeprefix("```python\n").removesuffix("\n```")
    (version_dir / figure.data_path).write_text(csv_text + "\n")
    (version_dir / figure.code_path).write_text(script_text + "\n")
    return figure.data_path, figure.code_path


def _plot_data_csv(dig) -> str:
    """The recovered series as CSV, with the per-axis scale (linear/log) as a header so the
    numbers are unambiguous rather than bare x,y columns. Axis titles aren't included here —
    they come through --figure-labels; see Digitization."""
    blocks = [f"# x scale: {dig.x_kind}\n# y scale: {dig.y_kind}"]
    for i, series in enumerate(dig.series, 1):
        rows = "\n".join(f"{x},{y}" for x, y in series)
        name = dig.series_names[i - 1] if dig.series_names else f"series {i}"
        blocks.append(f"# {name}\nx,y\n{rows}")
    return "```csv\n" + "\n\n".join(blocks) + "\n```"


_SCRIPT_LABEL_LINES = 20  # a labels dump is context, not data; don't let it swamp the script


def _script_context(caption, labels) -> list[str]:
    """The figure's verified/recovered printed text as comment lines at the top of the repro
    script, so the script alone carries what the plot says (axis titles, legend, caption) —
    as comments, not set_xlabel guesses, because which line is which axis isn't known."""
    lines = []
    if caption:
        lines.append(f"# caption: {' '.join(caption.split())}")
    if labels is not None and labels.text:
        kept = [ln for ln in labels.text.splitlines() if ln.strip()]
        lines.append("# printed on the figure (recovered labels):")
        lines += [f"#   {ln}" for ln in kept[:_SCRIPT_LABEL_LINES]]
        if len(kept) > _SCRIPT_LABEL_LINES:
            lines.append(f"#   ... {len(kept) - _SCRIPT_LABEL_LINES} more (see labels above)")
    return lines + [""] if lines else []


def _plot_script(dig, caption=None, labels=None) -> str:
    """A self-contained matplotlib script that redraws the plot from the recovered data —
    assembled from the extracted series and axis scale, NOT generated by a model, so it
    reproduces the numbers we read rather than inventing any."""
    lines = ["import matplotlib.pyplot as plt", ""]
    lines += _script_context(caption, labels)
    lines += ["series = ["]
    lines += [f"    {[[x, y] for x, y in s]!r}," for s in dig.series]
    lines += ["]", "", "fig, ax = plt.subplots()"]
    if dig.kind == "bar":
        lines += ["for s in series:",
                  "    xs, ys = zip(*s)",
                  "    w = 0.8 * min((b - a for a, b in zip(xs, xs[1:])), default=1.0)",
                  "    ax.bar(xs, ys, width=w)"]
    else:
        linestyle = "'none'" if dig.kind == "scatter" else "'-'"
        lines += ["for s in series:",
                  "    xs, ys = zip(*s)",
                  f"    ax.plot(xs, ys, marker='o', linestyle={linestyle})"]
    if dig.x_kind == "log":
        lines.append("ax.set_xscale('log')")
    if dig.y_kind == "log":
        lines.append("ax.set_yscale('log')")
    lines += ["ax.set_xlabel('x')", "ax.set_ylabel('y')", "plt.show()"]
    return "```python\n" + "\n".join(lines) + "\n```"


def _figure_labels(fl) -> str:
    """Printed text read off a figure (--figure-labels), confidence-tagged, below the image.
    Empty when there's none."""
    if fl is None:
        return ""
    # The source (text layer vs OCR) rides in the note, which differs per tier.
    head = f"> **[pdf2md: printed figure labels, confidence {fl.confidence:.2f}]** {fl.note}"
    return f"\n\n{head}\n\n{fl.text}"


def _caption(text: str | None) -> str:
    """The figure's own caption as visible text below the image. It otherwise lives only in
    the image alt attribute, which a reader scanning the markdown never sees — so the figure
    isn't text-sufficient without it. Authoritative document text (from the PDF, not AI/OCR),
    so it's rendered plainly in italics, without the [pdf2md: ...] annotation the generated
    layers carry."""
    return f"\n\n*{text}*" if text else ""


_FIGURE_ALT_LABEL = re.compile(
    r"^\s*((?:fig(?:ure)?\.?)\s*[A-Za-z]?\d+(?:[.\-]\d+)*(?:[a-z])?)",
    re.IGNORECASE,
)


def _clean_alt(s: str) -> str:
    return re.sub(r"\s+", " ", s).replace("[", "(").replace("]", ")").strip()


def _figure_alt(caption: str | None) -> str:
    """Keep the image label useful without copying the full adjacent caption."""
    match = _FIGURE_ALT_LABEL.match(caption or "")
    return _clean_alt(match.group(1) if match else "figure")


def _description(text: str | None) -> str:
    """A VLM crop description (`--describe`), labelled as generated and placed below
    the image. Empty when there's none, so it appends cleanly. The content rides
    outside the marker blockquote so a transcribed GFM table still renders."""
    return f"\n\n> **[pdf2md: AI-generated description]**\n\n{text}" if text else ""
