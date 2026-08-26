# Cell-Aligned Table Metric — Follow-Ups

**Date:** 2026-08-26
**Source:** seven per-task reviews, two scoped re-reviews, the whole-branch
review, and the fix-wave re-review carried out while building
`docs/superpowers/specs/2026-08-26-cell-aligned-table-metric-design.md`.
**Merged as:** `244db08..413104d` (12 commits).

Everything below was found by a review, triaged, and deliberately **not**
fixed. Nothing here blocks anything shipped. Two Important findings and one
Minor from the same reviews **were** fixed before merge (`a11f76a`, `8c030fb`,
`413104d`) and are not repeated.

---

## 1. Deferred items

**D1 — no test covers an empty reference against a non-empty prediction.**
The path was exercised directly during review: `score_tables("hello", <two
tables>)` returns rate `None`, `table_rows_spurious 4`, `table_count_ref 0`,
and does not crash. A coverage gap, not a risk.

**D2 — the unequal-length `replace` handling in `align_rows` is dead code.**
Confirmed independently across 20,000 random sequence pairs: `rapidfuzz`
never emits an unequal-length `replace` opcode. **Keep it anyway.** It is the
only thing standing between `zip(..., strict=True)` and a `ValueError` if
rapidfuzz's opcode grouping ever changes, and spec §7 forbids raising on
prediction content. Deleting dead code here would trade it for a crash risk.

**D3 — a row-order swap inside a table slips past the test suite.** A
mutation that reorders adjacent rows while leaving counts and shape untouched
is not caught by any pinned invariant. It does **not** translate into a
scoring defect: `align_rows` pairs rows by content, so a *prediction* with
adjacent rows swapped scores `0.360000` on `CASE002` — loudly. The gap is in
the tests' sensitivity, not the metric, and `tests/` is gitignored.

**D4 — an adversarially hand-crafted rows file can still raise a bare
`KeyError`.** A file carrying `table_cell_error_rate` but with only the three
new count fields deleted bypasses the stale-file guard and reaches
`aggregate`. No real `score_page` output has that shape — all eight fields are
emitted together — and it matches the pre-existing unguarded `degenerate`
read, so it is not a regression this branch introduced.

**D5 — the plan's Task 7 Step 4 command is uncollectable in
`docparse-score`.** `pytest tests/ --cov=scoring` pulls in generator tests
needing PIL, which that environment lacks by design. The implementer correctly
scoped to `tests/scoring/`; the plan text is wrong, not the code.

---

## 2. Known behaviours, documented rather than changed

Both are now in docstrings; recorded here so the reasoning survives.

**B1 — a cell of dashes or colons normalises to empty.** `_cell_form` passes
a single cell through `normalise()`, whose separator-row pattern is anchored
under `re.MULTILINE`, so a lone cell is a whole "line" to it. A model writing
`-` into a blank cell therefore scores that cell **correct** under
`cell_comparison: normalised`; `strict` does not forgive it. No reference cell
is affected — 0 of 8,856 normalise from non-empty to empty.

**B2 — duplicate values in a row can raise a spurious `misplaced` flag.**
Reachable on real data: 18 of 2,074 reference rows repeat a non-empty value,
typically an invoice line where quantity is 1 so unit price equals amount.
Affects only the diagnostic count, never the error rate.

---

## 3. The next increment, with constraints it must inherit

Two design observations were raised during execution and measured rather than
acted on. Both are correctly deferred; both carry a constraint that would
otherwise be rediscovered the hard way.

### 3.1 `normalised_cer` and `table_cell_error_rate` overlap

`normalise()` strips pipes, so table content flows into the character stream
CER measures. Measured share of transcript characters inside pipe tables:

| doc type | % table |
|---|---|
| bank_statements | 91.3% |
| invoices | 45.4% |
| receipts | 21.9% |
| all 165 pages | 73.5% |

So on a bank statement, 91% of what CER scores is content the table metric
scores a second way. Two numbers that are not independent are harder to reason
about than two that partition cleanly.

