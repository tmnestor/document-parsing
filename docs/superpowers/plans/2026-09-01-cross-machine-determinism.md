# Cross-machine determinism Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a degraded corpus render byte-identically on every machine, by removing every transcendental function from the pixel path and moving corpus identity from file bytes to pixels.

**Architecture:** The three augraphy augmentations (`InkBleed`, `LightingGradient`, `ShadowCast`) are re-derived in this repository using only IEEE-754-exact arithmetic, augraphy is dropped entirely, `cv2.GaussianBlur` is replaced by an integer box-blur cascade, and `manifest_record` gains `pixels_sha256` so identity survives a different PNG/JPEG encoder.

**Tech Stack:** Python 3.12, numpy 2.3.5, opencv-python-headless 4.13.0.92 (kept, for `dilate`/`warpPerspective` only), Pillow 12.2.0.

**Spec:** `docs/superpowers/specs/2026-09-01-cross-machine-determinism-design.md`

## Global Constraints

- **The pixel path may use only `+ − × ÷`, `sqrt`, comparisons and integer arithmetic.** No `exp`, `log`, `pow`, `**` on float arrays, `sin`, `cos`, `tan`, `tanh` on any value reaching a pixel. Measured: those four basic operations and `sqrt` are bit-identical across `arm64 Darwin` and `x86_64 Linux`; `exp`/`log`/`pow`/`sin` all differ.
- **All randomness comes from the tier's own seeded generator**, passed explicitly. No global RNG (`np.random.seed`, `random.seed`, `cv2.setRNGSeed`).
- Run everything from the repository root. Env: `conda run -n docparse <command>`.
- Tests: `conda run -n docparse pytest tests/ --ignore=tests/scoring -q`. `tests/` is gitignored — write and run, never `git add`.
- Gates before every commit: `pytest`, `ruff check --fix --ignore ARG001,ARG002,F841 .`, `ruff format .`, `mypy generators --ignore-missing-imports`.
- Line length 108. Google-style docstrings. Python 3.12 types (`X | Y`). `pathlib.Path` for paths.
- Fail-fast diagnostics carry four elements: What / Where / Expected / Recover.
- NEVER `--no-verify`. NEVER a bash heredoc (it hangs the tool — use Write/Edit). `cat` returns empty through the tool — use `head`/`sed -n`/Read.
- YAML is the single source of truth: no Python defaults shadowing `config/degradation.yml`.

## File Structure

| File | Responsibility |
|---|---|
| `generators/degradation/kernels.py` | **New.** Integer box blur and the sigma→radius conversion. Shared by the shadow's soft edge and the Gaussian replacement. |
| `generators/degradation/effects.py` | **New.** The three re-derived effects, each a callable class taking an explicit `rng`. |
| `generators/degradation/augment.py` | Registry now points at `effects.py`; `apply_augraphy` loses `AugraphyPipeline`, the temp-dir chdir and the global seed. |
| `generators/degradation/geometry.py` | `cv2.GaussianBlur` → `kernels.box_blur`. |
| `generators/export.py` | `manifest_record` gains `pixels_sha256`. |
| `environment.yml`, `build_corpus.sh`, `config/degradation.yml` | Augraphy removed from install, verification and commentary. |

---

### Task 1: Integer box blur

**Files:**
- Create: `generators/degradation/kernels.py`
- Test: `tests/test_degradation_kernels.py`

**Interfaces:**
- Produces: `box_blur(plane: np.ndarray, radius: int, passes: int = 3) -> np.ndarray` (uint8 in, uint8 out, 2-D); `radius_for_sigma(sigma: float, passes: int = 3) -> int`.

- [ ] **Step 1: Write the failing tests**

```python
import numpy as np
import pytest

from generators.degradation.kernels import box_blur, radius_for_sigma


def test_a_flat_field_is_unchanged():
    """The classic blur bug: rounding that drifts a constant region."""
    flat = np.full((40, 40), 200, np.uint8)
    assert np.array_equal(box_blur(flat, 3), flat)


def test_the_same_input_blurs_identically_every_time():
    rng = np.random.default_rng(0)
    plane = rng.integers(0, 256, (60, 80), dtype=np.uint8)
    assert np.array_equal(box_blur(plane, 3), box_blur(plane, 3))


def test_a_radius_of_zero_is_a_no_op():
    rng = np.random.default_rng(1)
    plane = rng.integers(0, 256, (20, 20), dtype=np.uint8)
    assert np.array_equal(box_blur(plane, 0), plane)


def test_blurring_spreads_a_single_bright_pixel():
    plane = np.zeros((21, 21), np.uint8)
    plane[10, 10] = 255
    blurred = box_blur(plane, 2)
    assert blurred[10, 10] < 255
    assert blurred[10, 12] > 0


def test_output_stays_in_range_and_dtype():
    rng = np.random.default_rng(2)
    plane = rng.integers(0, 256, (32, 32), dtype=np.uint8)
    blurred = box_blur(plane, 4)
    assert blurred.dtype == np.uint8
    assert 0 <= int(blurred.min()) and int(blurred.max()) <= 255


@pytest.mark.parametrize("sigma", [0.5, 1.0, 3.0, 8.0])
def test_radius_for_sigma_grows_with_sigma(sigma):
    assert radius_for_sigma(sigma) >= 0
    assert radius_for_sigma(sigma * 2) >= radius_for_sigma(sigma)
```

