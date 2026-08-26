"""Cell-aligned scoring over pipe tables.

`normalise()` under `strip_markdown` substitutes every pipe with a space, so
the existing NORMALISED metric cannot see which cell a value landed in: a debit
row's `$590.52 |` and a credit row's `| $590.52` reduce to the same token
stream. A probe against `CASE002_bank_statements.md` scored 27 reversed
transactions — a sign error on every row — as a perfect transcription, exactly
0.000000, while a one-character typo scored 0.000401. This module is the metric
that sees the difference.

It also scores an instruction that is already shipped and currently unscored.
`config/prompt.md` tells the model to "leave it blank in the table rather than
dropping it or shifting the other cells across"; nothing measured whether that
happened.

Pure by contract: data in, data out. No filesystem, no CLI, and no import of
`generators` — the interface between generation and scoring is a directory
(`tests/scoring/test_boundaries.py`).
"""

import re

from rapidfuzz.distance import Levenshtein

from scoring.normalise import normalise

_SEPARATOR_CELL = re.compile(r"^[\s:\-]*$")

# A cell separator that cannot occur inside a cell, so two different rows cannot
# collide on one signature by splitting their text differently.
_SIGNATURE_JOIN = "\x1f"


def _split_row(stripped: str) -> list[str]:
    """Split one pipe-table line into stripped cell texts.

    Args:
        stripped: The line, already stripped of surrounding whitespace, known
            to begin with `|`.

    Returns:
        The row's cells. One leading and one trailing pipe are removed first,
        so `| a | b |` yields two cells rather than four.
    """
    body = stripped
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|"):
        body = body[:-1]
    return [cell.strip() for cell in body.split("|")]


def _is_separator(cells: list[str]) -> bool:
    """Report whether a row is a pipe table's header separator.

    A separator carries no content: every cell is dashes, colons and space, and
    at least one cell contains a dash. The dash requirement is what keeps an
    empty header row (`|  |  |`) — which `config/serialisation.yml` emits under
    `headerless_table: empty_header_row`, and which `config/prompt.md`
    instructs the model to reproduce — from being discarded as decoration.

    Args:
        cells: The row's cells.

    Returns:
        True when the row is a separator.
    """
    return any("-" in cell for cell in cells) and all(_SEPARATOR_CELL.match(cell) for cell in cells)


