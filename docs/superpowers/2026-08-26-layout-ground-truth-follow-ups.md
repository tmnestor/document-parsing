# Layout and Structure Ground Truth — Follow-Ups

**Date:** 2026-08-26
**Source:** eleven per-task reviews, the whole-branch review, the scoped re-review
of the fix wave, and two controller rulings made after it, carried out while
building
`docs/superpowers/specs/2026-08-26-layout-and-structure-ground-truth-design.md`.
**Merged as:** `10c6b01..7fc3b17` (15 commits).

Everything below was found by a review, triaged, and deliberately **not** fixed.
Nothing here blocks anything shipped. Line references are as-reviewed and may
have shifted under the fix wave.

Three Critical, four Important and three upgraded Minor findings from the same
reviews **were** fixed before merge (`134b933`, `dfc05a2`, `7dfc884`,
`7fc3b17`); they are in the git history and are not repeated here.

---

## 1. Known gaps worth a ticket

**G1 — the pixel gate covers text ink only; 22,113px of non-text ink is
unannotated.** `TranscriptDraw` measures `.text` calls, so a `rule` drawn as a
line and a table's frame put ink on the page that no annotation covers. The
mask test is marked `xfail(strict=True)` — deliberately, so the gap flips
loudly if it is ever closed rather than being silently fixed-and-forgotten or
silently persisting.

Closing it properly is a subsystem, not a fix round: it means teaching the draw
proxy to measure non-text primitives and giving those events boxes that are not
span-derived. OmniDocBench would class rule lines `abandon`/`ignore` anyway, so
the scoring consequence is small.

**Two things to carry forward.** Record the gap in the spec, not only in a
test's `reason` string. And note the consequence for table-frame IoU: a table
annotation's `poly` is the union of its cells' spans, so it hugs the glyphs and
excludes the drawn frame. A layout-detection scorer comparing against a
human-drawn box that includes the frame will see a systematically smaller box.

**G2 — `_has_ink` is a presence check, so a shifted-but-overlapping box
passes.** The suite proves two things honestly: a box is not on blank canvas,
and ink is not uncovered. It does **not** prove a box is on the *right* ink.
Quote it with that caveat attached and never as "boxes verified".

Mitigating evidence: block text was checked against the transcript on all 165
pages, and spans against `textlength` arithmetic. A shifted box would have to
preserve both to escape — a narrow failure surface, but not an empty one.

**G3 — spec §3.4 (decoration annotated `abandon` with `ignore: true`) is
deferred.** `category_for("rule")`, `_IGNORED_CATEGORIES` and the `ignore`
field are live code with **no producer**. That is fine as documented intent and
not fine if undocumented — which is why it is written here.

Implementing it means adding a `rule` emit site, which risks the byte-identity
gate for the one feature §13 itself calls "the one place we annotate something
no metric will read". Note the trap this hides: receipts set
`rule_fill_char: "-"` and therefore *do* draw glyph rules, but the Task 10 mask
test renders invoices only, and invoices set `rule_fill_char: none`. The test
passes because invoices carry no decoration ink, not because decoration is
annotated. Anyone extending that test to receipts will see it fail with no
explanation unless they read this.

---

## 2. Consistency and polish

**G4 — `table_html` is called twice per table-bearing page.** `export.py`
computes it once for `tables/*.html` and again inside `layout_dets`. Pure and
cheap, so this is not a performance note: it becomes a correctness risk only if
the two calls could ever be handed different policies. Passing the result
through rather than recomputing would close it permanently.

**G5 — a `column_key` matching neither the open row nor `table_columns` is
silently dropped**, with no diagnostic (`tables.py`, and identically in
`serialise.py`). Flagged Minor for consistency rather than Important because it
mirrors the sibling projection exactly — it is not a gap this branch
introduced. Worth one change fixing both, since fixing one alone would create
the very divergence the shared-helper rulings exist to prevent.

