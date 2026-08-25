"""Text-bearing primitives: text, pair, block, rule, spacer.

Each takes (block, ctx, y) and returns the advanced y-cursor, matching the
convention the existing renderers already use.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation

from generators.common import (
    Font,
    draw_fitted_center,
    draw_fitted_left,
    draw_fitted_right,
    draw_separator,
    draw_separator_line,
    fmt_amount,
    load_font,
)
from generators.layout_budgets import field_budget
from generators.layout_dsl.binding import interpolate
from generators.layout_dsl.context import Region, RenderContext
from generators.layout_dsl.defaults import DefaultsError, resolve_param

# Public: schema.py imports this as the single source of truth for validating
# a `text` block's `align:` key, so a typo (e.g. "centre") fails at validate
# time rather than silently left-aligning (the pre-typo-check default here).
ALIGNMENTS = ("left", "center", "right")

# Public: schema.py imports this to validate a `pair` block's `value_align:`
# key. A pair has no "center" concept -- its value either trails the label
# inline (left) or is pinned to the region's right edge (right) -- so this is
# deliberately narrower than ALIGNMENTS above, not a re-export of it.
PAIR_VALUE_ALIGNS = ("left", "right")

# Public: schema.py imports this to validate a `pair` block's `currency:` key.
# A pair's value is a raw ground-truth string ("137.73"); a totals line prints
# a formatted amount ("$137.73" for a receipt's TOTAL, "137.73" for its GST).
# `symbol` and `plain` name exactly that difference, matching the vocabulary a
# table column's own `currency: plain` already uses (primitives_table._cell_text).
PAIR_CURRENCIES = ("symbol", "plain")


@contextmanager
def _decoration(ctx: RenderContext) -> Iterator[None]:
    """Scope decorative text, tolerating a context with no recorder.

    Args:
        ctx: Render context, whose `transcript` may be None on a bare render.
    """
    if ctx.transcript is None:
        yield
        return
    with ctx.transcript.decoration():
        yield


class RoleError(RuntimeError):
    """Raised when a block names a typographic role the layout does not define."""


class CurrencyError(RuntimeError):
    """Raised when a block asks for currency formatting of a non-amount value."""


def format_currency(value: str, style: str, *, layout_id: str, layout_path: str) -> str:
    """Format a raw amount string the way the legacy renderers printed it.

    Args:
        value: The already-interpolated amount, e.g. "137.73".
        style: `symbol` (keep `fmt_amount`'s `$`) or `plain` (drop it).
        layout_id: Layout id, used in the diagnostic.
        layout_path: Path to the layout YAML, used in the diagnostic.

    Returns:
        `"$1,234.56"` for `symbol`, `"1,234.56"` for `plain`.

    Raises:
        CurrencyError: If `value` does not parse as a decimal amount.
    """
    try:
        amount = Decimal(value)
    except (InvalidOperation, ValueError, TypeError) as err:
        raise CurrencyError(
            "Cannot format a value as currency.\n"
            f"  What:     value {value!r} is not a decimal amount, but this block "
            f"declares currency: {style}.\n"
            f"  Where:    {layout_path} -> {layout_id}.body (a pair block's `currency` key)\n"
            "  Expected: the block's `value:` to resolve to a bare decimal string, e.g. "
            'value: "{TOTAL_AMOUNT}" resolving to "137.73".\n'
            "  Recover:  drop `currency:` from the block if its value is already formatted "
            "(e.g. a provider-formatted PAYMENT_TENDERED), or point `value:` at a raw "
            "amount field."
        ) from err
    text = fmt_amount(amount)
    return text if style == "symbol" else text.lstrip("$")


def resolve_role(layout: dict, role: str) -> int:
    """Return the font size a role maps to.

    Args:
        layout: The resolved layout dict, carrying a `font_sizes` mapping.
        role: The role name, e.g. "body".

    Returns:
        The font size in points.

    Raises:
        RoleError: If the layout defines no such role.
    """
    sizes = layout.get("font_sizes")
    if not isinstance(sizes, dict) or role not in sizes:
        available = sorted(sizes) if isinstance(sizes, dict) else []
        raise RoleError(
            "Unknown typographic role.\n"
            f"  What:     role '{role}' is not defined by this layout.\n"
            f"  Where:    config/layouts/*.yml -> <layout>.font_sizes.{role}\n"
            f"  Expected: one of {available}, e.g. font_sizes: {{body: 32}}.\n"
            f"  Recover:  add '{role}:' under the layout's font_sizes, or use "
            f"an existing role."
        )
    return int(sizes[role])


def line_advance(layout: dict, block: dict, *, layout_id: str, layout_path: str) -> int:
    """Return the vertical advance for one line, in pixels.

    Replaces the former `line_height(size) = int(size * 1.4)`. That ratio
    was a function of the drawing block's own *role* font size -- CBA's
    header logo line advanced by a different amount than its footer ABN
    block. A single flat number per layout cannot express that (a first
    attempt at this tried exactly that and needed 29 hand-computed
    per-block overrides across the 8 bank layouts to stay pixel-identical
    -- see the plan's fix-round-1 note). Receipts also contradict the old
    ratio outright: `receipts.yml` declares `line_height: 20` against
    `font_size: 18`, a ratio of 1.11, not 1.4.

    So a layout's `defaults.line_advance` is a mapping of role -> pixels
    (e.g. `{header: 61, body: 44, footer: 25}`), and this resolves the
    block's own role first, then looks that role up in the mapping. A block
    may instead carry its own bare-integer `line_advance:` to override the
    per-role mapping entirely, for the rare line that is not simply "this
    role's usual advance" -- `resolve_param`'s block-key-wins-over-layout
    resolution already gives us that for free: if the block supplies a
    plain int, that int is what comes back below, and the `isinstance`
    check falls through the role lookup entirely.

    Args:
        layout: The resolved layout dict, carrying a `defaults:` mapping.
        block: The block requesting the advance; its own `line_advance` key,
            if present, wins over the layout default (and if it is a bare
            int, wins outright, without any role lookup).
        layout_id: Layout id, used in the diagnostic.
        layout_path: Path to the layout YAML, used in the diagnostic.

    Returns:
        The vertical advance in pixels.

    Raises:
        DefaultsError: If the block's role is absent from a layout-level
            `line_advance` mapping (and the block carries no override of
            its own).
    """
    value = resolve_param(block, layout, "line_advance", layout_id=layout_id, layout_path=layout_path)
    if not isinstance(value, dict):
        return int(value)

    role = str(resolve_param(block, layout, "role", layout_id=layout_id, layout_path=layout_path))
    if role not in value:
        raise DefaultsError(
            "Missing line_advance for role.\n"
            f"  What:     layout '{layout_id}' declares line_advance for role(s) "
            f"{sorted(value)}, but this block's role is '{role}'.\n"
            f"  Where:    {layout_path} -> {layout_id}.defaults.line_advance.{role}\n"
            "  Expected: a defaults.line_advance mapping covering every role this "
            f"layout draws, e.g.\n"
            f"              defaults:\n"
            f"                line_advance:\n"
            f"                  {role}: <int(font_sizes.{role} * 1.4)>\n"
            f"  Recover:  add '{role}:' under {layout_id}.defaults.line_advance, or "
            "set 'line_advance: <int>' directly on the block if it needs a value "
            "unrelated to any role."
        )
    return int(value[role])


def font_for(
    layout: dict, block: dict, size: int, *, bold: bool = False, layout_id: str, layout_path: str
) -> Font:
    """Load a font honouring the layout's declared face.

    Every `load_font` call in the primitives used to omit the face entirely,
    silently defaulting to the sans face even for layouts (e.g. receipts)
    declaring a monospace family. This resolves `family` the same way every
    other primitive parameter resolves -- block key, then layout `defaults:`.

    Args:
        layout: The resolved layout dict, carrying a `defaults:` mapping.
        block: The block requesting the font; its own `family` key, if
            present, wins over the layout default.
        size: Font size in points.
        bold: Whether to load the bold weight.
        layout_id: Layout id, used in the diagnostic.
        layout_path: Path to the layout YAML, used in the diagnostic.

    Returns:
        The loaded font.

    Raises:
        FontFamilyError: The resolved `family` is not a vendored family.
    """
    family = str(resolve_param(block, layout, "family", layout_id=layout_id, layout_path=layout_path))
    return load_font(size, family=family, bold=bold)


def _draw_line(
    ctx: RenderContext, text: str, y: int, *, font: Font, align: str, color: str
) -> tuple[int, int]:
    """Draw one line honouring alignment; return (left, right) pixel extent."""
    bbox = font.getbbox(text)
    text_width = int(bbox[2] - bbox[0])
    if align == "right":
        x = ctx.region.right - text_width
    elif align == "center":
        x = ctx.region.x + (ctx.region.width - text_width) // 2
    else:
        x = ctx.region.x
    ctx.draw.text((x, y), text, font=font, fill=color)
    return x, x + text_width


def _draw_fitted_text(
    block: dict,
    ctx: RenderContext,
    y: int,
    *,
    text: str,
    size: int,
    bold: bool,
    align: str,
    color: str,
    budget_name: str,
) -> int:
    """Draw `text` through its declared fit budget, dispatching on alignment.

    Shared by `draw_text_block` and `draw_pair` (for the value only -- see
    there). The `draw_fitted_*` helpers in `generators.common` already return
    the y *below* whatever they wrapped to, so that return value is the
    advance here, not a fresh `line_advance()` computation -- a wrapped
    budget consumes more vertical space than one line, and the whole point of
    a budget is that the caller does not have to know in advance how much.

    `draw_fitted_center` centres within a canvas width, not a region: a
    receipt centres its header within the full page width (see
    `receipt.py`'s legacy `draw_fitted_center(draw, text, y, width, ...)`
    call, where `width` is the page width, not a margin-inset region).
    `ctx.region.x * 2 + ctx.region.width` reconstructs that page width from a
    symmetric margin -- region.x is the margin, and region.width is the page
    width minus twice that same margin, so doubling region.x and adding it
    back gives the page width without the region ever needing to know it.

    Args:
        block: The block requesting the budget; its own `family`, if present,
            wins over the layout default (the same resolution `font_for`
            uses, preserved here since these helpers build their own font
            internally rather than accepting a pre-built one).
        ctx: Render context.
        y: Current y-cursor.
        text: The already-interpolated string to fit and draw.
        size: The resolved role's nominal font size.
        bold: Whether to draw bold.
        align: "left", "center", or "right".
        color: Fill colour.
        budget_name: The `field_budgets` key to resolve.

    Returns:
        The y below the fitted (possibly wrapped) text.

    Raises:
        LayoutBudgetError: If `budget_name` is not a valid budget in this
            layout's `field_budgets`.
    """
    budget = field_budget(ctx.layout, ctx.layout_id, budget_name, layout_path=ctx.layout_path)
    family = str(
        resolve_param(block, ctx.layout, "family", layout_id=ctx.layout_id, layout_path=ctx.layout_path)
    )
    spacing = line_advance(ctx.layout, block, layout_id=ctx.layout_id, layout_path=ctx.layout_path)
    if align == "right":
        return draw_fitted_right(
            ctx.draw,
            text,
            ctx.region.right,
            y,
            budget=budget,
            nominal_size=size,
            family=family,
            bold=bold,
            fill=color,
            line_spacing=spacing,
        )
    if align == "center":
        canvas_width = ctx.region.x * 2 + ctx.region.width
        return draw_fitted_center(
            ctx.draw,
            text,
            y,
            canvas_width,
            budget=budget,
            nominal_size=size,
            family=family,
            bold=bold,
            fill=color,
            line_spacing=spacing,
        )
    return draw_fitted_left(
        ctx.draw,
        text,
        ctx.region.x,
        y,
        budget=budget,
        nominal_size=size,
        family=family,
        bold=bold,
        fill=color,
        line_spacing=spacing,
    )


def draw_text_block(block: dict, ctx: RenderContext, y: int) -> int:
    """Draw a single line of text, or a fitted (possibly wrapped) block.

    `content` and `from_layout` are mutually exclusive alternatives for
    *what* to draw (enforced at validate time, in schema.py's
    `_validate_block`); `suppress_if_equals` separately controls *whether*
    to draw at all. Together they let a letterhead/supplier pair share one
    block vocabulary instead of hand-written Python (mirrors the legacy
    renderers' `_draw_supplier_line`):

    - `content: '{FIELD}'` interpolates an entry field, as usual.
    - `from_layout: <layout_key>` reads that key directly off the layout
      dict instead — e.g. `from_layout: logo_text` — so the drawn brand
      genuinely comes from the layout, not a Python or YAML literal that
      can drift from it. Unlike `content`, this is not a template: the
      layout's value is used verbatim, with no `{FIELD}` substitution.
    - `suppress_if_equals: <layout_key>` skips drawing (and recording)
      entirely when the resolved text is empty or equals that layout key's
      value — the content supplier line is redundant whenever it already
      matches the letterhead already on the page.

    `budget: <FIELD_BUDGET_NAME>` opts the block into a fit budget (see
    `_draw_fitted_text`) instead of the plain, always-one-line path below --
    the field may shrink, wrap onto up to `max_lines`, or both, per its
    `field_budgets` entry. A block without `budget:` renders exactly as
    before; this is an additive, opt-in engine capability.

    `title: true` marks the block as the page's main title, emitting a `title`
    event rather than a `line` — see the comment at the emit below. It changes
    no pixel, only the event kind.

    Args:
        block: The `text` block.
        ctx: Render context.
        y: Current y-cursor.

    Returns:
        The advanced y-cursor (unchanged if the block was suppressed).
    """
    role = str(
        resolve_param(block, ctx.layout, "role", layout_id=ctx.layout_id, layout_path=ctx.layout_path)
    )
    size = resolve_role(ctx.layout, role)
    if "from_layout" in block:
        text = str(ctx.layout[block["from_layout"]])
    else:
        text = interpolate(block["content"], ctx.entry["fields"])

    suppress_key = block.get("suppress_if_equals")
    if suppress_key is not None and (not text or text == str(ctx.layout.get(suppress_key))):
        return y

    # `title: true` marks this block as the page's main title, so it emits a
    # `title` event (an H1) instead of a `line`. It is purely an event-kind
    # switch: nothing about the draw changes, so turning it on re-serialises
    # without re-rendering a pixel. It exists because `banner` — a full-bleed
    # coloured masthead pinned to (0, 0) — was the only primitive that could
    # emit `title`, which left the H1 firing on bank statements and on nothing
    # else. A model cannot infer "coloured bar is a heading, equally large bold
    # text is not", so the convention was unlearnable rather than merely
    # inconsistent. Only a document's own title takes it; a `block` heading
    # stays a `line`, being a section sub-head rather than the page's title.
    is_title = bool(
        resolve_param(
            block,
            ctx.layout,
            "text_title",
            layout_id=ctx.layout_id,
            layout_path=ctx.layout_path,
            block_key="title",
        )
    )

    # Emitted below the suppression gate, above every draw path: the code that
    # suppresses is the code that would have emitted, so a suppressed block
    # cannot leave a transcript event behind (design §4.2). Captured pre-wrap —
    # `_draw_fitted_text` may split this string across lines, but wrapping is an
    # artifact of the fit budget, not of content.
    if ctx.transcript is not None:
        ctx.transcript.emit("title" if is_title else "line", text)

    bold = bool(
        resolve_param(block, ctx.layout, "bold", layout_id=ctx.layout_id, layout_path=ctx.layout_path)
    )
    align = str(
        resolve_param(block, ctx.layout, "align", layout_id=ctx.layout_id, layout_path=ctx.layout_path)
    )
    color = str(
        resolve_param(block, ctx.layout, "color", layout_id=ctx.layout_id, layout_path=ctx.layout_path)
    )

    budget_name = block.get("budget")
    if budget_name is not None:
        return _draw_fitted_text(
            block,
            ctx,
            y,
            text=text,
            size=size,
            bold=bold,
            align=align,
            color=color,
            budget_name=budget_name,
        )

    font = font_for(
        ctx.layout, block, size, bold=bold, layout_id=ctx.layout_id, layout_path=ctx.layout_path
    )
    _draw_line(ctx, text, y, font=font, align=align, color=color)
    return y + line_advance(ctx.layout, block, layout_id=ctx.layout_id, layout_path=ctx.layout_path)


def draw_pair(block: dict, ctx: RenderContext, y: int) -> int:
    """Draw a label and value on one line.

    `pair_separator` (block key `separator`) is what the label is followed by,
    on every path below. It is the layout's decision, not this module's: a
    receipt's "Date" line wants `": "`, an invoice's right-aligned "Total"
    wants `":"`, and a receipt's right-aligned "TOTAL" wants `""`, because
    legacy drew that one with no punctuation at all. Nothing here infers it
    from `value_align` -- doing so is how `pair` briefly came to have two
    label conventions, with right-aligned layouts writing their own colons
    into `label:` to work around the one they could not get.

    `pair_value_align` (block key `value_align`) chooses between two drawing
    styles:

    - `left` (the default, and the only style bank statements use): label,
      separator and value draw as one string, left-aligned at `ctx.region.x`.
    - `right`: mirrors the legacy `draw_line_item` -- label and separator
      draw at `ctx.region.x` and the value right-aligns to
      `ctx.region.right`, so the two never share one string.
      `pair_min_gap` (block key `min_gap`) then reproduces `invoice.py:283-
      285`: when the label is long enough to otherwise collide with the
      value, it is pushed left just far enough to keep `min_gap` px clear
      between them, rather than merging into one OCR token. `min_gap: 0`
      demands no gap at all and never moves the label, matching what both
      legacy renderers do everywhere except that one invoice line.

    `currency: symbol|plain` formats the resolved value as an amount before
    anything is drawn or measured (see `format_currency`) -- a receipt's
    `TOTAL` line prints `$137.73` and its `GST` line `137.73` from the same
    raw `"137.73"` ground truth, a difference the value template alone cannot
    express. Absent, the value draws exactly as it interpolates.

    `bold: true` draws both the label and the value in the bold weight -- a
    receipt's `TOTAL` and its cash `CHANGE` line, the only pairs the legacy
    renderers drew in `font_bold`.

    `budget: <FIELD_BUDGET_NAME>` fits the *value* only -- the label always
    draws in full, unshrunk and unwrapped, exactly as it does today. This
    forks the drawing itself, not just the recording: the unbudgeted path
    below draws the label and value as described above (so both stay
    pixel-identical to before when no budget is given), while the budgeted
    path draws the label first and then fits the value into the remaining
    space via `_draw_fitted_text`, since a fit budget must know the value's
    own text to shrink or wrap it -- it cannot operate on a combined
    joined "label: value" string without also constraining the label. The
    budgeted path honours `value_align` too (an unshrunk label plus a
    right-aligned, budget-fitted value), but does not apply `min_gap` -- a
    budget already exists precisely to keep the value's own extent bounded,
    and `min_gap`'s render-time label repositioning has no defined interaction
    with a value that may still wrap across multiple lines. That is enforced
    rather than merely documented: `_validate_text_budget` rejects a budgeted
    right-aligned pair whose `min_gap` resolves above zero, so no layout can
    ask for a gap this path would drop.

    Args:
        block: The `pair` block.
        ctx: Render context.
        y: Current y-cursor.

    Returns:
        The advanced y-cursor.

    Raises:
        LayoutBudgetError: If `budget` is present but not a valid budget in
            this layout's `field_budgets`.
        CurrencyError: If `currency` is present but the value is not an amount.
    """
    role = str(
        resolve_param(block, ctx.layout, "role", layout_id=ctx.layout_id, layout_path=ctx.layout_path)
    )
    size = resolve_role(ctx.layout, role)
    label = interpolate(block["label"], ctx.entry["fields"])
    value = interpolate(block["value"], ctx.entry["fields"])
    currency = block.get("currency")
    if currency is not None and value:
        value = format_currency(value, str(currency), layout_id=ctx.layout_id, layout_path=ctx.layout_path)
    color = str(
        resolve_param(block, ctx.layout, "color", layout_id=ctx.layout_id, layout_path=ctx.layout_path)
    )
    bold = bool(
        resolve_param(block, ctx.layout, "bold", layout_id=ctx.layout_id, layout_path=ctx.layout_path)
    )
    font = font_for(
        ctx.layout, block, size, bold=bold, layout_id=ctx.layout_id, layout_path=ctx.layout_path
    )
    value_align = str(
        resolve_param(
            block,
            ctx.layout,
            "pair_value_align",
            layout_id=ctx.layout_id,
            layout_path=ctx.layout_path,
            block_key="value_align",
        )
    )

    # What sits between the label and the value -- ": " on a receipt's "Date"
    # line, ":" on an invoice's right-aligned "Total", "" on a receipt's
    # right-aligned "TOTAL", which legacy drew with no punctuation at all.
    # Resolved once and applied on all three paths below, so `pair` has one
    # label convention rather than one per `value_align`.
    separator = str(
        resolve_param(
            block,
            ctx.layout,
            "pair_separator",
            layout_id=ctx.layout_id,
            layout_path=ctx.layout_path,
            block_key="separator",
        )
    )
    label_text = f"{label}{separator}"

    # The label is captured exactly as drawn, trailing separator included.
    # §4.3's `pair_strip_trailing_colon` is the serialiser's job, so the raw
    # drawn form survives in the event stream and the convention stays a policy
    # decision rather than something baked into the corpus.
    if ctx.transcript is not None:
        ctx.transcript.emit("pair", None, label=label_text, value=value)

    budget_name = block.get("budget")
    if budget_name is not None:
        ctx.draw.text((ctx.region.x, y), label_text, font=font, fill=color)
        label_width = int(ctx.draw.textlength(label_text, font=font))
        value_ctx = ctx.within(Region(x=ctx.region.x + label_width, width=ctx.region.width - label_width))
        return _draw_fitted_text(
            block,
            value_ctx,
            y,
            text=value,
            size=size,
            bold=bold,
            align=value_align,
            color=color,
            budget_name=budget_name,
        )

    if value_align == "right":
        min_gap = int(
            resolve_param(
                block,
                ctx.layout,
                "pair_min_gap",
                layout_id=ctx.layout_id,
                layout_path=ctx.layout_path,
                block_key="min_gap",
            )
        )
        label_bbox = font.getbbox(label_text)
        label_width = int(label_bbox[2] - label_bbox[0])
        value_bbox = font.getbbox(value)
        value_width = int(value_bbox[2] - value_bbox[0])
        # Mirrors invoice.py:283-285: the label sits at the region's left
        # edge, unless the value (right-aligned to the region's right edge)
        # would otherwise leave less than min_gap px clear between the two,
        # in which case the label is pushed left just far enough to restore it.
        # `min_gap: 0` means no gap is demanded and the label never moves,
        # which is what both legacy renderers do by default: `draw_line_item`
        # (receipts) and the invoice totals' "separate" branch each draw the
        # label at a fixed x and let a long value run into it. Enforcing a
        # zero gap instead would silently shift the label left of where legacy
        # put it — on 1 of the 55 corpus invoices today ($19,176.69 under a
        # 48px "Total:" needs 456px of a 400px column).
        label_x = ctx.region.x
        if min_gap > 0:
            label_x = min(label_x, ctx.region.right - value_width - label_width - min_gap)
        value_x = ctx.region.right - value_width
        ctx.draw.text((label_x, y), label_text, font=font, fill=color)
        ctx.draw.text((value_x, y), value, font=font, fill=color)
        return y + line_advance(ctx.layout, block, layout_id=ctx.layout_id, layout_path=ctx.layout_path)

    text = f"{label_text}{value}"
    _draw_line(ctx, text, y, font=font, align="left", color=color)
    return y + line_advance(ctx.layout, block, layout_id=ctx.layout_id, layout_path=ctx.layout_path)


def draw_block(block: dict, ctx: RenderContext, y: int) -> int:
    """Draw a group of lines, optionally under a heading.

    Args:
        block: The `block` block.
        ctx: Render context.
        y: Current y-cursor.

    Returns:
        The advanced y-cursor.
    """
    role = str(
        resolve_param(block, ctx.layout, "role", layout_id=ctx.layout_id, layout_path=ctx.layout_path)
    )
    size = resolve_role(ctx.layout, role)
    color = str(
        resolve_param(block, ctx.layout, "color", layout_id=ctx.layout_id, layout_path=ctx.layout_path)
    )
    advance = line_advance(ctx.layout, block, layout_id=ctx.layout_id, layout_path=ctx.layout_path)
    heading = block.get("heading")
    if heading is not None:
        heading_font = font_for(
            ctx.layout, block, size, bold=True, layout_id=ctx.layout_id, layout_path=ctx.layout_path
        )
        heading_text = interpolate(heading, ctx.entry["fields"])
        # A heading is a `line`, not a `title`. The H1 belongs to the page's
        # own title — a `banner`, or a `text` block carrying `title: true`;
        # a block heading is a section sub-head drawn bold, and emphasis is
        # deliberately outside the Markdown subset.
        if ctx.transcript is not None:
            ctx.transcript.emit("line", heading_text)
        _draw_line(ctx, heading_text, y, font=heading_font, align="left", color=color)
        y += advance
    line_font = font_for(ctx.layout, block, size, layout_id=ctx.layout_id, layout_path=ctx.layout_path)
    for line in block["lines"]:
        line_text = interpolate(line, ctx.entry["fields"])
        if ctx.transcript is not None:
            ctx.transcript.emit("line", line_text)
        _draw_line(ctx, line_text, y, font=line_font, align="left", color=color)
        y += advance
    return y


def draw_rule(block: dict, ctx: RenderContext, y: int) -> int:
    """Draw a horizontal separator across the region.

    `rule_fill_char` (block key `fill_char`) chooses between two separator
    styles:

    - `none` (the default, and the only style bank statements use): a thin
      drawn line, `rule_thickness` px tall, via `draw_separator_line`.
    - any other string: a row of that glyph repeated to fill the region's
      width, via `common.draw_separator` -- reused rather than
      reimplementing its glyph-count arithmetic (`common.py:548-553`). This
      is visually a row of *characters*, not a drawn line, so it occupies a
      full text line: the cursor advances by `line_advance`, not by
      `thickness` (which this style ignores entirely).

    Args:
        block: The `rule` block.
        ctx: Render context.
        y: Current y-cursor.

    Returns:
        The advanced y-cursor.
    """
    y += int(
        resolve_param(
            block,
            ctx.layout,
            "rule_pad_above",
            layout_id=ctx.layout_id,
            layout_path=ctx.layout_path,
            block_key="pad_above",
        )
    )
    color = str(
        resolve_param(block, ctx.layout, "color", layout_id=ctx.layout_id, layout_path=ctx.layout_path)
    )
    pad_below = int(
        resolve_param(
            block,
            ctx.layout,
            "rule_pad_below",
            layout_id=ctx.layout_id,
            layout_path=ctx.layout_path,
            block_key="pad_below",
        )
    )
    fill_char = str(
        resolve_param(
            block,
            ctx.layout,
            "rule_fill_char",
            layout_id=ctx.layout_id,
            layout_path=ctx.layout_path,
            block_key="fill_char",
        )
    )

    if fill_char != "none":
        role = str(
            resolve_param(block, ctx.layout, "role", layout_id=ctx.layout_id, layout_path=ctx.layout_path)
        )
        size = resolve_role(ctx.layout, role)
        font = font_for(ctx.layout, block, size, layout_id=ctx.layout_id, layout_path=ctx.layout_path)
        # `ctx.region.x * 2 + ctx.region.width` reconstructs the symmetric-margin
        # page width draw_separator expects (margin=region.x on both sides) --
        # the same reconstruction _draw_fitted_text's center alignment uses.
        # A glyph rule paints a row of repeated characters, so it puts *text* on
        # the canvas that §4.3 says emits nothing. This is the only sanctioned
        # exemption from the coverage invariant; keep it this narrow.
        with _decoration(ctx):
            draw_separator(
                ctx.draw,
                y,
                ctx.region.x * 2 + ctx.region.width,
                ctx.region.x,
                font,
                fill=color,
                char=fill_char,
            )
        return (
            y
            + line_advance(ctx.layout, block, layout_id=ctx.layout_id, layout_path=ctx.layout_path)
            + pad_below
        )

    thickness = int(
        resolve_param(
            block,
            ctx.layout,
            "rule_thickness",
            layout_id=ctx.layout_id,
            layout_path=ctx.layout_path,
            block_key="thickness",
        )
    )
    draw_separator_line(ctx.draw, ctx.region.x, ctx.region.right, y, color=color, width=thickness)
    return y + thickness + pad_below


def draw_spacer(block: dict, ctx: RenderContext, y: int) -> int:
    """Advance the cursor by a fixed height.

    Args:
        block: The `spacer` block.
        ctx: Render context.
        y: Current y-cursor.

    Returns:
        The advanced y-cursor.
    """
    return y + int(
        resolve_param(
            block,
            ctx.layout,
            "spacer_height",
            layout_id=ctx.layout_id,
            layout_path=ctx.layout_path,
            block_key="height",
        )
    )


def draw_banner(block: dict, ctx: RenderContext, y: int) -> int:
    """Draw a full-bleed colour bar at the very top of the page.

    Every other primitive draws inside `ctx.region` — inset from the page
    edge by the layout's margin. A masthead like ANZ's blue header bar does
    not: it spans edge-to-edge, ignoring the margin entirely. This is the one
    primitive allowed to paint outside the region, and it always paints at
    the fixed page position `(0, 0)` regardless of the cursor, matching the
    legacy renderer it replaces, which draws it before establishing any
    cursor-driven layout and then jumps straight to a hardcoded y for the
    content below. It leaves the y-cursor untouched for the same reason — a
    `spacer` placed after it in the layout's `body:` reaches whichever y the
    content below the bar actually starts at.

    Args:
        block: The `banner` block, carrying `height`, `color`, and either
            `content` or `from_layout` (mutually exclusive — see
            `draw_text_block`), plus optional `text_color` (default white),
            `role` (font-size role, default "header"), `bold` (default
            False), and `text_y` (the text's absolute y from the page top,
            default 0).
        ctx: Render context.
        y: Current y-cursor, returned unchanged.

    Returns:
        `y`, unchanged — this primitive never advances the flow.
    """
    width = int(ctx.layout["page_dimensions"]["width"])
    height = int(block["height"])
    ctx.draw.rectangle([(0, 0), (width, height)], fill=block["color"])

    role = str(
        resolve_param(
            block,
            ctx.layout,
            "banner_role",
            layout_id=ctx.layout_id,
            layout_path=ctx.layout_path,
            block_key="role",
        )
    )
    size = resolve_role(ctx.layout, role)
    bold = bool(
        resolve_param(block, ctx.layout, "bold", layout_id=ctx.layout_id, layout_path=ctx.layout_path)
    )
    font = font_for(
        ctx.layout, block, size, bold=bold, layout_id=ctx.layout_id, layout_path=ctx.layout_path
    )
    if "from_layout" in block:
        text = str(ctx.layout[block["from_layout"]])
    else:
        text = interpolate(block["content"], ctx.entry["fields"])
    if ctx.transcript is not None:
        ctx.transcript.emit("title", text)
    text_y = int(
        resolve_param(
            block,
            ctx.layout,
            "banner_text_y",
            layout_id=ctx.layout_id,
            layout_path=ctx.layout_path,
            block_key="text_y",
        )
    )
    text_color = str(
        resolve_param(
            block,
            ctx.layout,
            "banner_text_color",
            layout_id=ctx.layout_id,
            layout_path=ctx.layout_path,
            block_key="text_color",
        )
    )
    ctx.draw.text((ctx.region.x, text_y), text, font=font, fill=text_color)
    return y
