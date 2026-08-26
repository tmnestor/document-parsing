# Side-by-Side Party Blocks (Subsystem B1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the corpus pages that exercise the side-by-side reading
convention the shipped prompt already instructs and nothing currently tests.

**Architecture:** Two new invoice layouts place the supplier and payer blocks
in a two-column `split`, and twelve new authored ground-truth cases use them.
The `split` primitive, the `split_order: column_major` policy and the prompt
instruction all already exist; this adds only YAML.

**Tech Stack:** YAML only — `config/layouts/invoices.yml` and
`ground_truth/invoices.yml`. No Python.

**Spec:** `docs/superpowers/specs/2026-08-26-side-by-side-party-blocks-design.md`

## Global Constraints

- Conda environment is **`docparse`** (not `docparse-score`, not the global
  `du`). Run everything as `conda run -n docparse <command>`, **from the
  repository root** — several modules resolve config with CWD-relative paths.
- **No Python file may change.** If a task appears to need one, stop and report
  BLOCKED; that is a signal the design is wrong, not that the rule bends.
- **`config/prompt.md` and `config/serialisation.yml` must not change.** Both
  already say what is needed; changing either would re-vintage every transcript.
- **All 165 pre-existing pages must stay byte-identical.**
  `tests/test_corpus_unchanged.py` is the guard and must stay green.
- **Never run `generate` or `export` without passing `--output` AND `--derived`
  explicitly** unless the task says to write to the real corpus. A previous
  agent overwrote the shipped corpus by omitting them.
- Every primitive parameter needs an explicit value in the layout YAML
  (`generators/layout_dsl/defaults.py`); a missing one is a render-time error,
  not a fallback.
- Values drawn on the page and therefore scored must be **authored or derived**,
  never hardcoded into a layout — see the `layout_fields` note at the top of
  `config/field_definitions.yml`.
- **`tests/` is gitignored — never `git add` a test file.**
- **No `--no-verify`. No Claude attribution in commit messages.** Commit format:
  gitmoji + conventional type.

### Harness quirks

- **`cat` silently returns empty through the Bash tool** and exits 0 — an
  existing file reads as blank. Use the Read tool, or `head`/`sed -n`/`grep`.
- **Heredocs hang the Bash tool forever.** Never use `<<EOF`. Write files with
  Write/Edit.
- Prefix git commands with `GIT_PAGER=cat` and end the chain with `< /dev/null`.

---

## File Structure

| File | Change |
|---|---|
| `config/layouts/invoices.yml` | Add one budgets anchor, two party-block anchors, two layout entries. Existing anchors and layouts untouched. |
| `ground_truth/invoices.yml` | Add twelve cases, `CASE056`–`CASE067`. Existing 55 untouched. |

Nothing else changes.

---

## Task 1: The two layouts

**Files:**
- Modify: `config/layouts/invoices.yml`

**Interfaces:**
- Consumes: existing anchors `*invoice_page`, `*invoice_field_budgets`,
  `*invoice_field_providers`, `*invoice_defaults_values`,
  `*invoice_seller_name`, `*invoice_seller_address`, `*invoice_seller_abn`,
  `*invoice_bill_to`, `*invoice_payer_name`, `*invoice_payer_address`,
  `*invoice_buyer_gap`, `*invoice_date`, `*invoice_rule`, `*invoice_title`,
  `*invoice_line_items`, `*invoice_table_gap`, `*invoice_totals_separate`,
  `*invoice_payment_terms`.
- Produces: layout ids `tax_invoice_parties_split` and
  `tax_invoice_parties_ruled`, referenced by Task 2's cases.

**Why the budgets must be re-declared.** The existing party budgets declare
`width: 1700` because those blocks span the full content box. Inside an 820px
column that number is wrong: text is measured against a width it does not
have, so `shrink_then_wrap` will not shrink text that overflows. This renders
without raising and looks wrong, which is exactly the class of defect Task 3
exists to catch.

- [ ] **Step 1: Add the split-column budgets anchor**

