# Date Bands Without a Date Column — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give `cba_date_grouped` the shape a real CBA statement has — no date column, with each day's date standing as a full-width band row — without changing any other page.

**Architecture:** Three inert changes land first (band position, band `colspan`, scorer weighting), each provably a no-op on today's corpus. Only the fourth task, which drops the date column, activates them. The prompt is taught the new shape before the corpus contains it.

**Tech Stack:** Python 3.12, PIL, PyYAML, typer, pytest. Two conda envs: `docparse` for `generators/`, `docparse-score` for `scoring/`.

**Spec:** `docs/superpowers/specs/2026-08-31-date-bands-without-a-date-column-design.md`

## Global Constraints

- Run every command **from the repository root**; several modules resolve config with CWD-relative paths.
- `generators/` runs in `docparse`; `scoring/` runs in `docparse-score`. `pytest tests/` under `docparse` **fails at collection** — always pass `--ignore=tests/scoring`.
- `tests/` is gitignored. Write and run tests; **never commit them**. Only non-test files appear in `git add` lines below.
- Line length 108. `ruff check --fix --ignore ARG001,ARG002,F841 .` then `ruff format .` then `mypy generators --ignore-missing-imports` before every commit.
- **Never bypass pre-commit hooks** with `--no-verify`.
- Fail-fast diagnostics use the four-element shape (What / Where / Expected / Recover), asserted with `assert_diagnostic_error` from `tests/helpers.py`.
- **No real corpus content in `config/prompt.md`.** Every value in a worked example must be invented and verified absent from all 189 transcripts. `tests/test_prompt.py::test_no_prompt_example_leaks_corpus_content` enforces this by scanning every `<td>` in every fenced block.
- Determinism is a contract: same inputs render byte-identical images.

---

## File Structure

| File | Responsibility | Task |
|---|---|---|
| `generators/tables.py` | `carry_group_key_down` — band position | 1 |
| `generators/layout_dsl/primitives_table.py` | band emits `colspan` = column count | 2 |
| `scoring/table_html.py` | full-width span occupies one grid position | 3 |
| `config/prompt.md` | teach both limbs of the carry rule | 4 |
| `config/layouts/bank_statements.yml` | `cba_date_grouped` drops its date column | 5 |
| `config/generation_config.yml` | new vintage stamp | 6 |

---

### Task 1: Keep a band in its own position

Today `carry_group_key_down` parks a band in `pending` and flushes it only when the next band arrives or the table ends. With a date column the band is always consumed, so nothing shows. Without one, bands are displaced — each lands after the transactions it heads.

The fix inverts the bookkeeping: append the band **in place**, and delete it if a later row carries from it.

**Files:**
- Modify: `generators/tables.py:134-172` (the body of `carry_group_key_down`)
- Test: `tests/test_tables.py`

**Interfaces:**
- Consumes: `Cell` (NamedTuple: `text`, `colspan=1`, `rowspan=1`), already in `generators/tables.py`.
- Produces: `carry_group_key_down(rows: list[tuple[list[Cell], bool]]) -> list[tuple[list[Cell], bool]]` — signature unchanged.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_tables.py`:

```python
from generators.tables import Cell, carry_group_key_down


def _band(text: str, width: int) -> tuple[list[Cell], bool]:
    return ([Cell(text, colspan=width)], False)


def _row(*texts: str) -> tuple[list[Cell], bool]:
    return ([Cell(t) for t in texts], False)


def test_a_band_with_no_column_to_carry_into_stays_in_position():
    """Without a date column no row has a blank first cell, so nothing carries."""
    rows = [
        ([Cell("Description"), Cell("Debit"), Cell("Credit"), Cell("Balance")], True),
        _band("Sat 07 Oct 2023", 4),
        _row("EFTPOS REGIONAL BUS", "", "$2,905.75", "$18,067.77"),
        _row("DD GREENHALGH P", "$481.76", "", "$17,586.01"),
        _band("Mon 09 Oct 2023", 4),
        _row("VISA DEBIT PURCHASE", "", "$1,416.86", "$-849.63"),
    ]
    out = [[c.text for c in cells] for cells, _ in carry_group_key_down(rows)]
    assert out == [
        ["Description", "Debit", "Credit", "Balance"],
        ["Sat 07 Oct 2023"],
        ["EFTPOS REGIONAL BUS", "", "$2,905.75", "$18,067.77"],
        ["DD GREENHALGH P", "$481.76", "", "$17,586.01"],
        ["Mon 09 Oct 2023"],
        ["VISA DEBIT PURCHASE", "", "$1,416.86", "$-849.63"],
    ]


