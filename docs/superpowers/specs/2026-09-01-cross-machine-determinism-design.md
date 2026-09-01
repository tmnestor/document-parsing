# Cross-machine determinism

**Date:** 2026-09-01
**Status:** design, awaiting review

## 1. What this changes

The corpus is built by more than one team, on more than one machine. Its
identity is the hashes in `manifest.jsonl`, so "the same inputs render
byte-identical images" has to hold across hosts, not merely across runs on one
host. It does not.

This was measured rather than assumed, on two machines with **identical pinned
versions** — Python 3.12.14, Pillow 12.2.0, FreeType 2.14.3, numpy 2.3.5,
opencv-python-headless 4.13.0.92, augraphy 8.2.6, faker 40.37.0 — differing only
in architecture: `arm64 Darwin` against `x86_64 Linux`.

Three defects were found. **The first is already fixed** and is recorded here
only because it is what made the other two visible.

| # | Defect | Status |
|---|---|---|
| 1 | `ImageFont.truetype` chose its layout engine from the wheel: Raqm on manylinux, Basic on macOS. Same font, different glyph layout, different pixels. | **Fixed** (`2c87f45`), pinned to `Layout.BASIC` |
| 2 | Degraded pixels differ because augraphy's augmentations evaluate transcendental functions, which are not portable. | This spec, §3 |
| 3 | Clean pages with *identical pixels* still produce different PNG bytes, because zlib-ng and zlib compress differently — and the manifest hashes bytes. | This spec, §5 |

After defect 1 was fixed, all 189 clean pages became pixel-identical across both
machines (`pixels=051a4b67c5664a52` on each). That is the primary
transcription corpus, and it is now portable. What remains is the degraded half
and the definition of corpus identity.

## 2. The measurement that names the cause

`probe_phases.py` hashes the image after each of the four degradation phases.
The clean input was identical on both machines (`462ce0caddde0ae2`) and the
**first** phase already diverged, so geometry, marks and camera are excluded as
causes — they inherit a difference rather than introduce one.

`probe_augmentations.py` then ran each augmentation alone, through the real
seeded pipeline. All three diverge, and each is stable on its own machine:

| augmentation | arm64 Darwin | x86_64 Linux |
|---|---|---|
| `InkBleed` | `b27ab86f377b3e41` | `52e1cfe4126792b9` |
| `LightingGradient` | `295e9c97c3454920` | `78f3a7fec69ce7dc` |
| `ShadowCast` | `09799d81a4f2ed74` | `d8485f448c8c1b43` |

Three unrelated effects failing the same way indicates one shared cause. A
direct probe of numpy confirmed it:

| operation | portable? |
|---|---|
| `+ − × ÷` (float32 and float64) | **yes**, identical |
| `sqrt` | **yes**, identical |
| `sum` reduction | **yes**, identical |
| `exp`, `log`, `pow`, `sin` | **no**, every one differs |

This is what IEEE-754 promises and does not promise. The standard **mandates**
correctly-rounded results for the four basic operations and `sqrt`, so those are
bit-identical on any conforming platform. It says **nothing** about
transcendental functions: those come from the platform's libm, and glibc's
differ from Apple's in the final bits. A last-bit difference in a brightness
ramp is then amplified by blending, warping and JPEG quantisation into a
visibly different hash.

## 3. Decisions taken

| Decision | Chosen | Rejected, and why |
|---|---|---|
| The rule | **The pixel path may use only `+ − × ÷`, `sqrt`, comparisons, and integer arithmetic.** No `exp`, `log`, `pow`, `sin`, `cos`, `tanh` on any value that reaches a pixel. | "Round the output to hide small differences" does not work: the differences are amplified before they are rounded, and a corpus that is *nearly* identical still fails a hash. |
| How to satisfy it | **Reimplement `InkBleed`, `LightingGradient` and `ShadowCast` in this repository**, and drop augraphy. | Patching augraphy is not viable: it is a third-party dependency whose internals would have to be forked, and the fork maintained. Pinning a libm is not possible — it is the platform's. |
| Precedent | This is the treatment `config/degradation.yml` already records for `Folding` and `DirtyRollers`: dropped as not reproducible, reimplemented as `marks:` in `generators/degradation/geometry.py`, drawing randomness from the tier's own seeded generator. | — |
| Gaussian blur | **Replace `cv2.GaussianBlur` with a box-blur cascade.** Three passes of a box blur approximate a Gaussian closely and use only integer addition and division. | OpenCV builds its kernel with `std::exp`, so it is subject to the same defect. Keeping it would leave a transcendental in the pixel path. |
| Corpus identity | **The manifest gains `pixels_sha256`**, and that becomes what identifies an image. `sha256` stays, and keeps its existing job. | Deterministic PNG encoding is not achievable: the deflate implementation is compiled into the Pillow wheel. Storing images uncompressed is deterministic but takes the clean corpus from 35 MB to over a gigabyte. |
| Visual fidelity | **Re-derive the effects to look right, rather than to match today's output.** | Matching augraphy bit-for-bit is impossible by construction — that is the defect. Attempting to approximate it closely would trade a clear implementation for a worse one and still not match. |

## 4. The three effects, re-derived

Each is simple enough that an exact integer formulation is straightforward.
Parameters keep their current YAML names and ranges, so `config/degradation.yml`
needs no schema change and the severity ladders retune only if measurement says
they must.

### 4.1 `LightingGradient`

A brightness falloff across the page: brightest at one edge, dimmest at the
other, with `max_brightness` setting the ceiling and `direction` the angle.

Built as a per-pixel multiplier from a **linear ramp along the rotated axis**,
evaluated in fixed point. The direction vector uses `sqrt` for normalisation,
which is exact. A quadratic falloff is available with a multiply if a linear one
reads too flat; neither needs `exp`.

