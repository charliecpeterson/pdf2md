"""Symbol-font garbage detection: real GRASP mojibake vs clean scientific text."""

from __future__ import annotations

from pdf2md.legibility import is_garbage, score_legibility

# Verbatim from the GRASP2018 manual's broken-font extraction ("A practical guide
# to Grasp"): dingbats + `/aNNN` glyph-name tokens standing in for letters.
GRASP_GARBAGE = "❆ ♣/a114❛❝/a116✐❝❛❧ ❣✉✐❞❡ /a116♦ ●/a114❛/a115♣"


def test_grasp_mojibake_is_garbage():
    assert score_legibility(GRASP_GARBAGE) < 0.2
    assert is_garbage(GRASP_GARBAGE)


def test_clean_prose_scores_high():
    text = "This is a practical guide to the Grasp2018 atomic structure package."
    assert score_legibility(text) == 1.0
    assert not is_garbage(text)


def test_chemistry_notation_not_flagged():
    # Dense identifiers/term symbols are exactly what a vowel-ratio check would
    # wrongly flag; the substitution-only signal leaves them legible.
    assert not is_garbage("CSF MR-CC AQCC expansions; 1s2 2s2 S and 1s2 2p2 P in Li I.")


def test_math_and_greek_excluded():
    assert not is_garbage("ψ = Σ cᵢ φᵢ, with ∂ρ/∂t ≤ 0 and energy → minimum")


def test_no_letters_is_legible():
    # Page numbers and pure-numeral blocks have nothing to judge.
    assert score_legibility("2018") == 1.0
    assert score_legibility("$$ 1 / 2 $$") == 1.0


def test_glyph_name_tokens_count_as_garbage():
    # The `a` inside each `/a116` must not be miscounted as a real letter.
    assert score_legibility("/a116/a114/a115/a80") < 0.2
