"""Which OmniDocBench category each primitive's ink belongs to.

Derived from the primitive that drew it, never authored per block, so a layout
cannot mislabel its own content and the mapping is checkable at startup.

The category names are OmniDocBench's, verbatim (design §3.1). This corpus
populates three of its eighteen block categories: it is three Australian
business document types and contains no figures, formulas, captions, headers,
footers, page numbers, code or references. The unused names are honest
absences, not gaps to fill.

`rule` is mapped to `abandon` because a rule with a `fill_char` paints a row of
repeated glyphs — real ink that no metric should score. Annotating it with
`ignore: true` states that, where emitting nothing would leave the annotation
silently disagreeing with the page.
"""

# OmniDocBench's block-level category names, verbatim. Listed in full rather
# than trimmed to what we use, so a reader can see what this corpus does not
# contain, and so a future document type can be mapped without re-deriving them.
BLOCK_CATEGORIES: tuple[str, ...] = (
    "title",
    "text_block",
    "figure",
    "figure_caption",
    "figure_footnote",
    "table",
    "table_caption",
    "table_footnote",
    "equation_isolated",
    "equation_caption",
    "header",
    "footer",
    "page_number",
    "page_footnote",
    "abandon",
    "code_txt",
    "code_txt_caption",
    "reference",
)

# The only span-level category this corpus produces. OmniDocBench also defines
# `equation_ignore`, `equation_inline` and `footnote_mark`, none of which can
# occur here.
SPAN_CATEGORY = "text_span"

# None means "draws no annotatable ink": a container shapes the walk, a spacer
# is whitespace. `banner` is a title because the bank mastheads are the page's
# own title line.
CATEGORY_BY_PRIMITIVE: dict[str, str | None] = {
    "text": "text_block",
    "pair": "text_block",
    "block": "text_block",
    "banner": "title",
    "table": "table",
    "rule": "abandon",
    "spacer": None,
    "panel": None,
    "split": None,
}


class CategoryError(RuntimeError):
    """Raised when a primitive has no declared category."""


def category_for(primitive: str) -> str | None:
    """Return the OmniDocBench category for a primitive's ink.

    Args:
        primitive: A key of `engine.PRIMITIVE_DRAWERS`, e.g. `text`.

    Returns:
        The category name, or None when the primitive draws no annotatable ink.

    Raises:
        CategoryError: The primitive is not declared in the map.
    """
    if primitive not in CATEGORY_BY_PRIMITIVE:
        raise CategoryError(
            "Primitive has no OmniDocBench category.\n"
            f"  What:     '{primitive}' is not declared in the category map.\n"
            "  Where:    generators/layout_dsl/categories.py -> CATEGORY_BY_PRIMITIVE\n"
            f"  Expected: an entry mapping it to one of {list(BLOCK_CATEGORIES)}, or to None "
            'when it draws no annotatable ink, e.g.\n              "'
            f'{primitive}": "text_block",\n'
            f"  Recover:  add '{primitive}' to CATEGORY_BY_PRIMITIVE."
        )
    return CATEGORY_BY_PRIMITIVE[primitive]
