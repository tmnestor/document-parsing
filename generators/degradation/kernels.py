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
