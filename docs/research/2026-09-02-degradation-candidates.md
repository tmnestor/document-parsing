# Degradation candidates for real taxpayer submissions

**Date:** 2026-09-02
**Status:** research, no decision taken

## 1. The question

The corpus models two intake channels — `scan` (flatbed/ADF) and `photo` (phone
on a desk). Production receives documents from taxpayers. What else arrives in
that mail, and what would it cost to model?

This is research, not a proposal. Nothing here is scheduled, and §7 argues for
measuring before adding anything at all.

## 2. What is modelled today

| stage | effect | file |
|---|---|---|
| ink | `InkBleed` | `generators/degradation/effects.py` |
| paper | `LightingGradient`, `ShadowCast` | `generators/degradation/effects.py` |
| marks | `roller_streaks`, `fold_ridges` | `generators/degradation/geometry.py` |
| geometry | `skew_on_platen`, `warp_to_photo` | `generators/degradation/geometry.py` |
| camera | blur, sensor noise, JPEG | `generators/degradation/geometry.py` |

Two families × three severities, plus the clean baseline: seven corpora.

## 3. The structural finding

**The current model has one axis. Taxpayer submissions have two.**

`config/degradation.yml` is organised around *how the page was captured*,
because it wants "which intake channel breaks it?" to be answerable, and that
question only stays answerable if each family is its own ladder.

But much of what arrives has been damaged *before* capture:

- a thermal receipt that faded in a wallet for eight months
- a document photocopied twice before anyone scanned it
- a page printed on a laser that was low on toner

None of those is an intake channel. Photographing a faded receipt perfectly
still yields a faded receipt. Modelling them inside `scan` or `photo` would
conflate two independent things and make the family attribution meaningless —
the exact failure the two-ladder design exists to prevent.

So they need either a third family, or — more honestly — **a provenance stage
that runs before the capture ladder**, with its own severity axis. A page would
then carry two labels: what state it was in, and how it was captured. That is a
larger change than adding an effect, and it is the decision this document exists
to surface rather than to make.

## 4. The constraint that filters everything

Since `docs/superpowers/specs/2026-09-01-cross-machine-determinism-design.md`,
the pixel path may use only `+ − × ÷`, `sqrt`, comparisons and integer
arithmetic. IEEE-754 mandates correct rounding for those and says nothing about
`exp`, `log`, `pow` or the trigonometric family, which come from the platform's
libm and were measured to differ between arm64 macOS and x86_64 Linux.

This is a real filter on the candidate list. It rules out the *natural
formulation* of several effects — Augraphy's bleed-through, for instance, is
built as a "virtual diffusion process" [4], and diffusion is exponential. Every
candidate below is therefore assessed on whether it can be **re-derived** in
exact arithmetic, not on whether a library already implements it.

Augraphy's 24 augmentations [1][2] remain a useful *taxonomy of phenomena*.
They are no longer available as code: the library was removed from this
repository precisely because its effects were not reproducible across machines.

## 5. Candidates

Ranked by value against cost.

### 5.1 Thermal fade — receipts

Thermal receipts fade continuously after printing. The documented causes are
heat and UV first, then moisture, skin and plastic oils, and friction [5][6].
Fading is commonly **uneven** — heavier on one side, or showing streaks, light
spots and intermittent voids where the print head or paper roll was imperfect
[7]. The chemistry is one-way: once faded, the coating has already reacted and
the print cannot be recovered [5].

Scale of the problem is not hypothetical. One receipt-processing operation
reports that of 3.5 million receipts scanned since January 2024, **15.5%
arrived with no payment type any software could read off the image** [6].

**Portability: trivial.** A directional ramp multiplied into a contrast
reduction, in integers. The ramp machinery already exists (`_ramp` in
`effects.py`), and the blend is the same integer form `ShadowCast` uses.

**Why it ranks first:** the corpus has 55 receipts, and
`config/degradation.yml` already records that receipts score at ceiling on
clean pages — there is no discrimination left to lose. This is the single
best-documented real-world receipt defect, and it is a handful of lines.

### 5.2 Photocopy and fax — a genuine third channel

Generational loss, 1-bit thresholding, halftone patterning. Augraphy models
this family explicitly, describing its target as documents "distorted from
standard office operations, such as printing, scanning, and faxing through old
or dirty machines" [1].

**Portability: yes, and exactly.** Ordered dithering via a Bayer threshold
matrix is pure integer comparison — no arithmetic that IEEE-754 leaves
unspecified. Generational loss is repeated application.

**Why it ranks second:** unlike thermal fade this genuinely *is* an intake
channel, so it extends the existing taxonomy cleanly as a third family rather
than forcing the structural change in §3.

### 5.3 Low toner and print streaking

