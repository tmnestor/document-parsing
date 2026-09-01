"""Blur without a transcendental in sight.

`cv2.GaussianBlur` builds its kernel with `std::exp`, and libm is not portable:
glibc's `exp` and Apple's differ in the last bits, which is enough to change
every image hash. A cascade of box blurs converges on a Gaussian (central limit)
and uses only integer addition, subtraction and division, so it is exact on any
machine. Three passes is the usual point of diminishing returns.

A cascade alone can only represent integer radii, though, and the smallest of
them is already a sigma of 1.415. `blur_sigma` matches the requested VARIANCE
instead of the nearest radius, so a sub-pixel lens blur is a small blur rather
than no blur; `box_blur` and `radius_for_sigma` remain for the callers whose
sigma is a fraction of the page and never small.
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

    **Quantises hard, and floors to zero.** A three-pass cascade of radius 1 has
    a standard deviation of 1.415, so every sigma below that rounds to radius 0
    and blurs nothing at all. That is fine where sigma is a fraction of the page
    (`ShadowCast`, the only remaining caller) and wrong where sigma is a
    sub-pixel lens blur — the camera tiers declare 0.05 to 1.8, and four of the
    six were measured to be exact no-ops through this function. Use `blur_sigma`
    for anything whose sigma can be small.
    """
    if sigma <= 0:
        return 0
    return max(0, int((np.sqrt(12.0 * sigma * sigma / passes + 1.0) - 1.0) / 2.0))


def _tap3(plane: np.ndarray, weight: float) -> np.ndarray:
    """One separable 3-tap pass `(w, 1-2w, w)` along both axes.

    Each axis adds `2 * weight` to the variance. Edges are clamped — the rolled
    border row/column is overwritten with the original, so nothing wraps around
    from the far side of the page.

    Args:
        plane: 2-D array; read as float64.
        weight: The outer tap weight; `0 <= weight < 0.25` keeps the centre tap
            non-negative.

    Returns:
        A float64 array of the same shape.
    """
    out = plane.astype(np.float64)
    for axis in (0, 1):
        a = np.roll(out, 1, axis=axis)
        b = np.roll(out, -1, axis=axis)
        if axis == 0:
            a[0] = out[0]
            b[-1] = out[-1]
        else:
            a[:, 0] = out[:, 0]
            b[:, -1] = out[:, -1]
        out = a * weight + out * (1.0 - 2.0 * weight) + b * weight
    return out


def blur_sigma(plane: np.ndarray, sigma: float, passes: int = 3) -> np.ndarray:
    """Blur a uint8 plane to an exact target VARIANCE, using only `+ - * /`.

    A box cascade alone quantises to integer radii, so every sub-pixel sigma
    collapsed to no blur at all (see `radius_for_sigma`). Variances add, so this
    takes the largest cascade that fits, then covers the residual with at most a
    few single-pass boxes sized for it and one 3-tap, landing on the requested
    variance rather than on the nearest representable radius. Measured against
    `cv2.GaussianBlur`, the ratio of standard deviations stays within 0.97-1.01
    at and above a pixel; below one it blurs somewhat harder, because a sampled
    Gaussian loses its own variance there.

    The pass count is bounded and does not grow with sigma. That is a
    requirement, not an incidental property: the warp shadow asks for sigma ~70
    on a full page, and a residual loop that stepped by radius 1 ran ~105 passes
    over it, which put a corpus build at six hours instead of ten minutes.

    Args:
        plane: 2-D uint8 array.
        sigma: Target standard deviation in pixels. `<= 0` returns a copy.
        passes: Passes in the coarse box cascade; 3 approximates a Gaussian.

    Returns:
        A uint8 array of the same shape.
    """
    if sigma <= 0:
        return plane.copy()
    target = sigma * sigma

    def _vbox(radius: int, count: int = passes) -> float:
        return count * (((2 * radius + 1) ** 2) - 1) / 12.0

    r = 0
    while _vbox(r + 1) <= target:
        r += 1
    out = box_blur(plane, r, passes).astype(np.float64) if r > 0 else plane.astype(np.float64)

    residual = target - _vbox(r)
    # Cover the residual with boxes sized FOR it, not by repeating radius 1.
    # Variances add, so one pass of the largest box that fits takes most of it
    # and the next takes most of the rest -- a handful of passes instead of
    # `residual / (2/3)` of them, which reached ~105 for the warp shadow and
    # made a corpus build take hours rather than minutes.
    guard = 0
    while residual >= 2.0 / 3.0 and guard < 8:
        radius = int((np.sqrt(12.0 * residual + 1.0) - 1.0) / 2.0)
        if radius < 1:
            break
        out = _blur_axis(_blur_axis(out.astype(np.int64), radius, 0), radius, 1).astype(np.float64)
        residual -= ((2 * radius + 1) ** 2 - 1) / 12.0
        guard += 1
    if residual > 0:
        # A weight above 0.25 would make the centre tap negative.
        out = _tap3(out, min(residual / 2.0, 0.25))
    return np.clip(out + 0.5, 0, 255).astype(np.uint8)
