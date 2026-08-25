"""Horizontal geometry and per-render state for the layout engine.

`Region` is the only place nesting arithmetic lives: a container narrows or
divides its own region and hands the result to its children, so no primitive
needs to know how deeply it is nested.
"""

from collections.abc import Callable
from dataclasses import dataclass

from generators.transcript import DrawSurface, TranscriptRecorder


@dataclass(frozen=True)
class Region:
    """A horizontal slice of the page available to a block.

    Attributes:
        x: Absolute left edge in pixels.
        width: Usable content width in pixels.
    """

    x: int
    width: int

    @property
    def right(self) -> int:
        """Absolute right edge in pixels."""
        return self.x + self.width

    def indent(self, left: int, right: int = 0) -> "Region":
        """Return a narrowed region inset from this one.

        Args:
            left: Pixels to inset from the left edge.
            right: Pixels to inset from the right edge.

        Returns:
            A new Region shifted right by `left` and narrowed by `left + right`.

        Raises:
            ValueError: If the insets consume the whole region.
        """
        width = self.width - left - right
        if width < 1:
            raise self._indent_error(left, right, width)
        return Region(x=self.x + left, width=width)

    def _indent_error(self, left: int, right: int, width: int) -> ValueError:
        """Build a four-element diagnostic for an indent that consumes the region.

        `panel` is the only caller (see `generators/layout_dsl/primitives_container.py`),
        always with `left == right == padding`, so the remediation names that key.
        """
        max_padding = max((self.width - 1) // 2, 0)
        return ValueError(
            "Region.indent leaves no usable width.\n"
            f"  What:     indent(left={left}, right={right}) leaves width {width} from "
            f"a {self.width}px region.\n"
            "  Where:    config/layouts/*.yml -> a panel block's `padding` key.\n"
            f"  Expected: padding <= {max_padding} for a {self.width}px region, e.g. "
            f"padding: {max_padding}.\n"
            "  Recover:  reduce the panel's padding, or widen its content_width."
        )

    def divide(self, n: int, gap: int) -> list["Region"]:
        """Split this region into `n` columns separated by `gap` px.

        Columns differ by at most 1px: the remainder left over by floor
        division is handed out one pixel at a time to the leftmost columns,
        so the last column's right edge always reaches `self.right` instead
        of falling short by up to `n - 1` px.

        Args:
            n: Number of columns; must be at least 1.
            gap: Pixels between adjacent columns.

        Returns:
            `n` Regions, left to right.

        Raises:
            ValueError: If `n` < 1, or the gaps leave no usable column width.
        """
        if n < 1:
            msg = f"Region.divide needs n >= 1, got {n}. Remediation: pass a positive column count."
            raise ValueError(msg)
        total_gap = gap * (n - 1)
        usable = self.width - total_gap
        column = usable // n
        if column < 1:
            raise self._divide_error(n, gap, column)
        # Hand the remainder out one pixel at a time to the leftmost columns, so
        # the columns differ by at most 1px and the last one reaches self.right.
        remainder = usable - column * n
        regions: list[Region] = []
        x = self.x
        for i in range(n):
            width = column + (1 if i < remainder else 0)
            regions.append(Region(x=x, width=width))
            x += width + gap
        return regions

    def divide_widths(self, widths: list[int], gap: int) -> list["Region"]:
        """Split this region into columns of explicit widths, separated by `gap` px.

        Unlike `divide`, columns need not be equal: invoice totals occupy a
        fixed 400px column at the page's right edge (`right_edge - 400`),
        which equal division cannot express. Each column's x is the previous
        column's right edge plus `gap` -- the same stepping `divide` uses --
        so the two behave consistently wherever a layout mixes them.

        Args:
            widths: Column widths in pixels, left to right.
            gap: Pixels between adjacent columns.

        Returns:
            `len(widths)` Regions, left to right.

        Raises:
            ValueError: If `sum(widths) + gap * (len(widths) - 1)` exceeds
                `self.width`.
        """
        total = sum(widths) + gap * (len(widths) - 1)
        if total > self.width:
            raise self._divide_widths_error(widths, gap, total)
        regions: list[Region] = []
        x = self.x
        for width in widths:
            regions.append(Region(x=x, width=width))
            x += width + gap
        return regions

    def _divide_widths_error(self, widths: list[int], gap: int, total: int) -> ValueError:
        """Build a four-element diagnostic for divide_widths widths that overflow the region.

        `split` is the only caller (see `generators/layout_dsl/primitives_container.py`),
        so the remediation names its `widths` key.
        """
        return ValueError(
            "Region.divide_widths overflows the region.\n"
            f"  What:     widths {widths} with gap={gap} need {total}px but this region "
            f"is only {self.width}px.\n"
            "  Where:    config/layouts/*.yml -> a split block's `widths` key.\n"
            f"  Expected: sum(widths) + gap * (len(widths) - 1) <= {self.width}, e.g. "
            "shrink one or more of the declared widths.\n"
            "  Recover:  reduce a width in split.widths, or reduce the split's gap."
        )

    def _divide_error(self, n: int, gap: int, column: int) -> ValueError:
        """Build a four-element diagnostic for a divide that leaves no column width.

        `split` is the only caller (see `generators/layout_dsl/primitives_container.py`),
        so the remediation names its `gap` key.
        """
        max_gap = max((self.width - n) // max(n - 1, 1), 0)
        return ValueError(
            "Region.divide leaves no usable column width.\n"
            f"  What:     divide({n}, gap={gap}) leaves column width {column} from a "
            f"{self.width}px region.\n"
            "  Where:    config/layouts/*.yml -> a split block's `gap` key.\n"
            f"  Expected: gap <= {max_gap} for {n} columns in a {self.width}px region, "
            f"e.g. gap: {max_gap}.\n"
            "  Recover:  reduce the split's gap, or its number of columns."
        )


@dataclass
class RenderContext:
    """Everything a primitive needs besides its own block dict and the y-cursor.

    Attributes:
        draw: The PIL drawing surface.
        entry: The ground-truth entry being rendered.
        layout: The resolved layout dict.
        layout_id: Layout id, used in diagnostics.
        layout_path: Path to the layout YAML, used in diagnostics.
        region: The horizontal slice this block may draw into.
        transcript: Optional draw-time transcript capture (design §4.1). When
            given, primitives emit an event as they resolve each string, and
            `TranscriptDraw` refuses any text draw no event authorised.
        render_children: The walker, injected by the engine so containers can
            render nested blocks without importing the engine — which would be
            a circular import, since the engine's dispatch table imports them.
    """

    draw: DrawSurface
    entry: dict
    layout: dict
    layout_id: str
    layout_path: str
    region: Region
    transcript: TranscriptRecorder | None = None
    render_children: "Callable[[list, RenderContext, int], int] | None" = None

    def within(self, region: Region) -> "RenderContext":
        """Return a copy of this context scoped to a different region.

        Args:
            region: The child region.

        Returns:
            A new RenderContext sharing all state but the region.
        """
        return RenderContext(
            draw=self.draw,
            entry=self.entry,
            layout=self.layout,
            layout_id=self.layout_id,
            layout_path=self.layout_path,
            region=region,
            transcript=self.transcript,
            render_children=self.render_children,
        )
