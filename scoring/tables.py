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
