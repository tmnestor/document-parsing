# Cell-Aligned Table Metric Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Score whether a model put table cells in the right place, so that
correct values filed in the wrong column are penalised instead of being
invisible.

**Architecture:** A new pure module `scoring/tables.py` parses pipe tables out
of both the reference transcript and the prediction with one parser, aligns
rows by content signature so a dropped row does not cascade, then compares
cells position-by-position within matched rows. Every reference cell counts
toward the denominator, including cells of rows the prediction dropped.
`score_page` merges the resulting eight fields into each scored row.

**Tech Stack:** Python 3.12, `rapidfuzz` (already a `docparse-score`
dependency), PyYAML, pytest.

**Spec:** `docs/superpowers/specs/2026-08-26-cell-aligned-table-metric-design.md`

## Global Constraints

- Conda environment is **`docparse-score`**, not `docparse` and not the global
  `du`. Run everything as `conda run -n docparse-score <command>`.
- Run every command **from the repository root**.
- **`scoring/` must not import `generators/`.** `tests/scoring/test_boundaries.py`
  scans `scoring/` with an AST walk and fails on any such import. Tests are not
  scanned and may import `generators`.
- **No new dependency.** `rapidfuzz` is present; `apted` is not required.
- `scoring/tables.py` is **pure**: data in, data out, no filesystem access, no
  CLI, no I/O.
- Line length maximum **108**. Python 3.12 typing (`X | Y`, never `Union`). No
  `from __future__ import annotations`. No `TYPE_CHECKING` guards for types used
  in runtime signatures. `pathlib.Path` for paths. Google-style docstrings.
- **Configuration fails fast with four elements** (What / Where / Expected /
  Recover) via `scoring.errors.diagnostic`. **Prediction content never raises** —
  a model emitting nonsense is a result to record, not an error to crash on.
- Every config key is **required**; no Python default may shadow a YAML value.
- B904: in `except` blocks always `raise ... from err` or `from None`.
- **`tests/` is gitignored.** Write and run tests; never `git add` them.
- **No `--no-verify`. No Claude attribution in commit messages.** Commit format:
  gitmoji + conventional type, e.g. `✨ feat: ...`.
- **Never write under `../document-parsing-data/`.** Tests read the corpus; they
  must not write to it.

---

## File Structure

| File | Responsibility |
|---|---|
| `scoring/tables.py` (new) | Parse, align, compare. The whole metric, pure. |
| `config/scoring.yml` (modify) | The `tables:` section — the one operator choice. |
| `scoring/policy.py` (modify) | Validate that section with four-element diagnostics. |
| `scoring/score.py` (modify) | `score_page` merges the eight fields. |
| `scoring/report.py` (modify) | `_METRICS` gains `table_cell_error_rate`. |
| `tests/scoring/test_tables.py` (new) | Unit tests for the module. |
| `tests/scoring/test_tables_corpus.py` (new) | The acceptance gate against the real 165 pages. |

---

## Task 1: Parse pipe tables

**Files:**
- Create: `scoring/tables.py`
- Test: `tests/scoring/test_tables.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `parse_tables(text: str) -> list[list[list[str]]]` — a page's
  tables, each table a list of rows, each row a list of cell strings, with
  separator rows removed.

- [ ] **Step 1: Write the failing tests**

Create `tests/scoring/test_tables.py`:

```python
"""Parsing, aligning and comparing pipe tables."""

from scoring.tables import parse_tables


def test_a_single_table_parses_to_rows_of_cells():
    text = "Intro line\n\n| Date | Amount |\n| --- | --- |\n| 01/10/2023 | $5.00 |\n"
    assert parse_tables(text) == [[["Date", "Amount"], ["01/10/2023", "$5.00"]]]


def test_the_separator_row_is_discarded():
    text = "| A | B |\n| --- | --- |\n| 1 | 2 |\n"
    rows = parse_tables(text)[0]
    assert ["---", "---"] not in rows
    assert rows == [["A", "B"], ["1", "2"]]


def test_an_alignment_separator_row_is_also_discarded():
    text = "| A | B |\n| :--- | ---: |\n| 1 | 2 |\n"
    assert parse_tables(text)[0] == [["A", "B"], ["1", "2"]]


def test_an_empty_header_row_is_kept():
    """`headerless_table: empty_header_row` emits it and prompt.md instructs it."""
    text = "|  |  |\n| --- | --- |\n| Potting Mix 25L | 9.30 |\n"
    assert parse_tables(text)[0] == [["", ""], ["Potting Mix 25L", "9.30"]]


def test_two_tables_separated_by_prose_parse_as_two():
    text = "| A |\n| --- |\n| 1 |\n\nsome prose\n\n| B |\n| --- |\n| 2 |\n"
    assert parse_tables(text) == [[["A"], ["1"]], [["B"], ["2"]]]


def test_text_with_no_table_yields_no_tables():
    assert parse_tables("Just a paragraph.\nAnd another.\n") == []


