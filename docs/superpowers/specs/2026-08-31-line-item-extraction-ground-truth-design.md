# Line-item extraction ground truth

**Date:** 2026-08-31
**Status:** design, awaiting review

## 1. What this is

`extraction_<stamp>/ground_truth.{jsonl,csv}` emits **one row per document**: 189
rows, each carrying whole pipe-delimited lists in single cells. A real bank
export is **one row per transaction** — the CBA transaction history that prompted
this writes

```
28/08/2026,"-241.31","Direct Debit 485694 MEDIBANK PRIVATE","+1909.67"
```

one line per transaction, each carrying its own date, and its own balance.

This adds that grain: `line_items.jsonl`, one row per line item, alongside
the document-level file rather than replacing it.

### 1.1 The larger finding

The line-item explode is the small part. Specifying it surfaced that **the
running balance printed on every statement row is computed in Python**, not
authored: `generators/layout_dsl/providers.py` derives balances "from
ACCOUNT_BALANCE (the closing balance) by walking the transactions in reverse",
and its module docstring says outright that "balance and opening row exist
nowhere in ground truth".

That is a config value living in Python, which CLAUDE.md forbids: **YAML is the
single source of truth**. The balances are ink on the page and are scored; they
belong in `ground_truth/`. Authoring them is not a concession to this export — it
brings the corpus into compliance with a rule it already claims to follow, and
the export is what made the gap visible.

## 2. Decisions taken

| Decision | Chosen | Rejected, and why |
|---|---|---|
| What a row is | **Every line item, debits and credits alike**, carrying both amount columns. | Keeping only rows with a paid amount is what `eval_export.py` does for LMM_POC — "19 entries where the page has 27 transactions". Its own docstring calls that lossy and "the consumer's choice, not ours". Credit rows are on the page and in our ground truth. |
| Scope | **All three document types**, driven by the declared `parallel_field_groups`. | Bank-statements-only would force the type to be named in Python. Receipts and invoices already declare groups; the mechanism is type-agnostic and costs nothing extra. |
| Where the balance comes from | **Authored in `ground_truth/*.yml`.** | Capturing it from `events.jsonl` works and cannot drift, but makes `extract` depend on `generate` having run, and leaves the value un-authored. Re-deriving it inside the export would be a second implementation of the provider's arithmetic. |
| Who computes the balance after this | **Nobody. The provider reads the authored field.** | Authoring the values while the provider keeps computing them is two implementations of one truth, cross-checked at best. One source, or it is not a source. |
| File layout | **A new `line_items.jsonl` sibling.** | Replacing `ground_truth.*` discards fields with no per-line meaning (`SUPPLIER_NAME`, `BUSINESS_ABN`, `TOTAL_AMOUNT`) and breaks existing consumers. |
| Line-item format | **JSONL only, no CSV.** | A line-item row's columns differ per document type — a statement has `TRANSACTION_BALANCE`, an invoice `LINE_ITEM_QUANTITY`. CSV needs one fixed header, forcing either a sparse table implying a schema that does not exist, or a file per type. JSONL rows need not share keys. The document-level `ground_truth.csv` keeps its CSV: one fixed column set, existing consumers. |
| Column names | **Singularised via a declared mapping.** | A row reading `TRANSACTION_DATES: 01/09/2023` misstates its own grain. The mapping lives in `field_definitions.yml`; no name is invented in Python. |

## 3. Authored balances

### 3.1 The field

`TRANSACTION_BALANCES` joins the bank statement parallel group, as does
`TRANSACTION_AMOUNTS_RECEIVED`, which is already parallel in practice but not
declared:

```yaml
parallel_field_groups:
  BANK_STATEMENT:
    - [TRANSACTION_DATES, TRANSACTION_DESCRIPTIONS, TRANSACTION_AMOUNTS_PAID,
       TRANSACTION_AMOUNTS_RECEIVED, TRANSACTION_BALANCES]
```

Adding `TRANSACTION_AMOUNTS_RECEIVED` is free: all 61 statements already carry it
at the same length, verified — `validate` passes the moment it is declared.

### 3.2 The arithmetic, and the rule that enforces it

Verified against `CASE001`: walking backwards from `ACCOUNT_BALANCE` reproduces
the drawn values exactly, and the recovered opening balance (4224.77) is the one
the page prints as "Opening Balance $4,224.77".

Forward, the relation is:

```
balance[i] == balance[i-1] - paid[i] + received[i]
balance[last] == ACCOUNT_BALANCE
```

with `NOT_FOUND` reading as zero. The opening balance the synthetic leading row
prints is not an element of the list: it is `balance[0] + paid[0] - received[0]`,
the value the chain starts from.

This is enforced by a declared rule, the same shape as the existing
`gst_consistency`:

```yaml
balance_consistency:
  balances_field: TRANSACTION_BALANCES
  paid_field: TRANSACTION_AMOUNTS_PAID
  received_field: TRANSACTION_AMOUNTS_RECEIVED
  closing_field: ACCOUNT_BALANCE
  decimals: 2
```

Every key required; a missing one is a startup error with the four-element
diagnostic. `validate` fails on any statement whose chain does not close.

### 3.3 The backfill, and why pages cannot move

The 1,641 values are generated once **from the provider's current computation**,
so every authored value is by construction the number the renderer already draws.
The provider then reads the authored field instead of computing.

**`tests/test_corpus_unchanged.py` must stay green across the whole change.** If
any pixel moves, the backfill was wrong. That is a far better check than reading
1,641 numbers, and it is why the backfill and the provider switch belong in the
same change rather than separate ones.