Insert immediately after the existing `_invoice_field_budgets` anchor block
(which ends with the `LINE_ITEM_DESC` line). The merge key inherits every
existing entry and overrides only the four party widths; `ABN_LINE` is
overridden too because the ABN line also sits in a column.

```yaml
# The party blocks move into 820px columns in the two `parties_split` layouts,
# so their budgets cannot stay at the 1700px content width -- text would be
# measured against a box it does not occupy and `shrink_then_wrap` would not
# shrink an overflowing line. Everything else (fit, min_font, max_lines) is
# unchanged, and LINE_ITEM_DESC is inherited: the items table still spans the
# full page.
_invoice_split_field_budgets: &invoice_split_field_budgets
  <<: *invoice_field_budgets
  SUPPLIER_NAME: {width: 820, fit: shrink_then_wrap, min_font: 12, max_lines: 2}
  BUSINESS_ADDRESS: {width: 820, fit: shrink_then_wrap, min_font: 12, max_lines: 2}
  ABN_LINE: {width: 820, fit: shrink, min_font: 12, max_lines: 1}
  PAYER_NAME: {width: 820, fit: shrink_then_wrap, min_font: 12, max_lines: 2}
  PAYER_ADDRESS: {width: 820, fit: shrink_then_wrap, min_font: 12, max_lines: 2}
```

- [ ] **Step 2: Add the two party-block anchors**

Insert immediately after the existing `_invoice_buyer_gap` anchor. The children
reuse the existing seller and buyer anchors verbatim — same content, same
`field:` bindings, same `line_advance`, same `when:` guards — so the only thing
that differs from a stacked layout is where the ink lands.

`820 + 60 + 820 = 1700`, the invoice content width. `split_gap` defaults to `0`
for this layout, so the gap is declared on the block; `split_divider_color` is
already `"black"`.

```yaml
# The construct subsystem B1 exists to create. `config/prompt.md` already tells
# the model to read one column fully before starting the next, and
# `config/serialisation.yml` already sets `split_order: column_major` -- but no
# page in the corpus had two non-empty columns, so nothing tested either. The
# transcript reads the whole left block then the whole right block, which is
# textually identical to the stacked layouts: the difference is on the page.
_invoice_parties_split: &invoice_parties_split
  type: split
  widths: [820, 820]
  gap: 60
  children:
    - - *invoice_seller_name
      - *invoice_seller_address
      - *invoice_seller_abn
    - - *invoice_bill_to
      - *invoice_payer_name
      - *invoice_payer_address

# Identical but for the vertical rule down the gap. Separate from the plain
# variant so the rule's effect is attributable: a model may read column-major
# when a line marks the boundary and row-major when nothing does, and one
# layout carrying both cues could not distinguish those.
_invoice_parties_ruled: &invoice_parties_ruled
  type: split
  widths: [820, 820]
  gap: 60
  divider: true
  children:
    - - *invoice_seller_name
      - *invoice_seller_address
      - *invoice_seller_abn
    - - *invoice_bill_to
      - *invoice_payer_name
      - *invoice_payer_address
```

- [ ] **Step 3: Add the two layout entries**

Append to the `layouts:` mapping, after `tax_invoice_mixed`. The body is
`tax_invoice_standard`'s, with the three seller blocks and three buyer blocks
replaced by the single split block. Note `field_budgets` points at the new
anchor.

```yaml
  tax_invoice_parties_split:
    <<: *invoice_page
    field_budgets: *invoice_split_field_budgets
    field_providers: *invoice_field_providers
    defaults:
      <<: *invoice_defaults_values
    # tax_invoice_standard's body, with the six stacked party blocks replaced
    # by one two-column split.
    body:
      - *invoice_title
      - *invoice_rule
      - *invoice_parties_split
      - *invoice_buyer_gap
      - *invoice_date
      - *invoice_rule
      - *invoice_line_items
      - *invoice_table_gap
      - *invoice_rule
      - *invoice_totals_separate
      - *invoice_payment_terms

  tax_invoice_parties_ruled:
    <<: *invoice_page
    field_budgets: *invoice_split_field_budgets
    field_providers: *invoice_field_providers
    defaults:
      <<: *invoice_defaults_values
    # Identical to tax_invoice_parties_split but for the divider.
    body:
      - *invoice_title
      - *invoice_rule
      - *invoice_parties_ruled
      - *invoice_buyer_gap
      - *invoice_date
      - *invoice_rule
      - *invoice_line_items
      - *invoice_table_gap
      - *invoice_rule
      - *invoice_totals_separate
      - *invoice_payment_terms
```

