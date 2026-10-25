"""Plain text from the markup publishers put in titles.

Titles arrive with HTML sub- and superscripts (``Li<sub>2</sub>O``), entities,
MathML from Crossref and TeX from some publishers. Everywhere a title is shown
it is drawn as text - the map's canvas glyphs, the card, the chat, the MCP
tools - so the markup is resolved once, where a title enters, into the one
string all of them use.

A sub- or superscript becomes a Unicode character only where that character
is in the fonts a phone has: digits, signs and parentheses. Letters have no
such form everywhere, so ``V<sub>B</sub>`` reads ``VB`` rather than risking a
missing-glyph box on the map. Search is unaffected either way: NFKD folding
turns ``₂`` back into ``2``, so "Li2O" finds ``Li₂O``.
"""

import html
import re
import xml.etree.ElementTree as ET

_SUB = dict(zip("0123456789+-−–=()", "₀₁₂₃₄₅₆₇₈₉₊₋₋₋₌₍₎", strict=True))
_SUP = dict(zip("0123456789+-−–=()ni", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁻⁻⁼⁽⁾ⁿⁱ", strict=True))

_HTML_SCRIPT = re.compile(
    r"<(sub|inf|sup)\b[^>]*>(.*?)</\1\s*>", re.DOTALL | re.IGNORECASE
)
_MATHML = re.compile(
    r"<(mml:)?math\b[^>]*>.*?</(mml:)?math\s*>", re.DOTALL | re.IGNORECASE
)
_DECLARATION = re.compile(r"<!--.*?-->|<!\w+[^\[>]*(\[.*?\])?\s*>", re.DOTALL)
_PREFIX = re.compile(r"(</?)[\w.-]+:")
_XMLNS = re.compile(r"\s+xmlns(:[\w.-]+)?=(\"[^\"]*\"|'[^']*')")
# A tag starts with a letter, so "x<0.5" in a title is left alone.
_TAG = re.compile(r"</?[A-Za-z][\w:.-]*(\s[^<>]*)?/?>")
_SPACE = re.compile(r"\s+")

_SYMBOLS = {
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε",
    "zeta": "ζ", "eta": "η", "theta": "θ", "iota": "ι", "kappa": "κ",
    "lambda": "λ", "mu": "μ", "nu": "ν", "xi": "ξ", "pi": "π", "rho": "ρ",
    "sigma": "σ", "tau": "τ", "upsilon": "υ", "phi": "φ", "chi": "χ",
    "psi": "ψ", "omega": "ω", "Gamma": "Γ", "Delta": "Δ", "Theta": "Θ",
    "Lambda": "Λ", "Xi": "Ξ", "Pi": "Π", "Sigma": "Σ", "Upsilon": "Υ",
    "Phi": "Φ", "Psi": "Ψ", "Omega": "Ω",
    "varepsilon": "ε", "vartheta": "ϑ", "varphi": "φ", "varrho": "ϱ",
    "times": "×", "pm": "±", "mp": "∓", "cdot": "·", "cdots": "⋯",
    "ldots": "…", "dots": "…", "to": "→", "rightarrow": "→", "leftarrow": "←",
    "leftrightarrow": "↔", "rightleftharpoons": "⇌", "infty": "∞",
    "approx": "≈", "sim": "~", "simeq": "≃", "le": "≤", "leq": "≤",
    "ge": "≥", "geq": "≥", "ne": "≠", "neq": "≠", "propto": "∝",
    # "^\circ" is how TeX writes a degree, which is what titles mean by it
    "circ": "°", "degree": "°", "AA": "Å", "ell": "ℓ", "hbar": "ħ",
    "partial": "∂", "nabla": "∇", "prime": "′", "textendash": "–",
    "textemdash": "—", "uparrow": "↑", "downarrow": "↓",
}  # fmt: skip
# Commands that only style their argument, and those that script it.
_STYLE = re.compile(
    r"\\(?:math(?:rm|bf|it|sf|tt|cal|bb|scr)|text(?:rm|bf|it|sf|tt|up)?"
    r"|emph|operatorname|boldsymbol|ce)\s*\{([^{}]*)\}"
)
_TEXT_SCRIPT = re.compile(r"\\text(sub|super)script\s*\{([^{}]*)\}")
_COMMAND = re.compile(r"\\([A-Za-z]+)")
_MATH_COMMAND = re.compile(r"\\([A-Za-z]+)(\s*)")
_ESCAPED = re.compile(r"\\([%&_#{}])")
_TEX_SPACE = re.compile(r"\\[,;:! ]|~")
_TEX_SCRIPT = re.compile(r"([_^])\s*(?:\{([^{}]*)\}|(.))")
_MATH = re.compile(r"\$([^$]+)\$")
_MATH_MARK = re.compile(r"[\\_^{]")
_CASE_BRACES = re.compile(r"(?<![\\A-Za-z])\{([A-Za-z][A-Za-z -]*)\}")
_DOLLAR = "\x00"


def script(text: str, table: dict[str, str]) -> str:
    """``text`` in sub- or superscript characters, or as it is if one has none."""
    compact = "".join(text.split())
    if compact and all(ch in table for ch in compact):
        return "".join(table[ch] for ch in compact)
    return text.strip()


def _html_scripts(text: str) -> str:
    def one(m: re.Match[str]) -> str:
        inner = html.unescape(_TAG.sub("", m.group(2)))
        return script(inner, _SUP if m.group(1).lower() == "sup" else _SUB)

    return _HTML_SCRIPT.sub(one, text)


def _render(node: ET.Element) -> str:
    """One MathML element as text, scripts included."""
    kids = list(node)
    tag = node.tag.lower()
    if tag in {"msub", "msup", "msubsup"} and len(kids) >= 2:
        out = _inner(kids[0])
        if tag == "msup":
            out += script(_inner(kids[1]), _SUP)
        else:
            out += script(_inner(kids[1]), _SUB)
            if tag == "msubsup" and len(kids) > 2:
                out += script(_inner(kids[2]), _SUP)
    elif tag == "mfrac" and len(kids) == 2:
        out = f"{_inner(kids[0])}/{_inner(kids[1])}"
    elif tag == "msqrt":
        out = f"√({''.join(_render(k) for k in kids)})"
    else:
        out = (node.text or "") + "".join(_render(k) for k in kids)
    return out + (node.tail or "")


def _inner(node: ET.Element) -> str:
    """An element without its tail, for when it is a script's base or script."""
    tail, node.tail = node.tail, None
    try:
        return _render(node).strip()
    finally:
        node.tail = tail


def _mathml(text: str) -> str:
    def one(m: re.Match[str]) -> str:
        fragment = m.group(0)
        # A declaration could define entities; titles never need one.
        if "<!" not in fragment:
            fragment = _XMLNS.sub("", _PREFIX.sub(r"\1", fragment))
            # An HTML entity such as &nbsp; is not XML and fails the parse;
            # such a fragment keeps its text and loses its scripts.
            try:
                return _render(ET.fromstring(fragment)).strip()
            except ET.ParseError:
                pass
        return _TAG.sub("", _DECLARATION.sub("", m.group(0)))

    return _MATHML.sub(one, text)


def _unstyle(text: str) -> str:
    while _STYLE.search(text):
        text = _STYLE.sub(r"\1", text)
    return text


def _math_command(m: re.Match[str]) -> str:
    # Math ignores spaces: "\Delta G" is ΔG, while "\times 10" keeps its gap.
    # A command with no plain form is dropped.
    symbol = _SYMBOLS.get(m.group(1), "")
    return symbol if symbol.isalpha() else symbol + m.group(2)


def _tex_math(body: str) -> str:
    body = _TEX_SPACE.sub(" ", _unstyle(body))
    body = _ESCAPED.sub(r"\1", body)
    body = _MATH_COMMAND.sub(_math_command, body)
    body = _TEX_SCRIPT.sub(
        lambda m: script(
            m.group(2) if m.group(2) is not None else m.group(3),
            _SUB if m.group(1) == "_" else _SUP,
        ),
        body,
    )
    return body.replace("{", "").replace("}", "")


def _tex(text: str) -> str:
    if "\\" not in text and "$" not in text and "{" not in text:
        return text
    text = text.replace("\\$", _DOLLAR)
    # "$5 to $10" is two prices, not math: math has a command or a script in it.
    text = _MATH.sub(
        lambda m: (
            _tex_math(m.group(1)) if _MATH_MARK.search(m.group(1)) else m.group(0)
        ),
        text,
    )
    # Outside math, only what is unambiguous: braces around a word are BibTeX
    # keeping its case, braces around digits are part of the title, as in a
    # {111} facet.
    text = _CASE_BRACES.sub(r"\1", text)
    text = _TEXT_SCRIPT.sub(
        lambda m: script(m.group(2), _SUB if m.group(1) == "sub" else _SUP), text
    )
    text = _unstyle(text)
    text = _COMMAND.sub(lambda m: _SYMBOLS.get(m.group(1), m.group(0)), text)
    text = _ESCAPED.sub(r"\1", text)
    return text.replace(_DOLLAR, "$")


def plain(raw: str) -> str:
    """A title or journal name as plain text, its markup resolved.

    Tags are handled before entities are decoded, for the reason ``strip_jats``
    gives. A title escaped twice over (``&lt;sub&gt;``) is common enough in
    Crossref that the scripts are looked for once more after decoding.
    """
    if not raw:
        return ""
    text = _html_scripts(_mathml(raw))
    text = html.unescape(_TAG.sub("", text))
    if "<" in text:
        text = _TAG.sub("", _html_scripts(_mathml(text)))
    text = _tex(text)
    return _SPACE.sub(" ", text).strip()
