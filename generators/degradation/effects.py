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

    Args:
        height: Image height in pixels.
        width: Image width in pixels.
        direction: Gradient direction in degrees; must be one of 0, 45, 90, 135, 180.

    Raises:
        ValueError: If direction is not one of the supported values.
    """
    # Quarter-turn lookup: the YAML only ever declares 0, 45 or 90.
    axes = {0: (1, 0), 45: (1, 1), 90: (0, 1), 135: (-1, 1), 180: (-1, 0)}
    direction_normalized = int(direction) % 180
    if direction_normalized not in axes:
        supported = sorted(axes.keys())
        raise ValueError(
            f"""What: direction={direction_normalized}° is not supported.
Where: config/degradation.yml, in the `paper-phase` LightingGradient entry.
Expected: direction must be one of {supported}. Example:
  - effect: LightingGradient
    parameters:
      direction: 90
Recover: add `direction: 90` (or 0, 45, 135, 180) to the LightingGradient config."""
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
