# Cell-Aligned Table Metric — Design

**Date:** 2026-08-26
**Status:** approved for planning
**Source:** `docs/OmniDocBench_notes.md`, §"Recommendation: add a structural
metric", Option 1.

---

## 1. Why

`scoring/` reports two numbers today. NORMALISED measures reading; STRICT
measures reading plus markup adherence. Neither measures **placement**.

The probe recorded in `docs/OmniDocBench_notes.md` is the finding. Against
`CASE002_bank_statements.md`, every amount in the Debits column was moved to
Credits — a sign error on all 27 transactions:

| Perturbation | normalised CER | normalised WER | strict CER |
|---|---|---|---|
| 27 amounts misfiled Debits → Credits | **0.000000** | **0.000000** | 0.053465 |
| One-character typo (`FORWARD` → `FORWORD`) | 0.000401 | 0.002825 | 0.000330 |

The misfile is not under-weighted. It is **invisible**: `normalise()` under
`strip_markdown` substitutes every pipe with a space, so `$590.52 |` and
`| $590.52` reduce to the same token stream. STRICT sees *something*
(0.053465), but cannot distinguish a sign error from a model that merely
formats its pipe tables differently, so its signal cannot be acted on.

Our three document types are table-dominated; a bank statement is essentially
one long table. This is the central failure mode of the corpus, unscored.

**There is a second, sharper reason.** `config/prompt.md:81-83` already
instructs:

> Keep one cell per column on every row. Where a cell is blank on the page,
> leave it blank in the table rather than dropping it or shifting the other
> cells across.

Nothing scores that instruction. A model that ignores it is currently
indistinguishable from one that follows it. This metric is that instruction's
scorer.

**Requirement, stated plainly:** correct values in the wrong cell must be
penalised.

---

## 2. Scope

**In:** a per-page structural metric over pipe tables, computed from the
reference transcript and the model's prediction, reported alongside the
existing edit distances.

**Out, deliberately:**

- **TEDS.** Comparability with public leaderboards is real but secondary, and
  `apted`'s licence is unconfirmed (`docs/OmniDocBench_notes.md`, §Licensing).
  Cell alignment is the diagnostic metric; TEDS is the legibility metric. This
  spec builds the diagnostic one. TEDS remains available later and this design
  does not foreclose it.
- **A separate reading-order metric.** Named in the notes' secondary
  observations; a distinct increment.
- **Reading the authored `tables/*.html`.** See §3.1.
- **Changing any existing metric.** NORMALISED and STRICT keep their present
  definitions exactly. This adds a third number; it replaces nothing.

---

## 3. The metric

### 3.1 One parser, both sides

The grid is parsed from the **reference Markdown transcript**, not from the
authored `tables/*.html` this repository now exports.

Two reasons, and the first is decisive. Predictions arrive as Markdown, so the
prediction side must be parsed regardless. Parsing the reference by a second,
different route means a structural disagreement could originate in the
asymmetry between two parsers rather than in the model — and every drift found
on the layout-ground-truth branch was exactly that: two implementations of one
convention, kept in step by hand until they weren't
(`docs/superpowers/2026-08-26-layout-ground-truth-follow-ups.md` §4).

Second, `score_page` stays a pure function of two strings plus policy, with no
filesystem access, and the metric runs against the already-shipped
`parsing_20260825` corpus, which predates `tables/` and will not be re-exported.

### 3.2 Finding tables

A **table line** is a line whose stripped form begins with `|`.
A **table block** is a maximal run of consecutive table lines.

Within a block, a **separator row** is a row whose cells are all empty or
consist solely of `-` and `:` characters. Separator rows are discarded; they
carry no content. Every other row is a data row, the header row included.

Cells are extracted by stripping the line, removing one leading and one
trailing `|` when present, splitting on `|`, and stripping each cell.

**The empty header row is kept, not stripped.** `config/serialisation.yml`
sets `headerless_table: empty_header_row`, so a reference table that drew no
header on the page begins `|  |  |`. `config/prompt.md:86-95` instructs the
model to emit exactly that form, and a real transcript
(`CASE001_receipts.md:15`) confirms the two agree byte for byte. It is
instructed output, not an artifact — a model that omits it is genuinely
non-compliant, and stripping it would hide that.

### 3.3 Pairing tables within a page

Reference and prediction tables are paired by position in document order:
reference table *k* to prediction table *k*. An unpaired reference table
contributes its rows to `table_rows_missing` and its cells to the denominator
as incorrect, exactly as a dropped row does — a model that omits a whole table
must not score better than one that omits its rows one at a time. An unpaired
prediction table contributes its rows to `table_rows_spurious` and nothing
else.

Positional pairing matches the convention already used for multi-table pages
elsewhere in this project (`tables/*.html` holds a page's tables in page
order).

### 3.4 Aligning rows