def test_a_line_not_starting_with_a_pipe_is_not_a_table_line():
    """A prose line containing a pipe must not open a table."""
    assert parse_tables("costs $5 | maybe more\n") == []


def test_cells_are_stripped_of_surrounding_space():
    assert parse_tables("|   A   |   B   |\n")[0] == [["A", "B"]]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `conda run -n docparse-score python -m pytest tests/scoring/test_tables.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scoring.tables'`

- [ ] **Step 3: Write the implementation**

Create `scoring/tables.py`:

```python
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

_SEPARATOR_CELL = re.compile(r"^[\s:\-]*$")


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
    return any("-" in cell for cell in cells) and all(
        _SEPARATOR_CELL.match(cell) for cell in cells
    )


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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `conda run -n docparse-score python -m pytest tests/scoring/test_tables.py -v`
Expected: PASS, 8 tests.

- [ ] **Step 5: Lint, type-check and commit**

```bash
conda run -n docparse-score ruff check --fix --ignore ARG001,ARG002,F841 .
conda run -n docparse-score ruff format .
conda run -n docparse-score mypy scoring --ignore-missing-imports
git add scoring/tables.py
git commit -m "✨ feat: parse pipe tables out of a page of Markdown"
```

---

## Task 2: The `tables` policy section

**Files:**
- Modify: `config/scoring.yml`
- Modify: `scoring/policy.py` (`REQUIRED_POLICY_KEYS`, `_EXAMPLES`, `_validate_values`)
- Test: `tests/scoring/test_policy.py`

**Interfaces:**
- Consumes: `scoring.errors.diagnostic(what, *, path, key, expected, recover)`.
- Produces: a validated `policy["tables"]["cell_comparison"]`, one of
  `"normalised"` or `"strict"`, for Tasks 3, 4 and 5.

- [ ] **Step 1: Write the failing tests**

Append to `tests/scoring/test_policy.py`:

```python
def test_a_missing_tables_section_is_a_diagnostic_error(tmp_path):
    from scoring.errors import ScoringError
    from scoring.policy import load_scoring_policy
    from tests.helpers import assert_diagnostic_error

    source = Path("config/scoring.yml").read_text(encoding="utf-8")
    policy = yaml.safe_load(source)
    del policy["tables"]
    path = tmp_path / "scoring.yml"
    path.write_text(yaml.safe_dump(policy), encoding="utf-8")

    with pytest.raises(ScoringError) as excinfo:
        load_scoring_policy(path)
    assert_diagnostic_error(str(excinfo.value), mentions=("tables.cell_comparison", str(path.resolve())))


def test_an_unknown_cell_comparison_is_a_diagnostic_error(tmp_path):
    from scoring.errors import ScoringError
    from scoring.policy import load_scoring_policy
    from tests.helpers import assert_diagnostic_error

    source = Path("config/scoring.yml").read_text(encoding="utf-8")
    policy = yaml.safe_load(source)
    policy["tables"]["cell_comparison"] = "fuzzy"
    path = tmp_path / "scoring.yml"
    path.write_text(yaml.safe_dump(policy), encoding="utf-8")

    with pytest.raises(ScoringError) as excinfo:
        load_scoring_policy(path)
    message = str(excinfo.value)
    assert_diagnostic_error(message, mentions=("tables.cell_comparison", "normalised", "strict"))


def test_the_shipped_policy_declares_the_tables_section():
    from scoring.policy import load_scoring_policy

    policy = load_scoring_policy(Path("config/scoring.yml"))
    assert policy["tables"]["cell_comparison"] in ("normalised", "strict")
```

If `tests/scoring/test_policy.py` does not already import `yaml`, `pytest` and
`Path`, add those imports at the top of the file.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `conda run -n docparse-score python -m pytest tests/scoring/test_policy.py -v -k tables`
Expected: FAIL — `KeyError: 'tables'` in the test's own setup, because the
shipped config has no such section yet.

- [ ] **Step 3: Add the config section**

Append to `config/scoring.yml`:

```yaml
tables:
  # Which text form cell equality uses, for the cell-aligned table metric.
  #
  # `normalised` runs each cell through the normalisation section above before
  # comparing, so a curly quote or an en dash inside a cell is not scored as a
  # placement error -- placement is what this metric measures, and typography is
  # already forgiven by the normalised edit distance. `strict` compares raw cell
  # text, scoring typography and placement together.
  #
  # Allowed: normalised | strict.
  cell_comparison: normalised
```

- [ ] **Step 4: Add the validation**

In `scoring/policy.py`, append to `REQUIRED_POLICY_KEYS`:

```python
    "tables.cell_comparison",
```

Add to `_EXAMPLES`:

```python
    "tables.cell_comparison": "normalised",