- [ ] **Step 4: Validate**

Run: `conda run -n docparse python -m generators.pipeline validate`
Expected: `Validation passed.`

A malformed layout is the likeliest failure of a YAML-only change, and
`validate` catches it without rendering a pixel. If it reports an unknown field
or a missing default, fix the layout — do not add a Python default.

- [ ] **Step 5: Confirm the corpus has not moved**

Run: `conda run -n docparse python -m pytest tests/test_corpus_unchanged.py -q`
Expected: pass. No case names the new layouts yet, so nothing should render
differently.

- [ ] **Step 6: Commit**

```bash
GIT_PAGER=cat git add config/layouts/invoices.yml < /dev/null
GIT_PAGER=cat git commit -m "✨ feat: add side-by-side party-block invoice layouts" < /dev/null
```

---

## Task 2: Twelve new ground-truth cases

**Files:**
- Modify: `ground_truth/invoices.yml`

**Interfaces:**
- Consumes: layout ids `tax_invoice_parties_split` and
  `tax_invoice_parties_ruled` from Task 1.
- Produces: cases `CASE056`–`CASE067`, rendered by Task 3.

**Read `config/data_pools.yml` and the existing 55 entries before authoring.**
New values must look like they came from the same generator: same address
formats, same ABN spacing (`NN NNN NNN NNN`), same date format
(`DD/MM/YYYY`), same service-style line-item descriptions.

**Assignment:** `CASE056`–`CASE061` use `tax_invoice_parties_split`;
`CASE062`–`CASE067` use `tax_invoice_parties_ruled`. Six each.

**The arithmetic is not optional.** Verified across all 55 existing cases with
zero exceptions:

1. `LINE_ITEM_TOTAL_PRICES[i] == LINE_ITEM_PRICES[i] × LINE_ITEM_QUANTITIES[i]`
   for every item.
2. `sum(LINE_ITEM_TOTAL_PRICES) == TOTAL_AMOUNT − GST_AMOUNT`
3. `GST_AMOUNT == round(TOTAL_AMOUNT / 11, 2)`

`IS_GST_INCLUDED` is `'true'` on all 55; keep it `'true'`. Multi-value fields
are pipe-separated with no surrounding spaces. All values are strings.

**Vary the shapes deliberately** so the twelve are not twelve copies: item
counts between 1 and 5 across the set, at least two cases with a long
`SUPPLIER_NAME` or `PAYER_ADDRESS` that will test the 820px budget's wrapping,
and a spread of totals from two figures to five.

**Do not reuse an existing `(SUPPLIER_NAME, PAYER_NAME)` pair.**

- [ ] **Step 1: Author the twelve cases**

Append to `ground_truth/invoices.yml`. One complete worked example, to be
matched in shape by the other eleven:

```yaml
CASE056:
  layout: tax_invoice_parties_split
  fields:
    BUSINESS_ABN: '31 204 887 615'
    BUSINESS_ADDRESS: 88 Kingsford Smith Dr, Hamilton QLD 4007
    DOCUMENT_TYPE: INVOICE
    GST_AMOUNT: '86.24'
    INVOICE_DATE: 19/02/2024
    IS_GST_INCLUDED: 'true'
    LINE_ITEM_DESCRIPTIONS: Quarterly compliance audit|Document retention review
    LINE_ITEM_PRICES: 612.40|336.24
    LINE_ITEM_QUANTITIES: '1|1'
    LINE_ITEM_TOTAL_PRICES: 612.40|336.24
    PAYER_ADDRESS: 17 Rosebank Cr, Glenelg SA 5045
    PAYER_NAME: Marguerite Okafor
    SUPPLIER_NAME: Brightwater Advisory Partners
    TOTAL_AMOUNT: '1034.88'
```