Rows are **sequence-aligned before any cell is compared**, so that a dropped
row costs one row rather than cascading into every row after it.

Each row is reduced to a signature: its cells joined by `\x1f`, then passed
through `normalise()` under the configured comparison form (§5). Alignment is
`rapidfuzz.distance.Levenshtein.opcodes` over the two signature lists, which
accepts sequences of strings. Verified in `docparse-score`: for reference
signatures `[header, FEE|200, CHQ|300]` against a prediction that dropped the
first row, it returns `delete(0,1)` followed by `equal(1,3 → 0,2)` — exactly
the pairing this metric needs.

Opcodes map to outcomes:

| Tag | Meaning |
|---|---|
| `equal` | rows pair index-for-index |
| `replace` | the two ranges are zipped; where they differ in length, surplus reference rows count missing and surplus prediction rows count spurious |
| `delete` | reference rows with no counterpart → `table_rows_missing` |
| `insert` | prediction rows with no counterpart → `table_rows_spurious` |

**Alignment decides pairing, not the denominator.** These are two independent
choices and conflating them is a trap. Alignment exists so that one dropped row
costs one row rather than cascading into every row after it. The denominator is
a separate question, answered in §3.5: **every reference cell counts**,
including the cells of rows the prediction dropped, which count as incorrect.

The alternative — counting only matched cells — was rejected. It makes the
metric read "of what you transcribed, how much landed correctly?", under which
a model that transcribes 3 rows of a 27-row statement and drops the other 24
scores a **perfect** cell error rate. `table_rows_missing` would sit beside it
saying 24, but the headline number would be actively misleading rather than
merely partial. That is unlike the existing NORMALISED/STRICT pair, where each
number is individually honest about a different question.

The two denominators are **identical whenever the prediction drops no rows**.
They diverge only in the case the matched-only form is blind to, so counting
every reference cell costs nothing on well-behaved output and closes the hole.
A dropped row still costs exactly its own cells — five on a five-column
statement — not every row after it, so the anti-cascade property alignment was
chosen for is fully preserved.

### 3.5 Comparing cells

Within a matched row pair, both rows are padded with empty cells to the
reference row's width. Each position is compared after `normalise()` under the
configured form.

- `table_cells_compared` counts **every reference cell on the page** —
  those in matched rows, those in rows the prediction dropped, and those in
  reference tables the prediction produced no counterpart for — and it counts
  cells the reference leaves empty.
- `table_cells_correct` counts positions in matched rows that compare equal. A
  cell in a dropped row can never be correct.

Spurious rows the model invents contribute to `table_rows_spurious` and to
nothing else. There is no reference cell to compare them against, and adding
them to a reference-cell denominator would be incoherent.

Counting empty reference cells is the crux of the whole design. In the probe,
a debit row carries an empty Credits cell; the misfile empties Debits and
fills Credits. Comparing empty cells makes that **two** wrong cells. Skipping
them would reproduce exactly the blindness this metric exists to remove.

### 3.6 Naming the failure: misplacement

For each incorrect position *j* whose reference cell is non-empty, if that
reference value appears at any other position in the same predicted row, the
cell is additionally counted in `table_cells_misplaced`.

`table_cells_misplaced` is a **diagnostic subset of the incorrect cells**, not
a separate bucket: a misplaced cell is already counted as incorrect. It exists
to distinguish "the model could not read this value" from "the model read this
value and filed it in the wrong column" — the distinction the notes identify
as the one STRICT cannot make.

Lookup is confined to the same row. A value that migrates to a different row
is a structural failure that row alignment already reports.

---

## 4. What lands on a scored row

| Field | Type | Meaning |
|---|---|---|
| `table_cell_error_rate` | float \| None | `(compared - correct) / compared`; `None` when the reference has no table cells |
| `table_cells_compared` | int | every reference cell on the page, dropped rows included |
| `table_cells_correct` | int | positions in matched rows that compared equal |
| `table_cells_misplaced` | int | incorrect cells whose value sits elsewhere in the same row |
| `table_rows_missing` | int | reference rows with no prediction counterpart |
| `table_rows_spurious` | int | prediction rows with no reference counterpart |
| `table_count_ref` | int | tables found in the reference |
| `table_count_pred` | int | tables found in the prediction |

**An error rate, not an accuracy.** `report.py` reports percentiles where p100
is documented as the worst case — true for CER, where higher is worse. An
accuracy inverts that silently, making p100 the *best* page under a column
header that says worst. Reporting `table_cell_error_rate` keeps every existing
convention in `report.py` correct without modification.

When a prediction is absent, **all eight fields are `None`** — including the
counts, and including `table_count_ref`, which is computable but would invite a
reader to treat the row as scored. This matches the shape `score_page` already
uses, where every distance is `None` for an absent prediction so that an absence
is carried in the data rather than dropped or silently read as zero.