```

Add the allowed-value tuple beside `_UNICODE_FORMS`:

```python
_CELL_COMPARISONS = ("normalised", "strict")
```

Append to the end of `_validate_values`:

```python
    comparison = policy["tables"]["cell_comparison"]
    if comparison not in _CELL_COMPARISONS:
        raise diagnostic(
            f"'cell_comparison' is {comparison!r}, which is not a comparison form this "
            "scorer implements.",
            path=resolved,
            key="tables.cell_comparison",
            expected=f"one of {list(_CELL_COMPARISONS)}, e.g.\n"
            "              cell_comparison: normalised",
            recover=f"set 'tables.cell_comparison:' in {path} to one of those forms.",
        )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `conda run -n docparse-score python -m pytest tests/scoring/test_policy.py -v`
Expected: PASS, including the three new tests.

- [ ] **Step 6: Lint, type-check and commit**

```bash
conda run -n docparse-score ruff check --fix --ignore ARG001,ARG002,F841 .
conda run -n docparse-score ruff format .
conda run -n docparse-score mypy scoring --ignore-missing-imports
git add config/scoring.yml scoring/policy.py
git commit -m "✨ feat: declare and validate the table metric's comparison form"
```

---

## Task 3: Row signatures and alignment

**Files:**
- Modify: `scoring/tables.py`
- Test: `tests/scoring/test_tables.py`

**Interfaces:**
- Consumes: `parse_tables` from Task 1; `policy["tables"]["cell_comparison"]`
  and `policy["normalisation"]` from Task 2.
- Produces:
  - `row_signature(cells: list[str], policy: dict) -> str`
  - `align_rows(ref_rows: list[list[str]], pred_rows: list[list[str]], policy: dict) -> tuple[list[tuple[list[str], list[str]]], int, int]`
    returning `(matched_pairs, rows_missing, rows_spurious)`.

**Note on why alignment exists:** a dropped row must cost one row, not cascade
into every row after it. Alignment decides *pairing* only — the denominator is
Task 5's concern, and it counts every reference cell including those in dropped
rows.

- [ ] **Step 1: Write the failing tests**

Append to `tests/scoring/test_tables.py`:

```python
import pytest

from scoring.policy import load_scoring_policy
from scoring.tables import align_rows, parse_tables, row_signature


@pytest.fixture
def policy():
    from pathlib import Path

    return load_scoring_policy(Path("config/scoring.yml"))


def test_identical_row_lists_pair_one_for_one(policy):
    rows = [["A", "B"], ["1", "2"], ["3", "4"]]
    pairs, missing, spurious = align_rows(rows, rows, policy)
    assert len(pairs) == 3
    assert (missing, spurious) == (0, 0)


def test_a_dropped_row_is_reported_once_and_does_not_cascade(policy):
    ref = [["h", ""], ["FEE", "200"], ["CHQ", "300"]]
    pred = [["FEE", "200"], ["CHQ", "300"]]
    pairs, missing, spurious = align_rows(ref, pred, policy)
    assert (missing, spurious) == (1, 0)
    assert pairs == [(["FEE", "200"], ["FEE", "200"]), (["CHQ", "300"], ["CHQ", "300"])]


def test_a_spurious_row_is_counted_and_does_not_corrupt_pairing(policy):
    ref = [["FEE", "200"]]
    pred = [["FEE", "200"], ["INVENTED", "999"]]
    pairs, missing, spurious = align_rows(ref, pred, policy)
    assert (missing, spurious) == (0, 1)
    assert pairs == [(["FEE", "200"], ["FEE", "200"])]


def test_a_changed_row_still_pairs_so_its_cells_can_be_compared(policy):
    ref = [["FEE", "200"]]
    pred = [["FEE", "999"]]
    pairs, missing, spurious = align_rows(ref, pred, policy)
    assert (missing, spurious) == (0, 0)
    assert pairs == [(["FEE", "200"], ["FEE", "999"])]


def test_an_empty_prediction_loses_every_reference_row(policy):
    ref = [["A"], ["B"], ["C"]]
    pairs, missing, spurious = align_rows(ref, [], policy)
    assert (pairs, missing, spurious) == ([], 3, 0)


def test_a_signature_folds_typography_under_the_normalised_form(policy):
    assert row_signature(["it's"], policy) == row_signature(["it\u2019s"], policy)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `conda run -n docparse-score python -m pytest tests/scoring/test_tables.py -v -k "align or signature"`
Expected: FAIL — `ImportError: cannot import name 'align_rows' from 'scoring.tables'`

- [ ] **Step 3: Write the implementation**

Add to the imports at the top of `scoring/tables.py`:

```python
from rapidfuzz.distance import Levenshtein