def test_a_band_with_a_date_column_is_still_carried_down_and_removed():
    """The existing behaviour, re-pinned: this task must not change it."""
    rows = [
        ([Cell("Date"), Cell("Description"), Cell("Balance")], True),
        ([Cell("07/10/2023"), Cell(""), Cell("")], False),
        _row("", "DD GREENHALGH P", "$17,586.01"),
        _row("", "DIRECT DEBIT CENTRAL", "$-2,266.49"),
    ]
    out = [[c.text for c in cells] for cells, _ in carry_group_key_down(rows)]
    assert out == [
        ["Date", "Description", "Balance"],
        ["07/10/2023", "DD GREENHALGH P", "$17,586.01"],
        ["07/10/2023", "DIRECT DEBIT CENTRAL", "$-2,266.49"],
    ]


def test_a_band_that_heads_nothing_is_kept_where_it_stands():
    """It is the only record the date was on the page; it must not move to the end."""
    rows = [
        ([Cell("Date"), Cell("Description")], True),
        ([Cell("07/10/2023"), Cell("")], False),
        ([Cell("09/10/2023"), Cell("")], False),
        _row("", "DD GREENHALGH P"),
    ]
    out = [[c.text for c in cells] for cells, _ in carry_group_key_down(rows)]
    assert out == [
        ["Date", "Description"],
        ["07/10/2023"],
        ["09/10/2023", "DD GREENHALGH P"],
    ]
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
conda run -n docparse pytest tests/test_tables.py -q -k "band"
```

Expected: `test_a_band_with_no_column_to_carry_into_stays_in_position` FAILS, showing both bands moved after their rows. `test_a_band_that_heads_nothing_is_kept_where_it_stands` FAILS, showing `07/10/2023` at the end. The date-column test PASSES already — it pins existing behaviour.

- [ ] **Step 3: Replace the body of `carry_group_key_down`**

Replace lines 134-172 of `generators/tables.py` (everything from `carried: list[...] = []` to `return carried`) with:

```python
    carried: list[tuple[list[Cell], bool]] = []
    # Index in `carried` of a band no row has carried from yet. The band is
    # appended where it occurs and REMOVED if a row later takes its key, rather
    # than held back and flushed later: holding it back put each band after the
    # rows it heads whenever nothing carried, which is every table that has no
    # column to carry into.
    pending_index: int | None = None
    last_key = ""

    for cells, was_header in rows:
        if was_header:
            carried.append((cells, was_header))
            continue

        is_group_header = (
            bool(cells) and bool(cells[0].text.strip()) and not any(cell.text.strip() for cell in cells[1:])
        )
        if is_group_header:
            carried.append((cells, was_header))
            pending_index = len(carried) - 1
            last_key = cells[0].text
            continue

        if cells and not cells[0].text.strip():
            if last_key.strip():
                # `_replace` rather than a fresh Cell: the carried date inherits
                # whatever span the blank cell had, so a merged column stays
                # merged when its text is filled in.
                cells = [cells[0]._replace(text=last_key), *cells[1:]]
                if pending_index is not None:
                    # The band was consumed: its date now stands on this row, so
                    # the band row itself would be a duplicate.
                    del carried[pending_index]
                    pending_index = None
        elif cells:
            last_key = cells[0].text
        carried.append((cells, was_header))

    return carried