`scoring/report.py`'s `_METRICS` gains `table_cell_error_rate`. `aggregate`
already skips `None` values per metric and yields `None` for an empty sample,
so a page with no table needs no special handling. All 165 shipped transcripts
contain at least one pipe table (verified), so on this corpus the field is
never `None`; the handling exists for other corpora.

---

## 5. Configuration

`config/scoring.yml` gains one section. Every key is required; the loader fails
fast on omission, per this repository's rule that operator intent must be
visible in the YAML.

```yaml
tables:
  # Which text form cell equality uses. `normalised` runs each cell through the
  # normalisation section above before comparing, so a curly quote or an en dash
  # inside a cell is not scored as a placement error. `strict` compares raw cell
  # text. Allowed: normalised | strict.
  cell_comparison: normalised
```

That is the only genuine operator choice in the design, so it is the only key.
Everything else in §3 is a correctness property, not a preference, and is fixed
in code with its reasoning in a comment — the same split `scoring/normalise.py`
already documents, where which steps run is policy but the order they run in is
correctness.

`scoring/policy.py` validates the section: presence, and `cell_comparison`
against its allowed values, with a four-element diagnostic naming the file, the
dotted key path, the allowed values and the remedy.

---

## 6. Files

**Create `scoring/tables.py`** — pure, no filesystem, no CLI:

- `parse_tables(text: str) -> list[list[list[str]]]` — a page's tables, each a
  list of rows, each a list of cell strings, separator rows removed
- `align_rows(ref, pred, policy) -> tuple[list[tuple[list[str], list[str]]], int, int]`
  — matched pairs, missing count, spurious count
- `compare_row(ref_cells, pred_cells, policy) -> tuple[int, int, int]` —
  compared, correct, misplaced
- `score_tables(reference: str, prediction: str, policy: dict) -> dict` — the
  eight fields of §4

**Modify `scoring/score.py`** — `score_page` calls `score_tables` and merges
its fields. Signature unchanged; purity preserved.

**Modify `scoring/policy.py`** — validate the `tables` section.

**Modify `config/scoring.yml`** — add the section, with its comment.

**Modify `scoring/report.py`** — add `table_cell_error_rate` to `_METRICS`.

The module boundary matters: `scoring/tables.py` shares no code with
`generators/tables.py`. The generator writes authored HTML at export time; this
reads Markdown at score time. They serve different sides of the interface and
must not be coupled.

---

## 7. Failure behaviour

The metric is computed over model output, which is arbitrary text. It must not
raise on malformed input — a model emitting nonsense is a result to record, not
an error to crash on. A prediction with no table lines yields
`table_count_pred: 0` and every reference row missing.

Fail-fast applies to **configuration**, not to prediction content:
a missing or invalid `tables` key raises a four-element diagnostic at policy
load, before any page is scored.

This split already exists in `scoring/` and this design follows it rather than
inventing a second convention.

---

## 8. Testing

**The acceptance test is the probe.** `CASE002_bank_statements.md` with all 27
Debits moved to Credits currently scores `0.000000` normalised CER. Under this
metric it must score loudly, and the one-character typo must stay small. The
inversion recorded in `docs/OmniDocBench_notes.md` is asserted as a regression
test, so the blindness cannot return unnoticed.

**Verify against all 165 real transcripts, not only constructed fixtures.**
A transcript scored against *itself* must yield `table_cell_error_rate == 0.0`,
`table_rows_missing == 0` and `table_rows_spurious == 0` on every one of the
165 pages, exercising every real table shape — headerless receipts,
date-grouped statements, the two-table ANZ layouts. This proves the metric
runs without crashing and is deterministic across real input, and no more:
self-comparison is trivially satisfied by any deterministic parse regardless
of whether that parse is *correct*, so on its own it would **not** have caught
the `headerless_table` defect that three reviews missed on the previous
branch — a reviewer of this design demonstrated that directly, by mutating
the empty-header rule and watching self-comparison pass unchanged on all 165
pages. Catching that class of defect needs a check with a known-correct
answer that differs from its input: pin `parse_tables`'s shape against the
corpus (table count, headerless-table count, row count, cell count, and a
zero ragged-row count, each computed independently from the corpus rather
than asserted from memory), and perturb a real page in a way whose correct
score is known in advance — dropping the final row of a page's final table
must be reported as exactly one missing row on every one of the 165 pages.
Together these three checks — self-comparison, the pinned parse invariants,
and the row-drop perturbation — are what a real page can offer that a
hand-written fixture cannot.

A unit test that passes on a hand-written fixture while failing on a real page
is the dominant failure mode of this codebase, recorded five times in
`docs/superpowers/2026-08-26-layout-ground-truth-follow-ups.md` §4. Every
stage of this work is verified against real corpus output.

