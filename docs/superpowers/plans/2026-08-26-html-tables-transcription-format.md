# HTML Tables as the Transcription Target — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Change every transcript's tables from Markdown pipe tables to HTML `<table>` elements, with one shared table walk serving both the transcript and the exported `tables/*.html`.

**Architecture:** `generators/tables.py` becomes the sole owner of the table-event walk (a new `TableBuilder`), the cell helpers it currently borrows from `serialise.py`, and the HTML renderer it already has. The existing `tables → serialise` import edge is deleted and replaced by `serialise → tables`. `serialise.serialise` feeds the same events to a `TableBuilder` during its own walk and appends the returned HTML where the table closed, so the transcript's table and `tables/{stem}.html` are byte-identical by construction rather than by two implementations agreeing.

**Tech Stack:** Python 3.12, PyYAML, pytest. Conda env `docparse` (not the global `du`).

**Spec:** `docs/superpowers/specs/2026-08-26-html-tables-transcription-format-design.md`

## Global Constraints

- **Environment:** run everything through `conda run -n docparse <command>`, **from the repository root** — several modules resolve config with CWD-relative paths.
- **`tests/` is gitignored.** Write and run tests; **never `git add` anything under `tests/`**. Every commit in this plan stages only `generators/`, `config/` and `docs/`.
- **Never bypass pre-commit hooks** (`--no-verify` is forbidden). Every commit must pass: `pytest tests/ --ignore=tests/scoring`, `ruff check --fix --ignore ARG001,ARG002,F841 .`, `ruff format .`, `mypy generators --ignore-missing-imports`.
- `tests/scoring/` fails to collect in `docparse` (`ModuleNotFoundError: rapidfuzz`). This is pre-existing and unrelated — always pass `--ignore=tests/scoring`.
- **Coverage floor is 80%** (`pytest tests/ --ignore=tests/scoring --cov=generators`).
- **Line length 108.** Type hints are Python 3.12 style (`X | Y`, no `from __future__ import annotations`, no `TYPE_CHECKING` guards for runtime signatures).
- **Fail-fast diagnostics use the four-element shape** — What / Where / Expected / Recover — asserted with `assert_diagnostic_error` from `tests/helpers.py`. Never invent a per-test approximation.
- **In `except` blocks always `raise ... from err` or `from None`** (B904).
- **No prompt example may contain a real corpus value.** Invent every example cell value and verify it appears nowhere in the eval set before shipping.
- **Never write "ATO"** — use "PROD".
- **No Claude attribution in commit messages.** Repo convention is a gitmoji prefix (`✨ feat:`, `♻️ refactor:`, `📝 docs:`).
- **No image may move.** This is a `serialise` + `export` change; `tests/test_pipeline.py::test_the_same_input_renders_byte_identical_images` must stay green throughout.

---

## File Structure

| File | Responsibility after this plan |
|---|---|
| `generators/tables.py` | **Owner.** `TableBuilder` (the walk), cell helpers (`_join_cell`, `pad_row`, `carry_group_key_down`, `RowWidthError`), `_render`/`_row` (HTML), `table_html` (page → list of HTML). Imports `decoration` only. |
| `generators/serialise.py` | Policy loading/validation, `pair_text`, the document walk. Imports `TableBuilder` from `tables`. Loses `_render_table` and the four cell helpers. |
| `generators/export.py` | Gains `transcript_sha256` in `manifest_record`; README "Verify before you score" mentions it. |
| `generators/layout.py` | Unchanged. Still imports `pair_text` from `serialise` and `table_html`/`TableHtmlError` from `tables`. |
| `config/serialisation.yml` | `table_style: html`. |
| `config/prompt.md` | Teaches HTML tables. |

---

## Task 1: Move the shared cell helpers into `tables.py`

Pure move. No behaviour changes; every existing test must pass untouched. This deletes the `tables → serialise` edge so Task 4 can add `serialise → tables` without a cycle.

**Files:**
- Modify: `generators/serialise.py` (delete `_join_cell` 244-251, `carry_group_key_down` 253-325, `RowWidthError` 327-335, `pad_row` 337-366; add an import)
- Modify: `generators/tables.py:35` (drop the `from generators.serialise import ...` line; add the moved definitions)
- Test: `tests/test_serialise.py`, `tests/test_tables_html.py` (existing, unchanged)

**Interfaces:**
- Consumes: nothing.
- Produces: `generators.tables._join_cell(text: str, policy: dict) -> str`, `generators.tables.pad_row(cells: list[str], width: int, blank: str) -> list[str]`, `generators.tables.carry_group_key_down(rows: list[tuple[list[str], bool]]) -> list[tuple[list[str], bool]]`, `generators.tables.RowWidthError(ValueError)`.

- [ ] **Step 1: Confirm the current suite is green before moving anything**

Run: `conda run -n docparse pytest tests/ --ignore=tests/scoring -q`
Expected: PASS (434 passed, 3 skipped, 1 xfailed as of 2026-08-26).

- [ ] **Step 2: Move the four helpers into `tables.py`**

Cut `_join_cell`, `carry_group_key_down`, `RowWidthError` and `pad_row` from `generators/serialise.py` verbatim — docstrings included — and paste them into `generators/tables.py` above `table_html`. Then replace `tables.py:35`:

```python
# DELETE this line:
from generators.serialise import RowWidthError, _join_cell, carry_group_key_down, pad_row
```