The backfill script is a one-time tool, not shipped: it writes the YAML and is
then deleted. The authored values are the artifact.

## 4. The line-item projection

### 4.1 Mechanism

For each case, read the declared group for its document type, split each field on
`|`, and emit one row per index. `validate` already guarantees the lists are equal
length, so the exploder trusts that rather than re-checking.

The group key is the entry's **own authored `DOCUMENT_TYPE` field** — `validate`
already does exactly this at `generators/schema.py:317` (`doc_type =
fields.get("DOCUMENT_TYPE")`) before looking up `parallel_field_groups`. The
exploder follows that precedent, so no document type is named in Python and no
directory-name-to-type mapping is invented. (`document_type_values` is the list of
permitted values, not a mapping — it is not usable for this.)

### 4.2 Row shape

`case_id`, `doc_type`, `image`, `line_no`, then the singularised group columns.
`line_no` is 0-based in authored order, which is page order. `case_id` joins back
to `ground_truth.jsonl`.

```
case_id line_no TRANSACTION_DATE TRANSACTION_DESCRIPTION      ..._PAID  ..._RECEIVED ..._BALANCE
CASE001 0       01/09/2023       EFTPOS HARROWGATE B          328.15    NOT_FOUND    3896.62
CASE001 4       04/09/2023       SALARY PAYMENT               NOT_FOUND 906.72       4421.41
```

### 4.3 Column names

```yaml
line_item_column_names:
  TRANSACTION_DATES:            TRANSACTION_DATE
  TRANSACTION_DESCRIPTIONS:     TRANSACTION_DESCRIPTION
  TRANSACTION_AMOUNTS_PAID:     TRANSACTION_AMOUNT_PAID
  TRANSACTION_AMOUNTS_RECEIVED: TRANSACTION_AMOUNT_RECEIVED
  TRANSACTION_BALANCES:         TRANSACTION_BALANCE
  LINE_ITEM_DESCRIPTIONS:       LINE_ITEM_DESCRIPTION
  LINE_ITEM_QUANTITIES:         LINE_ITEM_QUANTITY
  LINE_ITEM_PRICES:             LINE_ITEM_PRICE
  LINE_ITEM_TOTAL_PRICES:       LINE_ITEM_TOTAL_PRICE
```

Every field in every declared group must appear here. A group field with no
mapping is a startup error, so a newly grouped field cannot ship un-named.

### 4.4 Sentinels and empty documents

`NOT_FOUND` is preserved per cell. On a credit row `TRANSACTION_AMOUNT_PAID` is
`NOT_FOUND`, which says exactly the right thing: no value in this column on this
row. That is also what keeps credit rows visible rather than dropped.

A document whose group fields are absent, or wholly `NOT_FOUND`, emits **zero
rows** — never one row of sentinels. No such document exists today (verified
across all 189); the rule keeps a future one honest.

Synthetic rows — the provider's Opening Balance and Carried forward — have no
authored counterpart and never appear. They are `row=None` in the event stream
and absent from the parallel lists.

## 5. Scale

| | documents | line-item rows |
|---|---|---|
| bank_statements | 61 | 1,641 |
| invoices | 73 | 224 |
| receipts | 55 | 184 |
| **total** | **189** | **2,049** |

## 6. Testing

- The chain rule rejects a statement whose balances do not close, with the
  four-element diagnostic, and accepts all 61 shipped statements.
- `test_corpus_unchanged` stays green through the backfill and the provider
  switch — the load-bearing check that no page moved.
- Exploding produces exactly 2,049 rows, and the per-type counts above.
- A credit row survives with `TRANSACTION_AMOUNT_PAID: NOT_FOUND`.
- A group field missing from `line_item_column_names` fails at startup.
- A document with no line items yields zero rows.
- Join integrity: every `case_id` in `line_items.jsonl` appears in
  `ground_truth.jsonl`, and every document-level row has line items or is
  legitimately empty.
- `extract` still runs without `generate` — the projection stays pure YAML.

## 7. Out of scope

- **Per-row balance for receipts and invoices.** There is none; the group differs
  per type and that is already handled.
- **The `$`-prefixing and credit-row dropping in `eval_export.py`.** A consumer's
  narrowing of our output, not ours to adopt.
- **Retiring `bank-statement-error-analysis`'s duplicate copy of
  `ground_truth/bank_statements.yml`.** This change makes that possible — that
  repo could consume `line_items.jsonl` instead of re-authoring — but it is a
  change to another repository and belongs in its own piece of work. It is worth
  doing: two copies of authored ground truth in two repositories is the drift
  this architecture exists to prevent.
- **An extraction prompt.** This corpus emits IE ground truth and runs no
  extractor (`2026-08-27-self-contained-corpus-repo-design.md` §2). Nothing here
  changes that.

## 8. References

- `generators/layout_dsl/providers.py` — the backward balance computation this
  replaces, and its docstring admitting balances "exist nowhere in ground truth".
- `config/field_definitions.yml` — `parallel_field_groups`, and `gst_consistency`,
  the precedent for `balance_consistency`.
- `generators/schema.py:317` — where `validate` takes the group key from an
  entry's own `DOCUMENT_TYPE`.
- `bank-statement-error-analysis`, `evaluation/eval_export.py` — the existing
  projection into LMM_POC's five-field schema, including `TRANSACTION_DATES`.
- The CBA transaction history that prompted this: date bands on screen, one row
  per transaction with a per-row balance in the CSV export.