- [ ] **Step 2: Run to verify failure**

Run: `conda run -n docparse pytest tests/test_degradation_kernels.py -q`
Expected: FAIL, `ModuleNotFoundError: generators.degradation.kernels`

- [ ] **Step 3: Implement**

```python
"""Blur without a transcendental in sight.

`cv2.GaussianBlur` builds its kernel with `std::exp`, and libm is not portable:
glibc's `exp` and Apple's differ in the last bits, which is enough to change
every image hash. A cascade of box blurs converges on a Gaussian (central limit)
and uses only integer addition, subtraction and division, so it is exact on any
machine. Three passes is the usual point of diminishing returns.
"""

import numpy as np


def _blur_axis(plane: np.ndarray, radius: int, axis: int) -> np.ndarray:
    """One moving-average pass along `axis`, in exact integer arithmetic.

    A prepended zero turns the cumulative sum into a prefix sum, so each output
    is one subtraction. `+ window // 2` rounds half up rather than truncating,
    which is what keeps a flat field flat.
    """
    window = 2 * radius + 1
    pad = [(0, 0), (0, 0)]
    pad[axis] = (radius, radius)
    padded = np.pad(plane.astype(np.int64), pad, mode="edge")
    cumulative = np.cumsum(padded, axis=axis, dtype=np.int64)
    zero = np.zeros_like(np.take(cumulative, [0], axis=axis))
    cumulative = np.concatenate([zero, cumulative], axis=axis)
    length = plane.shape[axis]
    upper = np.take(cumulative, np.arange(window, window + length), axis=axis)
    lower = np.take(cumulative, np.arange(0, length), axis=axis)
    return (upper - lower + window // 2) // window


def box_blur(plane: np.ndarray, radius: int, passes: int = 3) -> np.ndarray:
    """Blur a 2-D uint8 plane by `passes` separable box filters.

    Args:
        plane: 2-D uint8 array.
        radius: Half-width of the box. 0 returns a copy.
        passes: How many times to apply it; 3 approximates a Gaussian well.

    Returns:
        A uint8 array of the same shape.
    """
    if radius < 1:
        return plane.copy()
    out = plane.astype(np.int64)
    for _ in range(passes):
        out = _blur_axis(out, radius, 0)
        out = _blur_axis(out, radius, 1)
    return out.astype(np.uint8)


def radius_for_sigma(sigma: float, passes: int = 3) -> int:
    """The box radius whose cascade has roughly the variance of `sigma`.

    Solves `sigma^2 = passes * ((2r+1)^2 - 1) / 12` for r. Uses only `sqrt`,
    which IEEE-754 requires to be correctly rounded, so it is portable.
    """
    if sigma <= 0:
        return 0
    return max(0, int((np.sqrt(12.0 * sigma * sigma / passes + 1.0) - 1.0) / 2.0))
```

- [ ] **Step 4: Run to verify pass**

Run: `conda run -n docparse pytest tests/test_degradation_kernels.py -q`
Expected: PASS (6 tests)

- [ ] **Step 5: Gates and commit**

```bash
conda run -n docparse ruff check --fix --ignore ARG001,ARG002,F841 . && conda run -n docparse ruff format . && conda run -n docparse mypy generators --ignore-missing-imports
git add generators/degradation/kernels.py
git commit -m "✨ feat: add an integer box blur with no transcendental"
```

---

### Task 2: Re-derive LightingGradient

**Files:**
- Create: `generators/degradation/effects.py`
- Test: `tests/test_degradation_effects.py`

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces: `class LightingGradient` with `__init__(self, max_brightness: int, direction: int)` and `__call__(self, image: np.ndarray, rng: np.random.Generator) -> np.ndarray`. Parameter names match `_PARAM_NAMES["LightingGradient"]` in `augment.py`, so the YAML and `_build` need no change.

- [ ] **Step 1: Write the failing tests**

```python
import numpy as np

from generators.degradation.effects import LightingGradient


def _page(value: int = 220) -> np.ndarray:
    return np.full((60, 80, 3), value, np.uint8)


def test_the_gradient_darkens_one_side_more_than_the_other():
    out = LightingGradient(max_brightness=235, direction=90)(_page(), np.random.default_rng(0))
    top = int(out[:10].mean())
    bottom = int(out[-10:].mean())
    assert top != bottom, "a gradient that is flat is not a gradient"


def test_it_never_brightens_past_max_brightness():
    out = LightingGradient(max_brightness=200, direction=45)(_page(255), np.random.default_rng(0))
    assert int(out.max()) <= 200


def test_it_is_reproducible_at_one_seed():
    first = LightingGradient(max_brightness=235, direction=45)(_page(), np.random.default_rng(7))
    second = LightingGradient(max_brightness=235, direction=45)(_page(), np.random.default_rng(7))
    assert np.array_equal(first, second)


def test_direction_changes_the_result():
    across = LightingGradient(max_brightness=235, direction=0)(_page(), np.random.default_rng(0))
    down = LightingGradient(max_brightness=235, direction=90)(_page(), np.random.default_rng(0))
    assert not np.array_equal(across, down)


def test_shape_and_dtype_are_preserved():
    out = LightingGradient(max_brightness=235, direction=45)(_page(), np.random.default_rng(0))
    assert out.shape == (60, 80, 3) and out.dtype == np.uint8
```

- [ ] **Step 2: Run to verify failure**

Run: `conda run -n docparse pytest tests/test_degradation_effects.py -q`
Expected: FAIL, `ModuleNotFoundError: generators.degradation.effects`

- [ ] **Step 3: Implement**

Create `effects.py` with this module docstring and the class:

```python
"""The ink and paper effects, re-derived to be portable.

