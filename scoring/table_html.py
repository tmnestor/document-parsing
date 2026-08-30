"""HTML tables to a dense cell grid.

`config/serialisation.yml` sets `table_style: html` and `generators/serialise.py`
admits no other value, so every reference table is HTML. Pipe tables are not
accepted: a model that emits one reports `table_count_pred` of 0, which reads as
a format failure rather than being scored as partial cells.

Spans expand by REPLICATION -- a cell covering several grid positions holds its
text at every one of them. That is what a browser renders and what a reader sees
at each position, and it leaves every row of a table the same width, which the
positional comparison in `scoring.tables` depends on. A model that writes a
merged label out on each covered row instead of using `rowspan` therefore scores
those cells correct; one that leaves them blank does not.

Built on `html.parser` from the standard library rather than a regex or a third
party parser. Predictions are arbitrary model output, and `score_tables` promises
never to raise on them: `HTMLParser` is tolerant of unclosed tags and malformed
markup by construction, decodes character references on the way through, and adds
no dependency to an environment whose own comments say it must stay small.

Pure by contract: text in, data out. No filesystem, no policy, no import of
`generators`.
"""

from html.parser import HTMLParser

# A prediction must not be able to size the scorer's grid arbitrarily. The widest
# table in the corpus is five columns, so this is far above any honest value and
# only bites on `colspan="999999999"`.
_MAX_SPAN = 64

_CELL_TAGS = ("td", "th")


def _span(attrs: dict[str, str | None], name: str) -> int:
    """Read one span attribute, clamped to something a grid can hold.

    Args:
        attrs: The tag's attributes, lowercased by `HTMLParser`.
        name: `colspan` or `rowspan`.

    Returns:
        The span. Absent, non-numeric, or less than one all count as one; a
        value above `_MAX_SPAN` is capped there.
    """
    raw = attrs.get(name)
    if raw is None:
        return 1
    try:
        value = int(raw.strip())
    except (AttributeError, ValueError):
        return 1
    return max(1, min(value, _MAX_SPAN))


class _GridParser(HTMLParser):
    """Accumulate `<table>` elements as rectangular grids of cell text.

    `<thead>` and `<tbody>` are ignored as structure: rows come out flat in
    document order. That keeps an empty header row -- which
    `headerless_table: empty_header_row` emits and `config/prompt.md` instructs
    the model to reproduce -- as a row of empty strings rather than discarding
    it.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[str]]] = []
        self._grid: list[list[str]] | None = None
        self._row: list[str] | None = None
        self._column = 0
        # Column -> [text, rows still to fill, counting the row being built].
        self._carries: dict[int, list] = {}
        self._cell: list[str] | None = None
        self._colspan = 1
        self._rowspan = 1

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Open a table, row or cell, closing any of those left open."""
        if tag == "table":
            # A nested table flushes the one around it. Not a corpus shape; this
            # keeps a malformed prediction predictable rather than clever.
            self._close_table()
            self._grid = []
            self._carries = {}
        elif tag == "tr":
            if self._grid is None:
                return
            self._close_row()
            self._row = []
            self._column = 0
        elif tag in _CELL_TAGS:
            if self._row is None:
                return
            self._close_cell()
            self._cell = []
            attributes = dict(attrs)
            self._colspan = _span(attributes, "colspan")
            self._rowspan = _span(attributes, "rowspan")

    def handle_endtag(self, tag: str) -> None:
        """Close a cell, row or table."""
        if tag in _CELL_TAGS:
            self._close_cell()
        elif tag == "tr":
            self._close_row()
        elif tag == "table":
            self._close_table()

    def handle_data(self, data: str) -> None:
        """Collect text, but only inside a cell.

        Markup nested in a cell contributes its text and loses its tags, so
        `<td><b>Total</b></td>` is the cell `Total`.
        """
        if self._cell is not None:
            self._cell.append(data)

    def _close_cell(self) -> None:
        """Place the open cell into the row, replicating it across its span."""
        if self._cell is None or self._row is None:
            return
        text = "".join(self._cell).strip()
        self._cell = None

        self._fill_carried()
        start = self._column
        for offset in range(self._colspan):
            self._row.append(text)
            if self._rowspan > 1:
                # Counting the current row, so the decrement at row close leaves
                # exactly `rowspan - 1` rows still to fill.
                self._carries[start + offset] = [text, self._rowspan]
        self._column += self._colspan

    def _fill_carried(self) -> None:
        """Advance past columns a `rowspan` from an earlier row already owns."""
        if self._row is None:
            return
        while self._column in self._carries:
            self._row.append(self._carries[self._column][0])
            self._column += 1

    def _close_row(self) -> None:
        """Finish the open row and age every carry by one row."""
        if self._row is None:
            return
        self._close_cell()
        # A carry can sit past the last cell the row wrote, so the row is not
        # rectangular until these are filled in.
        self._fill_carried()
        if self._grid is not None:
            self._grid.append(self._row)
        self._row = None

        for column in list(self._carries):
            self._carries[column][1] -= 1
            if self._carries[column][1] <= 0:
                del self._carries[column]

    def _close_table(self) -> None:
        """Finish the open table, closing any row still open inside it."""
        if self._grid is None:
            return
        self._close_row()
        self.tables.append(self._grid)
        self._grid = None
        self._carries = {}

    def finish(self) -> list[list[list[str]]]:
        """Flush anything left open by truncated input and return the tables."""
        self._close_table()
        return self.tables


def parse_html_tables(text: str) -> list[list[list[str]]]:
    """Extract every HTML table from a page as a grid of cell text.

    Args:
        text: A reference transcript or a model prediction.

    Returns:
        One entry per table, in document order; each a list of rows, each row a
        list of cell strings. Spanning cells are replicated into every grid
        position they cover, so all rows of one table share a width.
    """
    parser = _GridParser()
    parser.feed(text)
    parser.close()
    return parser.finish()
