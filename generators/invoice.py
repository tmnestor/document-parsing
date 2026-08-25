"""Invoice renderer — a thin adapter over the declarative layout engine.

Renders A4-format Australian tax invoices carrying the mandatory fields:
1. "Tax Invoice" header  2. Seller identity  3. ABN
4. Issue date  5. Item descriptions with qty/price
6. GST amount  7. Taxable sale extent

The visual DNA per invoice format lives in config/layouts/invoices.yml as a
`body:` tree of layout primitives; this module only sets up the page and
delegates to the DSL. The one value an invoice draws that exists nowhere in
ground truth — the ex-GST subtotal — comes from the `computed_totals` field
provider (generators/layout_dsl/field_providers.py), which each layout declares
under `field_providers:`; the line items come from the `pipe_fields` row
provider (generators/layout_dsl/providers.py).
"""

from PIL import Image, ImageDraw

from generators.layout_dsl.context import Region
from generators.layout_dsl.engine import render_body
from generators.transcript import TranscriptDraw, TranscriptRecorder

_LAYOUT_PATH = "config/layouts/invoices.yml"


def render_invoice(entry: dict, layout: dict) -> tuple[Image.Image, TranscriptRecorder]:
    """Render a compliant Australian tax invoice from ground truth and layout config.

    Invoices are fixed-page, so this is page setup and nothing else: no crop and
    no vertical rescale (contrast generators/receipt.py, which is
    variable-height).

    Args:
        entry: Ground truth YAML entry with 'fields' dict and 'layout' id.
        layout: Layout registry entry carrying a `body:` tree,
            'page_dimensions', 'margin' and 'content_width'.

    Returns:
        The rendered page, and the transcript captured while drawing it.

    Raises:
        CoverageError: A primitive drew text without emitting an event.
    """
    dims = layout["page_dimensions"]
    width, height = int(dims["width"]), int(dims["height"])
    image = Image.new("RGB", (width, height), "white")
    recorder = TranscriptRecorder()

    render_body(
        layout,
        entry,
        layout_id=str(entry.get("layout", "")),
        layout_path=_LAYOUT_PATH,
        draw=TranscriptDraw(ImageDraw.Draw(image), recorder),
        region=Region(x=int(layout["margin"]), width=int(layout["content_width"])),
        y=int(layout["margin"]),
        transcript=recorder,
    )
    return image, recorder