These replace augraphy's `InkBleed`, `LightingGradient` and `ShadowCast`, which
were measured to produce different pixels on arm64 macOS and x86_64 Linux at
identical library versions. The cause was libm: augraphy evaluates
transcendental functions, and IEEE-754 does not constrain them.

Everything here uses only `+ - * /`, `sqrt`, comparisons and integer arithmetic,
all of which the standard requires to be correctly rounded. Randomness comes
from the caller's seeded generator, never a global.

This is the same treatment `config/degradation.yml` records for `Folding` and
`DirtyRollers`: an augraphy effect that cannot reproduce is rebuilt here.
"""

import numpy as np


def _ramp(height: int, width: int, direction: int) -> np.ndarray:
    """A 0..1 ramp across the page along `direction`, in degrees.

    Built from integer coordinates and normalised by a dot product. The only
    non-integer operation is the `sqrt` that normalises the direction vector,
    which is exact. Degrees are turned into a direction with a small exact
    lookup rather than `cos`/`sin`, which are not portable.
    """
    # Quarter-turn lookup: the YAML only ever declares 0, 45 or 90.
    axes = {0: (1, 0), 45: (1, 1), 90: (0, 1), 135: (-1, 1), 180: (-1, 0)}
    dx, dy = axes.get(int(direction) % 180, (1, 1))
    length = float(np.sqrt(float(dx * dx + dy * dy)))
    ys = np.arange(height, dtype=np.float64).reshape(-1, 1)
    xs = np.arange(width, dtype=np.float64).reshape(1, -1)
    projected = (xs * dx + ys * dy) / length
    low = float(projected.min())
    high = float(projected.max())
    span = high - low if high > low else 1.0
    return (projected - low) / span


class LightingGradient:
    """Uneven illumination: brightest at one edge, falling off across the page.

    Args:
        max_brightness: Ceiling for the lit end, 0-255.
        direction: Angle of the falloff in degrees.
    """

    def __init__(self, max_brightness: int, direction: int) -> None:
        self.max_brightness = int(max_brightness)
        self.direction = int(direction)

    def __call__(self, image: np.ndarray, rng: np.random.Generator) -> np.ndarray:
        height, width = image.shape[:2]
        ramp = _ramp(height, width, self.direction)
        # Linear falloff from the ceiling down to 78% of it: a multiplicative
        # field, so paper and ink dim together as real lighting does.
        ceiling = self.max_brightness / 255.0
        scale = ceiling * (1.0 - 0.22 * ramp)
        scaled = image.astype(np.float64) * scale[:, :, None]
        return np.clip(scaled + 0.5, 0, 255).astype(np.uint8)
```

- [ ] **Step 4: Run to verify pass**

Run: `conda run -n docparse pytest tests/test_degradation_effects.py -q`
Expected: PASS (5 tests)

- [ ] **Step 5: Gates and commit**

```bash
git add generators/degradation/effects.py
git commit -m "✨ feat: re-derive LightingGradient without a transcendental"
```

---

### Task 3: Re-derive ShadowCast

**Files:**
- Modify: `generators/degradation/effects.py`
- Test: `tests/test_degradation_effects.py` (append)

**Interfaces:**
- Consumes: `box_blur`, `radius_for_sigma` from Task 1; `_ramp` from Task 2.
- Produces: `class ShadowCast` with `__init__(self, shadow_side: str, shadow_opacity_range: tuple[float, float])` and the same `__call__(image, rng)`. Names match `_PARAM_NAMES["ShadowCast"]`.

- [ ] **Step 1: Write the failing tests**

```python
import numpy as np
import pytest

from generators.degradation.effects import ShadowCast


def _page(value: int = 220) -> np.ndarray:
    return np.full((60, 80, 3), value, np.uint8)


def test_the_named_side_is_darkened():
    out = ShadowCast(shadow_side="top", shadow_opacity_range=(0.5, 0.5))(
        _page(), np.random.default_rng(0)
    )
    assert int(out[:10].mean()) < int(out[-10:].mean())


def test_the_opposite_side_darkens_the_other_end():
    out = ShadowCast(shadow_side="bottom", shadow_opacity_range=(0.5, 0.5))(
        _page(), np.random.default_rng(0)
    )
    assert int(out[-10:].mean()) < int(out[:10].mean())


def test_a_heavier_opacity_darkens_further():
    light = ShadowCast(shadow_side="top", shadow_opacity_range=(0.2, 0.2))(
        _page(), np.random.default_rng(0)
    )
    heavy = ShadowCast(shadow_side="top", shadow_opacity_range=(0.6, 0.6))(
        _page(), np.random.default_rng(0)
    )
    assert int(heavy[:10].mean()) < int(light[:10].mean())


def test_it_is_reproducible_at_one_seed():
    kwargs = {"shadow_side": "top", "shadow_opacity_range": (0.3, 0.6)}
    first = ShadowCast(**kwargs)(_page(), np.random.default_rng(11))
    second = ShadowCast(**kwargs)(_page(), np.random.default_rng(11))
    assert np.array_equal(first, second)


def test_an_unknown_side_fails_with_a_diagnostic():
    with pytest.raises(ValueError) as excinfo:
        ShadowCast(shadow_side="sideways", shadow_opacity_range=(0.3, 0.5))(
            _page(), np.random.default_rng(0)
        )
    message = str(excinfo.value)
    for element in ("What:", "Where:", "Expected:", "Recover:"):
        assert element in message
```

- [ ] **Step 2: Run to verify failure**

Run: `conda run -n docparse pytest tests/test_degradation_effects.py -q -k Shadow`
Expected: FAIL, `ImportError: cannot import name 'ShadowCast'`

- [ ] **Step 3: Implement**

Append to `effects.py`:

```python
_SIDES = {"top": (0, 1), "bottom": (0, -1), "left": (1, 1), "right": (1, -1)}


class ShadowCast:
    """A soft-edged shadow falling across one side of the page.

    Args:
        shadow_side: Which edge the shadow falls from: top, bottom, left, right.
        shadow_opacity_range: Range to sample the darkest opacity from.
    """

    def __init__(self, shadow_side: str, shadow_opacity_range: tuple[float, float]) -> None:
        self.shadow_side = str(shadow_side)
        self.shadow_opacity_range = (
            float(shadow_opacity_range[0]),
            float(shadow_opacity_range[1]),
        )

    def __call__(self, image: np.ndarray, rng: np.random.Generator) -> np.ndarray:
        if self.shadow_side not in _SIDES:
            raise ValueError(
                "Unknown shadow side.\n"
                f"  What:     ShadowCast was given side '{self.shadow_side}', "
                "which names no edge of the page.\n"
                "  Where:    config/degradation.yml -> a paper-phase ShadowCast entry\n"
                f"  Expected: one of {sorted(_SIDES)}, e.g. 'side: top'.\n"
                "  Recover:  correct the 'side:' value in that entry."
            )
        height, width = image.shape[:2]
        axis, sign = _SIDES[self.shadow_side]
        ramp = _ramp(height, width, 0 if axis else 90)
        if sign > 0:
            ramp = 1.0 - ramp

        low, high = self.shadow_opacity_range
        opacity = float(rng.uniform(low, high))

        # A uint8 mask, softened by the same integer blur the Gaussian
        # replacement uses, so no transcendental builds the falloff.
        mask = np.clip(ramp * opacity * 255.0 + 0.5, 0, 255).astype(np.uint8)
        mask = box_blur(mask, radius_for_sigma(max(height, width) * 0.02))

        alpha = mask.astype(np.int32)[:, :, None]
        base = image.astype(np.int32)
        # Exact integer blend toward black: no float rounding to disagree about.
        return ((base * (255 - alpha) + 127) // 255).astype(np.uint8)
```

Add `from generators.degradation.kernels import box_blur, radius_for_sigma` to the imports.

- [ ] **Step 4: Run to verify pass**

Run: `conda run -n docparse pytest tests/test_degradation_effects.py -q`
Expected: PASS (10 tests)

- [ ] **Step 5: Gates and commit**

```bash
git add generators/degradation/effects.py
git commit -m "✨ feat: re-derive ShadowCast with an integer blend"
```

---

### Task 4: Re-derive InkBleed

**Files:**
- Modify: `generators/degradation/effects.py`
- Test: `tests/test_degradation_effects.py` (append)

**Interfaces:**
- Produces: `class InkBleed` with `__init__(self, intensity_range: tuple[float, float], kernel_size: tuple[int, int])` and `__call__(image, rng)`. Names match `_PARAM_NAMES["InkBleed"]`; `kernel_size` arrives as a `(w, h)` pair via `_SQUARE_KERNEL_KEYS`.

- [ ] **Step 1: Write the failing tests**

```python
import numpy as np

from generators.degradation.effects import InkBleed


def _text_page() -> np.ndarray:
    page = np.full((60, 80, 3), 255, np.uint8)
    page[20:24, 10:70] = 0          # a dark stroke
    return page


def test_ink_spreads_outward_from_a_stroke():
    out = InkBleed(intensity_range=(0.45, 0.45), kernel_size=(5, 5))(
        _text_page(), np.random.default_rng(0)
    )
    # The row just outside the stroke should have darkened.
    assert int(out[19, 40].mean()) < 255


def test_a_blank_page_is_left_alone():
    blank = np.full((40, 40, 3), 255, np.uint8)
    out = InkBleed(intensity_range=(0.4, 0.4), kernel_size=(5, 5))(
        blank, np.random.default_rng(0)
    )
    assert np.array_equal(out, blank), "there is no ink to bleed"


def test_a_heavier_intensity_spreads_more():
    light = InkBleed(intensity_range=(0.05, 0.05), kernel_size=(5, 5))(
        _text_page(), np.random.default_rng(0)
    )
    heavy = InkBleed(intensity_range=(0.45, 0.45), kernel_size=(5, 5))(
        _text_page(), np.random.default_rng(0)
    )
    assert int(heavy.mean()) < int(light.mean())


def test_it_is_reproducible_at_one_seed():
    kwargs = {"intensity_range": (0.25, 0.45), "kernel_size": (5, 5)}
    first = InkBleed(**kwargs)(_text_page(), np.random.default_rng(3))
    second = InkBleed(**kwargs)(_text_page(), np.random.default_rng(3))
    assert np.array_equal(first, second)
```

- [ ] **Step 2: Run to verify failure**

Run: `conda run -n docparse pytest tests/test_degradation_effects.py -q -k InkBleed`
Expected: FAIL, `ImportError: cannot import name 'InkBleed'`

- [ ] **Step 3: Implement**

Append to `effects.py`:

```python
class InkBleed:
    """Ink spreading outward from dark strokes, as on absorbent paper.

    Args:
        intensity_range: Range to sample the bleed strength from, 0-1.
        kernel_size: `(w, h)` of the dilation kernel; the YAML declares one int.
    """

    def __init__(
        self, intensity_range: tuple[float, float], kernel_size: tuple[int, int]
    ) -> None:
        self.intensity_range = (float(intensity_range[0]), float(intensity_range[1]))
        self.kernel_size = (int(kernel_size[0]), int(kernel_size[1]))

    def __call__(self, image: np.ndarray, rng: np.random.Generator) -> np.ndarray:
        import cv2

        intensity = float(rng.uniform(*self.intensity_range))
        width, height = self.kernel_size
        kernel = np.ones((max(1, height), max(1, width)), np.uint8)

        # Dilating the INVERSE grows the dark strokes. cv2.dilate on uint8 is a
        # max filter: integer, exact, and portable.
        spread = 255 - cv2.dilate(255 - image, kernel)

        weight = int(round(intensity * 255))
        base = image.astype(np.int32)
        toward = spread.astype(np.int32)
        blended = (base * (255 - weight) + toward * weight + 127) // 255
        return blended.astype(np.uint8)
```

- [ ] **Step 4: Run to verify pass**

Run: `conda run -n docparse pytest tests/test_degradation_effects.py -q`
Expected: PASS (14 tests)

- [ ] **Step 5: Gates and commit**

```bash
git add generators/degradation/effects.py
git commit -m "✨ feat: re-derive InkBleed as an integer dilation blend"
```

---

### Task 5: Point the registry at the re-derived effects

**Files:**
- Modify: `generators/degradation/augment.py`
- Test: `tests/test_degradation_augment.py`

**Interfaces:**
- Consumes: `InkBleed`, `LightingGradient`, `ShadowCast` from `effects.py`.
- Produces: `apply_augraphy(image, tier, seed)` keeps its signature — callers in `__init__.py` are unchanged.

**Context:** `_build` and `_PARAM_NAMES` need no change: the re-derived classes deliberately take the same keyword names. What changes is where `AUGMENTATIONS` points, and that `apply_augraphy` no longer needs `AugraphyPipeline`, the temp-dir `chdir`, or `np.random.seed`. `DirtyDrum` is registered but declared unused by `config/degradation.yml`; remove it with augraphy.

- [ ] **Step 1: Write the failing test**

```python
import numpy as np

from generators.degradation.augment import apply_augraphy
from generators.degradation.tiers import Tier
from PIL import Image


def _tier(**over) -> Tier:
    base = {
        "family": "t", "name": "t", "suffix": "t", "description": "d",
        "ink": [], "paper": [], "marks": [],
        "geometry": {"mode": "skew", "rotation_deg": [0, 0], "margin": [0, 0]},
        "camera": {"blur": [0, 0], "noise_sigma": [0, 0], "jpeg": [100, 100]},
    }
    return Tier(**{**base, **over})


def test_the_phase_is_reproducible_at_one_seed():
    page = Image.new("RGB", (80, 60), "white")
    tier = _tier(ink=[{"augmentation": "InkBleed", "intensity": [0.2, 0.4], "kernel": 3}])
    first = np.asarray(apply_augraphy(page.copy(), tier, 99))
    second = np.asarray(apply_augraphy(page.copy(), tier, 99))
    assert np.array_equal(first, second)


def test_no_augmentations_returns_the_page_unchanged():
    page = Image.new("RGB", (40, 40), "white")
    assert np.array_equal(np.asarray(apply_augraphy(page.copy(), _tier(), 1)), np.asarray(page))


def test_the_module_does_not_import_augraphy():
    import generators.degradation.augment as module

    assert "augraphy" not in module.__dict__
```

- [ ] **Step 2: Run to verify failure**

Run: `conda run -n docparse pytest tests/test_degradation_augment.py -q`
Expected: FAIL on `test_the_module_does_not_import_augraphy`

- [ ] **Step 3: Implement**

In `augment.py`: delete the `try: from augraphy import ...` block and its `ImportError` diagnostic, delete `import contextlib` and `import tempfile`, import the three classes from `generators.degradation.effects`, drop `"DirtyDrum"` from `AUGMENTATIONS` and `_PARAM_NAMES`, and replace the body of `apply_augraphy` after the `_build` calls with:

```python
    if not ink and not paper:
        return image.copy()

    # One seeded generator for the whole phase, passed explicitly. The previous
    # implementation set NumPy's global seed because Augraphy sampled from it in
    # places its own `random_seed` did not reach; nothing here reads a global.
    rng = np.random.default_rng(seed)
    frame = np.array(image.convert("RGB"))
    for effect in (*ink, *paper):
        frame = effect(frame, rng)
    return Image.fromarray(np.asarray(frame, dtype=np.uint8), "RGB")
```

Update `_build`'s return annotation and its docstring (it no longer returns "the constructed Augraphy augmentation").

- [ ] **Step 4: Run to verify pass**

Run: `conda run -n docparse pytest tests/ --ignore=tests/scoring -q`
Expected: PASS. The corpus byte-identity test **will fail** — degraded output has legitimately changed. Update its expected hashes in the same commit, and say so in the message.

- [ ] **Step 5: Gates and commit**

```bash
git add generators/degradation/augment.py
git commit -m "♻️ refactor: run the re-derived effects instead of augraphy"
```

---

### Task 6: Replace cv2.GaussianBlur in geometry

**Files:**
- Modify: `generators/degradation/geometry.py`
- Test: `tests/test_degradation_geometry.py`

**Interfaces:**
- Consumes: `box_blur`, `radius_for_sigma` from Task 1.

**Context:** `geometry.py:~150` blurs the warp's alpha channel:
`cv2.GaussianBlur(warped[:, :, 3], (0, 0), max(w, h) * 0.02) * 0.45`. OpenCV builds that kernel with `std::exp`, so it carries the same defect.

- [ ] **Step 1: Write the failing test**

```python
import ast
from pathlib import Path


def test_geometry_calls_no_gaussian_blur():
    """OpenCV builds its Gaussian kernel with std::exp, which is not portable."""
    source = Path("generators/degradation/geometry.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    called = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "GaussianBlur" not in called
```

- [ ] **Step 2: Run to verify failure**

Run: `conda run -n docparse pytest tests/test_degradation_geometry.py -q`
Expected: FAIL, `GaussianBlur` present.

- [ ] **Step 3: Implement**

Replace that call with the box-blur cascade, keeping the same `0.45` weight:

```python
    sigma = max(w, h) * 0.02
    softened = box_blur(warped[:, :, 3], radius_for_sigma(sigma)) * 0.45
```

Add the import and a comment naming why, in the style of the file.

- [ ] **Step 4: Run to verify pass**

Run: `conda run -n docparse pytest tests/ --ignore=tests/scoring -q`
Expected: PASS, with degraded-output hashes updated as in Task 5.

- [ ] **Step 5: Gates and commit**

```bash
git add generators/degradation/geometry.py
git commit -m "♻️ refactor: blur the warp alpha without a Gaussian kernel"
```

---

### Task 7: Remove augraphy from the environment

**Files:**
- Modify: `environment.yml`, `build_corpus.sh`, `config/degradation.yml`
- Test: `tests/test_no_augraphy.py`

- [ ] **Step 1: Write the failing test**

```python
from pathlib import Path


def test_nothing_installs_or_mentions_augraphy_as_a_dependency():
    for name in ("environment.yml", "build_corpus.sh"):
        text = Path(name).read_text(encoding="utf-8").lower()
        assert "augraphy" not in text, f"{name} still references augraphy"


def test_no_module_imports_augraphy():
    for path in Path("generators").rglob("*.py"):
        assert "augraphy" not in path.read_text(encoding="utf-8"), path
```

- [ ] **Step 2: Run to verify failure**

Expected: FAIL on both.

- [ ] **Step 3: Implement**

- `environment.yml`: delete the augraphy paragraph from the header comment and the `numba` pin (only augraphy needed it). Keep `numpy`, `opencv-python-headless`, `scikit-image`, `scikit-learn`, `requests`, `matplotlib` **only if** something still imports them — check with `grep -rn "skimage\|sklearn\|matplotlib\|requests" generators/` and remove those that nothing uses, stating the removal in the comment.
- `build_corpus.sh`: delete `AUGRAPHY_VERSION`, the `pip install --no-deps` block, and the GUI-OpenCV verification. Keep the `import cv2, numpy` check.
- `config/degradation.yml`: rewrite the "DELIBERATELY NOT USED" paragraphs — they describe augraphy's catalogue. Replace with a short note that the effects are implemented in `generators/degradation/effects.py` and why (portability), citing the spec.

- [ ] **Step 4: Run to verify pass**

Run: `conda run -n docparse pytest tests/ --ignore=tests/scoring -q` and `DEGRADE=yes ./build_corpus.sh` in a scratch `EVAL_ROOT`, to prove a clean build no longer needs augraphy.

- [ ] **Step 5: Gates and commit**

```bash
git add environment.yml build_corpus.sh config/degradation.yml
git commit -m "🔥 remove: drop the augraphy dependency"
```

---

### Task 8: Guard the arithmetic rule

**Files:**
- Test: `tests/test_pixel_path_arithmetic.py`

**Context:** This is the invariant the whole plan rests on, and it must be enforced by a scan rather than by review — in the manner of `tests/scoring/test_boundaries.py`.

- [ ] **Step 1: Write the test**

```python
"""No transcendental may reach a pixel.