```

- [ ] **Step 4: Update the docstring**

In the same function's docstring, replace the paragraph beginning "A group header is a row whose first cell has content" with:

```
    A group header is a row whose first cell has content and whose other cells
    are all empty. Where the table has a column to carry into, the header's key
    is copied onto every row of its group and the header row itself is dropped.
    Where it has none -- no row ever presents a blank first cell -- the band
    stays exactly where it occurred, as its own row. That is the second limb of
    one rule, not a second rule: see
    docs/superpowers/specs/2026-08-31-date-bands-without-a-date-column-design.md
    §3. A blank first cell with no date anywhere above it stays blank: an
    opening-balance row genuinely predates the first group, and there is nothing
    to carry.
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
conda run -n docparse pytest tests/test_tables.py -q
conda run -n docparse pytest tests/ --ignore=tests/scoring -q
```

Expected: all pass. **This task must not change a single transcript** — every banded layout still has a date column, so the new limb is unreachable. `tests/test_corpus_unchanged.py` passing is the proof.

- [ ] **Step 6: Lint, type-check, commit**

```bash
conda run -n docparse ruff check --fix --ignore ARG001,ARG002,F841 . && conda run -n docparse ruff format .
conda run -n docparse mypy generators --ignore-missing-imports
git add generators/tables.py
git commit -m "🐛 fix: keep a date band in its own position"
```

---

### Task 2: A band declares the width it spans

The band is drawn across the whole table but emitted as one cell with an implicit `colspan` of 1. Recording the span makes the capture honest and gives Task 5 the markup it needs.

**Files:**
- Modify: `generators/layout_dsl/primitives_table.py` (the band's `emit("cell", ...)` call, currently at line ~578-585)
- Test: `tests/layout_dsl/test_group_date_format.py`

**Interfaces:**
- Consumes: `TranscriptRecorder.emit(kind, text, **meta)`; `generators/tables.py:281` already reads `colspan` from cell meta generically.
- Produces: band `cell` events carrying `colspan=<column count>`.

- [ ] **Step 1: Write the failing test**

Add to `tests/layout_dsl/test_group_date_format.py`:

```python
def test_a_band_declares_a_colspan_covering_every_column():
    """The band is drawn across the table; the capture must say so."""
    recorder = _render_layout("cba_date_grouped")
    columns = None
    in_band = False
    for event in recorder.events:
        if event.kind == "table_open":
            columns = len(event.meta.get("columns", []))
        elif event.kind == "row_open":
            in_band = bool(event.meta.get("group_header"))
        elif event.kind == "cell" and in_band:
            assert columns, "table_open declared no columns"
            assert event.meta.get("colspan") == columns, (
                f"band cell spans {event.meta.get('colspan')} of {columns} columns"
            )
```

- [ ] **Step 2: Run it to verify it fails**

```bash
conda run -n docparse pytest tests/layout_dsl/test_group_date_format.py -q -k colspan
```

Expected: FAIL — `band cell spans None of 5 columns`.

- [ ] **Step 3: Add `colspan` to the band's emit**

In `generators/layout_dsl/primitives_table.py`, find:

```python
                ctx.transcript.emit(
                    "cell",
                    group_date,
                    row=None,
                    col=0,
                    column_key=str(columns[0]["key"]),
                    header=False,
                )
```

Replace with:

```python
                ctx.transcript.emit(
                    "cell",
                    group_date,
                    row=None,
                    col=0,
                    # The band belongs to no column -- it is drawn across all of
                    # them -- so it names none rather than borrowing the first
                    # column's key, which would be the date column in one layout
                    # and the description column in another.
                    column_key=None,
                    header=False,
                    colspan=len(columns),
                )
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
conda run -n docparse pytest tests/layout_dsl/ -q
conda run -n docparse pytest tests/ --ignore=tests/scoring -q
```

Expected: all pass, `test_corpus_unchanged` included. A band in a date-column layout is still carried away by Task 1's first limb, so no transcript changes.

If a test asserts a band's `column_key` is the first column's key, it was pinning the borrowed value this step removes — update it to expect `None` and note why in its docstring.

- [ ] **Step 5: Lint, type-check, commit**

```bash
conda run -n docparse ruff check --fix --ignore ARG001,ARG002,F841 . && conda run -n docparse ruff format .
conda run -n docparse mypy generators --ignore-missing-imports
git add generators/layout_dsl/primitives_table.py
git commit -m "✨ feat: record the width a date band spans"
```

---

### Task 3: A full-width span costs one cell

`scoring/table_html.py` replicates every spanning cell into each column it covers. That is right for a column label and wrong for a section header: it charges four wrong cells for one misread date.

**Files:**
- Modify: `scoring/table_html.py:119-135` (`_close_cell`) and the module docstring
- Test: `tests/scoring/test_table_html.py`, `tests/scoring/test_tables_corpus.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `parse_html_tables` returns a one-element row for a full-width span; unchanged for partial spans.

- [ ] **Step 1: Write the failing test**

Add to `tests/scoring/test_table_html.py`:

```python
def test_a_full_width_span_occupies_one_cell_not_one_per_column():
    """A band is a section header: one datum, one cell charged."""
    text = (
        "<table><thead><tr><th>Description</th><th>Debit</th><th>Credit</th><th>Balance</th></tr></thead>"
        "<tbody>"
        '<tr><td colspan="4">Sat 07 Oct 2023</td></tr>'
        "<tr><td>EFTPOS REGIONAL BUS</td><td></td><td>$2,905.75</td><td>$18,067.77</td></tr>"
        "</tbody></table>"
    )
    rows = parse_html_tables(text)[0]
    assert rows[1] == ["Sat 07 Oct 2023"]
    assert rows[2] == ["EFTPOS REGIONAL BUS", "", "$2,905.75", "$18,067.77"]


def test_a_partial_span_still_replicates():
    """Unchanged: 'Amount' genuinely labels three columns."""
    text = (
        "<table><thead>"
        '<tr><th></th><th></th><th colspan="3">Amount</th></tr>'
        "<tr><th>Date</th><th>Description</th><th>Withdrawal</th><th>Deposit</th><th>Balance</th></tr>"
        "</thead></table>"
    )
    assert parse_html_tables(text)[0][0] == ["", "", "Amount", "Amount", "Amount"]


def test_a_full_width_span_in_a_one_column_table_is_still_one_cell():
    text = '<table><tbody><tr><td colspan="1">Sat 07 Oct 2023</td></tr></tbody></table>'
    assert parse_html_tables(text)[0][0] == ["Sat 07 Oct 2023"]
```

- [ ] **Step 2: Run it to verify it fails**

```bash
conda run -n docparse-score pytest tests/scoring/test_table_html.py -q -k span
```

Expected: `test_a_full_width_span_occupies_one_cell_not_one_per_column` FAILS with the band row as four repeated dates. The other two PASS already.

- [ ] **Step 3: Make `_close_cell` span-aware**

The parser learns a table's width from its first completed row. Add an instance attribute in `_GridParser.__init__`, beside `self._carries`:

```python
        # The grid's width, learned from the first row that closes. A cell
        # spanning all of it is a section header (a date band), not a column
        # label, so it occupies one position instead of replicating.
        self._width: int | None = None
```

Set it at the end of `_close_row`, immediately before `self._row = None`:

```python
        if self._width is None and self._row:
            self._width = len(self._row)
```

The width is unknown while the *first* row is still open, so a span in that row
replicates. That is safe here: `headerless_table: empty_header_row` means every
table emits a header row, so a band is never the first row of a table. A model
that omits the header entirely and opens with a band would have its band
replicated — a wrong prediction scored slightly differently, not a corpus fault.

Then in `_close_cell`, replace the placement loop:

```python
        self._fill_carried()
        start = self._column
        for offset in range(self._colspan):
            self._row.append(text)
            if self._rowspan > 1:
                # Counting the current row, so the decrement at row close leaves
                # exactly `rowspan - 1` rows still to fill.
                self._carries[start + offset] = [text, self._rowspan]
        self._column += self._colspan
```

with:

```python
        self._fill_carried()
        start = self._column
        # A cell covering the whole grid is a band: one datum introducing a
        # group, not a label over columns. Replicating it would charge one
        # misread date as several wrong cells. A partial span still replicates,
        # because a spanning header genuinely labels each column it covers.
        full_width = self._width is not None and start == 0 and self._colspan >= self._width
        spans = 1 if full_width else self._colspan
        for offset in range(spans):
            self._row.append(text)
            if self._rowspan > 1:
                # Counting the current row, so the decrement at row close leaves
                # exactly `rowspan - 1` rows still to fill.
                self._carries[start + offset] = [text, self._rowspan]
        self._column += self._colspan
```

- [ ] **Step 4: Restate the ragged invariant**

In `tests/scoring/test_tables_corpus.py`, replace the `ragged` accumulation inside `test_the_parse_invariants_are_pinned_against_the_real_corpus`:

```python
            width = len(table[0]) if table else 0
            for row in table:
                cells_total += len(row)
                if len(row) != width:
                    ragged += 1
```

with:

```python
            width = len(table[0]) if table else 0
            for row in table:
                cells_total += len(row)
                # A full-width band is legitimately one cell wide; anything else
                # narrower than the grid means cells went missing, which is the
                # failure this guard exists to catch.
                if len(row) != width and len(row) != 1:
                    ragged += 1
```

Update that test's docstring sentence about `ragged` to say it now permits a single-cell band row.

- [ ] **Step 5: Run the tests to verify they pass**

```bash
conda run -n docparse-score pytest tests/scoring -q
```

