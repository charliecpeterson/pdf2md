"""Token-level signals: what the page printed against what was emitted.

Read-only. Nothing here repairs a block — `enrich` does the repairing, and these
run after it so they measure the finished text. Two signals, and the difference
between them is the point.

Word recall compares a block's emitted words against the glyph layer's reading of
its own region, both tokenized the same way: script-split, hyphen-joined, glued
words separated. Getting those three wrong is what made ten of eleven low-recall
blocks on a clean paper metric bugs rather than defects, and the corpus went from
over 1,200 recall actions to 124 when they were fixed. Precision against poppler
is 0.72-0.74.

Symbol loss is the check recall cannot be: a 200-word paragraph that drops one
`χ` scores 0.995 and passes the floor, which is the right answer to the question
recall asks. So it asks a different one, over a character class narrow enough to
be exact — a Greek letter or math operator in the region and absent from the
output admits no innocent reading, where a dash does. Precision 0.97, and 0.3% of
symbol loss goes unraised.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter, defaultdict

from pdf2md.conservation import semantic_output
from pdf2md.logging import get_logger
from pdf2md.normalize import expand_ligature_glyphs
from pdf2md.schema import (
    PROSE_TYPES,
    BBox,
    Block,
    BlockType,
    CoverageFlag,
    TableData,
)
from pdf2md.tables import render_table

log = get_logger("recall")

# What counts as a shattered fragment rather than content: both the emitted
# text and its source region are this small. Sized so a contents entry like
# `Simple mixtures` missing its printed `5` stays measured -- that is a real
# list-marker case with 15 characters of output, not a fragment.
_FRAGMENT_TOKENS = 3

_FRAGMENT_CHARS = 3

LOW_RECALL_BELOW = 0.90

# How much of the smaller box two prose regions may share before neither
# block's recall is decidable.
_AMBIGUOUS_REGION_SHARE = 0.15

_SCRIPT_TAGS = re.compile(r"</?(?:sub|sup)>")

_WORDS = re.compile(r"\w+")

# A word the page breaks across a line is one word; the emitter rejoins it. The
# break character is not always a hyphen: a font with no ToUnicode entry for its
# soft hyphen surfaces as U+00AD, U+FFFE, or the TeX control byte, and a metric
# that only knew about `-` would score every such line break as a lost word.
_INTRAWORD_HYPHEN = re.compile(
    "([^\\W\\d_])[-\u2010\u2011\u00ad\ufffe\x02]\\s*([^\\W\\d_])"
)

def _recall_words(text: str) -> list[str]:
    """Tokenize a reading for the recall comparison. Both sides get the same
    treatment, so the only differences left are content differences: a word the
    page breaks across lines is one word (the emitter rejoins it, and a metric
    that didn't would score every hyphenated line-break as a loss), and script
    tags are emission syntax.

    A TeX f-ligature is expanded here for the same reason the line-break hyphen
    is: the emitted side had it expanded upstream, so leaving the glyph layer's
    raw control byte alone scores `configuration` against the layer's `con` +
    `guration` as two losses and a phantom word. Every f-ligature in the
    document, on every side of the comparison."""
    text = expand_ligature_glyphs(text)
    # A space, not nothing: the source side is read script-split, so `X<sub>UFF</sub>`
    # has to tokenize as `X UFF` the way the glyph reading of the same ink does.
    # Dropping the tags instead makes every scripted run a phantom miss in one
    # direction or the other, depending on whether the engine glued or separated.
    text = unicodedata.normalize("NFKC", _SCRIPT_TAGS.sub(" ", text)).lower()
    while (joined := _INTRAWORD_HYPHEN.sub(r"\1\2", text)) != text:
        text = joined
    return _WORDS.findall(text)

def record_block_recall(block: Block, pc, emitted: str | None = None) -> None:
    """Measure how much of the block's source region survived into its emitted
    text: a word-multiset comparison against the glyph layer, order-insensitive
    so scrambled draw order doesn't read as loss. Stored in `extra` for
    provenance; aggregated by `recall_summary`. Skipped when the region holds no
    words (a bbox/layer mismatch measures nothing).

    `emitted` is the output side when it isn't `block.text` -- a block carrying a
    table renders from its cells and leaves `text` empty, so comparing against
    `text` scores every printed word as lost.

    The source side is read script-split, like the page prints it rather than
    like the draw order glues it: `technetium67` is two tokens on the page, and
    comparing the glued reading against correctly separated output scores the
    marker as a phantom loss.

    `strict` is the same comparison without diacritic folding, and readers of it
    default to `matched` so a bundle written before it existed reloads cleanly
    through `StoredEngine`. The gap between
    the two is accent damage specifically — an emitted `Co te` for `Côté` — which
    is a real defect but a different one from a missing word, and worth telling
    apart before either is flagged."""
    src = _recall_words(pc.region_scriptsplit(block.bbox))
    if not src:
        return
    content = block.text if emitted is None else emitted
    # A shattered display equation reaches here as blocks of one or two
    # characters -- `aT`, `bV`, `T`, `)` -- whose region holds as little. A
    # ratio over one or two tokens says nothing either way, and 30 of the 46
    # low-recall blocks with three source tokens or fewer are these. Both sides
    # have to be tiny: a block whose region holds a hundred words and emits two
    # characters is a catastrophic loss and stays measured, which is what keeps
    # the 33 table blocks here (empty `text`, measured against their markup).
    if len(src) <= _FRAGMENT_TOKENS and len(content.strip()) < _FRAGMENT_CHARS:
        return
    out = _recall_words(content)
    src = _split_glued(_rejoin_split(src, Counter(out)), out)
    strict = sum((Counter(src) & Counter(out)).values())
    folded = sum(
        (Counter(_fold(w) for w in src) & Counter(_fold(w) for w in out)).values()
    )
    record = {"matched": folded, "total": len(src), "strict": strict}
    missing = list((Counter(src) - Counter(out)).elements())
    if (block.type is BlockType.LIST and missing and src
            and all(word.isdigit() for word in missing) and src[0] in missing):
        # The emitter renders a list item as `- text`, so the printed number the
        # region carries is replaced by the bullet rather than dropped by the
        # extraction. Expected normalization, and the largest remaining source
        # of recall actions: 81 of 90 numeral-only flags are list items, and in
        # 84 of 90 the numeral leads the region.
        record["list_marker_only"] = True
    block.extra["glyph_word_recall"] = record

# Greek letters and mathematical operators. Dashes and quotes are deliberately
# out: `−` emitting as `-` is normalization, and a class where loss and
# normalization both live needs a threshold, which is what this check avoids.
_SYMBOLS = re.compile(r"[\u0370-\u03ff\u1f00-\u1fff]|[\u2200-\u22ff]|[°±×÷µ√]")

_SYMBOL_DASHES = frozenset("−‑–—-")

def record_symbol_loss(block: Block, pc, emitted: str | None = None) -> None:
    """Symbols the page prints inside this block that the emitted text lacks.

    Word recall is the check that ought to see this and is structurally blind to
    it: a 200-word paragraph that loses one `χ` scores 0.995 and passes, so of 40
    blocks dropping a symbol across a 28-paper corpus only 4 crossed the floor.
    The loss changes meaning -- `where χ is the van der Waals radius` emits as
    `where is the van der Waals radius` -- and one block emits `Dc MX` for the
    printed `Δχ MX`, which reads as ordinary text and is worse.

    Exact rather than calibrated, which is why the character class is narrow: a
    Greek letter or a math operator in the region and not in the output is never
    the emitter normalizing, the way a dash or a quote mark is. Engine-side --
    the symbols are already gone from `base-state.json` -- so this reports and
    never repairs: that a `χ` is missing is certain, where to put it back is not.
    """
    content = block.text if emitted is None else emitted
    source = Counter(
        c for c in _SYMBOLS.findall(pc.text_region(block.bbox)) if c not in _SYMBOL_DASHES
    )
    if not source:
        return
    lost = source - Counter(
        c for c in _SYMBOLS.findall(content) if c not in _SYMBOL_DASHES
    )
    if not lost:
        return
    block.extra["glyph_symbols_lost"] = {
        "count": sum(lost.values()),
        # `χ (4)` rather than `χχχχ`: the reader wants the character and how
        # often. Not `χ×4` -- `×` is itself one of the symbols this reports, and
        # `× (2)` has to be readable.
        "symbols": " ".join(
            f"{symbol} ({count})" if count > 1 else symbol
            for symbol, count in sorted(lost.items())
        ),
    }

def record_recall(blocks: list[Block], tables: list[TableData], glyphs) -> None:
    """Record per-block word recall, after every repair pass has run.

    Split out of `enrich_blocks` because a table's markup is only finalized in
    `enrich_tables`, which runs later. A block carrying a table renders from its
    cells and leaves `text` empty, so measuring it in `enrich_blocks` compared
    the printed region against nothing and scored every word as lost: the
    GRASP2018 manual's three contents pages read 0/266, 0/456 and 0/237 while
    their tables were emitted in full.

    Preformatted blocks stay out: their text was re-read from the glyph layer, so
    comparing it against that same layer measures the copy, not the extraction.
    """
    by_block = {t.block_id: t for t in tables}
    for b in blocks:
        if b.type not in PROSE_TYPES or b.bbox is None or b.extra.get("preformatted"):
            continue
        pc = glyphs.page_chars(b.page)
        if pc is None:
            continue
        table = by_block.get(b.id)
        emitted = None
        if table is not None:
            emitted = semantic_output(table.preformatted or render_table(table))
        record_block_recall(b, pc, emitted)
        record_symbol_loss(b, pc, emitted)
    _record_neighbour_attribution(blocks, glyphs)

def _record_neighbour_attribution(blocks: list[Block], glyphs) -> None:
    """Whether a low-recall block's missing words turn up in a block whose box
    overlaps its own.

    The region-boundary guard silences a finding on the grounds that a word
    counted missing may belong to the neighbour sharing the region. That is a
    claim about where the words went, and it is checkable: if none of them
    appears in any overlapping block's text, the overlap does not explain the
    loss. Measured over the corpus, the guard was silencing 63 findings, of
    which 16 had no missing word anywhere in a neighbour -- and poppler,
    reading the same regions independently, corroborated 19 of the 24 it could
    judge. Suppressing those is hiding content, not deferring on it.
    """
    by_page: dict[int, list[Block]] = defaultdict(list)
    for block in blocks:
        if block.bbox is not None and block.text.strip():
            by_page[block.page].append(block)

    for block in blocks:
        record = block.extra.get("glyph_word_recall")
        if not record or not record["total"]:
            continue
        if record["matched"] / record["total"] >= LOW_RECALL_BELOW:
            continue
        pc = glyphs.page_chars(block.page)
        if pc is None:
            continue
        source = _recall_words(pc.region_scriptsplit(block.bbox))
        emitted = _recall_words(block.text)
        source = _split_glued(_rejoin_split(source, Counter(emitted)), emitted)
        missing = Counter(source) - Counter(emitted)
        if not missing:
            continue
        nearby: Counter = Counter()
        for other in by_page[block.page]:
            if other.id != block.id and _boxes_overlap(block.bbox, other.bbox):
                nearby.update(_recall_words(other.text))
        record["missing_in_neighbour"] = sum((missing & nearby).values())

def _boxes_overlap(a: BBox, b: BBox) -> bool:
    ax0, ax1 = sorted((a.x0, a.x1))
    ay0, ay1 = sorted((a.y0, a.y1))
    bx0, bx1 = sorted((b.x0, b.x1))
    by0, by1 = sorted((b.y0, b.y1))
    return min(ax1, bx1) > max(ax0, bx0) and min(ay1, by1) > max(ay0, by0)

def recall_review_flags(blocks: list[Block]) -> tuple[list[CoverageFlag], list[CoverageFlag]]:
    """Turn per-block recall into review actions: (marked, informational).

    The measurement has run since the first version of this file; until now it
    reached `profile.json` as a count and nothing else, so a block that lost
    words looked identical to one that didn't in the Markdown a reader opens.

    Words missing from the emitted text are an action, and the marker rides
    beside the block. Lost diacritics are separated out because they are a
    different defect with a different remedy: the content is present and
    mis-spelled (`Co te` for `Côté`), which a reader checking a reference list
    needs to know about but which does not make the block's content suspect.
    Those stay informational, so a bibliography in French or German doesn't bury
    the Markdown in markers."""
    marked: list[CoverageFlag] = []
    informational: list[CoverageFlag] = []
    ambiguous = _overlapping_regions(blocks)
    for b in blocks:
        rec = b.extra.get("glyph_word_recall")
        if not rec or not rec["total"]:
            continue
        missing = rec["total"] - rec["matched"]
        if b.id in ambiguous and rec.get("missing_in_neighbour", 1):
            # The recall was measured over a region another block also claims, so
            # a word "missing" here may simply belong to the neighbour. Recorded
            # in provenance, not raised: `quality.py` names region-boundary
            # accuracy as something block accounting does not measure, and this
            # is the honest form of that admission.
            informational.append(CoverageFlag(
                b.id, b.page,
                f"region boundary: this block's box overlaps a neighbour's by "
                f"{ambiguous[b.id]:.0%}, so its text-layer recall is not decidable",
                "", disposition="informational", severity="low", content_impact="low",
            ))
            continue
        low_recall = rec["matched"] / rec["total"] < LOW_RECALL_BELOW
        if low_recall and rec.get("list_marker_only"):
            # Said only where a finding would otherwise have been raised.
            # Explaining the bullet on every numbered list item put 608
            # informational rows in one textbook's review against the 70 blocks
            # that were actually low-recall -- noise that buries the rest.
            informational.append(CoverageFlag(
                b.id, b.page,
                "list marker: the item's printed number is rendered as a bullet, "
                "so it reaches the Markdown as list structure rather than as text",
                "", disposition="informational", severity="low", content_impact="low",
            ))
            continue
        if low_recall:
            reason = (
                f"text layer recall: {missing} of {rec['total']} source word(s) "
                f"reach no part of the emitted text"
            )
            severity = "high" if missing >= 5 else "medium"
            marked.append(CoverageFlag(
                b.id, b.page, reason,
                f"> **[pdf2md: action required ({severity}): {reason}; verify against "
                f"[source page {b.page}](../source.pdf#page={b.page})]**",
                disposition="action_required", severity=severity,
                content_impact=severity,
            ))
        elif rec.get("strict", rec["matched"]) < rec["matched"]:
            lost = rec["matched"] - rec["strict"]
            reason = (
                f"diacritics lost: {lost} word(s) are present but stripped of "
                f"their accents by the font decode"
            )
            informational.append(CoverageFlag(
                b.id, b.page, reason,
                f"> **[pdf2md: {reason}]**",
                disposition="informational", severity="low", content_impact="low",
            ))
    marked += _symbol_loss_flags(blocks)
    return marked, informational

def _symbol_loss_flags(blocks: list[Block]) -> list[CoverageFlag]:
    """Raised separately from recall because recall cannot see it.

    A block that drops one `χ` out of two hundred words scores 0.995 and passes
    the floor; over a 28-paper corpus 40 blocks lost a symbol and 4 of them were
    low-recall as well. Nothing about the ratio is wrong -- one word in two
    hundred is not a loss of the paragraph -- so the signal has to be its own,
    and it can be, because a Greek letter present in the region and absent from
    the output admits no innocent reading."""
    flags = []
    for b in blocks:
        record = b.extra.get("glyph_symbols_lost")
        if not record:
            continue
        reason = (
            f"symbols dropped: the page prints {record['symbols']} in this block "
            "and the emitted text does not carry them"
        )
        flags.append(CoverageFlag(
            b.id, b.page, reason,
            f"> **[pdf2md: action required (medium): {reason}; verify against "
            f"[source page {b.page}](../source.pdf#page={b.page})]**",
            disposition="action_required", severity="medium", content_impact="medium",
        ))
    return flags

def _overlapping_regions(blocks: list[Block]) -> dict[str, float]:
    """Blocks whose region another block materially claims too.

    Recall compares a block's text against the glyphs in its box, which assumes
    the box is the block's alone. Where two overlap, a word counted missing may
    belong to the neighbour -- the last surviving false positive in this metric
    was a stray numeral inside a paragraph's box. Across 951 prose blocks the
    median overlap is zero and the 97th percentile 0.085, so a sixth of the
    smaller box is far outside normal and rare enough to refuse on."""
    by_page: dict[int, list[Block]] = defaultdict(list)
    for block in blocks:
        if block.type in PROSE_TYPES and block.bbox is not None:
            by_page[block.page].append(block)

    def area(box) -> float:
        return abs(box.x1 - box.x0) * abs(box.y1 - box.y0)

    def overlap(a, b) -> float:
        wide = max(0.0, min(max(a.x0, a.x1), max(b.x0, b.x1))
                   - max(min(a.x0, a.x1), min(b.x0, b.x1)))
        high = max(0.0, min(max(a.y0, a.y1), max(b.y0, b.y1))
                   - max(min(a.y0, a.y1), min(b.y0, b.y1)))
        smaller = min(area(a), area(b))
        return (wide * high) / smaller if smaller > 0 else 0.0

    ambiguous: dict[str, float] = {}
    for page in by_page.values():
        for index, block in enumerate(page):
            worst = max(
                (overlap(block.bbox, other.bbox)
                 for other in page[:index] + page[index + 1:]),
                default=0.0,
            )
            if worst > _AMBIGUOUS_REGION_SHARE:
                ambiguous[block.id] = worst
    return ambiguous

def _rejoin_split(source: list[str], emitted: Counter) -> list[str]:
    """Merge a run of adjacent source words whose concatenation the output has.

    A styled capital or a multi-run glyph draw splits one printed word across
    several source tokens with no hyphen to join on: `ReAct` reads as `reac` +
    `t`, and a layer that draws a word one character at a time gives `e` + `x` +
    `trapolation` for `extrapolation`, `krylo` + `v`, `broyde` + `n`. Scoring
    those as lost words measures the draw order rather than the content.

    A run of any length is merged, not just a pair, and the longest one wins --
    the per-character case needs three or more. Only a join the output actually
    contains is made, which is a stricter validator than
    `normalize.rejoin_split_word`'s page vocabulary and is available here
    because the emitted text is the thing being compared. Candidate runs grow
    only while they still prefix some output word, so the search stays cheap.
    """
    prefixes = {word[:size] for word in emitted for size in range(1, len(word) + 1)}
    merged: list[str] = []
    index = 0
    while index < len(source):
        glued, best = "", 0
        for end in range(index, len(source)):
            glued += source[end]
            if glued not in prefixes:
                break
            if end > index and emitted[glued]:
                best = end
        if best:
            merged.append("".join(source[index:best + 1]))
            index = best + 1
        else:
            merged.append(source[index])
            index += 1
    return merged

def _split_glued(source: list[str], emitted: list[str]) -> list[str]:
    """Split a source word the layer glued from words the output separates.

    The mirror of `_rejoin_split`, and needed for the same reason: the layer
    draws `Carlo calculations` as one run with no space glyph between them, so
    the region reads `carlocalculations` while the output correctly has two
    words, and the metric scores one phantom loss plus one phantom extra. Found
    on flags an independent reader refuted -- `multiwaveletbasis`, `zerofirst`,
    `rangular` -- where nothing was actually missing.

    Validated the same strict way: the split is made only into words the output
    actually has, consecutively, so a genuine compound the page prints as one
    word is left alone unless the output really did separate it.

    There is no bound on how many words a run may hold, because a journal that
    draws a heading without space glyphs glues all of it: an AIP paper's
    `Articles You May Be Interested In` arrives as one token, as does
    `correlationconsistentbasissetsforactinides`. Candidate runs are grown
    only while they still prefix the source word, so the search costs about
    what a single scan does rather than what every possible run would.
    """
    have = Counter(emitted)

    def run_for(word: str) -> list[str] | None:
        for start in range(len(emitted)):
            if not word.startswith(emitted[start]):
                continue
            # Forwards, then backwards. A run may be emitted in the opposite
            # order on a bidirectional page: the first right-to-left document
            # measured here reads `اسةمنالم` in its region where the output has
            # the three words the other way round, and the forward-only rule
            # left 151 such tokens counted as missing. Reversal is what bidi
            # does to a run, so this is the same claim about the same words --
            # not a weaker one. Measured over the corpus: 8 of 40 Arabic blocks
            # improve, 2 of 1002 Latin blocks improve, and nothing gets worse.
            for step in (1, -1):
                glued, run = "", []
                index = start
                while 0 <= index < len(emitted):
                    glued += emitted[index]
                    run.append(emitted[index])
                    if not word.startswith(glued):
                        break
                    if glued == word and len(run) > 1:
                        return run
                    index += step
        return None

    out: list[str] = []
    for word in source:
        run = run_for(word) if not have[word] else None
        out.extend(run if run else [word])
    return out

def _fold(word: str) -> str:
    """The word with its combining marks removed, so a lost accent doesn't read
    as a lost word."""
    return "".join(
        c for c in unicodedata.normalize("NFD", word)
        if not unicodedata.combining(c)
    )

def recall_summary(blocks: list[Block]) -> dict[str, int]:
    """Aggregate the per-block recalls into profile-level counts. Accent damage
    is counted apart from missing words: both are defects, but only one is a
    question about whether the content is there."""
    measured = matched = total = low = accented = 0
    for b in blocks:
        rec = b.extra.get("glyph_word_recall")
        if not rec:
            continue
        measured += 1
        matched += rec["matched"]
        total += rec["total"]
        if rec["matched"] / rec["total"] < LOW_RECALL_BELOW:
            low += 1
        elif rec.get("strict", rec["matched"]) < rec["matched"]:
            accented += 1
    return {
        "blocks_measured": measured,
        "words_total": total,
        "words_matched": matched,
        "low_recall_blocks": low,
        "accent_damaged_blocks": accented,
    }
