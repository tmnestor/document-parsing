# Sprint Summary — Synthetic Document Parsing Corpus

**Sprint ending:** 2026-08-26
**Epic:** Extend the YAML-driven synthetic generator from Information
Extraction to Document Parsing

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

## 🔄 In Progress — Feature: Structural Realism (B1)

- Adding **side-by-side vendor/payer invoice blocks** — the layout convention
  competent models most often get wrong
- Notable: our prompt already instructs the reading rule and the renderer
  already implements it, **but no page in the corpus exercised it**
- Additive only — the existing 165 pages stay byte-identical
- Spec and plan approved, implementation underway

## 📋 Proposed — Next Sprint

- **Change transcription target to Markdown with HTML tables** (currently
  Markdown only)
  - Driver: Markdown pipe tables **cannot express merged cells**, blocking
    spanning-header support
  - Matches OmniDocBench, MinerU and Docling conventions — adopting a standard,
    not inventing one
  - We already generate the HTML; transcripts re-emit in seconds with **no
    image re-rendering**
  - Cost: ~1.46x tokens on table content; scoring parser and prompt need
    updating
- **Then B2:** colspan / rowspan / spanning headers — trivial once tables are
  HTML
- **Sequencing agreed:** B1 → format change → B2

## ⚠️ Risks / Notes

- **Corpus saturation:** receipts and invoices cannot currently separate
  models — only bank statements discriminate. Better metrics will not fix this;
  harder documents will
- The format change will invalidate predictions scored against current
  transcripts (images unaffected)

---

## Suggested framings for the room

- The misfile finding is the headline: **we were scoring a sign error on every
  transaction as a perfect transcription.**
- The format proposal lands better as *adopting the field standard* than as a
  rewrite.
