"""Cell-aligned scoring over the HTML tables the corpus emits.

A character metric answers "do these pages differ?"; this one answers "what
moved, and where?".

The difference used to be detection. Under the retired pipe format `normalise()`
substituted every `|` with a space, so the NORMALISED metric could not see which
cell a value landed in: a probe against `CASE002_bank_statements.md` that
reversed 27 transactions — a sign error on every row — scored exactly 0.000000,
while a one-character typo scored 0.000401. The character metric ranked a
whole-page failure below a typo. `table_style: html` ended that inversion,
because `<td>` boundaries survive normalisation: the same perturbation now
scores 0.086076 against the typo's 0.000230.

What remains, and what this module is for, is DIAGNOSIS. On that same probe it
reports 27 cells misplaced, no rows missing and no rows spurious — a column
swap, named as one — where a CER reports a number that could equally be 27
misread amounts. Both figures are pinned against the real corpus in
`tests/scoring/test_tables_corpus.py`.

It also scores an instruction that is already shipped and currently unscored.
`config/prompt.md` tells the model to "leave it blank in the table rather than
dropping it or shifting the other cells across"; nothing measured whether that
happened.

Parsing lives in `scoring.table_html`, which turns a page's `<table>` elements
into rectangular grids, replicating a spanning cell into every position it
covers. This module is the scoring built on that: signature, alignment, cell
comparison.

Pure by contract: data in, data out. No filesystem, no CLI, and no import of
`generators` — the interface between generation and scoring is a directory
(`tests/scoring/test_boundaries.py`).
"""

from rapidfuzz.distance import Levenshtein

from scoring.normalise import normalise
from scoring.table_html import parse_html_tables

# A cell separator that cannot occur inside a cell, so two different rows cannot
# collide on one signature by splitting their text differently.
_SIGNATURE_JOIN = "\x1f"


def parse_tables(text: str) -> list[list[list[str]]]:
    """Extract every table from a page as a grid of cell text.

    HTML only. `config/serialisation.yml` sets `table_style: html` and
    `generators/serialise.py` admits no other value, so a prediction written as
    a pipe table yields no tables at all — it reports `table_count_pred` of 0,
    which reads as the format failure it is rather than being scored as partial
    cells.

    Args:
        text: A reference transcript or a model prediction.

    Returns:
        One entry per table, in document order; each a list of rows, each row a
        list of cell strings. Every row of one table shares a width, spanning
        cells having been replicated across the positions they cover.
    """
    return parse_html_tables(text)


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
