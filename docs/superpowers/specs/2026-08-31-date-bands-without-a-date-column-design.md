# Date bands without a date column

**Date:** 2026-08-31
**Status:** design, awaiting review
**Supersedes:** `2026-08-27-merged-cells-and-spanning-headers-design.md` §9's first
bullet, which placed this out of scope and said it "belongs in its own spec".
This is that spec.

## 1. What this changes and why

A real CBA transaction history has **no date column**. Its columns are
Description, Debit, Credit and Balance; the date appears once per day, as a band
spanning the table, with that day's transactions listed beneath it.

`cba_date_grouped` currently keeps a Date column whose cells are blank under
every band. That is a synthetic compromise, not a document convention, and it
exists only because `carry_group_key: down` needs a column to carry the date
into. The layout was built when the transcript format was pipe tables, which
could not express a band at all.

Two things have changed since. `colspan` arrived on 2026-08-27, so the format can
now say "this cell spans the row". And `group_date_format: long_au` (2026-08-31,
`1a14841`) made the band print the long Australian date a real statement uses.
What remains is the column itself.

### 1.1 The blocker this spec exists to resolve

Dropping the column breaks `carry_group_key_down`. It parks a band in `pending`
and waits for a row with a blank first cell to carry into; with no date column no
such row ever arrives, so `pending_used` stays `False` and the band is flushed
only when the next band appears or the table ends. **Bands are displaced** —
each lands after the transactions it heads, and the last lands at the end of the
table. Verified empirically:

```
INPUT                                   AFTER carry_group_key_down
  HDR [Description, Debit, ...]           HDR [Description, Debit, ...]
      [Sat 07 Oct 2023]                       [EFTPOS REGIONAL BUS, ...]
      [EFTPOS REGIONAL BUS, ...]              [DD GREENHALGH P, ...]
      [DD GREENHALGH P, ...]                  [Sat 07 Oct 2023]      <- moved
      [Mon 09 Oct 2023]                       [VISA DEBIT PURCHASE, ...]
      [VISA DEBIT PURCHASE, ...]              [Mon 09 Oct 2023]      <- moved
```

`carry_group_key` is *global* policy, so this cannot be fixed inside one layout.
That is why this needs a spec rather than an edit.

## 2. Decisions taken

| Decision | Chosen | Rejected, and why |
|---|---|---|
| The carry rule | **One rule, whose outcome depends on the table's shape.** A group key is carried onto the rows it heads; where the table has no column to carry it into, the band stands as its own full-width row. | A per-table opt-in (`band_row: keep`) would let a banded table *with* a date column be authored either way, reviving exactly what `2026-08-27` §2 refused: "two contradictory ground truths for one construct". Here the shape decides and no layout may choose, so there is still one answer per page shape. |
| The band's markup | **`<tr><td colspan="N">Sat 07 Oct 2023</td></tr>`**, N = the table's column count. | A bare `<tr><td>date</td></tr>` needs no scorer change, but stops the transcript recording that the date spans the table, and visibly breaks the prompt's "keep one cell per column on every row". |
| A band's cost in the table metric | **One cell.** `scoring/table_html.py` narrows its replication rule: a **partial** span replicates into every column it covers; a **full-width** span occupies one grid position. | Blanket replication charges 4 wrong cells for one misread date, weighting a band as heavily as a whole transaction row. The 2026-08-31 replication decision was not wrong, it was too broad — it was taken for column labels and silently inherited by section headers. |
| The rectangular-grid invariant | **Every row is either the grid's width, or a single cell that spanned the full width.** | Dropping the invariant loses the guard that catches span expansion going wrong, which is the specific bug it was written for. |

### 2.1 Why partial and full-width spans differ

A **partial** span is a column label. `<th colspan="3">Amount</th>` genuinely
labels Withdrawal, Deposit and Balance; a model that associates "Amount" with
only one of them has read the page less well, so replication measures something
real.

A **full-width** span is a section header. `<td colspan="4">Sat 07 Oct 2023</td>`
is one datum introducing a group. It occupies a row, not a set of columns. One
reading act, one cell charged.

The distinction is mechanical — `colspan == column count` — so no judgement is
needed at parse time.

## 3. The rule, stated once

> A group key is carried onto every row of the group it heads. Where the table
> has no column to carry it into, the band stands as its own row, spanning the
> full width.

