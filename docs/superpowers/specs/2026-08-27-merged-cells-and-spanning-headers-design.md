# Merged Cells and Spanning Headers (B2) — Design

**Date:** 2026-08-27
**Status:** Approved, not yet implemented
**Sequencing:** B1 (done) → HTML tables (done) → **this**

---

## 1. Purpose

Add the two merged-cell constructs a real Australian business document uses, now
that the transcription format can express them:

- **Spanning headers (`colspan`)** — a tier above the column row grouping
  several columns under one heading.
- **Merged category labels (`rowspan`)** — one cell merged down the consecutive
  line items it covers.

Both were unimplementable against Markdown pipe tables at any cost: a pipe table
has no way to say a heading spans three columns, and any rule invented for it
would have been a convention no model has seen
(`docs/superpowers/2026-08-26-transcription-format-sequencing.md`). The HTML
format change removed the blocker; `colspan` and `rowspan` are native.

This is **additive**. Twelve new pages; the existing 177 stay byte-identical.

## 2. Decisions taken

Each closed off a cheaper-looking option, and the reasoning matters more than the
choice:

| Decision | Chosen | Rejected, and why |
|---|---|---|
| `rowspan`'s meaning | **A merged category label** over consecutive line items. | Using it for the grouped date collides with `carry_group_key: down`, which already answers that exact page feature by repeating the value. Two contradictory ground truths for one construct is the failure this architecture exists to prevent — and `carry_group_key` is *global* policy, so every table would be affected, not just new ones. |
| Corpus growth | **Additive** — 12 new pages, existing 177 untouched. | Retrofitting spanning headers onto shipped layouts moves pixels, which is a corpus revision in the expensive sense: predictions already scored become invalid and no re-scoring can recover them without re-inference. |
| Distribution | **6 bank statements (colspan), 6 invoices (rowspan).** | Each construct sits where a real document puts it. Concentrating both in one type makes the feature read as a synthetic quirk rather than a document convention. |
| Cell representation | **One `Cell` record** carrying text and both spans. | A parallel spans list reintroduces the two-structures-kept-in-step hazard the HTML change just removed — this time inside a single function. |

## 3. What the code already does

Worth stating, because it shrinks the work and because a reader would otherwise
assume otherwise:

- **Multi-row `<thead>` already renders.** `_check_header_shape` permits *any
  contiguous header prefix*, not just one row, and `_render` builds `head` from
  every `header=True` row. A two-tier header needs no change to row handling.
- **`emit` already takes arbitrary meta** (`emit(kind, text, *,
  category_type=None, **meta)`), so cells can carry spans with no signature
  change and no effect on the coverage invariant: a spanning label is still one
  `text()` draw behind one `cell` event.
- **`layout/*.json` needs no structural change.** The `table` annotation carries
  `html`, which picks up the attributes for free, and there are still no cell
  boxes — OmniDocBench has no `table_cell` category (§3.3 of the layout design).

**The actual obstacle is `pad_row`.** A spanning row carries *fewer* cells than
the table declares columns: a 4-column table's tier-1 header may be 3 cells, one
of them spanning 2. `pad_row` pads it to 4 and emits four `<th>` — silently
wrong, and an over-wide row raises outright. Padding must become span-aware.

## 4. DSL surface

Both constructs are declared in YAML and fully checkable before a pixel is drawn,
per `layout_dsl/schema.py`'s rule that everything a layout references must
resolve at validate time.

```yaml
# colspan: one tier above the column row.
header_groups:
  - {label: "",          span: [date]}
  - {label: Transaction, span: [description]}
  - {label: Amount,      span: [debit, credit, balance]}

# rowspan: merge runs of consecutive rows sharing this column's value.
row_span_key: category
```

**`span` names column keys, not counts.** `colspan` is derived as `len(span)`, so
a typo fails validation naming the unknown key rather than producing a table that
is well-formed and wrong. `row_span_key` must likewise name a declared column.

**Groups must tile the columns exactly** — every column covered once, in order,
no gaps and no overlaps. This is the invariant that keeps the header rectangular,
and it is checkable at validate time.

A spanning label centres over its span's x-range. An empty `label` is legitimate
(the `date` column above sits under no grouping) and emits an empty `<th>`,
keeping the tier rectangular without inventing text the page does not show —
the same reasoning as `headerless_table: empty_header_row`.

## 5. Event model and the table walk

Cells carry `colspan`/`rowspan` in event meta. Inside `TableBuilder`, a cell
stops being a bare string:

