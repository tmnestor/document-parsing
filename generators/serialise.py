"""Events plus policy to Markdown.

A pure function of the event stream and `config/serialisation.yml` — it imports
no PIL and renders nothing. That split is deliberate (design §6): the convention
is the risky, iterate-on-it part of this design, so it can change and every
transcript re-emit in seconds without re-rendering an image.

It takes plain dicts rather than `Event` objects so it can run straight off a
stored `events.jsonl` without importing the recorder.

**It never normalises.** No whitespace collapsing, no case folding, no Unicode
folding, no dash or quote substitution. Design §5 puts all of that in the
scoring tool so that scoring policy can change without regenerating the corpus;
baking any of it in here would freeze a policy into the data.
"""

from pathlib import Path

import yaml

REQUIRED_POLICY_KEYS: tuple[str, ...] = (
    "title_style",
    "pair_separator",
    "pair_strip_trailing_colon",
    "table_style",
    "empty_cell_token",
    "cell_sub_line_join",
    "cell_newline_join",
    "split_order",
    "headerless_table",
    "carry_group_key",
    "block_separator",
    "emphasis",
)

# Enum-valued keys, each mapped to every value this serialiser implements. A
# value outside these is a configuration error, not a silently-ignored setting.
_ALLOWED: dict[str, tuple[str, ...]] = {
    "title_style": ("atx_h1",),
    "table_style": ("pipe_with_header_rule",),
    "split_order": ("column_major",),
    "carry_group_key": ("down", "none"),
    "headerless_table": ("empty_header_row",),
    "emphasis": ("none",),
}

_EXAMPLES: dict[str, str] = {
    "title_style": "atx_h1",
    "pair_separator": '": "',
    "pair_strip_trailing_colon": "true",
    "table_style": "pipe_with_header_rule",
    "empty_cell_token": '""',
    "cell_sub_line_join": '" "',
    "cell_newline_join": '" "',
    "split_order": "column_major",
    "headerless_table": "empty_header_row",
    "carry_group_key": "down",
    "block_separator": '"\\n\\n"',
    "emphasis": "none",
}

# Structural markers: they shape the stream but put nothing in the transcript.
_STRUCTURE = frozenset(
    {"panel_open", "panel_close", "split_open", "split_close", "column_open", "column_close"}
)


class SerialisationError(RuntimeError):
    """Raised when the serialisation policy or the event stream is unusable."""


def _err(what: str, *, path: Path | str, key: str, expected: str, recover: str) -> SerialisationError:
    """Build a four-element fail-fast diagnostic."""
    return SerialisationError(
        "Invalid serialisation policy.\n"
        f"  What:     {what}\n"
        f"  Where:    {path} -> {key}\n"
        f"  Expected: {expected}\n"
        f"  Recover:  {recover}"
    )


def load_serialisation_policy(path: Path) -> dict:
    """Load and validate the serialisation convention.

    Every key is required. Omitting one is an error, never a silent default,
    including the keys whose value is a no-op (design §4.4) — so that reading
    the file alone answers what a transcript looks like.

    Args:
        path: Path to `serialisation.yml`.

    Returns:
        The validated policy mapping.

    Raises:
        SerialisationError: The file is missing, unparseable, missing a required
            key, or gives an enum key a value this serialiser does not implement.
    """
    resolved = path.resolve()
    if not path.exists():
        raise _err(
            f"{path} does not exist.",
            path=resolved,
            key="(whole file)",
            expected="a YAML mapping declaring every key of "
            f"{list(REQUIRED_POLICY_KEYS)}, e.g.\n              title_style: atx_h1",
            recover="create config/serialisation.yml (see the copy shipped with any exported corpus).",
        )

    try:
        policy = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as err:
        raise _err(
            f"the file is not valid YAML: {err}",
            path=resolved,
            key="(whole file)",
            expected="parseable YAML, e.g.\n              emphasis: none",
            recover="fix the syntax error at the line named above.",
        ) from err

    if not isinstance(policy, dict):
        raise _err(
            f"expected a mapping, got {type(policy).__name__}.",
            path=resolved,
            key="(document root)",
            expected="a top-level mapping of policy keys, e.g.\n              split_order: column_major",
            recover="wrap the settings in a top-level mapping.",
        )

    for key in REQUIRED_POLICY_KEYS:
        if key not in policy:
            raise _err(
                f"'{key}' is not declared.",
                path=resolved,
                key=key,
                expected=f"every key of {list(REQUIRED_POLICY_KEYS)} present — none has a "
                f"Python default, including no-op values, e.g.\n              {key}: "
                f"{_EXAMPLES[key]}",
                recover=f"add '{key}:' to {path}.",
            )

    for key, allowed in _ALLOWED.items():
        if policy[key] not in allowed:
            raise _err(
                f"'{key}' is {policy[key]!r}, which this serialiser does not implement.",
                path=resolved,
                key=key,
                expected=f"one of {list(allowed)}, e.g.\n              {key}: {allowed[0]}",
                recover=f"set '{key}:' to a supported value, or implement it in generators/serialise.py.",
            )

    return policy


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


