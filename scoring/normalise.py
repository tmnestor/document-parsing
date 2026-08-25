"""Policy-driven text normalisation.

Order is load-bearing and is fixed here rather than configurable: compose first
so every later step sees one representation of each character, strip markup
before collapsing whitespace so removed syntax cannot leave doubled gaps, and
fold case last if at all. Which steps run is policy; the order they run in is a
correctness property.
"""

import re
import unicodedata

_DASHES = str.maketrans({"\u2013": "-", "\u2014": "-", "\u2012": "-", "\u2015": "-", "\u2212": "-"})
_QUOTES = str.maketrans(
    {
        "\u2018": "'",
        "\u2019": "'",
        "\u201a": "'",
        "\u201b": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u201e": '"',
        "\u201f": '"',
    }
)

# A pipe table's separator row carries no text: every cell is dashes and colons.
_SEPARATOR_ROW = re.compile(r"^\s*\|?[\s:\-|]+\|?\s*$", re.MULTILINE)
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+", re.MULTILINE)
_EMPHASIS = re.compile(r"(\*{1,3}|_{1,3}|`+)")
_PIPES = re.compile(r"\|")
_WHITESPACE = re.compile(r"\s+")


def normalise(text: str, policy: dict) -> str:
    """Apply the configured normalisation steps, in fixed order.

    Args:
        text: The raw prediction or transcript.
        policy: The `normalisation` section of `config/scoring.yml`.

    Returns:
        The normalised string.
    """
    result = unicodedata.normalize(policy["unicode_form"], text)

    if policy["fold_dashes"]:
        result = result.translate(_DASHES)
    if policy["fold_quotes"]:
        result = result.translate(_QUOTES)

    if policy["strip_markdown"]:
        result = _SEPARATOR_ROW.sub(" ", result)
        result = _HEADING.sub("", result)
        result = _EMPHASIS.sub("", result)
        result = _PIPES.sub(" ", result)

    if policy["collapse_whitespace"]:
        result = _WHITESPACE.sub(" ", result).strip()
    if policy["fold_case"]:
        result = result.casefold()

    return result