IEEE-754 mandates correctly-rounded `+ - * /` and `sqrt`; it says nothing about
`exp`, `log`, `pow` or the trigonometric family, which come from the platform's
libm. Measured on two machines at identical library versions, the first group is
bit-identical and the second differs in every case.
"""

import ast
from pathlib import Path

import pytest

FORBIDDEN = {"exp", "log", "log2", "log10", "power", "sin", "cos", "tan", "tanh", "arctan"}
PIXEL_PATH = sorted(Path("generators/degradation").rglob("*.py"))


@pytest.mark.parametrize("path", PIXEL_PATH, ids=lambda p: p.name)
def test_no_transcendental_is_called(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr not in FORBIDDEN, (
                f"{path}:{node.lineno} calls {node.func.attr}, which libm makes "
                "machine-dependent. Use +, -, *, / or sqrt."
            )


@pytest.mark.parametrize("path", PIXEL_PATH, ids=lambda p: p.name)
def test_no_float_exponentiation(path):
    """`x ** 1.3` is `pow` by another name."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Pow):
            assert isinstance(node.right, ast.Constant) and isinstance(node.right.value, int), (
                f"{path}:{node.lineno} raises to a non-integer power."
            )


def test_the_guard_would_catch_a_violation(tmp_path):
    """A scan that cannot fail is worse than no scan."""
    planted = tmp_path / "planted.py"
    planted.write_text("import numpy as np\nx = np.exp(np.zeros(3))\n", encoding="utf-8")
    tree = ast.parse(planted.read_text(encoding="utf-8"))
    calls = {
        n.func.attr for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
    }
    assert calls & FORBIDDEN