```python
class Cell(NamedTuple):
    """One table cell: its text and how far it spans."""

    text: str
    colspan: int = 1
    rowspan: int = 1
```

`rows` becomes `list[tuple[list[Cell], bool]]`. `carry_group_key_down` reads
`.text`. `_row` writes the attributes, **omitting them when 1**, so all 191
existing tables render byte-identical — that byte-identity is the regression
check for this whole change.

### 5.1 Rowspan occupancy is the part with real logic

A cell with `rowspan="3"` owns its column on the next two rows, and those rows
carry no cell for it. So a row is full when

```
sum(cell.colspan for cell in row) + inherited_occupancy == len(columns)
```

`TableBuilder` keeps a per-column countdown, decremented at each `row_close`.
Without it a following row looks short, gets padded, and every later column
shifts one place left — well-formed HTML stating the wrong thing, which no
existing test would catch.

This is the one piece of genuinely stateful logic in the change and gets tests of
its own: a rowspan run of two and of three, a rowspan in a middle column, two
rowspans overlapping in different columns, and a rowspan whose run reaches the
last row of the table.

### 5.2 Validation

`_check_header_shape` gains one rule: **every header tier must sum to the same
width.** A ragged tier is a table no metric can align, and it should fail at the
source with the four-element diagnostic rather than ship.

The existing over-wide-row check keeps its meaning under the new arithmetic: a
row whose spans exceed the column count still raises.

## 6. Prompt

`config/prompt.md` says nothing about spanning cells today because there was
nothing it could say. It gains a rule for each construct, with worked examples in
the shipped HTML style.

Every example value is **invented** and verified absent from the corpus before
shipping — not only the new ones. `_PROMPT_COVERAGE` in `tests/test_prompt.py`
gains an entry per construct so the prompt cannot silently stop stating a
convention the renderer applies. The leak guard already reads `<td>`, so it
covers the new examples without further change.

## 7. Corpus impact

- **Pixels move only on the 12 new pages.** `test_corpus_unchanged` and
  `test_the_same_input_renders_byte_identical_images` must stay green for the
  existing 177 — that is the additive claim, mechanically checked.
- **`tables/*.html` for existing pages must be byte-identical**, which is what
  proves omitting `colspan="1"`/`rowspan="1"` was done correctly.
- **Predictions already scored stay valid** for the 177 pages they cover. The
  new pages are new work, not a revision.
- Corpus becomes **189 pages**: 61 bank statements, 73 invoices, 55 receipts.

## 8. Testing

| Area | What is asserted |
|---|---|
| Span-aware padding | A row of 3 cells with one `colspan=2` fills a 4-column table; a row whose spans exceed the count raises with a four-element diagnostic. |
| Rowspan occupancy | Runs of 2 and 3; a middle column; two overlapping runs; a run reaching the final row. Each asserts the *following* rows' cells land in the right columns. |
| Ragged tiers | Header tiers of unequal width raise, naming both widths. |
| Byte-identity | The transcript's spanning table is byte-identical to its `tables/{stem}.html` — the invariant the shared walk exists to guarantee, now exercised on merged cells. |
| No-span regression | All 191 existing tables render byte-identical; no `colspan="1"` or `rowspan="1"` appears anywhere. |
| Schema | An unknown key in `span`, a `row_span_key` naming no column, and groups that leave a gap or overlap each fail validation with the four-element shape. |
| Visual | `preview` on one page of each new layout, checked against the render. No field-level check catches a table that is well-formed but wrong. |

## 9. Out of scope

- **`rowspan` for grouped dates.** `carry_group_key: down` remains the single
  answer to that construct (§2). Revisiting it is a change to a shipped
  convention and belongs in its own spec.
- Retrofitting spanning headers onto the existing 177 pages.
- Nested spans — a spanning header above another spanning header. Two tiers is
  what these documents use; a third is expressible in the same model if it is
  ever needed.
- The scoring repository's TEDS handling of merged cells. The interface is the
  exported corpus, not shared code.

## 10. References

- `docs/superpowers/specs/2026-08-26-html-tables-transcription-format-design.md`
  — the format change that unblocked this, and `TableBuilder`
- `docs/superpowers/2026-08-26-transcription-format-sequencing.md` — why B2 was
  blocked and why it is table-primitive work once the format changed
- `docs/superpowers/specs/2026-08-26-layout-and-structure-ground-truth-design.md`
  — §3.3 on why table cells get no boxes
- `docs/superpowers/specs/2026-08-26-side-by-side-party-blocks-design.md` — B1,
  the precedent for an additive corpus increment