def parse_tables(text: str) -> list[list[list[str]]]:
    """Extract every pipe table from a page of Markdown.

    A table line is one whose stripped form begins with `|`; a table is a
    maximal run of consecutive table lines. Requiring the leading pipe keeps a
    prose line that merely contains a pipe from opening a table.

    Args:
        text: A reference transcript or a model prediction.

    Returns:
        One entry per table, in document order; each a list of rows, each row a
        list of cell strings, with separator rows removed.
    """
    tables: list[list[list[str]]] = []
    current: list[list[str]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("|"):
            cells = _split_row(stripped)
            if not _is_separator(cells):
                current.append(cells)
        elif current:
            tables.append(current)
            current = []
    if current:
        tables.append(current)
    return tables


def _cell_form(cell: str, policy: dict) -> str:
    """Return the text form cell equality uses, under the configured policy.

    Under `normalised`, a cell of only dashes and/or colons (e.g. `-`, `--`,
    `:`, `- -`) normalises to the empty string: `normalise()`'s separator-row
    rule is anchored per line, and a lone cell is a whole "line" to it. So a
    model that writes `-` into a blank reference cell scores that cell correct
    under `normalised`. `strict` does not forgive this — it compares raw text.

    Args:
        cell: One cell's text.
        policy: The whole validated policy mapping.

    Returns:
        The cell normalised, or the cell unchanged under `strict`.
    """
    if policy["tables"]["cell_comparison"] == "strict":
        return cell
    return normalise(cell, policy["normalisation"])


def row_signature(cells: list[str], policy: dict) -> str:
    """Reduce a row to the string alignment compares.

    Args:
        cells: The row's cells.
        policy: The whole validated policy mapping.

    Returns:
        The cells in their comparison form, joined by a separator that cannot
        appear inside a cell.
    """
    return _SIGNATURE_JOIN.join(_cell_form(cell, policy) for cell in cells)


def align_rows(
    ref_rows: list[list[str]], pred_rows: list[list[str]], policy: dict
) -> tuple[list[tuple[list[str], list[str]]], int, int]:
    """Pair reference rows with prediction rows by content.

    Sequence alignment rather than position: a model that drops one row should
    be charged one missing row, not charged again for every row after it, which
    is what strict positional comparison would do.

    Args:
        ref_rows: The reference table's rows.
        pred_rows: The prediction table's rows.
        policy: The whole validated policy mapping.

    Returns:
        `(pairs, rows_missing, rows_spurious)`. Pairs are `(ref_row, pred_row)`
        in reference order. Missing counts reference rows with no counterpart;
        spurious counts prediction rows with none.
    """
    ref_signatures = [row_signature(row, policy) for row in ref_rows]
    pred_signatures = [row_signature(row, policy) for row in pred_rows]

    pairs: list[tuple[list[str], list[str]]] = []
    missing = 0
    spurious = 0
    for op in Levenshtein.opcodes(ref_signatures, pred_signatures):
        if op.tag in ("equal", "replace"):
            ref_slice = ref_rows[op.src_start : op.src_end]
            pred_slice = pred_rows[op.dest_start : op.dest_end]
            paired = min(len(ref_slice), len(pred_slice))
            pairs.extend(zip(ref_slice[:paired], pred_slice[:paired], strict=True))
            missing += len(ref_slice) - paired
            spurious += len(pred_slice) - paired
        elif op.tag == "delete":
            missing += op.src_end - op.src_start
        elif op.tag == "insert":
            spurious += op.dest_end - op.dest_start
    return pairs, missing, spurious


def compare_row(ref_cells: list[str], pred_cells: list[str], policy: dict) -> tuple[int, int, int]:
    """Compare one matched row's cells position by position.

    Empty reference cells are compared like any other. That is the crux of the
    metric: the misfile it exists to catch empties one cell and fills its
    neighbour, so skipping empties would score the failure as a perfect row.

    A prediction row shorter than the reference is padded with empty cells; a
    longer one has its surplus ignored for correctness, though the surplus is
    still searched when deciding whether a value was merely misplaced.

    Args:
        ref_cells: The reference row's cells.
        pred_cells: The paired prediction row's cells.
        policy: The whole validated policy mapping.

    Returns:
        `(compared, correct, misplaced)`. `compared` is the reference row's
        width. `misplaced` counts incorrect cells whose non-empty reference
        value appears at some other position in the same prediction row, and is
        a diagnostic subset of the incorrect cells rather than a separate
        bucket.

        When a reference row repeats the same non-empty value at two
        positions (e.g. quantity 1 makes unit price equal amount), a wrong
        cell can be flagged `misplaced` even though nothing actually moved —
        the lookup finds the coincidental duplicate rather than a genuine
        relocation. Reachable on real data (18 of 2074 corpus reference rows
        repeat a value); affects only this diagnostic count, never
        `table_cell_error_rate`.
    """
    width = len(ref_cells)
    reference = [_cell_form(cell, policy) for cell in ref_cells]
    predicted = [_cell_form(cell, policy) for cell in pred_cells]
    aligned = predicted[:width] + [""] * max(0, width - len(predicted))

    correct = 0
    misplaced = 0
    for index in range(width):
        if reference[index] == aligned[index]:
            correct += 1
            continue
        value = reference[index]
        if value and any(other != index and predicted[other] == value for other in range(len(predicted))):
            misplaced += 1
    return width, correct, misplaced


def score_tables(reference: str, prediction: str, policy: dict) -> dict:
    """Score a page's tables cell by cell.

    Never raises on prediction content. A model emitting nonsense is a result to
    record, not an error to crash on; fail-fast in this package applies to
    configuration, which `scoring.policy` validates before any page is scored.

    Args:
        reference: The corpus transcript.
        prediction: The model's output.
        policy: The whole validated policy mapping.

    Returns:
        The eight table fields of a scored row. `table_cell_error_rate` is None
        when the reference holds no table cells to compare.
    """
    ref_tables = parse_tables(reference)
    pred_tables = parse_tables(prediction)

    compared = sum(len(row) for table in ref_tables for row in table)
    correct = 0
    misplaced = 0
    missing = 0
    spurious = 0

    for index in range(max(len(ref_tables), len(pred_tables))):
        ref_table = ref_tables[index] if index < len(ref_tables) else []
        pred_table = pred_tables[index] if index < len(pred_tables) else []
        pairs, table_missing, table_spurious = align_rows(ref_table, pred_table, policy)
        missing += table_missing
        spurious += table_spurious
        for ref_row, pred_row in pairs:
            _, row_correct, row_misplaced = compare_row(ref_row, pred_row, policy)
            correct += row_correct
            misplaced += row_misplaced

    return {
        "table_cell_error_rate": None if not compared else (compared - correct) / compared,
        "table_cells_compared": compared,
        "table_cells_correct": correct,
        "table_cells_misplaced": misplaced,
        "table_rows_missing": missing,
        "table_rows_spurious": spurious,
        "table_count_ref": len(ref_tables),
        "table_count_pred": len(pred_tables),
    }