```

- [ ] **Step 2: Run**

Run: `conda run -n docparse pytest tests/test_pixel_path_arithmetic.py -q`
Expected: PASS if Tasks 1-6 are complete. Any failure names a file and line to fix.

- [ ] **Step 3: Commit**

Nothing to stage — `tests/` is gitignored. Record completion in the ledger instead.

---

### Task 9: Corpus identity moves to the pixels

**Files:**
- Modify: `generators/export.py`
- Test: `tests/test_export_pixels_hash.py`

**Interfaces:**
- Produces: `manifest_record(...)` returns an eighth key, `pixels_sha256`.

- [ ] **Step 1: Write the failing tests**

```python
import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image

from generators.export import manifest_record


def _pair(root: Path) -> tuple[Path, Path]:
    (root / "images").mkdir(parents=True)
    (root / "transcripts").mkdir(parents=True)
    image = root / "images" / "CASE001_invoices.png"
    Image.new("RGB", (20, 20), "white").save(image)
    transcript = root / "transcripts" / "CASE001_invoices.md"
    transcript.write_text("# x\n", encoding="utf-8")
    return image, transcript


def test_the_record_carries_a_pixel_hash(tmp_path):
    image, transcript = _pair(tmp_path)
    row = manifest_record(image, transcript, "invoices", family="clean", severity="none")
    with Image.open(image) as handle:
        expected = hashlib.sha256(np.asarray(handle.convert("RGB")).tobytes()).hexdigest()
    assert row["pixels_sha256"] == expected