`tables.py` keeps `import html as html_escape` and `from generators.decoration import strip_decoration_run`.

Update the moved docstrings that name their old home. In `RowWidthError`:

```python
class RowWidthError(ValueError):
    """Raised when a row carries more cells than its table declares columns.

    A plain `ValueError` rather than a module error type: padding is shared by
    every table projection, so this stays a caller-agnostic signal each caller
    wraps into its own four-element diagnostic rather than a second
    implementation of padding.
    """
```

In `pad_row`, replace the "Shared by `serialise._render_table` and `tables._render`" sentence with:

```
    The single implementation of padding, used by every table projection so
    they cannot compute it two different ways and drift apart.
```

- [ ] **Step 3: Point `serialise.py` at the new home**

Add to `generators/serialise.py`, beside the existing `decoration` import:

```python
from generators.tables import RowWidthError, _join_cell, carry_group_key_down, pad_row
```

This import is temporary scaffolding — Task 4 deletes it along with `_render_table`. It exists so this task is a pure move that ships green.

- [ ] **Step 4: Run the full suite**

Run: `conda run -n docparse pytest tests/ --ignore=tests/scoring -q`
Expected: PASS, same counts as Step 1. A failure here means the move was not verbatim.

- [ ] **Step 5: Verify no import cycle and no stragglers**

Run: `conda run -n docparse python -c "import generators.serialise, generators.tables, generators.layout, generators.export; print('ok')"`
Expected: `ok`

Run: `grep -n "from generators.serialise import" generators/tables.py`
Expected: no output.

- [ ] **Step 6: Quality gates and commit**

```bash
conda run -n docparse ruff check --fix --ignore ARG001,ARG002,F841 .
conda run -n docparse ruff format .
conda run -n docparse mypy generators --ignore-missing-imports
git add generators/serialise.py generators/tables.py
git commit -m "♻️ refactor: move the shared table cell helpers into tables.py

tables.py imported _join_cell, pad_row, carry_group_key_down and
RowWidthError from serialise.py, which made serialise unable to import
tables. Every one of those helpers is used in serialise only inside the
table walk, so they move wholesale and the edge reverses.

Pure move: no behaviour change, no test changes."
```

---

## Task 2: `TableBuilder` owns the walk

Extract the row-accumulation state machine from `table_html` into a class, and rebuild `table_html` on top of it. `table_html`'s contract does not change, so `tests/test_tables_html.py` passes untouched — that is the check that the extraction was faithful.

**Files:**
- Modify: `generators/tables.py` (add `TableBuilder`; rewrite `table_html` 69-148)
- Test: `tests/test_tables_html.py` (existing, unchanged), `tests/test_table_builder.py` (create)

**Interfaces:**
- Consumes: Task 1's helpers.
- Produces: `generators.tables.TableBuilder`, with `__init__(self, policy: dict)`, `feed(self, event: dict) -> str | None`, and `finish(self) -> None`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_table_builder.py`:

```python
"""The single table walk, driven directly."""

import pytest

from generators.tables import TableBuilder, TableHtmlError
from tests.helpers import assert_diagnostic_error

POLICY = {
    "empty_cell_token": "",
    "cell_newline_join": " ",
    "cell_sub_line_join": " ",
    "carry_group_key": "none",
    "headerless_table": "empty_header_row",
    "decoration_glyphs": ".-_=*",
    "decoration_min_run": 4,
}


def _events(*rows_of_cells):
    """Build a minimal table event stream: first row is the header."""
    events = [{"kind": "table_open", "seq": 0, "text": None, "meta": {"columns": ["a", "b"]}}]
    seq = 1
    for index, cells in enumerate(rows_of_cells):
        events.append({"kind": "row_open", "seq": seq, "text": None, "meta": {}})
        seq += 1
        for key, value in zip(["a", "b"], cells, strict=True):
            events.append(
                {
                    "kind": "cell",
                    "seq": seq,
                    "text": value,
                    "meta": {"column_key": key, "header": index == 0},
                }
            )
            seq += 1
        events.append({"kind": "row_close", "seq": seq, "text": None, "meta": {}})
        seq += 1
    events.append({"kind": "table_close", "seq": seq, "text": None, "meta": {}})
    return events


def test_the_builder_returns_html_only_at_table_close():
    """Every other event returns None, so a caller can append exactly once."""
    builder = TableBuilder(POLICY)
    events = _events(["Widget", "Amount"], ["Grommet", "4.00"])

    returns = [builder.feed(event) for event in events]

    assert returns[-1] is not None, "table_close must yield the rendered table"
    assert all(value is None for value in returns[:-1]), "only table_close yields"
    assert returns[-1].startswith("<table>")
    assert "<th>Widget</th>" in returns[-1]
    assert "<td>Grommet</td>" in returns[-1]


def test_the_builder_yields_one_table_per_close():
    """A two-table page yields twice, in walk order."""
    builder = TableBuilder(POLICY)
    stream = _events(["A", "B"], ["first", "1.00"]) + _events(["C", "D"], ["second", "2.00"])

    yielded = [html for event in stream if (html := builder.feed(event)) is not None]

    assert len(yielded) == 2
    assert "first" in yielded[0] and "second" in yielded[1]


def test_an_unclosed_row_fails_with_a_diagnostic():
    builder = TableBuilder(POLICY)
    for event in _events(["A", "B"])[:-1]:  # drop table_close
        builder.feed(event)

    with pytest.raises(TableHtmlError) as excinfo:
        builder.feed({"kind": "table_close", "seq": 99, "text": None, "meta": {}})

    assert_diagnostic_error(str(excinfo.value))