def _render_table(columns: list[str], rows: list[tuple[list[str], bool]], policy: dict) -> str:
    """Render captured rows as a pipe table with a header separator row.

    A pipe table's first row is its header by definition, so a table that drew
    no header on the page (several receipt layouts set `header: false`) must not
    have its first line item promoted into that slot — a parser would read the
    goods as column names. `headerless_table` decides what happens instead;
    `empty_header_row` keeps the table parseable without inventing any text that
    is not on the page.

    Args:
        columns: The table's column keys, in order.
        rows: (cells, was_header) per captured row.
        policy: The validated serialisation policy.

    Returns:
        The pipe table as one block.
    """
    if policy["carry_group_key"] == "down":
        rows = carry_group_key_down(rows)

    width = len(columns)
    blank = policy["empty_cell_token"]
    separator = "| " + " | ".join(["---"] * width) + " |"

    def render(cells: list[str]) -> str:
        padded = list(cells) + [blank] * (width - len(cells))
        return "| " + " | ".join(padded[:width]) + " |"

    lines: list[str] = []
    if not rows[0][1]:
        lines.append(render([blank] * width))
        lines.append(separator)
        lines.extend(render(cells) for cells, _ in rows)
        return "\n".join(lines)

    for index, (cells, _) in enumerate(rows):
        lines.append(render(cells))
        if index == 0:
            lines.append(separator)
    return "\n".join(lines)


def serialise(events: list[dict], policy: dict) -> str:
    """Turn one document's event stream into its Markdown transcript.

    Args:
        events: The document's events, in walk order, as plain dicts.
        policy: The validated policy from `load_serialisation_policy`.

    Returns:
        The transcript. Never normalised — exactly what was drawn, arranged
        under the policy's conventions.

    Raises:
        SerialisationError: An event kind has no serialisation rule. A new kind
            must fail loudly rather than vanish from every transcript.
    """
    blocks: list[str] = []

    columns: list[str] = []
    table_rows: list[tuple[list[str], bool]] = []
    row: list[str] = []
    row_keys: list[str] = []
    row_is_header = False
    in_table = False

    for event in events:
        kind = event["kind"]
        text = event["text"]
        meta = event.get("meta", {})

        if kind in _STRUCTURE:
            # Structure only. Column order is already `column_major` in the
            # stream because `draw_split` walks columns in DSL order, so there
            # is nothing to reorder here — see the policy's `split_order`.
            continue

        if kind == "title":
            blocks.append(f"# {text}")
        elif kind == "line":
            blocks.append(str(text))
        elif kind == "pair":
            label = str(meta["label"])
            if policy["pair_strip_trailing_colon"]:
                label = label.rstrip().removesuffix(":").rstrip()
            blocks.append(f"{label}{policy['pair_separator']}{meta['value']}")
        elif kind == "table_open":
            in_table = True
            columns = [str(key) for key in meta["columns"]]
            table_rows = []
        elif kind == "row_open":
            row, row_keys, row_is_header = [], [], False
        elif kind == "cell":
            row.append(_join_cell(str(text or policy["empty_cell_token"]), policy))
            row_keys.append(str(meta.get("column_key", "")))
            row_is_header = row_is_header or bool(meta.get("header"))
        elif kind == "cell_sub_line":
            # Folded into the cell it belongs to, found by column key so a
            # sub-line under column 2 cannot land on column 0.
            key = str(meta.get("column_key", ""))
            if key in row_keys:
                position = row_keys.index(key)
                row[position] = f"{row[position]}{policy['cell_sub_line_join']}{text}"
        elif kind == "row_close":
            table_rows.append((row, row_is_header))
            row, row_keys, row_is_header = [], [], False
        elif kind == "table_close":
            if table_rows:
                blocks.append(_render_table(columns, table_rows, policy))
            in_table = False
            columns, table_rows = [], []
        else:
            raise SerialisationError(
                "Unknown event kind.\n"
                f"  What:     no serialisation rule for event kind '{kind}' "
                f"(seq {event.get('seq')}).\n"
                "  Where:    generators/serialise.py -> serialise()\n"
                "  Expected: a branch for every kind a primitive emits, e.g. "
                '`elif kind == "line": blocks.append(text)`.\n'
                "  Recover:  add a rule for this kind, and a matching row to the "
                "event table in the design doc's §4.3."
            )

    if in_table:
        raise SerialisationError(
            "Unbalanced event stream.\n"
            "  What:     a table_open was never closed by a table_close.\n"
            "  Where:    the document's captured event stream\n"
            "  Expected: every table_open matched by a table_close, which "
            "`draw_table` emits on every path.\n"
            "  Recover:  regenerate this document; if it recurs, a table "
            "primitive is returning early without closing its events."
        )

    return policy["block_separator"].join(blocks)