def test_re_encoding_changes_the_bytes_but_not_the_pixels(tmp_path):
    """The distinction the field exists for: zlib differs, the image does not."""
    image, transcript = _pair(tmp_path)
    first = manifest_record(image, transcript, "invoices", family="clean", severity="none")
    with Image.open(image) as handle:
        handle.copy().save(image, compress_level=1)   # same pixels, different bytes
    second = manifest_record(image, transcript, "invoices", family="clean", severity="none")

    assert second["sha256"] != first["sha256"], "the file really did change"
    assert second["pixels_sha256"] == first["pixels_sha256"]
```

- [ ] **Step 2: Run to verify failure**

Expected: FAIL, `KeyError: 'pixels_sha256'`

- [ ] **Step 3: Implement**

Add to `manifest_record`'s returned dict, after `sha256`:

```python
        # Identity of the IMAGE, not of the file. PNG is lossless, so a
        # different zlib compresses identical pixels into different bytes --
        # measured: zlib-ng 1.3.1 and zlib 1.3.2 disagree on every page. The
        # byte hash still catches a truncated transfer; this is what a vintage
        # check compares.
        "pixels_sha256": pixels_sha256_of(image),
```

and a helper beside `sha256_of`:

```python
def pixels_sha256_of(path: Path) -> str:
    """Hash an image's decoded RGB pixels, independent of its encoding."""
    with Image.open(path) as handle:
        return hashlib.sha256(np.asarray(handle.convert("RGB")).tobytes()).hexdigest()