Check it against the rules: `612.40 × 1 = 612.40`, `336.24 × 1 = 336.24`;
`612.40 + 336.24 = 948.64 = 1034.88 − 86.24`; `round(1034.88 / 11, 2) = 86.24`. ✓

- [ ] **Step 2: Verify the arithmetic across every new case**

Run this — it checks all 67 cases, so a mistake in an existing one would also
surface:

```bash
conda run -n docparse python -c "
import yaml
d = yaml.safe_load(open('ground_truth/invoices.yml'))
bad = []
for k, v in d.items():
    f = v['fields']
    tot = float(f['TOTAL_AMOUNT']); gst = float(f['GST_AMOUNT'])
    pr = [float(x) for x in str(f['LINE_ITEM_PRICES']).split('|')]
    qt = [float(x) for x in str(f['LINE_ITEM_QUANTITIES']).split('|')]
    lt = [float(x) for x in str(f['LINE_ITEM_TOTAL_PRICES']).split('|')]
    if not (len(pr) == len(qt) == len(lt) == len(str(f['LINE_ITEM_DESCRIPTIONS']).split('|'))):
        bad.append((k, 'ragged multi-value fields')); continue
    if any(abs(p*q - t) > 0.01 for p, q, t in zip(pr, qt, lt)):
        bad.append((k, 'price x quantity != line total')); continue
    if abs(sum(lt) - (tot - gst)) > 0.02:
        bad.append((k, f'sum(lt)={sum(lt):.2f} != tot-gst={tot-gst:.2f}')); continue
    if abs(gst - round(tot/11, 2)) > 0.02:
        bad.append((k, f'gst={gst} != tot/11={round(tot/11,2)}'))
print('cases', len(d), 'bad', len(bad))
for b in bad: print('  ', b)
"
```

Expected: `cases 67 bad 0`.

- [ ] **Step 3: Validate**

Run: `conda run -n docparse python -m generators.pipeline validate`
Expected: `Validation passed.` — this checks ground truth as well as layouts.

- [ ] **Step 4: Commit**

```bash
GIT_PAGER=cat git add ground_truth/invoices.yml < /dev/null
GIT_PAGER=cat git commit -m "✨ feat: add twelve invoices with side-by-side party blocks" < /dev/null
```

---

## Task 3: Render, inspect, and prove the corpus did not move

**Files:**
- No source changes expected. This task is the gate.

**Interfaces:**
- Consumes: Task 1's layouts and Task 2's cases.
- Produces: nothing consumed downstream.

**This task is not a formality.** No field-level check catches a layout that is
wrong but well-formed — a column overflowing its 820px, a payer block colliding
with the divider, an address shrunk to unreadability because a budget stayed at
1700. This repository's history is the argument: five separate times, work green
under its own tests misdescribed real output, and every catch came from looking
at what was actually produced.

- [ ] **Step 1: Render to a scratch directory first**

Never write to the real corpus while iterating.

```bash
conda run -n docparse python -m generators.pipeline generate --type invoices \
  --output /tmp/b1/out --derived /tmp/b1/derived
```

Expected: exits 0, renders 67 invoice pages.

- [ ] **Step 2: Serialise and read each new page against its image**

```bash
conda run -n docparse python -m generators.pipeline serialise \
  --derived /tmp/b1/derived
```

Then for **each** of `CASE056` … `CASE067`:

```bash
conda run -n docparse python -m generators.pipeline preview CASE056 \
  --derived /tmp/b1/derived
```

**Open the image.** Use the Read tool on the PNG path `preview` reports — it
renders images visually. For every one of the twelve, confirm and report:

- the two columns do not overlap, and neither overflows its 820px
- on `CASE062`–`CASE067`, the divider sits in the 60px gap, touching neither
  column
- no line has shrunk to an unreadable size, and no address is truncated
- the transcript reads **the whole left column, then the whole right column** —
  supplier name, address, ABN, then `Bill To:`, payer name, payer address
- the party text is identical to what the stacked layouts produce; only the
  page differs

If any of these fails, the fix is in `config/layouts/invoices.yml` — a width, a
gap, or a budget. Report what you saw before changing anything.

- [ ] **Step 3: Prove the 165 pre-existing pages have not moved**

```bash
conda run -n docparse python -m pytest tests/test_corpus_unchanged.py \
  tests/test_pipeline.py -q
```

Expected: pass. Adding layouts and cases must not perturb a single existing
page — each case names its own layout, so assignment is not positional.

- [ ] **Step 4: Full gates**

```bash
conda run -n docparse python -m pytest tests/ --cov=generators --cov-report=term
conda run -n docparse ruff check --fix --ignore ARG001,ARG002,F841 .
conda run -n docparse ruff format .
conda run -n docparse mypy generators --ignore-missing-imports
conda run -n docparse python -m generators.pipeline validate
```

Expected: suite passes with coverage at or above the 80% floor; ruff and mypy
clean; validation passes. Note one test is a deliberate `xfail(strict=True)`
for a recorded non-text-ink gap — that is expected, and the new dividers do not
change it, since they draw lines exactly as the existing `rule` blocks do.

- [ ] **Step 5: Confirm no Python changed**

```bash
GIT_PAGER=cat git diff --stat main...HEAD -- '*.py' < /dev/null
```

Expected: empty output. If any Python file appears, stop and report — the
design says a Python change means the design is wrong.

- [ ] **Step 6: Commit only if something needed fixing**

If Steps 1–5 required a layout correction, commit it. If nothing needed
changing, say so plainly rather than inventing a commit — the deliverable of
this task is the verification, and `/tmp/b1` is scratch that is not committed.

---

## Self-Review

**Spec coverage.**

| Spec section | Task |
|---|---|
| §3 the construct, column contents, `when:` guards | 1 |
| §3.1 two variants, divider attributable | 1 |
| §3.2 geometry (820/60/820) | 1 |
| §3.3 fit budgets re-declared | 1 |
| §3.4 field bindings unchanged | 1 |
| §4 existing corpus does not move | 1 Step 5, 3 Step 3 |
| §5 twelve authored cases, six per layout, arithmetic | 2 |
| §6 verification, visual check | 3 |
| §7 criteria 1–2 | 3 Steps 2–3 |
| §7 criterion 3 (no Python) | 3 Step 5 |
| §7 criterion 4 (prompt/serialisation untouched) | Global Constraints; 3 Step 5 covers Python, and neither file is touched by any step |
| §7 criteria 5–6 | 3 Step 2 |

No gaps.

**Placeholder scan.** No "TBD", no "add appropriate…", no "similar to Task N".
Task 2 gives one complete worked case plus three arithmetic rules and a
verification script that fails loudly — the remaining eleven are authored
against explicit, checkable constraints, not a vague instruction.

**Type consistency.** Anchor names used in Task 1's layout entries
(`*invoice_split_field_budgets`, `*invoice_parties_split`,
`*invoice_parties_ruled`) are exactly those defined in Steps 1 and 2. Layout
ids in Task 2 (`tax_invoice_parties_split`, `tax_invoice_parties_ruled`) match
Task 1's `layouts:` keys exactly. Field names match
`ground_truth/invoices.yml`'s existing entries.

**One risk worth stating.** `divider: true` draws a line, which is non-text
ink. Subsystem A's box-coverage invariant guards text draws only, and the
existing `rule` blocks already draw lines the same way, so the divider is not a
new class of ink. The recorded `xfail(strict=True)` mask test measures
*uncovered* non-text ink and is already expected to fail; more uncovered ink
keeps it failing, so it stays xfail rather than flipping to an unexpected pass.
