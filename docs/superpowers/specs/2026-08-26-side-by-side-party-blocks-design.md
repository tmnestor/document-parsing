# Side-by-Side Party Blocks (Subsystem B1) — Design

**Date:** 2026-08-26
**Status:** approved for planning
**Source:** `docs/superpowers/2026-08-26-layout-ground-truth-follow-ups.md` §5,
subsystem B; scoped and sequenced by
`docs/superpowers/2026-08-26-transcription-format-sequencing.md`.

---

## 1. Why

`config/prompt.md:122-125` already instructs the model:

> **Where a page is laid out in side-by-side columns, read one column fully
> before starting the next, working left to right.** Do not read across the
> page in [rows] … right is transcribed as all of the left block, then all of
> the right block.

`config/serialisation.yml` already sets `split_order: column_major`.
`serialise.py` validates it. `draw_split`
(`generators/layout_dsl/primitives_container.py:108`) implements it, walking
columns in DSL order and emitting `split_open` / `column_open` events.

**And no page in the corpus exercises any of it.** The only two `split` blocks
in `config/layouts/invoices.yml` (`:181`, `:196`) have an **empty left column**
and exist to place a fixed 400px totals column at the right edge. Every party
block on all 55 invoices is stacked vertically: supplier, then `Bill To:`,
then payer.

So the corpus ships an instruction, a policy setting and a renderer for a
convention that nothing tests. This is the same shape as the "leave blank cells
blank" instruction that the cell-aligned table metric was built to score — a
rule a model can ignore with no consequence, because no page can tell.

The previous increment's follow-ups name this construct specifically:
vendor-left/payer-right is "the one convention competent models genuinely
disagree on," and it occurs in real Australian tax invoices.

**B1 gives the existing convention something to bite on. It introduces
nothing new.**

---

## 2. Scope

**In:** two new invoice layouts that place the supplier and payer blocks side
by side, and new authored ground-truth cases that use them.

**Out, deliberately:**

- **`colspan` / `rowspan` / spanning headers (subsystem B2).** Blocked on the
  output format: Markdown pipe tables cannot express a merged cell. Sequenced
  after the format change, per
  `docs/superpowers/2026-08-26-transcription-format-sequencing.md`.
- **Any change to the existing 18 layouts.** See §4.
- **Any Python change.** If this spec turns out to need one, that is a signal
  the design is wrong, not that the constraint should be relaxed.
- **Any change to `config/prompt.md` or `config/serialisation.yml`.** Both
  already say what is needed. Changing either would alter the transcription
  convention for the whole corpus, which is out of scope here and would make
  every existing transcript a new vintage.

---

## 3. The construct

Today each invoice draws the two parties stacked
(`config/layouts/invoices.yml:102-124`):

```
Coastal Plumbing Pty Ltd
14 Wharf Rd, Fremantle WA 6160
ABN: 57 773 872 148

Bill To:
Robin Wood
238 Wolfe Ave, Unley SA 5061
```

The new layouts draw them side by side:

```
Coastal Plumbing Pty Ltd          Bill To:
14 Wharf Rd, Fremantle WA 6160    Robin Wood
ABN: 57 773 872 148               238 Wolfe Ave, Unley SA 5061
```

Under `split_order: column_major` the transcript reads the whole left block,
then the whole right block — identical in content and order to today's stacked
form. **That is the point.** A model that reads across the page produces
`Coastal Plumbing Pty Ltd Bill To:` and is wrong in a way the corpus can now
measure; today it would be wrong in a way nothing can see.

### 3.1 Two layouts, not one

- **`tax_invoice_parties_split`** — two columns, no divider.
- **`tax_invoice_parties_ruled`** — the same construct with `divider: true`,
  which `draw_split` renders as a vertical rule down the gap using
  `split_divider_color`.

The variants are separate because a ruled separator is a visual cue that may
change model behaviour: a model may read column-major when a line tells it
where the column ends and row-major when nothing does. Confounding the two
would leave that indistinguishable. Everything else about the two layouts is
identical, so the difference in scores is attributable to the rule alone.

### 3.2 Geometry

Invoices use `margin: 100` and `content_width: 1700`
(`config/layouts/invoices.yml:15-16`). The party split divides that as:

```yaml
widths: [820, 820]   # 820 + 60 gap + 820 = 1700
gap: 60
```

`split_gap` defaults to `0` for this layout (`:85`), so the gap is declared on
the block. `split_divider_color` is already `"black"` (`:86`).

### 3.3 Fit budgets must be re-declared

This is the one non-obvious requirement. The existing party budgets declare
`width: 1700` (`:25-29`) because the blocks span the full content width. In a
820px column that budget is wrong — text would be measured against a width it
does not have, and `shrink_then_wrap` would fail to shrink text that overflows.