Vertical light bands from a laser near end of life. Distinct from the existing
`roller_streaks`, which model a scanner's feed path: this damage is in the
**print**, so it must be applied before any capture effect, and it is
multiplicative rather than additive.

**Portability: yes.** Reuses the streak machinery with a different composite.

### 5.4 Motion blur

Hand shake during phone capture. The corpus models focus blur but not
directional blur, and they look nothing alike on text.

**Portability: yes.** A directional box blur; `generators/degradation/kernels.py`
already provides exact integer box machinery and variance matching.

### 5.5 Specular glare

Phone flash on glossy or laminated paper, blowing out a region entirely.
Different in kind from `LightingGradient`, which darkens smoothly and never
saturates.

**Portability: yes.** A polynomial falloff, the same shape already used for the
lighting ramp and the fold ridge.

### 5.6 Bleed-through / show-through

Ink from the reverse of a double-sided page. Common in submitted paperwork.

Augraphy's implementation was rejected here for a different reason, recorded in
`config/degradation.yml`: it *reads images from an on-disk cache* and
composites whatever the last run left behind, making output depend on directory
state rather than on the seed.

**Portability: yes, if re-derived.** Mirror the page, attenuate, composite —
all integer. It needs a notion of the page's reverse, which the corpus does not
currently have, so it is the most invasive item on this list.

### 5.7 Cheap additions, lower value

Staple and punch holes; edge cut-off from a scanner misfeed; highlighter and
pen annotation; coffee and water stains. All are small occlusions or blends,
all portable, none individually compelling.

**Orientation** (90°/180° rotation — a page fed upside down) is deliberately
excluded: it is not a degradation but a different task, and folding it into a
severity ladder would confound "can the model read damaged text?" with "can the
model detect orientation?".

## 6. What is not worth modelling

- **Moiré from photographing a screen.** Real — taxpayers do submit photos of
  PDFs on monitors — but the natural formulation needs trigonometry, and a
  precomputed pattern would be a committed binary artifact with the provenance
  problem described in §7 of the determinism spec.
- **Anything requiring the document's reverse side**, until §5.6 is wanted
  enough to justify authoring reverse content.

## 7. Sequencing — measure first

The re-derived effects landed on 2026-09-02 and made the ladder materially
gentler at every rung: measured against the clean page, `scan` now retains
0.682 / 0.405 / 0.264 of its stroke detail and `photo` 0.428 / 0.183 / 0.071,
against 0.301 / 0.217 / 0.111 and 0.215 / 0.125 / 0.055 before. Both ladders
remain strictly monotonic, but every tier is easier than the tier of the same
name a week ago.

**No model has been scored against the new corpus.** Adding severity axes
before that measurement risks tuning blind — and the existing evidence says
tuning by eye is unreliable here: `scan-moderate` once scored *above* the clean
baseline for one system, which is how the collapsed rung was found at all.

The recommended order is therefore: score the current corpus, decide whether
the ladders need retuning, and only then consider §5.1 and §5.2.

## 8. References

1. Groleau et al., *Augraphy: A Data Augmentation Library for Document Images*,
   arXiv:2208.14558. https://arxiv.org/abs/2208.14558
2. Same, in *Document Analysis and Recognition — ICDAR 2023*, Springer.
   https://link.springer.com/chapter/10.1007/978-3-031-41682-8_24
3. *A Review of Document Image Enhancement Based on Document Degradation
   Problem*, Applied Sciences 13(13):7855. https://doi.org/10.3390/app13137855
4. *GL-PGENet: A Parameterized Generation Framework for Robust Document Image
   Enhancement*, arXiv:2505.22021. https://arxiv.org/pdf/2505.22021 — notes that
   existing datasets target single degradations while real documents carry
   several at once.
5. *Understanding Thermal Paper Fading: Causes, Recovery, and Prevention*.
   https://www.jotamachinery.com/academy/thermal-paper-fading/
6. *Why Thermal Receipts Fade — and How to Store Thermal Paper So Prints Last*.
   https://www.posatmparts.com/guides/thermal-receipt-fading-paper-storage
7. *Why Your Thermal Paper Keeps Fading, Smudging, or Turning Brown*.
   https://www.greenpapertech.com/why-your-thermal-paper-keeps-fading-smudging-or-turning-brown-a-no-fluff-guide-to-spotting-real-quality-issues/

### Internal references

- `docs/superpowers/specs/2026-09-01-cross-machine-determinism-design.md` — the
  arithmetic constraint in §4, and why augraphy was removed.
- `config/degradation.yml` — the two-ladder rationale, the rejected augraphy
  effects and the reasons, and the rule that a tier must discriminate.
- `generators/degradation/effects.py`, `kernels.py`, `geometry.py` — the
  machinery any new effect would reuse.
