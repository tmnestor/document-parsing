"""Captured table events to HTML, for TEDS.

A pure function of the event stream **and the serialisation policy**, like
`serialise` — it imports no PIL and renders nothing, so the convention can
change and every table re-emit in seconds without re-rendering an image
(design §6 of the original spec, applied to a third projection).

Taking the policy is not optional: a page draws a date-grouped table's date
once, and the transcript carries that date onto every row of the group
(`carry_group_key_down`) so a reader never needs a row that means "repeat the
row above". Without the same policy, this module's HTML disagreed with the
Markdown transcript on which cells hold a date — 34 of 55 bank statements,
corpus-wide — and disagreed with `config/prompt.md`'s own instruction to the
model. `carry_group_key_down`, `_join_cell` and `pad_row` therefore live here,
the one place every table projection reads them from, rather than being
re-implemented per projection — the same shared-helper ruling already applied
to `pair_text`. A sub-line's dot leader (§ the `cell_sub_line`
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


def _join_cell(text: str, policy: dict) -> str:
    """Fold an authored line break inside a cell onto one line.

    A pipe table cell is single-line, so a header authored as
    "Date of\\nTransaction" must be joined. Which joiner is policy, not code.
    """
    return policy["cell_newline_join"].join(part for part in text.split("\n"))


def carry_group_key_down(
    rows: list[tuple[list[str], bool]],
) -> list[tuple[list[str], bool]]:
    """Carry a grouped date onto every row of its group, so each row stands alone.

    The corpus draws a date group two ways, and which one a page gets is a
    layout detail no reader of the page can see:

    * **as a band** — a row containing only the date, then that day's
      transactions with an empty date cell (`nab_dense`, `cba_date_grouped`,
      `nab_classic`);
    * **on the first row** — the date in the first transaction's own date cell,
      with the rest of the group left blank (`Date of Transaction` layouts).

    Both mean "these rows belong to the date above". Carrying only the first
    left the corpus contradicting itself: measured 2026-08-19, 123 transaction
    rows across 7 statements kept a blank date while 329 rows on 27 other
    statements had theirs filled in. `prompt.md` states one rule, so a model
    obeying it was right on one layout family and wrong on the other —
    gemma-4-12B replicated dates onto 97.8% of grouped rows against a truth of
    79.8%, and every over-dated row failed to align.

    This **departs from recording only what the page shows**: the page prints
    the date once. It is the one place the corpus infers rather than
    transcribes, taken deliberately so that every row is self-contained.

    A group header is a row whose first cell has content and whose other cells
    are all empty. A trailing header with nothing beneath it is kept — dropping
    it would lose the only record that the date was on the page at all. A blank
    first cell with no date anywhere above it stays blank: an opening-balance
    row genuinely predates the first group, and there is nothing to carry.

    Args:
        rows: (cells, was_header) per captured row, in order.

    Returns:
        The rows with the group key carried onto every row of its group.
    """
    carried: list[tuple[list[str], bool]] = []
    pending: tuple[list[str], bool] | None = None
    pending_used = False
    last_key = ""

    for cells, was_header in rows:
        if was_header:
            carried.append((cells, was_header))
            continue

        is_group_header = (
            bool(cells) and bool(cells[0].strip()) and not any(cell.strip() for cell in cells[1:])
        )
        if is_group_header:
            # A header that headed nothing is kept rather than lost: it is the
            # only record that the date was on the page.
            if pending is not None and not pending_used:
                carried.append(pending)
            pending, pending_used = (cells, was_header), False
            last_key = cells[0]
            continue

        if cells and not cells[0].strip():
            if last_key.strip():
                cells = [last_key, *cells[1:]]
                if pending is not None:
                    pending_used = True
        elif cells:
            last_key = cells[0]
        carried.append((cells, was_header))

    if pending is not None and not pending_used:
        carried.append(pending)
    return carried


class RowWidthError(ValueError):
    """Raised when a row carries more cells than its table declares columns.

    A plain `ValueError` rather than a module error type: padding is shared by
    every table projection, so this stays a caller-agnostic signal each caller
    wraps into its own four-element diagnostic rather than a second
    implementation of padding.
    """


def pad_row(cells: list[str], width: int, blank: str) -> list[str]:
    """Pad one row's cells to a table's column width.

    The single implementation of padding, used by every table projection so
    they cannot compute it two different ways and drift apart.

    Args:
        cells: The row's cell texts, in column order.
        width: The table's column count, from `table_open`'s `columns`.
        blank: The token a short row's missing cells are filled with.

    Returns:
        `cells` padded on the right to exactly `width` entries.

    Raises:
        RowWidthError: `cells` has more entries than `width`. Truncating
            silently would drop a real cell rather than pad a missing one —
            the same class of hole an earlier fix round closed for an
            unclosed row.
    """
    if len(cells) > width:
        raise RowWidthError(
            f"a row has {len(cells)} cell(s) but the table declares {width} column(s): {cells!r}"
        )
    return list(cells) + [blank] * (width - len(cells))


class TableBuilder:
    """Accumulates one page's table events and renders each table at its close.

    The single implementation of the table walk. `serialise.serialise` and
    `table_html` both drive it with the same events in the same order, so the
    transcript's table and the exported `tables/{stem}.html` are the same bytes
    by construction — not by two walks being kept in step by hand, which is what
    this class replaces.
    """

    #: Event kinds this builder consumes. A caller checks membership to decide
    #: whether to delegate, so the set is part of the interface.
    KINDS: frozenset[str] = frozenset(
        {"table_open", "row_open", "cell", "cell_sub_line", "row_close", "table_close"}
    )

    def __init__(self, policy: dict) -> None:
        """Args:
        policy: The validated serialisation policy.
        """
        self._policy = policy
        self._rows: list[tuple[list[str], bool]] = []
        self._row: list[str] = []
        self._keys: list[str] = []
        self._is_header = False
        self._columns: list[str] = []
        self._open_seq: int | None = None
        self._open_row_seq: int | None = None

    def feed(self, event: dict) -> str | None:
        """Consume one event.

        Args:
            event: One event dict from the captured stream.

        Returns:
            The table's HTML when `event` is a `table_close`, else None.

        Raises:
            TableHtmlError: The stream is unbalanced, or a row carries more
                cells than the table declares columns.
        """
        kind = event["kind"]
        meta = event.get("meta") or {}
        if kind == "table_open":
            self._rows, self._open_seq = [], int(event["seq"])
            self._columns = list(meta.get("columns") or [])
        elif kind == "row_open":
            self._row, self._keys, self._is_header = [], [], False
            self._open_row_seq = int(event["seq"])
        elif kind == "cell":
            self._row.append(
                _join_cell(str(event["text"] or self._policy["empty_cell_token"]), self._policy)
            )
            self._keys.append(str(meta.get("column_key", "")))
            self._is_header = self._is_header or bool(meta.get("header"))
        elif kind == "cell_sub_line":
            self._fold_sub_line(event, meta)
        elif kind == "row_close":
            if self._open_row_seq is None:
                raise _err("a row_close has no matching row_open.", seq=int(event["seq"]))
            self._rows.append((self._row, self._is_header))
            self._row, self._keys, self._is_header = [], [], False
            self._open_row_seq = None
        elif kind == "table_close":
            if self._open_row_seq is not None:
                raise _err("a row_open has no matching row_close.", seq=self._open_row_seq)
            # A table that captured no rows drew no rows of ink. Rendering an
            # empty <table> would ask a model to transcribe markup for content
            # the page does not show, so both projections skip it — the rule
            # `serialise` has always applied, now applied to `tables` too.
            html = _render(self._rows, self._columns, self._policy) if self._rows else None
            self._rows, self._open_seq, self._columns = [], None, []
            return html
        return None

    def finish(self) -> None:
        """Assert the stream ended balanced.

        Raises:
            TableHtmlError: A `table_open` or `row_open` was never closed.
        """
        if self._open_seq is not None:
            raise _err("a table_open has no matching table_close.", seq=self._open_seq)
        if self._open_row_seq is not None:
            raise _err("a row_open has no matching row_close.", seq=self._open_row_seq)

    def _fold_sub_line(self, event: dict, meta: dict) -> None:
        """Fold a sub-line into the cell it belongs to, found by column key.

        Stripped before the fold, not after: the decoration run is trailing on
        the sub-line, and folding first would bury it mid-cell where the pattern
        no longer matches.
        """
        key = str(meta.get("column_key", ""))
        content = strip_decoration_run(
            str(event["text"] or ""),
            glyphs=self._policy["decoration_glyphs"],
            min_run=self._policy["decoration_min_run"],
        )
        join = self._policy["cell_sub_line_join"]
        if key in self._keys:
            position = self._keys.index(key)
            self._row[position] = f"{self._row[position]}{join}{content}"
        elif self._rows and key in self._columns:
            # Defensive only: `_draw_sub_lines` (primitives_table.py:870) always
            # emits cell_sub_line before that row's row_close, so this branch is
            # unreachable on the real corpus today.
            position = self._columns.index(key)
            cells, _header = self._rows[-1]
            if position < len(cells):
                cells[position] = f"{cells[position]}{join}{content}"


def table_html(events: list[dict], policy: dict) -> list[str]:
    """Render every table in an event stream as an HTML table.

    Args:
        events: One page's events, as stored in `events.jsonl`.
        policy: The validated serialisation policy (§`load_serialisation_policy`)
            — the same one `serialise.serialise` used to produce this page's
            Markdown transcript, so the two projections state one ground truth.

    Returns:
        One HTML string per table, in walk order. Empty when the page has none.
        Rendered by the same `TableBuilder` the transcript uses, so the two
        projections cannot disagree.

    Raises:
        TableHtmlError: A table is not closed, a row is not closed, or a row
            carries more cells than the table declares columns.
    """
    builder = TableBuilder(policy)
    tables = [html for event in events if (html := builder.feed(event)) is not None]
    builder.finish()
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
