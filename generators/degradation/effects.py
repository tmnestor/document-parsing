"""The ink and paper effects, re-derived to be portable.

These replace Augraphy's `InkBleed`, `LightingGradient` and `ShadowCast`, which
were measured to produce different pixels on arm64 macOS and x86_64 Linux at
identical library versions. The cause was libm: Augraphy evaluates
transcendental functions, and IEEE-754 does not constrain them.

Everything here uses only `+ - * /`, `sqrt`, comparisons and integer arithmetic,
all of which the standard requires to be correctly rounded. Randomness comes
from the caller's seeded generator, never a global.

This is the same treatment `config/degradation.yml` records for `Folding` and
`DirtyRollers`: an Augraphy effect that cannot reproduce is rebuilt here.
"""

import numpy as np

from generators.degradation.kernels import box_blur, radius_for_sigma


def _ramp(height: int, width: int, direction: int) -> np.ndarray:
    """A 0..1 ramp across the page along `direction`, in degrees.

    Built from integer coordinates and normalised by a dot product. The only
    non-integer operation is the `sqrt` that normalises the direction vector,
    which is exact. Degrees are turned into a direction with a small exact
    lookup rather than `cos`/`sin`, which are not portable.

    Args:
        height: Image height in pixels.
        width: Image width in pixels.
        direction: Gradient direction in degrees; a multiple of 45, taken
            modulo a full turn.

    Raises:
        ValueError: If direction is not a multiple of 45.
    """
    # Eighth-turn lookup over a FULL turn. It used to be taken modulo 180, which
    # is not a symmetry of a gradient: 180 mapped to 0 and 270 to 90, so a tier
    # asking for the reversed falloff silently got the original one while the
    # docstring and the diagnostic both advertised 180 as valid. A gradient
    # repeats every 360 degrees, not every 180, so the lookup covers all eight.
    axes = {
        0: (1, 0),
        45: (1, 1),
        90: (0, 1),
        135: (-1, 1),
        180: (-1, 0),
        225: (-1, -1),
        270: (0, -1),
        315: (1, -1),
    }
    direction_normalized = int(direction) % 360
    if direction_normalized not in axes:
        supported = sorted(axes.keys())
        raise ValueError(
            f"""What: direction={direction}° (={direction_normalized}° modulo a turn) is not
      supported; only eighth-turns are.
Where: config/degradation.yml, in the `paper-phase` LightingGradient entry.
Expected: direction must be one of {supported}, modulo 360. Example:
  - augmentation: LightingGradient
    max_brightness: 248
    direction: 90
Recover: round the `direction:` value to the nearest multiple of 45."""
        ) from None
    dx, dy = axes[direction_normalized]
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

    def __call__(self, image: np.ndarray, _rng: np.random.Generator) -> np.ndarray:
        """Apply the gradient effect to the image.

        Args:
            image: Input image array.
            _rng: Seeded random generator (unused; this effect samples nothing, but
                the parameter exists for signature parity with ShadowCast and InkBleed).

        Returns:
            Degraded image with the same shape and dtype as the input.
        """
        height, width = image.shape[:2]
        ramp = _ramp(height, width, self.direction)
        # Linear falloff from the ceiling down to 78% of it: a multiplicative
        # field, so paper and ink dim together as real lighting does.
        ceiling = self.max_brightness / 255.0
        scale = ceiling * (1.0 - 0.22 * ramp)
        scaled = image.astype(np.float64) * scale[:, :, None]
        return np.clip(scaled + 0.5, 0, 255).astype(np.uint8)


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
        """Apply the shadow effect to the image.

        Args:
            image: Input image array.
            rng: Seeded random generator for sampling opacity.

        Returns:
            Degraded image with the same shape and dtype as the input.

        Raises:
            ValueError: If shadow_side is not one of the supported values.
        """
        if self.shadow_side not in _SIDES:
            raise ValueError(
                "Unknown shadow side.\n"
                f"  What:     ShadowCast was given side '{self.shadow_side}', "
                "which names no edge of the page.\n"
                "  Where:    config/degradation.yml -> a paper-phase ShadowCast entry\n"
                f"  Expected: one of {sorted(_SIDES)}, e.g. 'side: top'.\n"
                "  Recover:  correct the 'side:' value in that entry."
            ) from None
        height, width = image.shape[:2]
        axis, sign = _SIDES[self.shadow_side]
        # Axis 0 (top/bottom) uses direction 90 (vertical); axis 1 (left/right) uses 0 (horizontal).
        ramp = _ramp(height, width, 0 if axis else 90)
        if sign > 0:
            ramp = 1.0 - ramp

        low, high = self.shadow_opacity_range
        opacity = float(rng.uniform(low, high))

        # A uint8 mask, softened by the same integer blur the Gaussian
        # replacement uses, so no transcendental builds the falloff.
        mask = np.clip(ramp * opacity * 255.0 + 0.5, 0, 255).astype(np.uint8)
        # The radius floors to 0 below sigma 1.415, i.e. whenever max(height,
        # width) is under about 71 px -- a test fixture, never a page. Real
        # pages are thousands of pixels, so the quantisation that made the
        # camera blur a no-op does not bite here: at 2000 px this asks for
        # sigma 40 and gets radius 22.
        mask = box_blur(mask, radius_for_sigma(max(height, width) * 0.02))

        alpha = mask.astype(np.int32)[:, :, None]
        base = image.astype(np.int32)
        # Exact integer blend toward black: no float rounding to disagree about.
        return ((base * (255 - alpha) + 127) // 255).astype(np.uint8)


class InkBleed:
    """Ink spreading outward from dark strokes, as on absorbent paper.

    Args:
        intensity_range: Range to sample the bleed strength from, 0-1.
        kernel_size: `(w, h)` of the dilation kernel; the YAML declares one int.
    """

    def __init__(self, intensity_range: tuple[float, float], kernel_size: tuple[int, int]) -> None:
        self.intensity_range = (float(intensity_range[0]), float(intensity_range[1]))
        self.kernel_size = (int(kernel_size[0]), int(kernel_size[1]))

    def __call__(self, image: np.ndarray, rng: np.random.Generator) -> np.ndarray:
        """Apply the ink bleed effect to the image.

        Args:
            image: Input image array.
            rng: Seeded random generator for sampling intensity.

        Returns:
            Degraded image with the same shape and dtype as the input.
        """
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


class ThermalFade:
    """A thermal receipt's print fading toward paper white, heavier at one edge.

    Thermal coating reacts to heat and UV first, then moisture, skin and plastic
    oils, and friction, and it fades unevenly rather than as a flat contrast
    loss -- commonly heavier toward one edge of the roll. Modelled as a
    directional ramp blended toward white: a pixel already close to white barely
    moves, and one far from white (ink) moves more for the same ramp value,
    which is why ink visibly fades before the paper does on a real receipt.

    Args:
        strength_range: Range to sample the fade's peak intensity from, 0
            (untouched) to 1 (the faded edge blends fully to white).
        direction: Angle of the fade in degrees, passed straight to `_ramp`.
    """

    def __init__(self, strength_range: tuple[float, float], direction: int) -> None:
        self.strength_range = (float(strength_range[0]), float(strength_range[1]))
        self.direction = int(direction)

    def __call__(self, image: np.ndarray, rng: np.random.Generator) -> np.ndarray:
        """Apply the fade effect to the image.

        Args:
            image: Input image array.
            rng: Seeded random generator for sampling strength.

        Returns:
            Degraded image with the same shape and dtype as the input.
        """
        height, width = image.shape[:2]
        ramp = _ramp(height, width, self.direction)
        strength = float(rng.uniform(*self.strength_range))

        # A uint8 fade field, built the same way ShadowCast's mask is: clip and
        # round is the only place a float touches a pixel value.
        fade = np.clip(ramp * strength * 255.0 + 0.5, 0, 255).astype(np.uint8)

        base = image.astype(np.int32)
        fade_i = fade.astype(np.int32)[:, :, None]
        # Exact integer blend toward white. At fade 0 the pixel is untouched; at
        # fade 255 it is pure white. Ink starts further from 255 than paper
        # does, so it moves more under the same fade -- the chemistry does the
        # same thing, unprompted by anything in this formula.
        return (base + ((255 - base) * fade_i + 127) // 255).astype(np.uint8)