Expected: all pass, with `cells_total == 10100` and `tables_total == 203` **unchanged** — every span in the current corpus is `colspan="3"` in a 5-column table, so nothing existing is full-width. If either pin moves, the change was not inert and must be re-examined before going further.

- [ ] **Step 6: Update the module docstring**

In `scoring/table_html.py`, the docstring paragraph beginning "Spans expand by REPLICATION" now overstates. Replace its first sentence with:

```
Spans expand by REPLICATION -- a cell covering several grid positions holds its
text at every one of them -- with one exception: a cell spanning the table's
FULL width is a section header (a date band), not a label over columns, so it
occupies a single position. One datum, one cell compared.
```

- [ ] **Step 7: Lint, type-check, commit**

```bash
conda run -n docparse ruff check --fix --ignore ARG001,ARG002,F841 . && conda run -n docparse ruff format .
conda run -n docparse-score mypy scoring --ignore-missing-imports
git add scoring/table_html.py
git commit -m "🐛 fix: charge a date band one cell, not one per column"
```

---

### Task 4: Teach the prompt both limbs

The prompt currently gives one unconditional instruction. A model reading a CBA page after Task 5 needs the other limb, and it must be able to tell which applies — it can, by looking at whether the table has a date column.

**Files:**
- Modify: `config/prompt.md:130-146`
- Test: `tests/test_prompt.py`

**Interfaces:**
- Consumes: nothing.
- Produces: prompt text containing the exact phrase `repeat it on every row`, which `_PROMPT_COVERAGE["carry_group_key"]` requires.

- [ ] **Step 1: Choose an example date and prove it is absent**

The example must be invented. Verify it appears in no transcript and no other prompt example:

```bash
grep -rl "Tue 03 Mar 2015\|03/03/2015" ../document-parsing-data/synthetic_data_2026-08-31/derived/transcripts/ config/prompt*.md
```

Expected: no output. If anything matches, pick another date and re-run. Do the same for every other value used below (`Sprocket Housing 6mm`, `Retainer Clip 2mm`, `41.20`, `58.90`).

- [ ] **Step 2: Write the failing test**

Add to `tests/test_prompt.py`:

```python
def test_the_prompt_states_both_limbs_of_the_carry_rule():
    """A model must know what to do when a table has no date column."""
    prompt = Path("config/prompt.md").read_text(encoding="utf-8")
    assert "repeat it on every row" in prompt, "the coverage phrase must survive"
    assert "no date column" in prompt, "the second limb is never stated"
    assert "colspan" in prompt.split("## ")[0] or "colspan" in prompt, "no span markup shown"
```

- [ ] **Step 3: Run it to verify it fails**

```bash
conda run -n docparse pytest tests/test_prompt.py -q -k both_limbs
```

Expected: FAIL on `the second limb is never stated`.

- [ ] **Step 4: Rewrite the instruction**

In `config/prompt.md`, after the paragraph ending "leave that cell blank rather than borrowing the date from below.", insert:

```markdown
**Where the table has no date column, give the date a row of its own.** Some
statements drop the date column entirely and print each day's date as a band
across the whole table. There is no cell to put it in, so write it as a single
cell spanning every column, in the position it appears on the page:

```html
<table><thead><tr><th>Description</th><th>Debit</th><th>Balance</th></tr></thead>
<tbody><tr><td colspan="3">Tue 03 Mar 2015</td></tr>
<tr><td>Sprocket Housing 6mm</td><td>41.20</td><td>908.15</td></tr>
<tr><td>Retainer Clip 2mm</td><td>58.90</td><td>849.25</td></tr></tbody></table>
```

Look at the header row to decide which of these two applies: if there is a date
column, fill it on every row; if there is not, the date takes a row of its own.
```

Leave the existing "repeat it on every row" paragraph exactly as it is — `_PROMPT_COVERAGE` requires that phrase verbatim.

- [ ] **Step 5: Make the leak guard see attributed cells**

The guard cannot currently see a band cell. `tests/test_prompt.py::_example_values`
gates each line on the literal string `"<td>"`, but a band line reads
`<tr><td colspan="3">Tue 03 Mar 2015</td></tr>` — no bare `<td>` — so it falls
through to the plain-text branch, which extracts only digit-bearing tokens. A
real leak of the band's date would pass vacuously, which CLAUDE.md calls worse
than no test at all. The extraction regex `<td[^>]*>(.*?)</td>` is already
attribute-aware; only the gate is wrong.