from scoring.normalise import normalise
```

Add beside `_SEPARATOR_CELL`:

```python
# A cell separator that cannot occur inside a cell, so two different rows cannot
# collide on one signature by splitting their text differently.
_SIGNATURE_JOIN = "\x1f"
```

Append to `scoring/tables.py`:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `conda run -n docparse-score python -m pytest tests/scoring/test_tables.py -v`
Expected: PASS, 14 tests.

- [ ] **Step 5: Lint, type-check and commit**

```bash
conda run -n docparse-score ruff check --fix --ignore ARG001,ARG002,F841 .
conda run -n docparse-score ruff format .
conda run -n docparse-score mypy scoring --ignore-missing-imports
git add scoring/tables.py
git commit -m "✨ feat: align table rows by content so a dropped row costs one row"
```

---

## Task 4: Compare cells and name misplacement

**Files:**
- Modify: `scoring/tables.py`
- Test: `tests/scoring/test_tables.py`

**Interfaces:**
- Consumes: `_cell_form` from Task 3.
- Produces: `compare_row(ref_cells: list[str], pred_cells: list[str], policy: dict) -> tuple[int, int, int]`
  returning `(compared, correct, misplaced)`.

**Note on why empty cells are compared:** the misfile this metric exists to
catch empties one cell and fills another. Skipping empty reference cells would
reproduce exactly the blindness being removed.

- [ ] **Step 1: Write the failing tests**

Append to `tests/scoring/test_tables.py`:

```python
from scoring.tables import compare_row


def test_an_identical_row_is_wholly_correct(policy):
    row = ["01/10/2023", "BPAY", "$590.52", "", "$18,214.80 CR"]
    assert compare_row(row, row, policy) == (5, 5, 0)


def test_a_value_moved_to_the_next_column_costs_two_cells_and_is_misplaced(policy):
    """The probe's failure: a debit filed as a credit."""
    ref = ["01/10/2023", "BPAY", "$590.52", "", "$18,214.80 CR"]
    pred = ["01/10/2023", "BPAY", "", "$590.52", "$18,214.80 CR"]
    compared, correct, misplaced = compare_row(ref, pred, policy)
    assert compared == 5
    assert correct == 3
    assert misplaced == 1


def test_an_unread_value_is_incorrect_but_not_misplaced(policy):
    ref = ["01/10/2023", "BPAY", "$590.52"]
    pred = ["01/10/2023", "BPAY", "$999.99"]
    assert compare_row(ref, pred, policy) == (3, 2, 0)


def test_a_short_prediction_row_is_padded_and_the_gap_counted_wrong(policy):
    ref = ["A", "B", "C"]
    pred = ["A", "B"]
    assert compare_row(ref, pred, policy) == (3, 2, 0)


def test_extra_prediction_cells_do_not_change_the_denominator(policy):
    ref = ["A", "B"]
    pred = ["A", "B", "C"]
    assert compare_row(ref, pred, policy) == (2, 2, 0)


def test_an_empty_reference_cell_the_model_filled_is_incorrect(policy):
    ref = ["A", ""]
    pred = ["A", "SOMETHING"]
    assert compare_row(ref, pred, policy) == (2, 1, 0)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `conda run -n docparse-score python -m pytest tests/scoring/test_tables.py -v -k compare_row`
Expected: FAIL — `ImportError: cannot import name 'compare_row' from 'scoring.tables'`

- [ ] **Step 3: Write the implementation**

Append to `scoring/tables.py`:

```python
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
        if value and any(
            other != index and predicted[other] == value for other in range(len(predicted))
        ):
            misplaced += 1
    return width, correct, misplaced
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `conda run -n docparse-score python -m pytest tests/scoring/test_tables.py -v`
Expected: PASS, 20 tests.

- [ ] **Step 5: Lint, type-check and commit**

```bash
conda run -n docparse-score ruff check --fix --ignore ARG001,ARG002,F841 .
conda run -n docparse-score ruff format .
conda run -n docparse-score mypy scoring --ignore-missing-imports
git add scoring/tables.py
git commit -m "✨ feat: compare cells and name a value filed in the wrong column"
```

---

## Task 5: Score a whole page's tables

**Files:**
- Modify: `scoring/tables.py`
- Test: `tests/scoring/test_tables.py`

**Interfaces:**
- Consumes: `parse_tables`, `align_rows`, `compare_row`.
- Produces: `score_tables(reference: str, prediction: str, policy: dict) -> dict`
  with exactly these eight keys: `table_cell_error_rate`,
  `table_cells_compared`, `table_cells_correct`, `table_cells_misplaced`,
  `table_rows_missing`, `table_rows_spurious`, `table_count_ref`,
  `table_count_pred`.

**Note on the denominator:** `table_cells_compared` counts **every reference
cell on the page** — matched rows, dropped rows, and rows of reference tables
the prediction produced no counterpart for. This is what stops a model
improving its score by dropping its hardest rows. Cells in dropped rows can
never be correct, so a dropped row costs exactly its own width.

- [ ] **Step 1: Write the failing tests**

Append to `tests/scoring/test_tables.py`:

```python
from scoring.tables import score_tables

