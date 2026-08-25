"""Nesting containers: panel and split.

These are the only primitives that create child regions, which is why all the
region arithmetic lives in `Region` rather than being duplicated here. Children
render through `ctx.render_children` — injected by the engine — so this module
never imports the engine, which imports it.
"""

from generators.layout_dsl.context import RenderContext
from generators.layout_dsl.defaults import resolve_param


class ContainerError(RuntimeError):
    """Raised when a container is asked to render without a walker."""


def _walker(ctx: RenderContext):
    """Return the injected child renderer, or fail with a diagnostic.

    Args:
        ctx: The render context.

    Returns:
        The injected `render_children` callable.

    Raises:
        ContainerError: If no walker was injected.
    """
    if ctx.render_children is None:
        raise ContainerError(
            "Container cannot render its children.\n"
            "  What:     RenderContext.render_children is None.\n"
            f"  Where:    {ctx.layout_path} -> {ctx.layout_id}.body\n"
            "  Expected: the engine injects render_children before rendering.\n"
            "  Recover:  render through generators.layout_dsl.engine.render_body, "
            "which sets it, rather than constructing a RenderContext by hand."
        )
    return ctx.render_children


def draw_panel(block: dict, ctx: RenderContext, y: int) -> int:
    """Draw a bordered container around a nested list of blocks.

    Args:
        block: The `panel` block, carrying `children` and optional `padding`,
            `border_color`, and a fixed `height`.
        ctx: Render context.
        y: Current y-cursor.

    Returns:
        The advanced y-cursor: past the panel's border and padding.

    Raises:
        ContainerError: If a fixed height is given but children overflow it.
    """
    render_children = _walker(ctx)
    padding = int(
        resolve_param(
            block,
            ctx.layout,
            "panel_padding",
            layout_id=ctx.layout_id,
            layout_path=ctx.layout_path,
            block_key="padding",
        )
    )
    inner_ctx = ctx.within(ctx.region.indent(padding, padding))
    if ctx.transcript is not None:
        ctx.transcript.emit("panel_open")
    inner_end = render_children(block["children"], inner_ctx, y + padding)
    if ctx.transcript is not None:
        ctx.transcript.emit("panel_close")

    fixed = block.get("height")
    if fixed is not None:
        natural = inner_end + padding
        limit = y + int(fixed)
        if natural > limit:
            raise ContainerError(
                "Panel content overflows its fixed height.\n"
                f"  What:     children need {natural - y}px but the panel declares "
                f"height: {int(fixed)}.\n"
                f"  Where:    {ctx.layout_path} -> {ctx.layout_id}.body (a panel block)\n"
                f"  Expected: height >= {natural - y}, or fewer/smaller children.\n"
                f"  Recover:  raise the panel's height to at least {natural - y}, or "
                "reduce its children."
            )
        bottom = limit
    else:
        bottom = inner_end + padding

    ctx.draw.rectangle(
        [(ctx.region.x, y), (ctx.region.right, bottom)],
        outline=str(
            resolve_param(
                block,
                ctx.layout,
                "panel_border_color",
                layout_id=ctx.layout_id,
                layout_path=ctx.layout_path,
                block_key="border_color",
            )
        ),
    )
    return bottom


def draw_split(block: dict, ctx: RenderContext, y: int) -> int:
    """Render child block lists side by side in equal columns.

    Args:
        block: The `split` block, carrying `children` (a list of block lists,
            one per column), an optional `gap`, an optional `widths` (explicit
            per-column pixel widths, e.g. invoice totals' fixed 400px column
            at the right edge — equal division cannot express that), and an
            optional `divider` (draws a vertical rule down the middle of each
            gap, e.g. Westpac's rewards panel, which splits into a points
            summary and a message column separated by a ruled line —
            decorative only, so unlike column geometry it is never checked by
            the equivalence harness).
        ctx: Render context.
        y: Current y-cursor.

    Returns:
        The advanced y-cursor: the bottom of the tallest column.
    """
    render_children = _walker(ctx)
    columns = block["children"]
    if ctx.transcript is not None:
        ctx.transcript.emit("split_open", None, columns=len(columns))
    gap = int(
        resolve_param(
            block,
            ctx.layout,
            "split_gap",
            layout_id=ctx.layout_id,
            layout_path=ctx.layout_path,
            block_key="gap",
        )
    )
    widths = block.get("widths")
    if widths is not None:
        regions = ctx.region.divide_widths([int(w) for w in widths], gap=gap)
    else:
        regions = ctx.region.divide(len(columns), gap=gap)
    # Column by column in DSL order, left to right, never interleaved by
    # vertical position (design §4.3). This is the one convention competent
    # models genuinely disagree on — a two-column header with payer left and
    # document metadata right is often read across visual rows instead — so no
    # normalisation can repair a mismatch and the shipped prompt must state it.
    ends = []
    for child_blocks, region in zip(columns, regions, strict=True):
        if ctx.transcript is not None:
            ctx.transcript.emit("column_open")
        ends.append(render_children(child_blocks, ctx.within(region), y))
        if ctx.transcript is not None:
            ctx.transcript.emit("column_close")
    bottom = max(ends)
    if block.get("divider"):
        color = str(
            resolve_param(
                block,
                ctx.layout,
                "split_divider_color",
                layout_id=ctx.layout_id,
                layout_path=ctx.layout_path,
                block_key="divider_color",
            )
        )
        # Deliberately unequal: pairing each region with its right-hand
        # neighbour yields one fewer divider than there are columns.
        for left_region, right_region in zip(regions, regions[1:], strict=False):
            divider_x = (left_region.right + right_region.x) // 2
            ctx.draw.line([(divider_x, y), (divider_x, bottom)], fill=color)
    if ctx.transcript is not None:
        ctx.transcript.emit("split_close")
    return bottom
