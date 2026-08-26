# Side-by-Side Party Blocks (B1) — Follow-Ups

**Date:** 2026-08-26
**Source:** three per-task reviews, two fix rounds, the whole-branch review and
its fix-wave re-review, carried out while building
`docs/superpowers/specs/2026-08-26-side-by-side-party-blocks-design.md`.
**Merged as:** `4bd2b5f..60e56b4` (10 commits).

The branch shipped two invoice layouts with genuinely two-column party blocks
and twelve new ground-truth cases using them. **No defect was found in what it
ships to the corpus.** Every finding below was in prose or is a latent hazard in
pre-existing code.

---

## 1. A real hazard in shipped code

**H1 — `export` has no guard against overwriting an existing dated export.**
`generators/export.py:307-310` builds `parsing_{date_stamp}` with
`mkdir(..., exist_ok=True)` and writes with plain `write_text`; `date_stamp`
defaults to today. Re-running `export` on the same calendar day as an existing
export **silently overwrites that export in place** — `images/`,
`transcripts/`, `layout/`, `tables/`, `manifest.jsonl` and `README.md`.

This is not hypothetical. During the whole-branch review, an agent following
this plan's own (incorrect, now fixed) constraint ran `export` with `--output`
and `--derived` pointed at scratch and wrote 712 files into the real data root.
It landed in a *new* dated directory only because the shipped export was dated
the previous day. Run twenty-four hours earlier, it would have destroyed the
deliverable.

**Worth a ticket.** A `--force` flag, or a refusal when the destination exists
and is non-empty, would close it. The repository's own fail-fast rule argues for
the refusal: a four-element diagnostic naming the existing directory and telling
the operator to pass `--date` or `--target`.

**H2 — `--output` and `--derived` do not redirect `export`.** They are
*sources* (`generators/pipeline.py:463-464`); the destination is **`--target`**,
defaulting to `cfg["exports_dir"]` inside the real data root
(`pipeline.py:469`). This is now documented in `CLAUDE.md` with a per-command
flag table, but the asymmetry itself remains a trap: two of the three pipeline
commands take their destination from `--output`/`--derived` and the third does
not.

**H3 — `preview` reports the shipped image path, not a scratch one.**
`generators/pipeline.py:423` builds the preview image path from
`cfg["output_dir"]` unconditionally. The command accepts `--derived` but has no
`--output`, so `preview CASE059 --derived /tmp/…` prints a scratch transcript
beside a path under the **real** corpus. Pre-existing; surfaced because this was
the first plan to route `preview` through a scratch derived directory.

---

## 2. Deferred items

**D1 — no case exercises the 820px budget's shrink or wrap path.** Measured
across the twelve: the widest party element is CASE061's payer address at
**599px**, against a budget of 820 — 221px of headroom. At the observed
8.6 px/char a supplier name would need roughly **95 characters** to reach it;
the longest authored is 53.

The authored values *do* extend the corpus envelope (`SUPPLIER_NAME` max
34 → 53, `PAYER_ADDRESS` max 41 → 50, both comfortably the longest in the
corpus) and are good realism. They simply do not reach the budget.

**Not a gap in the branch's stated goal**, which is exercising column-major
reading order, and which it demonstrably achieves. **Revisit if** the wrap path
ever needs coverage — either add one case with a ~95-character supplier name, or
accept that the budget is guarded statically (§3.3) and needs no data to
protect it.

**D2 — a task report mislabels CASE066's `PAYER_ADDRESS` as long.** It is 38
characters against an existing maximum of 41. The error is in a report, not in
shipped data.

**D3 — the twelve new cases use alphabetical key order and a quoted
`BUSINESS_ABN`**, against the existing 55's semantic order and unquoted form.
`'10 877 194 803'` and `57 773 872 148` parse to the identical Python string, so
the difference is cosmetic. Inherited from the plan's own worked example.
`ground_truth/` is not part of the export, so the tell cannot leak a layout to a
scored model. Worth tidying whenever the file is next touched.

---

## 3. Five defects in the plan, and what they have in common

None was in the code. All five were in the plan I wrote, and four were caught
before or during execution rather than after.

| # | Defect | Caught by |
|---|---|---|
| 1 | The worked example's GST arithmetic satisfied two of three rules and failed `round(TOTAL/11, 2)` | The pre-flight conflict scan, before any dispatch |
| 2 | The worked example's ABN failed its checksum | The implementer, via `validate`'s own diagnostic |
| 3 | The worked example's key order and quoting diverged from the existing 55 | Task 2's review |
| 4 | "Expected: pass" for a test that adding cases must break (`fresh == shipped`) | Task 3's implementer |
| 5 | The anti-clobber constraint named the wrong flags for `export` | The whole-branch reviewer, by triggering it |

**The common cause: I hand-authored example data and safety instructions
without running them through the system's own validators.**

Every one of these was checkable in advance. The GST example could have been
derived rather than invented. The ABN could have come from `generate_abn()` —
whose absence `validate` already diagnoses, with the remedy in the message. The
flags could have been read from `pipeline.py`.

**The rule to carry into the next plan:** worked examples for validated data
must be **produced by the validator or generator**, never invented; and any
instruction about which flag protects real data must be **read out of the source
that resolves it**. A confident sentence in a plan is executed verbatim by
someone with no context to doubt it — defect 5 was followed exactly as written
and put 712 files in the data root.

The corollary that worked: **the fix for defect 1 was not better arithmetic but
a derivation order** — items → sum → `GST = round(sum × 0.1, 2)` →
`TOTAL = sum + GST`. Derived that way the three rules cannot disagree. Where a
rule set can be made structurally unviolatable, do that instead of checking it.

---

## 4. What the visual gate actually bought

Task 3's requirement to *open the images* was carried, and both the implementer
and the reviewer described real pixel positions and character counts rather than
"rendered without error".

It is worth recording that the controller's own visual reading was **wrong**. I
reported that CASE059's 53-character supplier name had shrunk to fit its column,
and that at the old budget it would have overrun. Neither held: measured from
the rendered boxes the name draws at 456px against a 164px control, both at
8.6 px/char — **identical glyph metrics, no shrink**. What looked smaller is the
pre-existing design, where name lines render at `role: body` (20pt) against
addresses at `subheader` (28pt). And an over-wide budget does not overrun
anything; it fails `validate` before a pixel is drawn.

**Looking is necessary and not sufficient.** The correction came from measuring
the emitted `layout_dets` boxes, not from looking harder. Where a visual claim
is load-bearing, measure it — the corpus now emits the boxes that make that
possible.
