"""Captured table events to HTML, for TEDS.

A pure function of the event stream, like `serialise` — it imports no PIL and
renders nothing, so the convention can change and every table re-emit in seconds
without re-rendering an image (design §6 of the original spec, applied to a
third projection).

TEDS is defined over an HTML tree (Zhong et al., arXiv:1911.10683), so HTML is
the form that gets emitted. OmniDocBench also carries a `latex` field and marks
both optional; it is left absent here because nothing scores it and two
statements of one structure drift (design §5).

No `colspan` or `rowspan`: the table primitive has no merged-cell concept, so
every table is a uniform grid and the attributes would be constant. They arrive
with subsystem B.
"""

import html as html_escape

_CELL_JOIN = " "


class TableHtmlError(RuntimeError):
    """Raised when a table's event stream cannot be rendered as HTML."""


def _err(what: str, *, seq: int | None) -> TableHtmlError:
    """Build a four-element fail-fast diagnostic."""
    where = f"events.jsonl -> seq {seq}" if seq is not None else "events.jsonl -> end of stream"
    return TableHtmlError(
        "Cannot render a table as HTML.\n"
        f"  What:     {what}\n"
        f"  Where:    {where}\n"
        "  Expected: a balanced table stream, e.g.\n"
        "              table_open, row_open, cell..., row_close, ..., table_close\n"
        "  Recover:  regenerate the corpus; a truncated stream means `generate` did not "
        "finish, and a re-run repairs it."
    )


def table_html(events: list[dict]) -> list[str]:
    """Render every table in an event stream as an HTML table.

    Args:
        events: One page's events, as stored in `events.jsonl`.

    Returns:
        One HTML string per table, in walk order. Empty when the page has none.

    Raises:
        TableHtmlError: A table is not closed, or a row is not closed.
    """
    tables: list[str] = []
    rows: list[tuple[list[str], bool]] = []
    row: list[str] = []
    keys: list[str] = []
    is_header = False
    open_seq: int | None = None
    open_row_seq: int | None = None
    table_columns: list[str] = []

    for event in events:
        kind = event["kind"]
        meta = event.get("meta") or {}
        if kind == "table_open":
            rows, open_seq = [], int(event["seq"])
            table_columns = list(meta.get("columns") or [])
        elif kind == "row_open":
            row, keys, is_header = [], [], False
            open_row_seq = int(event["seq"])
        elif kind == "cell":
            row.append(str(event["text"] or ""))
            keys.append(str(meta.get("column_key", "")))
            is_header = is_header or bool(meta.get("header"))
        elif kind == "cell_sub_line":
            key = str(meta.get("column_key", ""))
            if key in keys:
                position = keys.index(key)
                row[position] = f"{row[position]}{_CELL_JOIN}{event['text'] or ''}".strip()
            elif rows and key in table_columns:
                position = table_columns.index(key)
                cells, header = rows[-1]
                if position < len(cells):
                    cells[position] = f"{cells[position]}{_CELL_JOIN}{event['text'] or ''}".strip()
        elif kind == "row_close":
            if open_row_seq is None:
                raise _err("a row_close has no matching row_open.", seq=int(event["seq"]))
            rows.append((row, is_header))
            row, keys, is_header = [], [], False
            open_row_seq = None
        elif kind == "table_close":
            if open_row_seq is not None:
                raise _err("a row_open has no matching row_close.", seq=open_row_seq)
            tables.append(_render(rows, table_columns))
            rows, open_seq, table_columns = [], None, []

    if open_seq is not None:
        raise _err("a table_open has no matching table_close.", seq=open_seq)
    if open_row_seq is not None:
        raise _err("a row_open has no matching row_close.", seq=open_row_seq)
    return tables


def _render(rows: list[tuple[list[str], bool]], columns: list[str]) -> str:
    """Render collected rows as one HTML table.

    Args:
        rows: Each row's cell texts, and whether it is a header row.
        columns: Column keys from table_open metadata.

    Returns:
        The table's HTML, header rows in `<thead>` and the rest in `<tbody>`.
        Every row is padded to match the column count.
    """
    width = len(columns)
    blank = ""

    def padded_cells(cells: list[str]) -> list[str]:
        """Pad cells to match table width."""
        return (cells + [blank] * (width - len(cells)))[:width]

    head = "".join(_row(padded_cells(cells), "th") for cells, header in rows if header)
    body = "".join(_row(padded_cells(cells), "td") for cells, header in rows if not header)
    parts = ["<table>"]
    if head:
        parts.append(f"<thead>{head}</thead>")
    if body:
        parts.append(f"<tbody>{body}</tbody>")
    parts.append("</table>")
    return "".join(parts)


def _row(cells: list[str], tag: str) -> str:
    """Render one row, escaping every cell.

    Args:
        cells: The row's cell texts.
        tag: `th` for a header row, `td` otherwise.

    Returns:
        The row's HTML. An empty cell keeps its element, so the column count
        survives — dropping it would shift every later column left.
    """
    return "<tr>" + "".join(f"<{tag}>{html_escape.escape(c)}</{tag}>" for c in cells) + "</tr>"