In `tests/test_prompt.py`, change:

```python
            if "<td>" in line or "<th>" in line:
```

to:

```python
            # `<td` not `<td>`: a band cell carries a colspan, and gating on the
            # bare tag dropped the whole line into the plain-text branch, where
            # a leaked date would have passed vacuously.
            if "<td" in line or "<th" in line:
```

- [ ] **Step 6: Prove the guard fires on a planted leak**

A guard that matches nothing passes vacuously. Plant a real corpus value in the
band example and confirm the test fails. Edit `config/prompt.md` with the Edit
tool — temporarily replace `Tue 03 Mar 2015` with a date that IS in the corpus,
`Sat 07 Oct 2023` — then run:

```bash
conda run -n docparse pytest tests/test_prompt.py -q -k leak
```

Expected: **FAIL**, naming `Sat 07 Oct 2023`. If it passes, the gate fix did not
take — the guard is still blind and must be fixed before going on. Then restore
`Tue 03 Mar 2015`.

- [ ] **Step 7: Run the tests to verify they pass**

```bash
conda run -n docparse pytest tests/test_prompt.py -q
```

Expected: all pass, including `test_no_prompt_example_leaks_corpus_content` and `test_every_convention_the_serialiser_applies_is_stated_in_the_prompt`. A leak failure here means Step 1's check missed a value — pick different content rather than weakening the guard.

- [ ] **Step 6: Commit**

```bash
git add config/prompt.md
git commit -m "📝 docs: tell the model what to do with no date column"
```

---

### Task 5: Drop the date column from `cba_date_grouped`

This is the task that activates Tasks 1-3. It changes 7 pages.

**Files:**
- Modify: `config/layouts/bank_statements.yml` (the `cba_date_grouped` table block and its `field_budgets`)
- Test: `tests/layout_dsl/test_group_date_format.py`, `tests/test_layout_budgets.py`

**Interfaces:**
- Consumes: Task 2's band `colspan`; Task 1's in-place band.
- Produces: a 4-column `cba_date_grouped` whose transcripts carry band rows.

- [ ] **Step 1: Write the failing test**

Add to `tests/layout_dsl/test_group_date_format.py`:

```python
def test_the_cba_grouped_layout_has_no_date_column():
    """A real CBA statement prints no date column; the band carries the date."""
    layout = load_layout_registry(LAYOUT_PATH)["cba_date_grouped"]
    for block in layout["body"]:
        if isinstance(block, dict) and block.get("type") == "table":
            keys = [c["key"] for c in block["columns"]]
            assert keys == ["description", "debit", "credit", "balance"]
            return
    raise AssertionError("cba_date_grouped has no table block")
```

- [ ] **Step 2: Run it to verify it fails**

```bash
conda run -n docparse pytest tests/layout_dsl/test_group_date_format.py -q -k no_date_column
```

Expected: FAIL — the list still begins with `date`.

- [ ] **Step 3: Give the layout its own budget**

`TRANSACTION_DESC` lives on the shared `_cba` anchor, which `cba_standard` and `cba_grouped_columns` also use — **do not edit it there**. Add an override inside the `cba_date_grouped:` layout, after its `<<: *cba` line:

```yaml
    field_budgets:
      <<: *cba_budgets
      # Widened by exactly the 200px the dropped date column occupied:
      # description now starts at the table's left edge instead of x: 200,
      # and the debit column has not moved (x_right: -420). The 220px gap
      # before it is unchanged.
      TRANSACTION_DESC: {width: 960, fit: wrap, min_font: 10, max_lines: 2}
```

This requires the `_cba` anchor's `field_budgets` mapping to carry its own anchor. On the `_cba` block, change:

```yaml
  field_budgets:
```

to:

```yaml
  field_budgets: &cba_budgets
```

- [ ] **Step 4: Remove the column and move description to the left edge**

In the `cba_date_grouped` table block, delete this line:

```yaml
          - {key: date, label: Date, align: left, x: 0}
```

and change the description column's `x: 200` to `x: 0`:

```yaml
          - {key: description, label: Description, align: left, x: 0,
             budget: TRANSACTION_DESC, field: TRANSACTION_DESCRIPTIONS}
```

- [ ] **Step 5: Validate and run the tests**

```bash
conda run -n docparse python -m generators.pipeline validate
conda run -n docparse pytest tests/ --ignore=tests/scoring -q
```