def test_an_unclosed_table_fails_on_finish():
    """finish() is how a caller asks 'was the stream balanced?'"""
    builder = TableBuilder(POLICY)
    builder.feed({"kind": "table_open", "seq": 0, "text": None, "meta": {"columns": ["a"]}})

    with pytest.raises(TableHtmlError) as excinfo:
        builder.finish()

    assert_diagnostic_error(str(excinfo.value))
```

Note: `_events` marks the first row's cells `header: True`. `zip(..., strict=True)` is required — a silent length mismatch would build a malformed stream and mask a real failure.

- [ ] **Step 2: Run it to make sure it fails**

Run: `conda run -n docparse pytest tests/test_table_builder.py -q`
Expected: FAIL with `ImportError: cannot import name 'TableBuilder' from 'generators.tables'`.

- [ ] **Step 3: Implement `TableBuilder`**

Add to `generators/tables.py`, above `table_html`:

```python
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
            html = _render(self._rows, self._columns, self._policy)
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
```

- [ ] **Step 4: Rebuild `table_html` on the builder**

Replace the body of `table_html` (keep its docstring, adding the note below) with:

```python
    builder = TableBuilder(policy)
    tables = [html for event in events if (html := builder.feed(event)) is not None]
    builder.finish()
    return tables
```

Add to its docstring, under Returns:

```
        One HTML string per table, in walk order. Empty when the page has none.
        Rendered by the same `TableBuilder` the transcript uses, so the two
        projections cannot disagree.
```

- [ ] **Step 5: Run both test files**

Run: `conda run -n docparse pytest tests/test_table_builder.py tests/test_tables_html.py -q`
Expected: PASS. `test_tables_html.py` is unchanged — its passing is the evidence the extraction was faithful.

- [ ] **Step 6: Run the full suite**

Run: `conda run -n docparse pytest tests/ --ignore=tests/scoring -q`
Expected: PASS.

- [ ] **Step 7: Quality gates and commit**

```bash
conda run -n docparse ruff check --fix --ignore ARG001,ARG002,F841 .
conda run -n docparse ruff format .
conda run -n docparse mypy generators --ignore-missing-imports
git add generators/tables.py
git commit -m "♻️ refactor: extract the table walk into TableBuilder

table_html's state machine becomes a class so serialise can drive the
same walk instead of keeping a second copy in step by hand. table_html
keeps its contract exactly; its unchanged tests are the evidence the
extraction was faithful."
```

---

## Task 3: Resolve the two divergences between the walks

The two walks disagree today in ways that were invisible while they emitted different syntaxes. Unifying them forces a decision on each, and each decision must be pinned by a test before Task 4 makes it load-bearing.

**Divergence 1 — the empty table.** `serialise.serialise:492` guards `if table_rows:` and appends nothing for a table with no rows; `table_html` appends `_render([], columns, policy)`, which yields `<table><thead><tr>…blank…</tr></thead></table>`. **Decision: keep `serialise`'s behaviour** — a table that captured no rows put no rows of ink on the page, and emitting an empty `<table>` into a transcript would ask a model to produce markup for something it cannot see. `tables/{stem}.html` is unaffected because a page with no table rows has no table file worth shipping.

**Divergence 2 — header-shape validation.** `_check_header_shape` runs in `_render`, so `table_html` rejects a `header=True` row that follows a `header=False` row while `serialise` silently renders it. **Decision: keep the check** — it is already reached on every export, so no page in the corpus can violate it, and `serialise` gaining it closes a hole rather than adding risk.

**Files:**
- Modify: `generators/tables.py` (`TableBuilder.feed`, `table_close` branch)
- Test: `tests/test_table_builder.py`

**Interfaces:**
- Consumes: `TableBuilder` from Task 2.
- Produces: `TableBuilder.feed` returns `None` at `table_close` when the table captured no rows.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_table_builder.py`:

```python
def test_a_table_with_no_rows_yields_nothing():
    """A table that captured no rows drew no rows of ink; a transcript must
    not carry markup for content the page does not show."""
    builder = TableBuilder(POLICY)
    builder.feed({"kind": "table_open", "seq": 0, "text": None, "meta": {"columns": ["a"]}})

    assert builder.feed({"kind": "table_close", "seq": 1, "text": None, "meta": {}}) is None
    builder.finish()


def test_a_header_row_after_a_body_row_fails_with_a_diagnostic():
    """A shape no projection can render consistently must fail at the source."""
    builder = TableBuilder(POLICY)
    stream = _events(["body", "0.00"], ["head", "Amount"])
    # Mark the SECOND row's cells as header, the first row's as body.
    for event in stream:
        if event["kind"] == "cell":
            event["meta"]["header"] = event["text"] in {"head", "Amount"}

    with pytest.raises(TableHtmlError) as excinfo:
        for event in stream:
            builder.feed(event)

    assert_diagnostic_error(str(excinfo.value))
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `conda run -n docparse pytest tests/test_table_builder.py -q -k "no_rows or header_row_after"`
Expected: `test_a_table_with_no_rows_yields_nothing` FAILS (returns an HTML string, not None). `test_a_header_row_after_a_body_row_fails_with_a_diagnostic` should already PASS — `_check_header_shape` is inside `_render`. If it fails, stop and report: the check is not where this plan assumes.

- [ ] **Step 3: Implement the empty-table decision**

In `TableBuilder.feed`, replace the `table_close` branch:

```python
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
```

- [ ] **Step 4: Run the tests**

Run: `conda run -n docparse pytest tests/test_table_builder.py tests/test_tables_html.py -q`
Expected: PASS. If a `test_tables_html.py` test asserted an empty table renders, it must be updated to assert it is skipped, and the reason recorded in its docstring.

- [ ] **Step 5: Confirm no corpus page is affected**

Run:

```bash
conda run -n docparse python -m generators.pipeline export \
  --derived /tmp/htmltables/derived --output /tmp/htmltables/out \
  --target /tmp/htmltables/exports