**The obvious remedy is wrong on its own, and this is the constraint to
inherit.** Restricting CER to non-table text would remove **the only reported
signal that catches invented table rows**. Two fabricated bank transactions
score `table_cell_error_rate 0.000000` with `degenerate False`; the number
that moves is `normalised_cer` (0.050943), and it moves precisely *because* it
sees the table content the reference-cell denominator is deliberately blind to.

**Ordering constraint:** if the non-table-CER split is ever built, it must
ship **together with or after** the diagnostic counts reaching the report —
never before. That half now exists (`a11f76a`), so the ordering constraint is
satisfiable, but it is not optional.

Also weighing against acting: spec §2 scopes changing an existing metric out
deliberately, and `normalised_cer` is the number every already-scored
prediction was measured with. Redefining it silently invalidates comparisons
against the shipped `parsing_20260825` vintage — the exact failure the
manifest hashes exist to prevent.

### 3.2 A wrong amount and a wrong description word score identically

Measured on the real `CASE002_bank_statements.md` (175 table cells):

| perturbation | norm CER | cell err | misplaced |
|---|---|---|---|
| amount wrong by one digit (`$590.52` → `$890.52`) | 0.000401 | 0.005714 | 0 |
| description wrong by one letter (`GOSWELL` → `GOSWELI`) | 0.000401 | 0.005714 | 0 |
| amount filed in the wrong column | 0.000000 | 0.011429 | 1 |

CER scores a $300 error and a cosmetic typo **identically**, because it
weights by character count and the description column is 31 characters against
the amount column's 7. Backwards for a financial document.

This branch fixed two of the three complaints: the misfile became visible and
named, and a wrong cell now counts as one cell regardless of its length — 14×
more visible than CER makes it. The residual is real: one cell is one cell, so
a wrong amount and a wrong description still score the same.

**Per-column error rates, keyed off the header row, are the right shape.**
Three constraints for whoever builds it:

1. **The column key must come from the reference header only.** If prediction
   headers participate, a model that renames or reorders its headers changes
   which column its errors are charged to — it could shift errors out of the
   money column by relabelling. That is the same gaming path §3.4 already
   rejected for the denominator.
2. **The positional fallback is not an edge case.** 69 of 179 corpus tables
   are headerless (`|  |  |`) — 39%.
3. **It is a second `_METRICS` migration**, with the same
   backward-compatibility problem this branch paid for once: `report` crashed
   with a bare `KeyError` on any rows file scored before the change.

### 3.3 The likelier priority than either

Both of the above improve the instrument. The corpus's own limit is that
receipts and invoices cannot separate models at all — recorded in
`make_degraded_statements.sh` and in the previous increment's follow-ups:
receipts have every gemma checkpoint misfiling zero, invoices are
near-saturated, so only bank statements are degraded.

**Subsystem B** (side-by-side vendor/payer blocks, `colspan`/`rowspan`,
spanning headers) makes the documents harder rather than better instrumented.
Better instruments on saturated documents measure nothing.

---

## 4. The lesson from this branch

**The acceptance gate could not fail for the defect class it was written to
catch.**

`test_every_shipped_transcript_scores_faultless_against_itself` compared
`score_tables(text, text, policy)` — a deterministic function against its own
output. That is equal whether or not the parsing rule is correct, so the test
could only ever catch a crash or non-determinism. Demonstrated by a reviewer:
mutating `_is_separator` to discard empty header rows changes `parse_tables`
output on **69 of 165** real transcripts, and every test still passed. The
spec compounded it by claiming the test "would have caught the
`headerless_table` defect" from the previous branch — it structurally could
not.

This is worse than a missing test. A gate that always passes manufactures
confidence, which is the failure mode recorded five times in
`docs/superpowers/2026-08-26-layout-ground-truth-follow-ups.md` §4.

**The rule to carry forward:** a test whose expected output is derived from
the same code path it is testing proves nothing about correctness. Pin values
computed independently — the replacement pins 179 tables / 69 headerless /
2,074 rows / 8,856 cells / 0 ragged, and 179 and 69 were separately derived
from the generator's event stream on the previous branch and match — and
perturb the input so the expected answer differs from it.

**And how it was found:** not by reading the test, but by **breaking the
implementation and observing which breakages the tests noticed**. Two
mutations were tried; one failed loudly, one slipped through silently. Only
the second revealed the tautology.