A reader decides which limb applies by looking at the header row, which is
observable in both the image and the transcript. This is the whole of the
convention; `config/serialisation.yml`'s `carry_group_key` comment should be
rewritten around it.

## 4. What changes

### 4.1 `generators/tables.py`

`carry_group_key_down` gains a span-aware branch. A band whose single cell spans
the table's full width has nothing to carry into, so it is appended **in place**
rather than parked in `pending`. This fits the module's existing note that
"counting is span-aware throughout".

**This is behaviour-preserving for every layout that exists today.** All three
banded layouts (`_nab_classic_body`, `_nab_dense_body`, `cba_date_grouped`)
currently have a date column, so the new branch is unreachable until §4.3 lands.
A test must pin that.

### 4.2 `scoring/table_html.py`

`_close_cell` places a full-width span once instead of `colspan` times. Provably
a no-op on the current corpus: every colspan in all 189 transcripts is
`colspan="3"` in a 5-column table, so nothing existing is full-width.

`tests/scoring/test_tables_corpus.py`'s `ragged` check is restated per §2, and
its `cells_total` pin (10100) is unchanged today by construction.

### 4.3 `config/layouts/bank_statements.yml`

`cba_date_grouped` drops its `date` column. `description` moves from `x: 200` to
`x: 0`, widening by 200px, so `TRANSACTION_DESC` must be re-derived — `validate`
enforces fit budgets and will fail until it is. The band's emit currently takes
`column_key=str(columns[0]["key"])`, which becomes `description`; the plan must
decide whether a band's `column_key` should instead be `none`, since the band
belongs to no column.

The other banded layouts are untouched and keep their date columns.

### 4.4 `config/prompt.md`

The instruction becomes conditional. It currently says, unconditionally:

> put the date in the date cell of **every** row of that group, and do not give
> the date a row of its own

Both limbs of §3 must be taught, keyed to something the model can see: whether
the table has a date column. `tests/test_prompt.py` fails if prompt and policy
disagree, so this is not optional.

This is the change with the most risk in it. The prompt is the corpus's contract
with the systems being scored, and a conditional instruction is harder to follow
than an absolute one. The plan should treat the wording as a first-class
deliverable, not a footnote.

## 5. Blast radius

- **7 pages** — the `cba_date_grouped` cases (CASE007, 009, 022, 027, 038, 042,
  055). Their images change (a column disappears) and their transcripts change
  (bands become rows).
- **Every other page is unchanged**, and §4.1 and §4.2 are no-ops until §4.3
  lands. That ordering is worth keeping in the plan: policy and scorer first,
  each provably inert, then the layout that activates them.
- A corpus revision. The 2026-08-31 vintage was built at `23e80f0`; this earns a
  new one.

## 6. Testing

- `carry_group_key_down` keeps a full-width band in position, and still carries
  down when a date column exists (the existing behaviour, re-pinned).
- The displacement in §1.1 is gone — a band precedes its own transactions.
- A full-width span parses to one cell; a partial span still replicates.
- The `ragged` invariant accepts a band row and still rejects a row that is
  narrow because cells went missing.
- `cba_date_grouped` renders four columns, and `validate` passes on the
  re-derived budget.
- **Visual**: `preview` on one `cba_date_grouped` page against its render. No
  field-level check catches a table that is well-formed but wrong.
- Byte-identical re-render, and every non-CBA transcript unchanged.

## 7. Out of scope

- The other two banded layouts. NAB prints a date column; that is a real
  convention and it stays.
- Retrofitting bands onto layouts that do not group by date.
- Whether `extraction_*/ground_truth.{jsonl,csv}` should become one row per
  transaction. Real bank exports are; ours is one row per document. Unrelated to
  this change, and still unconsumed.
- Nested spans, still.

## 8. References

- `2026-08-27-merged-cells-and-spanning-headers-design.md` §2 (why `rowspan` does
  not mean a grouped date) and §9 (which deferred this).
- `config/serialisation.yml`, `carry_group_key` — the rationale corrected in
  `67b534e` to say the format constraint is gone and the one-ground-truth rule is
  what remains.
- `1a14841` — `group_date_format: long_au`, the band's display format.
- The CBA transaction history that prompted this: date bands reading
  "Fri 28 Aug 2026" over columns Description / Debit / Credit / Balance, with no
  date column.
