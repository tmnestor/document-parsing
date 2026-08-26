"""Captured table events to HTML, for TEDS.

A pure function of the event stream **and the serialisation policy**, like
`serialise` — it imports no PIL and renders nothing, so the convention can
change and every table re-emit in seconds without re-rendering an image
(design §6 of the original spec, applied to a third projection).

Taking the policy is not optional: a page draws a date-grouped table's date
once, and `serialise._render_table` carries that date onto every row of the
group (`carry_group_key_down`) so a reader never needs a row that means
"repeat the row above". Without the same policy, this module's HTML disagreed
with the Markdown transcript on which cells hold a date — 34 of 55 bank
statements, corpus-wide — and disagreed with `config/prompt.md`'s own
instruction to the model. `carry_group_key_down` and `_join_cell` are imported
from `serialise.py` rather than re-implemented, the same shared-helper ruling
already applied to `pair_text`. A sub-line's dot leader (§ the `cell_sub_line`
branch below) gets the same treatment: stripped with `strip_decoration_run`
under `decoration_glyphs`/`decoration_min_run` and joined with
`cell_sub_line_join`, exactly as `serialise.serialise` does — not a second,
hardcoded `" "` join with no stripping at all.

TEDS is defined over an HTML tree (Zhong et al., arXiv:1911.10683), so HTML is
the form that gets emitted. OmniDocBench also carries a `latex` field and marks
both optional; it is left absent here because nothing scores it and two
statements of one structure drift (design §5).

No `colspan` or `rowspan`: the table primitive has no merged-cell concept, so
every table is a uniform grid and the attributes would be constant. They arrive
with subsystem B.
"""

import html as html_escape

from generators.decoration import strip_decoration_run
from generators.serialise import RowWidthError, _join_cell, carry_group_key_down, pad_row


class TableHtmlError(RuntimeError):
    """Raised when a table's event stream cannot be rendered as HTML."""


_DEFAULT_EXPECTED = (
    "a balanced table stream, e.g.\n"
    "              table_open, row_open, cell..., row_close, ..., table_close"
)
_DEFAULT_RECOVER = (
    "regenerate the corpus; a truncated stream means `generate` did not finish, and a re-run repairs it."
)


def _err(
    what: str,
    *,
    seq: int | None,
    expected: str = _DEFAULT_EXPECTED,
    recover: str = _DEFAULT_RECOVER,
) -> TableHtmlError:
    """Build a four-element fail-fast diagnostic."""
    where = f"events.jsonl -> seq {seq}" if seq is not None else "events.jsonl -> end of stream"
    return TableHtmlError(
        "Cannot render a table as HTML.\n"
        f"  What:     {what}\n"
        f"  Where:    {where}\n"
        f"  Expected: {expected}\n"
        f"  Recover:  {recover}"
    )