```

- [ ] **Step 4: Run to verify pass**

Run: `conda run -n docparse pytest tests/ --ignore=tests/scoring -q`
Expected: PASS. Fix any test asserting the manifest's exact key count.

- [ ] **Step 5: Gates and commit**

```bash
git add generators/export.py
git commit -m "✨ feat: identify an image by its pixels, not its encoding"
```

---

### Task 10: The consumer verifies the pixel hash

**Files:**
- Modify: `bank-statement-error-analysis/evaluation/cli.py` (a **different repository**, at `/Users/tod/Desktop/bank-statement-error-analysis`)
- Test: `bank-statement-error-analysis/tests/test_score_inputs.py`

**Context:** `evaluation/cli.py:105-134` reads the manifest and refuses to score if any image's `sha256` differs. That check now fails for a legitimately re-encoded corpus. Environment there is `du` for pandas-using modules; the suite runs with `conda run -n du pytest tests/ -q`.

- [ ] **Step 1: Write the failing test**

```python
def test_a_re_encoded_image_still_verifies(tmp_path):
    """Same pixels, different bytes: a different zlib, not a different corpus."""
    corpus = make_corpus(tmp_path / "parsing_1")          # existing helper
    manifest = corpus / "manifest.jsonl"
    rows = [json.loads(line) for line in manifest.read_text().splitlines() if line]
    for row in rows:
        image = corpus / row["image"]
        with Image.open(image) as handle:
            handle.copy().save(image, compress_level=1)
    manifest.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")

    verify_corpus(corpus)          # must not raise