```

Expected: succeeds. Note `--target` is mandatory — `--output`/`--derived` do **not** redirect `export`, and `export` has no overwrite guard, so omitting `--target` overwrites the real dated export in place.

Then compare against the shipped export to prove no table changed:

```bash
diff -r /tmp/htmltables/exports/parsing_*/tables \
        /Users/tod/Desktop/evaluation_data/parsing_20260826/tables
```

Expected: no differences. A difference here means a real page has an empty table and the decision needs revisiting before Task 4.

- [ ] **Step 6: Quality gates and commit**

```bash
conda run -n docparse ruff check --fix --ignore ARG001,ARG002,F841 .
conda run -n docparse ruff format .
conda run -n docparse mypy generators --ignore-missing-imports
git add generators/tables.py
git commit -m "♻️ refactor: settle the two ways the table walks disagreed

serialise skipped a table that captured no rows; table_html rendered an
empty <table> for it. serialise's rule wins: a table with no rows drew
no ink, and a transcript must not carry markup for content the page does
not show. Verified against the corpus — no exported table changes.

The header-shape check now covers both projections, closing a hole
rather than adding risk: it already ran on every export."
```

---

## Task 4: `serialise` emits HTML tables

The load-bearing task. `serialise` stops walking table events itself, the policy value changes, and `_render_table` is deleted. Transcripts change here.

**Files:**
- Modify: `generators/serialise.py` (imports; `_ALLOWED:50`; `_EXAMPLES:61`; delete `_render_table` 367-418; rewrite the table branches of `serialise` 462-495)
- Modify: `config/serialisation.yml:20-21`
- Test: `tests/test_serialise.py`, `tests/test_serialisation_policy.py`, `tests/test_pipeline_transcripts.py`

**Interfaces:**
- Consumes: `generators.tables.TableBuilder` (Task 2), with `KINDS`, `feed`, `finish`.
- Produces: transcripts whose tables are HTML. No new public names.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_serialise.py`:

```python
def test_a_table_serialises_as_html():
    """Pipe tables cannot express merged cells, which blocks spanning headers."""
    from generators.tables import TableBuilder  # noqa: F401  (documents the owner)

    events = _table_events()  # existing helper in this module
    policy = dict(POLICY, table_style="html")

    result = serialise(events, policy)

    assert "<table>" in result
    assert "<thead>" in result
    assert "|" not in result, "no pipe-table syntax may survive"


def test_the_transcript_table_is_byte_identical_to_the_exported_table_html():
    """The invariant the shared walk exists to guarantee.

    If these two ever diverge, the corpus contradicts itself about its own
    ground truth — and the only way they can diverge is if someone reintroduces
    a second table walk.
    """
    from generators.tables import table_html

    events = _table_events()
    policy = dict(POLICY, table_style="html")

    transcript = serialise(events, policy)
    exported = table_html(events, policy)

    assert len(exported) == 1
    assert exported[0] in transcript
```

Add to `tests/test_serialisation_policy.py`:

```python
def test_the_old_pipe_table_style_is_rejected_with_a_diagnostic(tmp_path):
    """A policy file from before the HTML change must fail loudly, not emit a
    format the shipped prompt no longer teaches."""
    policy_file = tmp_path / "serialisation.yml"
    body = Path("config/serialisation.yml").read_text(encoding="utf-8")
    policy_file.write_text(
        body.replace("table_style: html", "table_style: pipe_with_header_rule"),
        encoding="utf-8",
    )

    with pytest.raises(SerialisationError) as excinfo:
        load_serialisation_policy(policy_file)

    assert_diagnostic_error(str(excinfo.value), mentions=("table_style", "html"))
```

- [ ] **Step 2: Run them to make sure they fail**

Run: `conda run -n docparse pytest tests/test_serialise.py::test_a_table_serialises_as_html tests/test_serialisation_policy.py::test_the_old_pipe_table_style_is_rejected_with_a_diagnostic -q`
Expected: both FAIL — the first because pipes are still emitted, the second because `pipe_with_header_rule` is still allowed.

- [ ] **Step 3: Narrow the policy**

In `generators/serialise.py`:

```python
_ALLOWED: dict[str, tuple[str, ...]] = {
    "title_style": ("atx_h1",),
    "table_style": ("html",),
    "split_order": ("column_major",),
    "carry_group_key": ("down", "none"),
    "headerless_table": ("empty_header_row",),
    "emphasis": ("none",),
}
```

and in `_EXAMPLES`, `"table_style": "html",`.

In `config/serialisation.yml`, replace lines 20-21:

```yaml
# Tables become HTML tables: the header row in <thead>, the rest in <tbody>.
# Markdown pipe tables cannot express merged cells, and HTML is what
# OmniDocBench, MinerU and Docling score against.
table_style: html
```

