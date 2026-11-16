import pytest

from cra.core.library.markup import plain
from cra.core.library.text import overlap, query_tokens

MATHML = (
    '<mml:math xmlns:mml="http://www.w3.org/1998/Math/MathML">'
    "<mml:msub><mml:mrow><mml:mi>Li</mml:mi></mml:mrow>"
    "<mml:mrow><mml:mn>2</mml:mn></mml:mrow></mml:msub>"
    '<mml:mi mathvariant="normal">O</mml:mi></mml:math>'
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Li<sub>2</sub>O thin films", "Li₂O thin films"),
        ("Fe<sup>3+</sup> and SO<inf>4</inf>", "Fe³⁺ and SO₄"),
        # no subscript B in every font: inline rather than a missing glyph
        ("V<sub>B</sub><sup>-</sup> centres", "VB⁻ centres"),
        ("5<sup>th</sup> <i>in situ</i> study", "5th in situ study"),
        ("Energy &amp; Environmental Science", "Energy & Environmental Science"),
        ("CO&lt;sub&gt;2&lt;/sub&gt; reduction", "CO₂ reduction"),
        ("x<0.5 and T &lt; 4 K", "x<0.5 and T < 4 K"),
        (f"Ion transport in {MATHML}", "Ion transport in Li₂O"),
        (
            (
                "<mml:math><mml:msup><mml:mi>O</mml:mi><mml:mrow><mml:mn>2</mml:mn>"
                "<mml:mo>&#x2212;</mml:mo></mml:mrow></mml:msup></mml:math> ions"
            ),
            "O²⁻ ions",
        ),
        ("<mml:math><mml:mi>&nbsp;x</mml:mi></mml:math> broken", "x broken"),
        (r"$\alpha$-Fe$_2$O$_3$ at 90$^\circ$C", "α-Fe₂O₃ at 90°C"),
        (r"$\mathrm{TiO_{2}}$ under $10^{-3}$ mbar", "TiO₂ under 10⁻³ mbar"),
        (r"$\Delta G$ and $2 \times 10^{3}$", "ΔG and 2 × 10³"),
        (
            r"\textit{Ab initio} Li\textsubscript{2}S, 5\% Zn \& Ge",
            "Ab initio Li₂S, 5% Zn & Ge",
        ),
        (r"{DFT} study of the {111} facet", "DFT study of the {111} facet"),
        ("Costs of $5 to $10", "Costs of $5 to $10"),
        (r"\alpha-Fe and \unknown", r"α-Fe and \unknown"),
        ("  Plain   title ", "Plain title"),
        ("", ""),
    ],
)
def test_markup_becomes_plain_text(raw, expected):
    assert plain(raw) == expected


def test_a_cleaned_title_is_found_by_the_plain_spelling():
    assert overlap(plain("Li<sub>2</sub>O cathodes"), query_tokens("Li2O")) == 1


def test_a_declaration_is_neither_parsed_nor_shown():
    hostile = '<math><!DOCTYPE x [<!ENTITY a "aaaa">]><mi>&a;</mi></math> x'
    assert plain(hostile) == "&a; x"
