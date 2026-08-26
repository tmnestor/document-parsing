# Sprint Summary — Synthetic Document Parsing Corpus

**Sprint ending:** 2026-08-26
**Epic:** Extend the YAML-driven synthetic generator from Information
Extraction to Document Parsing

---

## 📍 Starting state (baseline, pre-sprint)

Commit 1 (`5c59470`, 2026-08-25) already delivered a **complete whole-page
transcription generator** — 61 files, ~20k lines, in one commit:

- Synthetic Australian **invoices, receipts and bank statements** — 18 layouts,
  55 cases each, **165 pristine page images**, each paired with a canonical
  Markdown transcript
- **YAML-driven**: layouts, field definitions, data pools and ground truth are
  all config; the Python only draws
- **Ground truth captured at draw time**, so labels cannot disagree with pixels
- Deterministic: the same inputs render byte-identical images

**Everything this sprint built sits on top of that and has not required
changing it.** The generator has not moved a pixel in ~65 commits — the work
below is about *measuring* what it produces, not producing it better.

### Why extend it

The generator was producing ground truth that nothing could use:

- **The labels were already there and being thrown away.** The renderer knows
  every element's box, category and draw order at the moment it draws — it
  computed those and discarded them. Emitting them costs no new annotation
  effort and gives us **authored** rather than hand-labelled ground truth for
  layout, reading order and table structure, which no public benchmark has
- **We could only score text, and the text score was misleading.** A sign error
  on every transaction of a bank statement scored as a perfect transcription.
  We could not have detected it, and neither could anyone using the corpus
- **Our documents are table-dominated** — a bank statement is essentially one
  long table, and 73% of the corpus by character count sits inside tables — so
  the one thing we could not measure was the one thing that matters most
- **The ask was to extend the Information Extraction dataset work to Document
  Parsing.** Document parsing is scored on layout, reading order, tables and
  text; we shipped only the last of those, and shipped it blind to placement

---

## ✅ Completed — Feature: Layout & Structure Ground Truth

- Corpus now emits **OmniDocBench-format annotations**: `layout_dets` with
  element boxes, category types, reading order, plus per-page table HTML
- Ground truth is **authored at draw time**, not annotated afterwards — labels
  cannot disagree with pixels
- Aligns our vocabulary with OmniDocBench, so our numbers are legible to teams
  referencing it
- **All 165 images and transcripts byte-identical** — no re-scoring required
  for existing predictions
- 15 commits, merged and pushed

## ✅ Completed — Feature: Cell-Aligned Table Metric

- **Problem found:** existing edit-distance metrics were blind to column
  placement. On a real bank statement, **27 transactions with Debits/Credits
  reversed scored 0.000000 — a perfect transcription**
- Root cause: normalisation strips table pipes before comparison, so a value in
  the wrong column is indistinguishable from one in the right column
- **Delivered:** the metric now scores that same failure at **0.3086, with all
  27 cells flagged as misplaced**
- Also scores an instruction we already ship and previously could not enforce
  ("leave blank cells blank")
- Report now surfaces missing / spurious / misplaced row counts — previously a
  model **inventing** transactions scored perfect
- 13 commits, merged and pushed. 158 tests, 98% coverage

## ✅ Completed — Research & Decision Records

- Defined what "Document Parsing" means across three senses, with external
  references
- Read OmniDocBench across our corpus — metric coverage gaps documented
- Recorded why we score the transcript rather than a parser's native object
  model (e.g. Docling's `DoclingDocument`)

## ✅ Completed — Feature: Structural Realism (B1)

- Added **side-by-side vendor/payer invoice blocks** — the layout convention
  competent models most often get wrong
- Notable: our prompt already instructed the reading rule and the renderer
  already implemented it, **but no page in the corpus exercised it**
- Additive only — the existing 165 pages stayed byte-identical. Corpus is now
  **177 pages** (67 invoices, 55 receipts, 55 bank statements)

## ✅ Completed — Feature: Markdown with HTML Tables

- Transcripts now carry tables as HTML `<table>` rather than Markdown pipe
  tables. Pipe tables cannot express merged cells, which blocked B2 entirely
- Matches OmniDocBench, MinerU and Docling — a field standard, not an invention
- **No image moved.** All 177 images byte-identical; only transcripts changed,
  re-emitted in seconds with no re-rendering
- Folded in a de-duplication that had become unsafe: `serialise` and `tables`
  each walked the same table events, kept in agreement by a comment. They now
  share one `TableBuilder`, so a transcript's table and the exported
  `tables/{stem}.html` are the same bytes **by construction**. Verified across
  all 191 tables on 177 pages
- Two hazards found and closed while scoping:
  - The manifest hashed only the image, so a re-serialised corpus was
    indistinguishable from the previous vintage by the very guard meant to
    prevent wrong-vintage scoring. Manifest rows now carry `transcript_sha256`
  - The prompt leak guard parsed pipe syntax, so against HTML examples it
    passed vacuously on a real leak — demonstrated by planting
    'Potting Mix 25L' and watching it go green. It now reads `<td>`/`<th>`,
    which is stricter than the positional heuristic it replaced
- **B2 is now unblocked**

## 📋 Proposed — Next Sprint

- **B2: colspan / rowspan / spanning headers.** Unblocked by the format change,
  and there is now exactly one place to add them — `TableBuilder` in
  `generators/tables.py`
- **Scoring repository:** its table parser needs updating for HTML. The
  interface is the exported corpus, not shared code, so this is separate work
- **Sequencing:** B1 → format change → B2. First two done

## ⚠️ Risks / Notes

- **Corpus saturation:** receipts and invoices cannot currently separate
  models — only bank statements discriminate. Better metrics will not fix this;
  harder documents will
- The format change **has invalidated** predictions scored against pipe-table
  transcripts. Images are unaffected, so re-scoring needs no new inference —
  only re-running the scorer against the new transcripts
- Table content costs ~1.46x tokens as HTML; worth restating when reporting any
  length-sensitive metric across the boundary

---

## Suggested framings for the room

- The misfile finding is the headline: **we were scoring a sign error on every
  transaction as a perfect transcription.**
- The format proposal lands better as *adopting the field standard* than as a
  rewrite.
- If anyone asks what changed in the generator: **nothing.** The corpus it
  produces is byte-identical to the baseline. That is the evidence the original
  design was right, and it is why the layout annotations could be added without
  invalidating a single existing prediction.