_HEADER = "| Date | Description | Debits | Credits | Balance |\n| --- | --- | --- | --- | --- |\n"
_ROW_A = "| 01/10/2023 | BPAY | $590.52 |  | $18,214.80 CR |\n"
_ROW_B = "| 03/10/2023 | VISA | $245.80 |  | $17,969.00 CR |\n"
_ROW_A_MISFILED = "| 01/10/2023 | BPAY |  | $590.52 | $18,214.80 CR |\n"


def test_a_page_scored_against_itself_is_faultless(policy):
    text = _HEADER + _ROW_A + _ROW_B
    result = score_tables(text, text, policy)
    assert result["table_cell_error_rate"] == 0.0
    assert result["table_rows_missing"] == 0
    assert result["table_rows_spurious"] == 0
    assert result["table_cells_misplaced"] == 0
    assert result["table_count_ref"] == 1
    assert result["table_count_pred"] == 1


def test_a_misfiled_amount_is_penalised_and_named(policy):
    reference = _HEADER + _ROW_A + _ROW_B
    prediction = _HEADER + _ROW_A_MISFILED + _ROW_B
    result = score_tables(reference, prediction, policy)
    assert result["table_cells_compared"] == 15
    assert result["table_cells_correct"] == 13
    assert result["table_cells_misplaced"] == 1
    assert result["table_cell_error_rate"] == pytest.approx(2 / 15)


def test_a_dropped_row_costs_its_own_cells_and_no_more(policy):
    reference = _HEADER + _ROW_A + _ROW_B
    prediction = _HEADER + _ROW_B
    result = score_tables(reference, prediction, policy)
    assert result["table_cells_compared"] == 15
    assert result["table_cells_correct"] == 10
    assert result["table_rows_missing"] == 1
    assert result["table_cell_error_rate"] == pytest.approx(5 / 15)


def test_a_table_dropped_whole_costs_the_same_as_its_rows_dropped_singly(policy):
    reference = _HEADER + _ROW_A + _ROW_B
    whole = score_tables(reference, "No tables here.\n", policy)
    assert whole["table_cells_compared"] == 15
    assert whole["table_cells_correct"] == 0
    assert whole["table_cell_error_rate"] == 1.0
    assert whole["table_rows_missing"] == 3
    assert whole["table_count_pred"] == 0


def test_two_tables_pair_in_document_order(policy):
    reference = "| A |\n| --- |\n| 1 |\n\nprose\n\n| B |\n| --- |\n| 2 |\n"
    prediction = "| A |\n| --- |\n| 1 |\n\nprose\n\n| B |\n| --- |\n| 9 |\n"
    result = score_tables(reference, prediction, policy)
    assert result["table_count_ref"] == 2
    assert result["table_count_pred"] == 2
    assert result["table_cells_compared"] == 4
    assert result["table_cells_correct"] == 3


def test_a_reference_with_no_table_yields_no_error_rate(policy):
    result = score_tables("Just prose.\n", "Just prose.\n", policy)
    assert result["table_cell_error_rate"] is None
    assert result["table_cells_compared"] == 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `conda run -n docparse-score python -m pytest tests/scoring/test_tables.py -v -k score_tables`
Expected: FAIL — `ImportError: cannot import name 'score_tables' from 'scoring.tables'`

- [ ] **Step 3: Write the implementation**

Append to `scoring/tables.py`:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `conda run -n docparse-score python -m pytest tests/scoring/test_tables.py -v`
Expected: PASS, 26 tests.

- [ ] **Step 5: Lint, type-check and commit**

```bash
conda run -n docparse-score ruff check --fix --ignore ARG001,ARG002,F841 .
conda run -n docparse-score ruff format .
conda run -n docparse-score mypy scoring --ignore-missing-imports
git add scoring/tables.py
git commit -m "✨ feat: score a page's tables against every reference cell"
```

---

## Task 6: Wire the metric into scored rows and the report

**Files:**
- Modify: `scoring/score.py` (`score_page`)
- Modify: `scoring/report.py` (`_METRICS`)
- Test: `tests/scoring/test_score.py`, `tests/scoring/test_report.py`

**Interfaces:**
- Consumes: `score_tables(reference, prediction, policy) -> dict` from Task 5.
- Produces: the eight fields on every scored row; `table_cell_error_rate`
  aggregated by `report.aggregate` alongside the existing metrics.

**Note:** `aggregate` already filters `None` per metric
(`values = [float(r[m]) for r in scored if r[m] is not None]`) and yields
`None` for an empty sample, so a page with no table needs no special handling
in `report.py` beyond the name.

- [ ] **Step 1: Write the failing tests**

Append to `tests/scoring/test_score.py`:

```python
def test_a_scored_row_carries_the_table_fields():
    from pathlib import Path

    from scoring.policy import load_scoring_policy
    from scoring.score import score_page

    policy = load_scoring_policy(Path("config/scoring.yml"))
    text = "| A | B |\n| --- | --- |\n| 1 | 2 |\n"
    row = score_page(text, text, policy, verified=True)
    assert row["table_cell_error_rate"] == 0.0
    assert row["table_cells_compared"] == 4
    assert row["table_count_ref"] == 1


def test_a_misfiled_cell_is_penalised_where_the_normalised_metric_is_blind():
    """The finding this metric exists for: the pipe strip hides the column."""
    from pathlib import Path

    from scoring.policy import load_scoring_policy
    from scoring.score import score_page

    policy = load_scoring_policy(Path("config/scoring.yml"))
    reference = "| Date | Debits | Credits |\n| --- | --- | --- |\n| 01/10 | $5.00 |  |\n"
    prediction = "| Date | Debits | Credits |\n| --- | --- | --- |\n| 01/10 |  | $5.00 |\n"
    row = score_page(reference, prediction, policy, verified=True)
    assert row["normalised_cer"] == 0.0
    assert row["table_cell_error_rate"] > 0.0
    assert row["table_cells_misplaced"] == 1


def test_an_absent_prediction_leaves_every_table_field_none():
    from pathlib import Path

    from scoring.policy import load_scoring_policy
    from scoring.score import score_page

    policy = load_scoring_policy(Path("config/scoring.yml"))
    row = score_page("| A |\n| --- |\n| 1 |\n", None, policy, verified=True)
    for field in (
        "table_cell_error_rate",
        "table_cells_compared",
        "table_cells_correct",
        "table_cells_misplaced",
        "table_rows_missing",
        "table_rows_spurious",
        "table_count_ref",
        "table_count_pred",
    ):
        assert row[field] is None, field
```

Append to `tests/scoring/test_report.py`:

```python
def test_the_table_error_rate_is_aggregated_like_the_other_metrics():
    from pathlib import Path

    from scoring.policy import load_scoring_policy
    from scoring.report import aggregate

    policy = load_scoring_policy(Path("config/scoring.yml"))
    rows = [
        {
            "model": "m",
            "prediction_present": True,
            "degenerate": False,
            "verified": True,
            "normalised_cer": 0.0,
            "strict_cer": 0.0,
            "normalised_wer": 0.0,
            "table_cell_error_rate": rate,
        }
        for rate in (0.0, 0.5)
    ]
    groups = aggregate(rows, ("model",), policy)
    assert groups[0]["table_cell_error_rate_mean"] == pytest.approx(0.25)


def test_a_page_with_no_table_does_not_drag_the_table_metric_down():
    from pathlib import Path

    from scoring.policy import load_scoring_policy
    from scoring.report import aggregate

    policy = load_scoring_policy(Path("config/scoring.yml"))
    rows = [
        {
            "model": "m",
            "prediction_present": True,
            "degenerate": False,
            "verified": True,
            "normalised_cer": 0.0,
            "strict_cer": 0.0,
            "normalised_wer": 0.0,
            "table_cell_error_rate": rate,
        }
        for rate in (0.4, None)
    ]
    groups = aggregate(rows, ("model",), policy)
    assert groups[0]["table_cell_error_rate_mean"] == pytest.approx(0.4)
```

If `tests/scoring/test_report.py` does not already import `pytest`, add it.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `conda run -n docparse-score python -m pytest tests/scoring/test_score.py tests/scoring/test_report.py -v -k table`
Expected: FAIL — `KeyError: 'table_cell_error_rate'`

- [ ] **Step 3: Wire it into `score_page`**

In `scoring/score.py`, add the import:

```python
from scoring.tables import score_tables
```

In the absent-prediction branch of `score_page`, add the eight fields to the
returned mapping:

```python
            "degenerate": False,
            "table_cell_error_rate": None,
            "table_cells_compared": None,
            "table_cells_correct": None,
            "table_cells_misplaced": None,
            "table_rows_missing": None,
            "table_rows_spurious": None,
            "table_count_ref": None,
            "table_count_pred": None,
```

In the scored branch, merge the table fields into the returned mapping by
adding this as its final entry, after `"degenerate"`:

```python
        **score_tables(reference, prediction, policy),
```

- [ ] **Step 4: Add the metric to the report**

In `scoring/report.py`, extend `_METRICS`:

```python
_METRICS = ("normalised_cer", "strict_cer", "normalised_wer", "table_cell_error_rate")
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `conda run -n docparse-score python -m pytest tests/scoring/ -v`
Expected: PASS, including the five new tests.

- [ ] **Step 6: Lint, type-check and commit**

```bash
conda run -n docparse-score ruff check --fix --ignore ARG001,ARG002,F841 .
conda run -n docparse-score ruff format .
conda run -n docparse-score mypy scoring --ignore-missing-imports
git add scoring/score.py scoring/report.py
git commit -m "✨ feat: report the table cell error rate on every scored row"
```

---

## Task 7: The acceptance gate — real pages and the probe

**Files:**
- Create: `tests/scoring/test_tables_corpus.py`
- Test: itself

**Interfaces:**
- Consumes: `score_tables` from Task 5, `score_page` from Task 6.
- Produces: nothing consumed by later tasks. This is the gate.

**Why this task exists and is not optional.** Unit tests on hand-written
fixtures have misdescribed real corpus output five times in this repository's
recent history, recorded in
`docs/superpowers/2026-08-26-layout-ground-truth-follow-ups.md` §4. In one case
a serialisation policy was never implemented in a second projection and three
separate reviews passed it, because every check compared structure rather than
content. **A green Task 1–6 proves nothing until this task passes.**

This test reads the corpus but must never write to it.

- [ ] **Step 1: Write the failing tests**

Create `tests/scoring/test_tables_corpus.py`:

```python
"""The table metric, against the real corpus rather than fixtures.

Every earlier test in this subsystem uses hand-written tables. This one uses the
165 shipped transcripts, which is where headerless receipts, date-grouped
statements and the two-table ANZ layouts actually live.
"""