### 4.2 `ShadowCast`

A soft-edged darkening over one side of the page, at a sampled `opacity`.

Built as a ramp perpendicular to the named `side`, with the soft edge produced
by the **same box-blur cascade** used for the Gaussian replacement, applied to a
uint8 mask. Composited with an integer blend: `out = (base * (255 − a) + shadow
* a + 127) // 255`, which is exact and needs no float at all.

### 4.3 `InkBleed`

Ink spreading slightly outward from dark strokes, at a sampled `intensity` with
a `kernel` size.

Morphological dilation of the dark channel — `cv2.dilate` on uint8 is integer
and exact — blended toward the original by an integer weight derived from
`intensity`. This is the closest of the three to what augraphy already does, and
the one most likely to look unchanged.

## 5. Corpus identity becomes the pixels

Even with §3 and §4 done, two machines will still write **different PNG bytes
from identical pixels**, because deflate differs between zlib-ng and zlib. That
is measured: after the layout fix, both machines produced `pixels=051a4b67c5664a52`
and `bytes=aaab119e3fbcf485` versus `3f0a8b3a031dd16f`.

So `manifest_record` gains one field:

```json
{"image": "images/CASE001_invoices.png",
 "sha256": "…",            // the file, unchanged in meaning
 "pixels_sha256": "…",     // sha256 of the decoded RGB pixel buffer
 "transcript": "…", "transcript_sha256": "…",
 "doc_type": "invoices", "family": "clean", "severity": "none"}
```

The two hashes answer different questions, and both are worth keeping:

- **`sha256`** catches a truncated or corrupted transfer. It is a property of
  the file that arrived.
- **`pixels_sha256`** identifies the *image*. It is stable across encoders,
  zlib builds and architectures, and it is what a vintage check should compare.

This is additive: no existing field moves or changes meaning. It also covers
JPEG, where libjpeg-turbo's SIMD paths raise the same question the clean PNGs
already answered — one mechanism, both formats.

**The consuming repository must follow.** `bank-statement-error-analysis`
verifies `row["sha256"]` in `evaluation/cli.py:105-134` before scoring anything.
It should verify `pixels_sha256` for vintage identity, and may keep the byte
hash as a transfer check. That is a change in a second repository and is called
out here so it is not discovered late.

## 6. Testing

- Every operation in the pixel path is `+ − × ÷ sqrt`, comparison or integer —
  enforced by an **AST scan** over `generators/degradation/`, in the manner of
  `tests/scoring/test_boundaries.py`, rather than by review. The scan names
  `numpy.exp`, `numpy.log`, `numpy.power`, `**` on float arrays, and the
  trigonometric family.
- `augraphy` is imported nowhere. The same scan enforces it, and
  `environment.yml` and `build_corpus.sh` lose their augraphy handling.
- Each re-derived effect is byte-identical across repeated runs at one seed, and
  differs between seeds — reproducibility and not-a-no-op, separately.
- `pixels_sha256` in the manifest matches the decoded image, for every record of
  every corpus.
- A page whose pixels are identical but whose file bytes differ compares **equal**
  by `pixels_sha256` and **unequal** by `sha256`. This pins the distinction the
  whole section rests on, and would have failed before the change.
- The degradation ladders still fall monotonically
  (`tests/test_degradation_ladder_monotonic.py` continues to apply, since
  parameters keep their names).
- **Cross-machine verification is a manual gate, not a test:** `probe_phases.py`
  and `probe_augmentations.py` must agree on two architectures before the
  corpus is declared portable. No single-host suite can assert this.

## 7. Consequences

- **The degraded images are a new vintage.** The effects are re-derived, not
  ported, so they will not match today's pixel for pixel. Predictions already
  scored against `corpus_20260902`'s degraded corpora are invalid.
- **The clean corpus is unaffected.** It contains no augraphy work, and the
  layout fix is already in. Its pixels do not move.
- **Severity ladders may need retuning.** The tiers were tuned against
  augraphy's output; re-derived effects at the same parameters may land
  elsewhere on the difficulty scale. The measurement tooling for this exists
  and was used on 2026-09-01 to correct `photo-heavy`'s inverted shadow and the
  `scan-light`/`scan-moderate` collapse.
- **The environment gets simpler.** Dropping augraphy removes the `--no-deps`
  install, the GUI-versus-headless OpenCV hazard `build_corpus.sh` currently
  polices, numba, and the numpy ceiling numba imposes.

## 8. Out of scope

- **Making augraphy portable.** It is a third-party library evaluating libm; the
  decision is to stop using it, not to fix it.
- **Bit-identical JPEG or PNG encoding.** §5 makes it unnecessary by moving
  identity to the pixels.
- **The clean rendering path.** Already portable, and nothing here touches it.
- **Retuning the ladders.** §7 notes it may be needed; the decision belongs to a
  later measurement, as the 2026-09-01 retune was.
- **`extraction_*` and the transcripts.** Degradation moves pixels, not authored
  values, and transcripts were already identical across both machines.

## 9. References

- `generators/degradation/augment.py` — the three augraphy augmentations to replace.
- `generators/degradation/geometry.py` — `cv2.GaussianBlur`, and the `marks:`
  implementations that set the precedent for re-deriving an augraphy effect.
- `config/degradation.yml` — the `Folding`/`DirtyRollers` precedent, stated in
  its header.
- `generators/export.py` — `manifest_record`, which gains `pixels_sha256`.
- `probe_determinism.py`, `probe_phases.py`, `probe_augmentations.py`,
  `compare_vintages.py` — the measurement tools, and the cross-machine gate.
- `bank-statement-error-analysis`, `evaluation/cli.py:105-134` — the consumer
  that verifies image hashes before scoring.
