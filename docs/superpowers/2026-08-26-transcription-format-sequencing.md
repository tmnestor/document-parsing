# Transcription Format: Decision and Sequencing

**Date:** 2026-08-26
**Status:** agreed, not yet implemented
**Prompted by:** scoping subsystem B (structural realism), which exposed that
Markdown pipe tables cannot express a merged cell.

---

## The finding

Subsystem B has two halves, and they are not comparable in cost.

**B1 — side-by-side vendor/payer blocks — is pure YAML.** The `split`
primitive already supports multiple non-empty columns with explicit widths and
already emits `split_open` events (`generators/layout_dsl/primitives_container.py:108`).
Invoices simply never use it for real content: both existing `split` blocks in
`config/layouts/invoices.yml` have an empty left column and exist only to
right-align totals. No Python, no new transcription convention.

**B2 — `colspan`/`rowspan`/spanning headers — is blocked on the output
format.** Markdown pipe tables have no way to say that a header spans three
columns. `config/prompt.md` says nothing about spanning cells because there is
nothing it could say. Any rule we invented — repeat the value across the spanned
columns, or fill the first and blank the rest — would be a convention no model
has seen, and it would put the Markdown and HTML projections in permanent
disagreement about a page feature. That is the risky class of change this
architecture exists to isolate.

**Read the other way round, this is not a constraint on B2. It is evidence that
the output format is the thing to fix.**

---

## The decision

**Move the transcription target to Markdown with HTML tables** — not to HTML
wholesale.

Reasons, in order of weight:

1. **It is the field convention, not an invention.** OmniDocBench's ground
   truth is Markdown for text with an `html` field for tables. MinerU and
   Docling both emit Markdown with HTML tables where structure demands it.
2. **The corpus already generates it.** Subsystem A ships `tables/*.html`,
   authored at draw time from the same event stream that produces the Markdown.
   The HTML ground truth for every table on all 165 pages exists today.
3. **It dissolves B2's blocker.** `colspan`/`rowspan` are native to HTML. No
   invented convention, and no projection disagreement.
4. **TEDS becomes directly applicable**, which is the comparability other teams
   reference.
5. **Full HTML buys nothing for prose.** Wrapping paragraphs in `<p>` costs
   tokens and readability for no expressive gain. Tables are the only place
   Markdown is actually inadequate.

**The architecture was built for this move.** `serialise` is a pure function of
`events.jsonl` plus `config/serialisation.yml`, so changing the convention
re-emits all 165 transcripts in seconds **without re-rendering a single image**.
No pixel moves; this is not a corpus revision in the expensive sense. It is the
operation the three-stage split exists to make cheap.

### Costs, recorded rather than glossed

- **Tokens.** Measured on `CASE002_bank_statements.md`: the table as Markdown
  is 2,899 characters, as HTML 4,225 — **1.46×**. On a bank statement, ~91% of
  which is table, that is roughly 40% more tokens per page for the downstream
  text-only LLM.
- **`scoring/tables.py` parses pipe tables.** An HTML variant of `parse_tables`
  is needed. That is *one function*: `align_rows`, `compare_row` and
  `score_tables` are untouched. A clean seam, and a more robust parser than
  pipe-splitting.
- **`scoring/normalise.py`'s `strip_markdown`** needs HTML handling.
- **`config/prompt.md` needs rewriting**, and models must follow the new
  instruction — a real source of variance. OmniDocBench sidesteps this by
  accepting either form; we may need to as well.
- **Transcripts change**, so predictions scored against the current transcripts
  become invalid. Softer than a pixel revision, but real.

---

## The sequencing

1. **B1 now.** Pure YAML, independent of everything above, and it delivers the
   construct the previous increment's follow-ups name as highest-value: the
   vendor-left/payer-right convention that competent models genuinely disagree
   about.
2. **The format change as its own spec.** It alters the benchmark's output
   contract, which is a larger decision than B2 and must not be smuggled in
   underneath it.
3. **B2 after the format change.** Once tables are HTML, B2 is table-primitive
   work — merged cells in `primitives_table.py` and their events — with no
   convention to invent and no projection left in disagreement.

**The point of this order:** B2 never forces anyone to invent something
Markdown cannot say.

---

## Constraints the format spec must inherit

From the cell-aligned metric's follow-ups
(`docs/superpowers/2026-08-26-cell-aligned-table-metric-follow-ups.md` §3),
because they bind whatever the format becomes:

- **If CER is ever restricted to non-table text, it must ship together with or
  after the diagnostic counts reaching the report** — never before. Today
  `normalised_cer` is the only reported signal that catches invented table
  rows, and it works precisely *because* it sees table content.
- **Per-column error rates must key off the reference header only.** If
  prediction headers participate, a model can relabel columns to shift errors
  out of the money column.
- Emphasis stays out. `config/serialisation.yml` sets `emphasis: none`
  deliberately — bold detection measures style recognition, not reading — and
  HTML invites style markup. The format spec needs a strict element subset.
