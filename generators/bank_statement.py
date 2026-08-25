"""Bank statement renderer — a thin adapter over the declarative layout engine.

Visual DNA per bank now lives in config/layouts/bank_statements.yml as a `body:`
tree of layout primitives; this module only sets up the page and delegates to
the DSL. `_parse_transactions` survives as a small standalone utility for
callers (tests/test_bank_fit.py) that need to iterate transaction fields
independently of rendering — the renderer's own equivalent is the
`bank_transactions` row provider in generators/layout_dsl/providers.py.
"""

from PIL import Image, ImageDraw

from generators.layout_dsl.context import Region
from generators.layout_dsl.engine import render_body
from generators.transcript import TranscriptDraw, TranscriptRecorder

_LAYOUT_PATH = "config/layouts/bank_statements.yml"


def _parse_transactions(fields: dict) -> list[dict]:
    """Parse pipe-delimited transaction fields into a list of transaction dicts."""
    dates = fields.get("TRANSACTION_DATES", "").split("|")
    descs = fields.get("TRANSACTION_DESCRIPTIONS", "").split("|")
    debits = fields.get("TRANSACTION_AMOUNTS_PAID", "").split("|")
    credits = fields.get("TRANSACTION_AMOUNTS_RECEIVED", "").split("|")

    txns = []
    for i in range(len(dates)):
        txn = {
            "date": dates[i].strip() if i < len(dates) else "",
            "description": descs[i].strip() if i < len(descs) else "",
            "debit": debits[i].strip() if i < len(debits) else "NOT_FOUND",
            "credit": credits[i].strip() if i < len(credits) else "NOT_FOUND",
        }
        txns.append(txn)
    return txns


def render_via_dsl(entry: dict, layout: dict, layout_id: str) -> tuple[Image.Image, TranscriptRecorder]:
    """Render a bank statement through the declarative layout DSL.

    Args:
        entry: Ground truth YAML entry with 'fields' dict.
        layout: Layout config carrying a `body:` tree, 'page_dimensions', etc.
        layout_id: The layout's registry id, used in diagnostics.

    Returns:
        The rendered page, and the transcript captured while drawing it.

    Raises:
        CoverageError: A primitive drew text without emitting an event.
    """
    dims = layout["page_dimensions"]
    width, height = dims["width"], dims["height"]
    image = Image.new("RGB", (width, height), "white")
    recorder = TranscriptRecorder()

    render_body(
        layout,
        entry,
        layout_id=layout_id,
        layout_path=_LAYOUT_PATH,
        draw=TranscriptDraw(ImageDraw.Draw(image), recorder),
        region=Region(x=layout["margin"], width=layout["content_width"]),
        y=layout["margin"],
        transcript=recorder,
    )
    return image, recorder


def render_bank_statement(entry: dict, layout: dict) -> tuple[Image.Image, TranscriptRecorder]:
    """Render a bank statement image from ground truth entry and layout config.

    Args:
        entry: Ground truth YAML entry with 'fields' dict and 'layout' id.
        layout: Layout registry entry carrying 'body', 'page_dimensions',
            'margin', and 'content_width'.

    Returns:
        The rendered page, and the transcript captured while drawing it.
    """
    return render_via_dsl(entry, layout, str(entry.get("layout", "")))