**G6 — `enclose`'s no-op branch (an empty table) is untested.** Unreachable in
the 18 shipped layouts.

**G7 — a header-optional zero-row table would trip `BoxCoverageError`.**
Unreachable today. The right response is a comment at the table primitive
explaining why the case cannot arise, rather than a guard for a shape nothing
emits.

**G8 — `test_a_block_is_a_text_block`'s docstring couples to
`westpac_premium`.** Documented coupling in a test, and the layout-pinning was
the correct response to the doc-type-parametrization lesson (see §4). Recorded
so the coupling is a known choice rather than a surprise.

**G9 — the wrong-box sensitivity analysis exists only as prose.** It would be
better as a parametrized test. Not load-bearing.

---

## 3. A defect in the plan, not the code

**G10 — the plan's Task 11 Step 3 example command is not scratch-safe.** It
omitted `--output` and `--derived`, and it led a careful implementer into
running `generate` against the shipped corpus. No data was lost — all 165
images were verified byte-identical afterwards — but the instruction is the
defect, not the implementer.

Any future plan step that invokes `generate` must pass both flags explicitly in
its example text. This recurred: a later agent did the same thing again, and
again the corpus survived only because the render is deterministic.

---

## 4. The lesson worth keeping

**Five times on this branch, work that was green under its own tests
misdescribed real corpus output.** Every catch came from rendering real pages;
none came from the suite. Twice the controller's own spot-check shared the
implementer's blind spot.

| # | What the tests said | What the pages said |
|---|---|---|
| 1 | Task 4 green | 3 of 5 emit sites had no coverage — `type: block` and `type: banner` exist only in `bank_statements.yml`, and every test rendered invoices |
| 2 | Task 7 green | `dedicated_row` grouping emitted a 1-cell row against 5-cell siblings, corrupting the TEDS tree |
| 3 | Task 8 green, 13 tests | Every `pair` block projected as `text: ""` |
| 4 | Fix wave green | 34 of 55 bank statements disagreed between Markdown and HTML |
| 5 | Three reviews passed it | `headerless_table: empty_header_row` was never implemented in the HTML projection — 69 of 179 tables shipped one fewer row than Markdown |

The last is the sharpest, and the most general. The fix wave verified C1 by
comparing **structure** — row counts and raw newlines — and was clean. Changing
the check to compare **cell text across all 165 pages** exposed a policy the
HTML projection had simply never implemented, which the whole-branch review,
the fix wave and the scoped re-review had all passed over.

**The rule to carry into the next subsystem:** when two projections must agree,
verify them by comparing their *content*, cell by cell, across the whole
corpus. A structural check is not a weaker version of that — it is blind to a
whole class of disagreement by construction, and it reports clean while being
blind.

The corollary already applied three times on this branch: when both projections
implement one convention, **share one helper**. Every drift found here
(`pair_text`, `pad_row`/`_join_cell`/`carry_group_key_down`,
`strip_decoration_run`, `headerless_table`) was two implementations of a single
rule, kept in step by hand until they weren't.

---

## 5. Deferred subsystems

**B — structural realism.** Side-by-side vendor/payer invoice blocks,
`colspan`/`rowspan`, spanning headers. Unchanged from the previous increment's
follow-ups, and now the blocker for the `colspan`/`rowspan` attributes
`tables.py` deliberately omits: the table primitive has no merged-cell concept,
so every table is a uniform grid and the attributes would be constant.

**Non-text box capture.** G1 above, stated as a subsystem rather than a gap.

**A structural scoring metric.** Out of scope here — this branch ships the
ground truth, not the metric that reads it. See
`docs/OmniDocBench_notes.md`, whose probe shows normalised CER scoring 27
reversed bank-statement transactions as a perfect transcription. The
`tables/*.html` this branch exports is the authored reference such a metric
needs, so the conversion step those notes assume is no longer required.