from pathlib import Path

import pytest

from generators.loader import load_generation_config
from scoring.policy import load_scoring_policy
from scoring.score import score_page
from scoring.tables import parse_tables, score_tables

CONFIG = Path("config/generation_config.yml")
TRANSCRIPTS = Path(load_generation_config(CONFIG)["derived_dir"]) / "transcripts"

pytestmark = pytest.mark.skipif(
    not TRANSCRIPTS.exists(), reason="no generated corpus on this machine"
)


@pytest.fixture(scope="module")
def policy():
    return load_scoring_policy(Path("config/scoring.yml"))


def _transcripts() -> list[Path]:
    return sorted(TRANSCRIPTS.glob("*.md"))


def test_every_shipped_transcript_scores_faultless_against_itself(policy):
    """The single most valuable test here: real shapes, not invented ones."""
    assert len(_transcripts()) == 165
    faults = []
    for path in _transcripts():
        text = path.read_text(encoding="utf-8")
        result = score_tables(text, text, policy)
        if (
            result["table_cell_error_rate"] != 0.0
            or result["table_rows_missing"]
            or result["table_rows_spurious"]
            or result["table_cells_misplaced"]
        ):
            faults.append((path.name, result))
    assert not faults, f"{len(faults)} transcript(s) do not score faultless against themselves: {faults[:5]}"


def test_every_shipped_transcript_contains_at_least_one_table(policy):
    empty = [p.name for p in _transcripts() if not parse_tables(p.read_text(encoding="utf-8"))]
    assert not empty, f"no table parsed from: {empty[:5]}"


def _misfile_debits_into_credits(text: str) -> tuple[str, int]:
    """Move every Debits value into the Credits column, as the probe did.

    `CASE002_bank_statements.md` has the header
    `| Date | Transaction Description | Debits | Credits | Balance |`, so Debits
    is column 2 and Credits column 3.

    Args:
        text: The transcript.

    Returns:
        The perturbed transcript and the number of rows changed.
    """
    debits, credits = 2, 3
    lines = []
    changed = 0
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|") or set(stripped) <= set("| -:"):
            lines.append(line)
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if len(cells) == 5 and cells[debits] and not cells[credits]:
            cells[debits], cells[credits] = "", cells[debits]
            changed += 1
            lines.append("| " + " | ".join(cells) + " |")
        else:
            lines.append(line)
    return "\n".join(lines) + "\n", changed


def test_the_probe_that_the_normalised_metric_scores_as_perfect(policy):
    """27 reversed transactions score 0.000000 normalised CER. They must not here."""
    path = TRANSCRIPTS / "CASE002_bank_statements.md"
    reference = path.read_text(encoding="utf-8")
    perturbed, changed = _misfile_debits_into_credits(reference)
    assert changed == 27, f"expected the probe's 27 rows, perturbed {changed}"

    row = score_page(reference, perturbed, policy, verified=True)
    assert row["normalised_cer"] == 0.0, "the premise of this test has changed"
    assert row["table_cell_error_rate"] > 0.1
    assert row["table_cells_misplaced"] == 27
    assert row["table_rows_missing"] == 0


def test_a_one_character_typo_stays_small(policy):
    """The inversion: the typo must not outweigh 27 misfiled transactions."""
    path = TRANSCRIPTS / "CASE002_bank_statements.md"
    reference = path.read_text(encoding="utf-8")
    typo = reference.replace("FORWARD", "FORWORD", 1)
    assert typo != reference

    misfiled, _ = _misfile_debits_into_credits(reference)
    typo_row = score_page(reference, typo, policy, verified=True)
    misfile_row = score_page(reference, misfiled, policy, verified=True)
    assert typo_row["table_cell_error_rate"] < misfile_row["table_cell_error_rate"]


def test_dropping_most_of_a_statement_is_not_a_perfect_score(policy):
    """The denominator decision: dropped rows must not be free."""
    path = TRANSCRIPTS / "CASE002_bank_statements.md"
    reference = path.read_text(encoding="utf-8")
    lines = reference.splitlines()
    kept = [line for line in lines if not line.strip().startswith("|")]
    table_lines = [line for line in lines if line.strip().startswith("|")]
    prediction = "\n".join(kept[:2] + table_lines[:5]) + "\n"

    row = score_page(reference, prediction, policy, verified=True)
    assert row["table_cell_error_rate"] > 0.5
    assert row["table_rows_missing"] > 20