def table_html(events: list[dict], policy: dict) -> list[str]:
    """Render every table in an event stream as an HTML table.

    Args:
        events: One page's events, as stored in `events.jsonl`.
        policy: The validated serialisation policy (§`load_serialisation_policy`)
            — the same one `serialise.serialise` used to produce this page's
            Markdown transcript, so the two projections state one ground truth.

    Returns:
        One HTML string per table, in walk order. Empty when the page has none.

    Raises:
        TableHtmlError: A table is not closed, a row is not closed, or a row
            carries more cells than the table declares columns.
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
            row.append(_join_cell(str(event["text"] or policy["empty_cell_token"]), policy))
            keys.append(str(meta.get("column_key", "")))
            is_header = is_header or bool(meta.get("header"))
        elif kind == "cell_sub_line":
            # Mirrors serialise.py's cell_sub_line handling exactly (stripped
            # before the fold, joined with the policy's join string, not a
            # hardcoded " "): a dot-leader continuation cell — NAB's reference
            # padded to 40 dots — must state one ground truth in both
            # projections, not ship the raw leader in HTML while Markdown
            # strips it.
            key = str(meta.get("column_key", ""))
            content = strip_decoration_run(
                str(event["text"] or ""),
                glyphs=policy["decoration_glyphs"],
                min_run=policy["decoration_min_run"],
            )
            if key in keys:
                position = keys.index(key)
                row[position] = f"{row[position]}{policy['cell_sub_line_join']}{content}"
            elif rows and key in table_columns:
                # Defensive only: `_draw_sub_lines` (primitives_table.py:870)
                # always emits cell_sub_line before that row's row_close, so
                # this branch is unreachable on the real corpus today.
                position = table_columns.index(key)
                cells, header = rows[-1]
                if position < len(cells):
                    cells[position] = f"{cells[position]}{policy['cell_sub_line_join']}{content}"
        elif kind == "row_close":
            if open_row_seq is None:
                raise _err("a row_close has no matching row_open.", seq=int(event["seq"]))
            rows.append((row, is_header))
            row, keys, is_header = [], [], False
            open_row_seq = None
        elif kind == "table_close":
            if open_row_seq is not None:
                raise _err("a row_open has no matching row_close.", seq=open_row_seq)
            tables.append(_render(rows, table_columns, policy))
            rows, open_seq, table_columns = [], None, []

    if open_seq is not None:
        raise _err("a table_open has no matching table_close.", seq=open_seq)
    if open_row_seq is not None:
        raise _err("a row_open has no matching row_close.", seq=open_row_seq)
    return tables


def _check_header_shape(rows: list[tuple[list[str], bool]]) -> None:
    """Reject a header-flag shape neither projection can render the same way.

    Exactly two shapes are renderable, and both projections agree on them:
    every row `header=False` (the headerless case — this module's blank-
    `<thead>` branch above, `serialise._render_table`'s `not rows[0][1]`
    branch), or the header rows forming a contiguous prefix (the normal
    case). For the prefix case, `serialise._render_table` never even reads
    a row's `header` flag beyond `rows[0]` — it renders row 0 as the header
    by position and every other row as an ordinary body line, in order.

    Any other shape has no rendering here. A `header=True` row that follows
    a `header=False` row lands in neither `head` (a single blank row in the
    headerless branch, unrelated to any row's actual flag) nor `body`
    (filtered to `not header`) and would vanish from the HTML with nothing
    raised — while `serialise.py` would render the identical stream as an
    ordinary row. 0 occurrences across the real 165-page corpus (179
    tables); this guard exists so a future table primitive that emits this
    shape fails loudly instead of silently dropping a row.

    Args:
        rows: Each row's cell texts, and whether it is a header row.

    Raises:
        TableHtmlError: A `header=True` row follows a `header=False` row.
    """
    seen_non_header = False
    for index, (_cells, is_header) in enumerate(rows):
        if not is_header:
            seen_non_header = True
        elif seen_non_header:
            raise _err(
                f"row {index} is flagged header=True after a non-header row — header flags "
                f"are {[flag for _, flag in rows]}, neither all non-header nor a contiguous "
                "header prefix.",
                seq=None,
                expected="either every row header=False (a headerless table, e.g. several "
                "receipt layouts), or the header rows forming a contiguous prefix — the two "
                "shapes serialise._render_table and tables._render can both render, e.g.\n"
                "              [(cells, True), (cells, False), (cells, False)]",
                recover="the table primitive that emitted this row "
                "(generators/layout_dsl/primitives_table.py) must not mark a row header=True "
                "once a non-header row has already been emitted for this table; serialise.py "
                "and tables.py render such a stream differently, so the shape must be "
                "resolved at the source rather than rendered here.",
            )


def _render(rows: list[tuple[list[str], bool]], columns: list[str], policy: dict) -> str:
    """Render collected rows as one HTML table.

    Args:
        rows: Each row's cell texts, and whether it is a header row.
        columns: Column keys from table_open metadata.
        policy: The validated serialisation policy — `carry_group_key_down` is
            applied under the same `carry_group_key` setting `serialise.py`
            reads, so a date-grouped table states one ground truth in both
            projections rather than two that disagree on which cells hold a
            date.

    Returns:
        The table's HTML, header rows in `<thead>` and the rest in `<tbody>`.
        Every row is padded to match the column count. A table that drew no
        header on the page gets a blank `<thead>` row rather than none, under
        `headerless_table` — the only enum value this serialiser implements.

    Raises:
        TableHtmlError: A row carries more cells than `columns` declares, or
            a `header=True` row follows a `header=False` row — a shape this
            module and `serialise.py` cannot both render the same way.
    """
    if policy["carry_group_key"] == "down":
        rows = carry_group_key_down(rows)

    _check_header_shape(rows)

    width = len(columns)
    blank = policy["empty_cell_token"]

    def padded_cells(cells: list[str]) -> list[str]:
        """Pad cells to match table width, or fail fast on an over-long row."""
        try:
            return pad_row(cells, width, blank)
        except RowWidthError as err:
            raise _err(f"a table row cannot be rendered: {err}", seq=None) from err

    if rows and not rows[0][1]:
        # Headerless on the page (several receipt layouts set `header:
        # false`): `serialise._render_table` inserts a blank header row under
        # `headerless_table: empty_header_row` so a pipe table's first line
        # is never mistaken for column names. Mirrored here so the two
        # projections keep the same row count and the same structure, not an
        # HTML table missing a `<thead>` a Markdown reader would see.
        head = _row(padded_cells([]), "th")
    else:
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
