"""The walker: dispatch each block in a layout body to its primitive drawer."""

from collections.abc import Callable

from generators.common import FitError
from generators.layout_budgets import LayoutBudgetError
from generators.layout_dsl.binding import BindingError, is_present
from generators.layout_dsl.context import Region, RenderContext
from generators.layout_dsl.defaults import DefaultsError
from generators.layout_dsl.field_providers import apply_field_providers
from generators.layout_dsl.primitives_container import ContainerError, draw_panel, draw_split
from generators.layout_dsl.primitives_table import TableError, draw_table
from generators.layout_dsl.primitives_text import (
    CurrencyError,
    RoleError,
    draw_banner,
    draw_block,
    draw_pair,
    draw_rule,
    draw_spacer,
    draw_text_block,
)
from generators.transcript import DrawSurface, TranscriptRecorder

Drawer = Callable[[dict, RenderContext, int], int]

PRIMITIVE_DRAWERS: dict[str, Drawer] = {
    "text": draw_text_block,
    "pair": draw_pair,
    "block": draw_block,
    "rule": draw_rule,
    "spacer": draw_spacer,
    "panel": draw_panel,
    "split": draw_split,
    "table": draw_table,
    "banner": draw_banner,
}


class EngineError(RuntimeError):
    """Raised when a block cannot be dispatched at render time."""


# Every runtime error a primitive (or a helper it calls) can raise while drawing.
# render_blocks tags each with the failing block's path as it unwinds through
# nested containers, so render_body can report exactly which block failed rather
# than just which layout.
#
# `DefaultsError` belongs here as much as any of the others: every primitive
# parameter resolves through `resolve_param`, so a missing default is the
# likeliest render-time DSL failure there is, and its own message cannot name
# the block that asked -- `resolve_param` sees a block dict, not its position
# in the body. Without it, the failure most likely to reach an author was the
# one escaping without the `At: <layout_id>.body[3].children[1]` suffix.
#
# `FieldProviderError` deliberately is *not* here. `apply_field_providers`
# runs once in `render_body` before the walk begins, outside its try, so no
# field-provider failure ever passes through `render_blocks` to be tagged.
# Listing it would promise a block path that cannot exist; its own diagnostic
# names the provider and the layout's `field_providers:` entry instead.
_DSL_ERRORS: tuple[type[RuntimeError], ...] = (
    EngineError,
    ContainerError,
    TableError,
    RoleError,
    CurrencyError,
    BindingError,
    LayoutBudgetError,
    FitError,
    DefaultsError,
)


def _tag_path(err: RuntimeError, segment: str) -> None:
    """Prepend a path segment to an exception's accumulating `dsl_path`.

    Uses `setattr`/`getattr` rather than attribute syntax so mypy does not
    need every DSL exception class to declare `dsl_path` — the attribute is
    walker bookkeeping, not part of any exception class's own contract.

    Args:
        err: The exception propagating out of a block's drawer.
        segment: This nesting level's own path segment, e.g. `[1](panel)`.
    """
    existing = getattr(err, "dsl_path", [])
    setattr(err, "dsl_path", [segment, *existing])  # noqa: B010


def render_blocks(blocks: list, ctx: RenderContext, y: int) -> int:
    """Render a list of blocks in order, threading the y-cursor.

    Args:
        blocks: Block dicts to render.
        ctx: Render context, already scoped to the right region.
        y: Starting y-cursor.

    Returns:
        The y-cursor after the last block.

    Raises:
        EngineError: If a block names a primitive with no registered drawer.
        ContainerError | TableError | RoleError | CurrencyError | BindingError |
        LayoutBudgetError | FitError | DefaultsError: Propagated from a
            primitive, tagged with the failing block's path so render_body can
            report exactly where it happened.
    """
    for position, block in enumerate(blocks):
        when = block.get("when")
        if when is not None and not is_present(ctx.entry["fields"], when):
            continue

        kind = block.get("type")
        drawer = PRIMITIVE_DRAWERS.get(str(kind))
        if drawer is None:
            unknown = EngineError(
                "Cannot render layout block.\n"
                f"  What:     no drawer registered for primitive '{kind}'.\n"
                f"  Where:    {ctx.layout_path} -> {ctx.layout_id}.body\n"
                f"  Expected: one of {sorted(PRIMITIVE_DRAWERS)}.\n"
                f"  Recover:  use a supported primitive, or register a drawer in "
                f"PRIMITIVE_DRAWERS in generators/layout_dsl/engine.py."
            )
            _tag_path(unknown, f"[{position}]({kind})")
            raise unknown

        try:
            if ctx.transcript is not None:
                # One authorisation scope per block. Without it a primitive that
                # draws but emits nothing would inherit its neighbour's seq and
                # pass the coverage check silently — the §8.2 hole exactly.
                with ctx.transcript.block():
                    y = drawer(block, ctx, y)
            else:
                y = drawer(block, ctx, y)
        except _DSL_ERRORS as err:
            # Each nesting level prepends its own segment as the error unwinds, so
            # the final path reads outermost-first: body[2].children[0].
            _tag_path(err, f"[{position}]({kind})")
            raise
    return y


def render_body(
    layout: dict,
    entry: dict,
    *,
    layout_id: str,
    layout_path: str,
    draw: DrawSurface,
    region: Region,
    y: int,
    transcript: TranscriptRecorder | None = None,
) -> int:
    """Render a layout's whole body onto a drawing surface.

    Args:
        layout: The resolved layout dict, carrying `body`.
        entry: The ground-truth entry.
        layout_id: Layout id, used in diagnostics.
        layout_path: Path to the layout YAML, used in diagnostics.
        draw: The PIL drawing surface.
        region: The page's content region.
        y: Starting y-cursor, normally the layout's top margin.
        transcript: Optional draw-time transcript capture (design §4.1).

    Returns:
        The y-cursor after the last block.

    Raises:
        EngineError: If the layout has no `body` key.
        FieldProviderError: If a `field_providers:` entry names an unknown
            provider, or a provider returns a key it did not declare. Raised
            before the walk starts, so it carries no block path.
        ContainerError | TableError | RoleError | CurrencyError | BindingError |
        LayoutBudgetError | FitError | DefaultsError: Propagated from a
            primitive, re-raised with the failing block's path appended to the
            message so the author knows exactly where to look, not just which
            layout.
    """
    if "body" not in layout:
        raise EngineError(
            "Cannot render layout.\n"
            f"  What:     layout '{layout_id}' has no 'body' key.\n"
            f"  Where:    {layout_path} -> {layout_id}.body\n"
            "  Expected: body: a list of block mappings, each with a 'type' key.\n"
            f"  Recover:  add a 'body:' list to {layout_id}, or run "
            "`python -m generators.pipeline validate` to see the full diagnostic."
        )

    entry = apply_field_providers(layout, entry)

    ctx = RenderContext(
        draw=draw,
        entry=entry,
        layout=layout,
        layout_id=layout_id,
        layout_path=layout_path,
        region=region,
        transcript=transcript,
        render_children=render_blocks,
    )
    try:
        return render_blocks(layout["body"], ctx, y)
    except _DSL_ERRORS as err:
        path = getattr(err, "dsl_path", None)
        if not path:
            raise
        raise type(err)(f"{err}\n  At:       {layout_id}.body{''.join(path)}") from err