```

- [ ] **Step 2: Run the tests to verify they fail or reveal reality**

Run: `conda run -n docparse-score python -m pytest tests/scoring/test_tables_corpus.py -v`

Expected on a first run: the self-scoring test is the one that matters. If it
fails, **the parser disagrees with a real shipped table shape** — that is the
defect this task exists to find, and it is fixed in `scoring/tables.py`, not by
weakening the test. Report the failing transcript names and shapes before
changing anything.

- [ ] **Step 3: Fix any real disagreement the corpus reveals**

Any change here goes in `scoring/tables.py`. Do not adjust the assertions to
match the code's behaviour: the assertion that every transcript scores faultless
against itself is a property of a correct parser, not a tunable.

- [ ] **Step 4: Run the whole suite**

```bash
conda run -n docparse-score python -m pytest tests/scoring/ -v
conda run -n docparse-score python -m pytest tests/ --cov=scoring --cov-report=term
```

Expected: all pass; coverage of `scoring/` at or above the 80% floor.

- [ ] **Step 5: Verify the existing metrics did not move**

The spec requires NORMALISED and STRICT to be numerically unchanged. Confirm by
scoring the corpus against itself and checking those two fields are untouched by
this work:

```bash
conda run -n docparse-score python -c "
from pathlib import Path
from scoring.policy import load_scoring_policy
from scoring.score import score_page
from generators.loader import load_generation_config
t = Path(load_generation_config(Path('config/generation_config.yml'))['derived_dir']) / 'transcripts'
p = load_scoring_policy(Path('config/scoring.yml'))
rows = [score_page(f.read_text(), f.read_text(), p, verified=True) for f in sorted(t.glob('*.md'))]
print('pages', len(rows))
print('normalised_cer all zero:', all(r['normalised_cer'] == 0.0 for r in rows))
print('strict_cer all zero:', all(r['strict_cer'] == 0.0 for r in rows))
print('table_cell_error_rate all zero:', all(r['table_cell_error_rate'] == 0.0 for r in rows))
"
```

Expected: `pages 165` and three `True` lines.

- [ ] **Step 6: Commit**

Only source is committed; `tests/` is gitignored.

```bash
git add -A scoring config
git commit -m "✅ test: pin the table metric against the real corpus and the probe"
```

If `git status` shows nothing to commit because Tasks 1–6 already committed
every source change and this task needed no fix, say so in the report rather
than inventing a commit.

---

## Self-Review

**Spec coverage.**

| Spec section | Task |
|---|---|
| §3.1 one parser, both sides | 1 |
| §3.2 finding tables, empty header kept | 1 |
| §3.3 pairing tables in page order | 5 |
| §3.4 row alignment, opcodes | 3 |
| §3.5 cell comparison, every reference cell | 4, 5 |
| §3.6 misplacement | 4 |
| §4 row fields, absent prediction, `_METRICS` | 6 |
| §5 configuration and its validation | 2 |
| §6 files and module boundary | all |
| §7 config fails fast, content never raises | 2, 5 |
| §8 testing, probe, 165-page verification | 7 |
| §9 known limitations | documented in the spec; L1 and L2 are behaviour of Task 1's parser and are covered by `test_a_line_not_starting_with_a_pipe_is_not_a_table_line` |
| §10 success criteria 1–4 | 7 |
| §10 success criterion 5 (metrics unchanged) | 7 Step 5 |
| §10 success criteria 6–7 | 2, and the no-new-dependency constraint |

No gaps.

**Placeholder scan.** No "TBD", no "add error handling", no "similar to Task N".
Every code step carries the actual code. Task 7 Step 3 is deliberately
conditional rather than vague: it describes what to do *if* the corpus reveals a
disagreement, and forbids the wrong fix.

**Type consistency.** `parse_tables -> list[list[list[str]]]` feeds
`align_rows(ref_rows, pred_rows, policy)`, whose pairs feed
`compare_row(ref_cells, pred_cells, policy) -> tuple[int, int, int]`, all
consumed by `score_tables(reference, prediction, policy) -> dict`. `_cell_form`
is defined in Task 3 and used in Task 4. The eight field names are written
identically in Tasks 5, 6 and 7 and match the spec's §4 table.

**One known behaviour worth stating rather than discovering.** Under
`cell_comparison: normalised`, a cell containing only `-` normalises to the
empty string, because `scoring/normalise.py`'s `_SEPARATOR_ROW` pattern matches
it. A model writing `-` where the reference leaves a cell blank therefore
compares equal. That is consistent with how the existing normalised metric
already forgives dash-for-blank, and it is the desired behaviour — but it is a
real property of the design, not an accident.