- [ ] **Step 4: Delete `_render_table` and rewrite the walk**

Delete `_render_table` (lines 367-418) entirely.

Replace the `serialise.py` import added in Task 1 Step 3 with:

```python
from generators.tables import TableBuilder
```

`strip_decoration_run`, `_join_cell`, `pad_row`, `carry_group_key_down` and `RowWidthError` are no longer referenced in `serialise.py` — remove the now-unused imports. `ruff check` will name any you miss.

In `serialise()`, delete the local table state (`columns`, `table_rows`, `row`, `row_keys`, `row_is_header`, `in_table`) and the six table branches, replacing them with a single delegation placed **before** the `if kind in _STRUCTURE:` check:

```python
    builder = TableBuilder(policy)

    for event in events:
        kind = event["kind"]
        text = event["text"]
        meta = event.get("meta", {})

        if kind in TableBuilder.KINDS:
            # One walk, driven by both projections. Placement stays a property
            # of the walk: the table lands where its table_close occurred.
            html = builder.feed(event)
            if html is not None:
                blocks.append(html)
            continue

        if kind in _STRUCTURE:
            ...
```

Replace the trailing `if in_table:` block (lines 505-514) with:

```python
    builder.finish()
```

`TableBuilder.finish` raises `TableHtmlError` with the four-element shape for an unclosed table, so the bespoke `SerialisationError` for the same condition is now a second implementation of one diagnostic and is deleted with it.

- [ ] **Step 5: Run the tests**

Run: `conda run -n docparse pytest tests/test_serialise.py tests/test_serialisation_policy.py tests/test_tables_html.py tests/test_table_builder.py -q`
Expected: PASS. Existing tests in `test_serialise.py` that assert pipe output must be rewritten to assert the HTML equivalent — update the assertion, keep the behaviour each test was pinning, and record in the docstring that the format changed.

- [ ] **Step 6: Run the full suite**

Run: `conda run -n docparse pytest tests/ --ignore=tests/scoring -q`
Expected: PASS, except `tests/test_prompt.py`, which now fails because `prompt.md` still teaches pipe tables. That is Task 5. If anything in `tests/test_pipeline.py` fails, stop — no image may move.

- [ ] **Step 7: Commit**

```bash
conda run -n docparse ruff check --fix --ignore ARG001,ARG002,F841 .
conda run -n docparse ruff format .
conda run -n docparse mypy generators --ignore-missing-imports
git add generators/serialise.py config/serialisation.yml
git commit -m "✨ feat: emit HTML tables in transcripts

Markdown pipe tables cannot express merged cells, which blocks spanning
headers entirely; HTML is also what OmniDocBench, MinerU and Docling
score against.

serialise no longer walks table events. It drives the same TableBuilder
tables.py uses, so the transcript's table and the exported
tables/{stem}.html are the same bytes by construction. _render_table and
the pipe path are deleted; table_style now accepts only html, so a
pre-change policy file fails at load rather than emitting a format the
prompt no longer teaches.

prompt.md still teaches pipe tables and its tests fail until the next
commit."
```

Note: this commit knowingly leaves `tests/test_prompt.py` red. If the pre-commit hook runs the full suite and blocks it, combine Tasks 4 and 5 into one commit rather than passing `--no-verify`.

---

## Task 5: Rewrite the prompt

**Files:**
- Modify: `config/prompt.md` (examples at 77-79, 93-96, 111-112; prose at 20, 45, 74, 89-90, 106, 119)
- Test: `tests/test_prompt.py` (`_PROMPT_COVERAGE:268`, `test_the_prompt_asks_for_the_table_style_the_policy_emits:45`)

**Interfaces:**
- Consumes: `table_style: html` from Task 4.
- Produces: a prompt whose table instructions match the policy.

- [ ] **Step 1: Update the two policy-agreement tests**

In `tests/test_prompt.py`:

```python
def test_the_prompt_asks_for_the_table_style_the_policy_emits():
    """Pipes must be gone from every worked example, not merely outnumbered."""
    import re

    assert POLICY["table_style"] == "html"
    assert "<table>" in PROMPT
    assert "<thead>" in PROMPT

    for block in re.findall("```(.*?)```", PROMPT, re.DOTALL):
        assert not any(line.strip().startswith("|") for line in block.splitlines()), (
            "a worked example still shows a pipe table"
        )
```

Checking inside fenced blocks rather than the whole file is deliberate: prose may legitimately mention a pipe character, but no *example* may still show one.

And in `_PROMPT_COVERAGE`, change `"table_style": "header separator row"` to `"table_style": "<thead>"`.

- [ ] **Step 2: Run to confirm they fail**

Run: `conda run -n docparse pytest tests/test_prompt.py -q`
Expected: FAIL — the prompt still shows pipe tables.

- [ ] **Step 3: Rewrite the worked examples**

Replace the pipe example at `config/prompt.md:77-79` with HTML. **Invent every value.** Do not reuse the existing example values — they were chosen for a pipe table and this is the moment a real value could slip in:

````markdown
```html
<table><thead><tr><th>Date</th><th>Reference</th><th>Charge</th></tr></thead>
<tbody><tr><td>03/02/2011</td><td>Flange Coupler 3mm</td><td>$71.42</td></tr></tbody></table>
```
````

Replace the headerless example at 93-96 with:

````markdown
```html
<table><thead><tr><th></th><th></th></tr></thead>
<tbody><tr><td>Toggle Latch 5pk</td><td>63.18</td></tr>
<tr><td>Spindle Cap 9mm</td><td>63.44</td></tr></tbody></table>
```
````

Replace the grouped-date example at 111-112 with:

````markdown
```html
<table><thead><tr><th>Date</th><th>Description</th><th>Debit</th></tr></thead>
<tbody><tr><td>09/07/2013</td><td>Bracket Shim 2mm</td><td>82.31</td></tr>
<tr><td>09/07/2013</td><td>Anchor Bolt 16mm</td><td>82.75</td></tr></tbody></table>
```
````

The repeated date is the point of this example — it shows `carry_group_key: down`, the rule that a grouped date is repeated on every row of its group rather than left blank below the first.

Update the prose at lines 20, 45, 74, 89-90, 106 and 119 so no sentence refers to pipes or a "header separator row". Line 45 currently exempts the pipe separator row from the decoration rule; that exemption is obsolete — HTML has no separator row — so the sentence is deleted, and `test_the_prompt_states_the_decoration_rule_the_capture_applies:204` (`assert "| --- |" in PROMPT`) must be deleted with it.

- [ ] **Step 4: Verify every invented value is absent from the corpus**

Run:

```bash
conda run -n docparse pytest tests/test_prompt.py::test_no_prompt_example_leaks_corpus_content -q
```

Expected: PASS — but do **not** trust it yet. This guard cannot read HTML until Task 6; a pass here is currently uninformative. Verify by hand as well:

```bash
grep -rF "Flange Coupler 3mm" /Users/tod/Desktop/evaluation_data/parsing_20260826/transcripts/ | head
grep -rF "71.42" /Users/tod/Desktop/evaluation_data/parsing_20260826/transcripts/ | head
```

Expected: no output for every invented string and amount. If any hits, change the invented value and re-check.

- [ ] **Step 5: Run the prompt tests and the full suite**

Run: `conda run -n docparse pytest tests/ --ignore=tests/scoring -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
conda run -n docparse ruff check --fix --ignore ARG001,ARG002,F841 .
conda run -n docparse ruff format .
git add config/prompt.md
git commit -m "📝 docs: teach HTML tables in the prompt

The prompt and the transcripts are a matched pair; changing one without
the other silently measures something else. Every example value is
invented and verified absent from the corpus.

Drops the pipe-separator exemption from the decoration rule — HTML has no
separator row, so the sentence described a case that can no longer arise."
```

---

## Task 6: Teach the leak guard to read HTML

The guard parses pipe tables. Against HTML examples it falls to its plain-text branch and tests tokens like `<td>03/02/2011</td>`, which appear in no transcript — so it would **pass vacuously on a real leak**. CLAUDE.md names this failure directly: a check that silently matches nothing is worse than no check.

**Files:**
- Test: `tests/test_prompt.py` (`test_no_prompt_example_leaks_corpus_content:76`, `test_the_leak_guard_actually_catches_a_leak:135`)

**Interfaces:**
- Consumes: the HTML prompt from Task 5.
- Produces: nothing importable — this is test-only, and `tests/` is gitignored, so **this task has no commit**.

- [ ] **Step 1: Prove the guard is currently blind**

Temporarily add a real corpus value into a `<td>` in `config/prompt.md` — pick one from a transcript:

```bash
grep -o "<td>[^<]*</td>" /Users/tod/Desktop/evaluation_data/parsing_20260826/tables/*.html | head -5
```

Paste one of those values into a prompt example cell, then run:

```bash
conda run -n docparse pytest tests/test_prompt.py::test_no_prompt_example_leaks_corpus_content -q
```

Expected: **PASS** — which is the bug. Record the value you used; you will reuse it in Step 4. Revert the prompt edit before continuing.

- [ ] **Step 2: Teach the guard `<th>` and `<td>`**

In `test_no_prompt_example_leaks_corpus_content`, add an HTML branch ahead of the pipe branch. `<th>` cells are exempt for the same reason a pipe header row is — header words like "Date" are vocabulary, not answers — but the test is now explicit rather than positional:

```python
        for block in re.findall("```(.*?)```", prompt, re.DOTALL):
            seen_header = False
            for line in block.splitlines():
                if "<td>" in line or "<th>" in line:
                    # HTML marks header cells explicitly, so exemption is by
                    # tag rather than by "the first row we saw". A <th> holds
                    # column vocabulary ("Date"); a <td> holds a value a model
                    # was meant to read off the page.
                    candidates = [
                        c.strip() for c in re.findall(r"<td>(.*?)</td>", line) if c.strip()
                    ]
                elif line.strip().startswith("|"):
                    ...  # existing pipe branch, unchanged
```

Keep the pipe branch: `config/prompt*.md` is globbed, and an experimental variant may still use pipes.

- [ ] **Step 3: Run the guard against the real prompt**

Run: `conda run -n docparse pytest tests/test_prompt.py::test_no_prompt_example_leaks_corpus_content -q`
Expected: PASS, and now meaningfully — Step 4 proves it.

- [ ] **Step 4: Prove the guard now catches the leak it missed**

Re-insert the same real corpus value from Step 1 into a `<td>`, then run the same command.
Expected: **FAIL**, naming the leaked value. Revert the prompt edit and confirm it passes again.

A guard that has not been observed failing on a deliberate leak has not been verified.