Further cases: a dropped row reports one missing row, costs exactly its own
cells and does not cascade into the rows after it — the case that separates
this design from both rejected alternatives, and worth asserting on the
numbers, not merely on the row counts; a table dropped whole costs the same as
its rows dropped one at a time; a spurious row is counted and does not corrupt
alignment; a misplaced value is
counted both incorrect and misplaced; an empty reference cell filled by the
model is counted incorrect; a page with two tables pairs them in order; a
prediction with no tables loses every row; `cell_comparison: strict` changes
the outcome for a cell differing only by a curly quote.

Floor is the repository's 80% coverage gate, run in `docparse-score`.

---

## 9. Known limitations, each with a revisit condition

**L1 — table detection requires a leading `|`.** A model emitting pipe tables
without leading pipes has its tables missed entirely, reported as
`table_count_pred: 0` and every row missing. `config/prompt.md:74-79` shows the
leading-pipe form explicitly, so this is scored non-compliance rather than a
parser gap. **Revisit if** a scored model emits header-separator tables without
leading pipes; the fix is to accept a line containing `|` when its block also
contains a separator row.

**L2 — escaped pipes inside cells are not handled.** The generator never emits
`\|`, so no reference contains one. A prediction containing one splits into an
extra cell. **Revisit if** any field value ever legitimately contains a pipe.

**L3 — the error rate mixes placement failure with omission.** Counting dropped
rows' cells as incorrect (§3.4) is what removes the gaming path, but it means a
single number no longer separates "filed in the wrong column" from "never
transcribed". `table_cells_misplaced` and `table_rows_missing` are reported
alongside precisely to separate them, and the two causes are distinguishable
from those counts. **Revisit if** a consumer needs placement isolated as its
own rate; the matched-cell denominator can be added as a second field without
disturbing this one.

**L4 — misplacement is detected within a row only.** A value moved to another
row is reported through row alignment instead. **Revisit if** column-swap
failures across rows appear in practice.

**L5 — a parser's own structured output is not consumed, only its Markdown.**
Docling produces a `DoclingDocument`, whose `document.tables` can be exported
to dataframes or CSV; other systems expose comparable object models. This
design deliberately scores the Markdown instead, for three reasons.

The interface settles the first: `scoring/predictions.py:140` reads one `.md`
per page stem. A parser's object model never crosses that boundary — the runner
has already exported to text by the time scoring begins — so consuming it would
not be a scoring change but a change to what a prediction *is*, for every
system.

The second is fairness, and it is the decisive one. This corpus scores
vision-language models alongside document parsers. A VLM has no object model to
offer; it expresses structure through pipes and is penalised when it cannot. If
a parser were scored from its native table objects while a VLM was scored from
its Markdown, the two would not be answering the same question, and the
comparison the benchmark exists to make would be void.

The third is symmetry, the same argument as §3.1: the reference is Markdown, so
a grid recovered from an object model on one side and from Markdown on the
other could disagree because of the conversion rather than because of the model.

**What this costs, stated plainly.** Where a parser holds a correct table
internally and its Markdown exporter flattens it, this metric charges the
exporter's failure to the parser. Today that cost is small — every table in the
corpus is a uniform grid, so there is little for an exporter to flatten.
`config/serialisation.yml:83` already records Docling emitting 124% of
transcript length on receipts, so its export does diverge measurably; this
metric will now report *where* rather than only how much.

**Revisit when subsystem B lands.** Adding `colspan`/`rowspan` and spanning
headers gives Markdown export something real to lose, and the cost above stops
being small. The remedy then is not to change this metric but to make the
runner's recording decision explicit — what text is stored as a parser's
prediction — and to document it beside the run.

A parser's structured output does have a legitimate use here that is not
scoring: running Docling's `document.tables` beside `parse_tables()` on the same
output is an independent check that this module recovers the grid the parser
believed it wrote.

---

## 10. Success criteria

1. The probe's 27-transaction misfile scores as a substantial, obvious error,
   and the one-character typo scores near zero — the inversion of today's
   behaviour.
2. Every one of the 165 shipped transcripts, scored against itself, yields a
   `table_cell_error_rate` of exactly 0.0 with no missing or spurious rows.
3. `table_cells_misplaced` distinguishes a misfiled value from an unread one on
   a constructed case where both occur on the same page.
4. A prediction keeping 3 rows of a 27-row statement and dropping the other 24
   scores a substantial error rate, not a perfect one — the property that
   distinguishes this design from the matched-cell denominator it replaced.
5. NORMALISED and STRICT are numerically unchanged for every page — verified by
   scoring the corpus before and after and diffing the rows.
6. `config/scoring.yml` alone answers what the table metric is configured to
   do, without consulting Python.
7. `scoring/` acquires no new dependency. `rapidfuzz` is already present, and
   `apted` is not required by this design.