The new layouts therefore declare their own budget entries at the column
width, keeping the existing `fit`, `min_font` and `max_lines` values so the
only thing that changes is the width. The existing entries are left untouched,
because the existing layouts still use them.

Every primitive parameter needs an explicit value in the layout YAML
(`generators/layout_dsl/defaults.py`); a missing one is a render-time error,
not a fallback.

### 3.4 What the columns contain

Left column, from the existing supplier block (`:102-109`), unchanged in
content, `field:` bindings and `line_advance` values:

- `{SUPPLIER_NAME}` — `field: SUPPLIER_NAME`
- `{BUSINESS_ADDRESS}` — `field: BUSINESS_ADDRESS`
- `ABN: {BUSINESS_ABN}` — `field: BUSINESS_ABN`

Right column, from the existing payer block (`:117-123`), likewise unchanged:

- `Bill To:` — gray label, `when: PAYER_NAME`
- `{PAYER_NAME}` — `field: PAYER_NAME`, `when: PAYER_NAME`
- `{PAYER_ADDRESS}` — `field: PAYER_ADDRESS`, `when: PAYER_ADDRESS`

The `when:` guards are carried across deliberately. The existing layout guards
the payer address on `PAYER_NAME` as well as its own field
(`:113-116`), and that reasoning is unchanged by moving the block into a
column.

No new field definitions are needed: every field already exists in
`config/field_definitions.yml` and every one of the 55 existing invoices
carries both parties.

---

## 4. The existing corpus does not move

Each ground-truth case names its own layout — `ground_truth/invoices.yml`
entries carry a `layout:` key read at `generators/invoice.py:53`. Layout
assignment is **not** positional, so adding layouts cannot reshuffle existing
cases.

**All 165 existing pages therefore stay byte-identical**: images, transcripts,
`layout/*.json` and `tables/*.html` alike. `tests/test_corpus_unchanged.py`
is the guard and must stay green throughout.

The corpus grows rather than changes. `manifest.jsonl` gains rows, so its
hash changes and the scoring vintage guard will flag predictions made against
the old manifest — correctly, since the corpus is not the same set. But every
page that existed before is provably unchanged, so page-level results remain
comparable, which the alternative (rewriting the four existing invoice
layouts) would not preserve.

---

## 5. New cases

**Twelve new authored entries** in `ground_truth/invoices.yml`, six naming
each new layout. Twelve is chosen to be enough to score meaningfully while
remaining small enough to author carefully and inspect individually against
its rendered page.

Field values are authored in the same shape as the existing 55, drawing on
`config/data_pools.yml` for names, addresses and business details. Values must
be internally consistent — `TOTAL_AMOUNT`, `GST_AMOUNT` and the line-item
figures must agree, because `computed_totals` and the scoring both read them.

Case ids continue the existing sequence rather than interleaving, so the new
pages are identifiable without consulting the layout — which the filename
deliberately does not reveal (`{case_id}_{doc_type}`, never
`{case_id}_{layout_id}`).

---

## 6. Verification

`validate` catches a malformed layout without rendering, and is the likeliest
failure mode of a YAML-only change.

Then, for **every** new case: `generate --type invoices`, and
`preview <CASE_ID>` to read the transcript beside its image.

**The visual check is not optional.** No field-level check catches a layout
that is wrong but well-formed — a column that overflows its 820px, a payer
block that collides with the divider, an address that shrinks to unreadability
because a budget was left at 1700. This repository's own history is the
argument: five separate times, work that was green under its own tests
misdescribed real output, and every catch came from looking at what was
actually produced.

Specifically confirm on the rendered pages:

- the two columns do not overlap, and neither overflows its width
- the divider in `tax_invoice_parties_ruled` sits in the gap, touching neither
  column
- the transcript reads the whole left column before the whole right column,
  matching `split_order: column_major` and `config/prompt.md:122-125`
- the transcript's party content is **identical in text and order** to what a
  stacked layout produces, so the only difference is on the page, not in the
  ground truth

And confirm across the corpus:

- `tests/test_corpus_unchanged.py` passes — all 165 pre-existing pages
  byte-identical
- the full suite passes, and `validate` is clean

---

## 7. Success criteria

1. Two new invoice layouts render side-by-side party blocks, and twelve new
   cases use them.
2. Every one of the 165 pre-existing pages is byte-identical — images,
   transcripts, `layout/*.json`, `tables/*.html`.
3. No Python file changes.
4. `config/prompt.md` and `config/serialisation.yml` are untouched.
5. Each new page has been read against its transcript by a human or an agent
   that reports what it saw, not merely rendered without error.
6. The new transcripts' party sections are textually identical to the stacked
   equivalent — the construct differs on the page, not in the ground truth.