```

- [ ] **Step 2: Run to verify failure**

Expected: FAIL — the byte hash no longer matches.

- [ ] **Step 3: Implement**

Verify `pixels_sha256` when the manifest states it, and keep the byte check as a transfer check that warns rather than refuses. If a manifest predates the field, **refuse** with a four-element diagnostic naming the corpus and telling the reader to re-export — consistent with the no-fallbacks rule applied to `analysis/degradation.py` in commit `2607a94`.

- [ ] **Step 4: Run to verify pass**

Run: `conda run -n du pytest tests/ -q`

- [ ] **Step 5: Commit in that repository**

```bash
git commit -m "🐛 fix: verify a corpus by its pixels, not its encoding"
```

---

## After the plan

**Cross-machine verification is a manual gate and cannot be a test.** Once Tasks 1-9 are merged, rebuild on both architectures and confirm:

```bash
python probe_phases.py <clean>/images/CASE001_bank_statements.png
python probe_augmentations.py <clean>/images/CASE001_bank_statements.png
python compare_vintages.py --fingerprint <corpus_root>
```

Every digest must match across the two machines. Until that has been run on both, the corpus is not portable — only expected to be.

**The severity ladders may need retuning.** The tiers were tuned against augraphy's output; re-derived effects at the same parameters may land elsewhere. Re-measure with the paired-sharpness method used on 2026-09-01, and only then decide.