- [ ] **Step 5: Extend the self-test to cover HTML**

`test_the_leak_guard_actually_catches_a_leak` builds a synthetic prompt and asserts the guard fires. Add an HTML case to it so Step 4's manual proof is automated from now on:

```python
def test_the_leak_guard_catches_an_html_leak(tmp_path, monkeypatch):
    """The pipe self-test cannot see an HTML example; without this, Task 6's
    manual proof decays the moment someone edits the guard."""
    transcripts = tmp_path / "transcripts"
    transcripts.mkdir()
    leaked = "Kingsford Buckle 7mm"
    (transcripts / "CASE001_invoices.md").write_text(
        f"<table><tbody><tr><td>{leaked}</td></tr></tbody></table>\n", encoding="utf-8"
    )

    prompts = tmp_path / "config"
    prompts.mkdir()
    fence = "```"
    (prompts / "prompt.md").write_text(
        f"{fence}html\n<table><tbody><tr><td>{leaked}</td></tr></tbody></table>\n{fence}\n",
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(test_prompt_module, "TRANSCRIPTS", transcripts)

    with pytest.raises(AssertionError) as excinfo:
        test_prompt_module.test_no_prompt_example_leaks_corpus_content()

    assert leaked in str(excinfo.value)
```

Mirror however the existing `test_the_leak_guard_actually_catches_a_leak` obtains `test_prompt_module` and redirects `TRANSCRIPTS` — read that test first and follow it exactly rather than inventing a second harness. The `fence` variable avoids nesting triple backticks inside the f-string.

A `<th>` counterpart is worth adding too, asserting the guard does **not** fire — header vocabulary is exempt by design, and a test that pins the exemption stops someone "fixing" it later.

- [ ] **Step 6: Run the full suite**

Run: `conda run -n docparse pytest tests/ --ignore=tests/scoring -q`
Expected: PASS.

No commit — `tests/` is gitignored.

---

## Task 7: Add `transcript_sha256` to the manifest

`manifest_record` hashes only the image, so a corpus re-serialised into HTML carries byte-identical hashes to the pipe-table vintage. The §6.1 guard — written to make wrong-vintage scoring impossible rather than merely detectable — is blind to exactly this change.

**Files:**
- Modify: `generators/export.py` (`manifest_record:79-105`; README "Verify before you score" in `readme_text`)
- Test: `tests/test_export.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: manifest rows gain `"transcript_sha256": str`. The existing `"sha256"` key keeps its name and its meaning (the image), so no consumer breaks.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_export.py`:

```python
def test_the_manifest_hashes_the_transcript_as_well_as_the_image(tmp_path):
    """A serialisation-policy change moves no pixel, so an image hash cannot
    detect it. Without this, a scorer re-verifies every hash successfully and
    scores last vintage's predictions against this vintage's ground truth."""
    image = tmp_path / "CASE001_invoices.png"
    image.write_bytes(b"pretend png")
    transcript = tmp_path / "CASE001_invoices.md"
    transcript.write_text("# Invoice\n", encoding="utf-8")

    row = manifest_record(image, transcript, "invoices")

    assert row["sha256"] == sha256_of(image), "the image hash keeps its name and meaning"
    assert row["transcript_sha256"] == sha256_of(transcript)
    assert row["sha256"] != row["transcript_sha256"]


def test_changing_only_the_transcript_changes_the_manifest(tmp_path):
    """The exact scenario this closes: same image, re-serialised transcript."""
    image = tmp_path / "CASE001_invoices.png"
    image.write_bytes(b"pretend png")
    transcript = tmp_path / "CASE001_invoices.md"

    transcript.write_text("| Date | Amount |\n", encoding="utf-8")
    before = manifest_record(image, transcript, "invoices")
    transcript.write_text("<table><thead><tr><th>Date</th></tr></thead></table>\n", encoding="utf-8")
    after = manifest_record(image, transcript, "invoices")

    assert before["sha256"] == after["sha256"], "no pixel moved"
    assert before["transcript_sha256"] != after["transcript_sha256"], "the ground truth did"
```

- [ ] **Step 2: Run to confirm failure**

Run: `conda run -n docparse pytest tests/test_export.py -q -k transcript_sha256 or changing_only`
Expected: FAIL with `KeyError: 'transcript_sha256'`.

- [ ] **Step 3: Implement**

In `manifest_record`, extend the returned dict and its docstring:

```python
    return {
        "image": f"images/{image.name}",
        "transcript": f"transcripts/{transcript.name}",
        "doc_type": doc_type,
        "sha256": sha256_of(image),
        # The image hash cannot see a serialisation-policy change: re-emitting
        # every transcript moves no pixel, so a scorer holding last vintage's
        # predictions verifies every image hash and scores garbage. Hashing the
        # transcript closes that for this change and every future one.
        "transcript_sha256": sha256_of(transcript),
    }
```

Update the Returns line to `` `{"image", "transcript", "doc_type", "sha256", "transcript_sha256"}` per design §6.1. ``

- [ ] **Step 4: Update the shipped README**

In `readme_text`, the "Verify before you score" section — change the first line to:

```
Check every image against its `sha256` **and every transcript against its
`transcript_sha256`** in `manifest.jsonl` before scoring.
```

and add, after the existing paragraph:

```
The transcript hash matters independently. Re-emitting the corpus under a
changed serialisation policy moves no pixel, so every image hash still matches
while the ground truth has changed underneath. An image hash alone cannot see
that; the transcript hash can.
```

Also update the `manifest.jsonl` row in the "What is here" table to list `transcript_sha256`.

- [ ] **Step 5: Run the tests**

Run: `conda run -n docparse pytest tests/test_export.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
conda run -n docparse ruff check --fix --ignore ARG001,ARG002,F841 .
conda run -n docparse ruff format .
conda run -n docparse mypy generators --ignore-missing-imports
git add generators/export.py
git commit -m "✨ feat: hash the transcript in the manifest, not only the image

The manifest hashes the image because the image is what a model reads —
but that makes it blind to a serialisation-policy change, which moves no
pixel while replacing every transcript. A scorer holding predictions
against the previous vintage verifies every hash successfully and scores
garbage.

The HTML table change is exactly that case, so the guard that exists to
make wrong-vintage scoring impossible needs to see it."
```

---

## Task 8: Regenerate and verify the corpus

**Files:**
- No source changes. Produces a new export.

**Interfaces:**
- Consumes: everything above.

- [ ] **Step 1: Re-serialise into a scratch directory**

```bash
conda run -n docparse python -m generators.pipeline serialise --derived /tmp/htmltables/derived
```

Expected: succeeds, all 177 transcripts re-emitted.

- [ ] **Step 2: Confirm no image moved**

Run: `conda run -n docparse pytest tests/test_pipeline.py -q`
Expected: PASS, including `test_the_same_input_renders_byte_identical_images`.

- [ ] **Step 3: Export to a scratch target**

```bash
conda run -n docparse python -m generators.pipeline export \
  --derived /tmp/htmltables/derived --output /tmp/htmltables/out \
  --target /tmp/htmltables/exports
```

`--target` is mandatory. `--output`/`--derived` are *sources* for `export`; passing only those sends the deliverable into the real `exports_dir` while appearing sandboxed.

- [ ] **Step 4: Verify the invariant on real pages**

```bash
conda run -n docparse python - <<'PY'
import json, pathlib, re
root = sorted(pathlib.Path("/tmp/htmltables/exports").glob("parsing_*"))[-1]
bad = []
for line in (root / "manifest.jsonl").read_text().splitlines():
    row = json.loads(line)
    if "tables" not in row:
        continue
    transcript = (root / row["transcript"]).read_text()
    for table in re.findall(r"<table>.*?</table>", (root / row["tables"]).read_text(), re.DOTALL):
        if table not in transcript:
            bad.append(row["transcript"])
print("pages checked:", sum(1 for _ in (root / "manifest.jsonl").read_text().splitlines()))
print("MISMATCHES:", bad or "none")
PY
```

Expected: `MISMATCHES: none` across all 177 pages. This is the invariant the whole refactor exists to guarantee, checked on real data rather than fixtures.

Note: write this script to a file and run it with `python <file>` rather than pasting the heredoc, if your shell tooling cannot pass a heredoc through.

- [ ] **Step 5: Confirm images are unchanged against the shipped export**

```bash
diff -r /tmp/htmltables/exports/parsing_*/images \
        /Users/tod/Desktop/evaluation_data/parsing_20260826/images
```

Expected: no differences. Transcripts *will* differ — that is the point.

- [ ] **Step 6: Inspect one page against its transcript**

```bash
conda run -n docparse python -m generators.pipeline preview CASE001
```

Look at the rendered page beside its transcript. No field-level check catches a table that is well-formed but wrong; the visual check is not optional.

- [ ] **Step 7: Full suite, coverage, and final gates**

```bash
conda run -n docparse pytest tests/ --ignore=tests/scoring --cov=generators --cov-report=term
conda run -n docparse ruff check --fix --ignore ARG001,ARG002,F841 .
conda run -n docparse ruff format .
conda run -n docparse mypy generators --ignore-missing-imports
```

Expected: all pass, coverage ≥ 80%.

- [ ] **Step 8: Update the sprint summary**

`docs/standups/2026-08-26-sprint-summary.md` lists B1 as "In Progress" (it is complete — `9389b60` landed the twelve side-by-side-party invoices) and the format change as "Proposed". Move B1 to Completed and the format change to Completed, and note that B2 is now unblocked.

```bash
git add docs/standups/2026-08-26-sprint-summary.md
git commit -m "📝 docs: record the HTML table format change as shipped

B1 was already complete; the format change is now too, which unblocks
B2's spanning headers."
```

- [ ] **Step 9: Produce the real export**

Only after everything above is green. `export` creates `parsing_{date_stamp}` with `exist_ok=True` and plain `write_text`, so re-running it on the same calendar day as an existing export **silently overwrites that export in place**. Use `--date` for a distinct stamp, and confirm the target directory before running.

---

## Notes for the executor

- **The riskiest step is Task 4 Step 4.** Deleting `_render_table` and rewriting the walk in one edit is where a subtle behaviour change hides. If tests fail in a way you cannot explain, re-read `serialise.serialise`'s original table branches against `TableBuilder.feed` line by line rather than adjusting a test to match.
- **Task 6 has no commit and produces no source change**, but skipping it leaves a guard that passes vacuously — the failure CLAUDE.md calls worse than no test.
- **If the pre-commit hook blocks Task 4** because `tests/test_prompt.py` is red, merge Tasks 4 and 5 into a single commit. Never pass `--no-verify`.
- **Determinism is a contract.** If any image moves, stop — that is a corpus revision, and predictions already scored against it become invalid.