Expected: `validate` passes. `tests/test_corpus_unchanged.py` **now fails** — that is correct, it is the corpus-revision tripwire, and Task 6 clears it. Everything else passes.

If `validate` reports a budget overflow, the 960 in Step 3 is wrong for this page's font metrics; read the diagnostic, which names the field and the width it needs.

- [ ] **Step 6: Inspect the page against its transcript**

```bash
conda run -n docparse python -m generators.pipeline generate --type bank_statements \
    --output /tmp/dbnd/out --derived /tmp/dbnd/derived
conda run -n docparse python -m generators.pipeline serialise --derived /tmp/dbnd/derived
grep -o "<table>.*</table>" /tmp/dbnd/derived/transcripts/CASE038_bank_statements.md | head -c 600
```

Expected: four `<th>` cells, and a band row `<tr><td colspan="4">Sun 01 Oct 2023</td></tr>` **before** the transactions it heads.

Then open `/tmp/dbnd/out/CASE038_bank_statements.png` and check by eye: no Date column, description flush at the table's left edge, bands still bold and above their groups, nothing overlapping. **This check is not optional** — no field-level test catches a table that is well-formed but wrong.

- [ ] **Step 7: Confirm the blast radius is exactly 7 pages**

```bash
OLD=../document-parsing-data/synthetic_data_2026-08-31/derived/transcripts
for f in /tmp/dbnd/derived/transcripts/*.md; do
  cmp -s "$f" "$OLD/$(basename "$f")" || echo "CHANGED: $(basename "$f")"
done
```

Expected: exactly CASE007, CASE009, CASE022, CASE027, CASE038, CASE042, CASE055 — all `cba_date_grouped`. Any other file changing means a shared anchor was edited; revisit Step 3.

- [ ] **Step 8: Lint and commit**

```bash
conda run -n docparse ruff check --fix --ignore ARG001,ARG002,F841 . && conda run -n docparse ruff format .
git add config/layouts/bank_statements.yml
git commit -m "✨ feat: drop the date column from CBA date-grouped statements"
```

---

### Task 6: Cut a new vintage

Images and transcripts have both changed, so this is a corpus revision.

**Files:**
- Modify: `config/generation_config.yml` (the `dataset_root` stamp)

- [ ] **Step 1: Bump the vintage**

Change `dataset_root` to a date that does not yet exist under `../document-parsing-data/`, e.g.:

```yaml
dataset_root: ../document-parsing-data/synthetic_data_2026-09-01
```

Do **not** reuse `2026-08-31` — `generation_config.yml`'s own comment explains that a fixed path silently overwrites the previous corpus. Leave the old vintage on disk.

- [ ] **Step 2: Commit the stamp on its own**

```bash
git add config/generation_config.yml
git commit -m "🗃️ data: start the 2026-09-01 corpus vintage"
```

Commit before building: `build_corpus.sh` reproduces a corpus byte-for-byte, which only means something if the vintage corresponds to a commit.

- [ ] **Step 3: Build**

```bash
DEGRADE=no ./build_corpus.sh
```

Expected: validate, generate 189 documents, serialise, export to `../evaluation_data/corpus_<stamp>/`. The script refuses to overwrite an existing target; pass `DATE_STAMP=` for a different one if it stops.

- [ ] **Step 4: Verify the whole suite is green**

```bash
conda run -n docparse pytest tests/ --ignore=tests/scoring -q
conda run -n docparse-score pytest tests/scoring -q
```

Expected: everything passes. `test_corpus_unchanged` passes again because the data root now matches the code.

`tests/scoring/test_tables_corpus.py`'s pins **will** move now — the 7 CBA tables lose a column and gain band rows. Re-derive them independently rather than pasting whatever the parser prints: count `<table` and `<tr` elements with a regex and confirm they match `parse_tables`, exactly as that test's docstring describes.

- [ ] **Step 5: Push**

```bash
git push
```

---

## Notes for the executor

- **Tasks 1-3 must not change any transcript.** If `test_corpus_unchanged` fails before Task 5, stop and find out why — the inertness of those three tasks is the safety property this ordering buys.
- The one behaviour change hiding in Task 1 is that a band heading nothing is now kept *in position* rather than appended at the end of its table. That is a fix, but if an existing test pins the old position, update it and say why in its docstring.
- Task 5 Step 6's visual check is the only thing that catches a layout that is well-formed but wrong. Do not skip it.
