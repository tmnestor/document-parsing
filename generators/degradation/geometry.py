"""Page geometry and sensor artefacts: the two intake channels' physics.

Augraphy treats the page as a rectangle square-on to the camera, so every
geometric effect lives here instead.

Two modes, because the two channels move the page differently:

`skew`  A platen or a feed roller holds the page flat and square. What varies
        is a degree or two of rotation and a little bed showing at the edges.
        There is no perspective and no cast shadow — a scanner's lamp travels
        with the sensor.

`warp`  A phone photograph: the page lies on a desk, foreshortened along one
        edge, rotated, with a drop shadow beneath it. This is the geometry a
        rectification preprocessor would have to undo.

The warp uses the same homography calls a rectifier uses
(cv2.getPerspectiveTransform / cv2.warpPerspective). Compositing and
photometrics stay in PIL/NumPy. Everything is RGB throughout — PIL does I/O and
NumPy arrays feed straight to cv2 — so there is no BGR channel swap.
"""

import io

import cv2
import numpy as np
from PIL import Image, ImageEnhance, ImageFilter

# The OpenCV the degraded corpora were built with. `warpPerspective` and
# `GaussianBlur` are not bit-stable across major versions, so this is corpus
# data, not a preference: a different build silently produces a different corpus
# under the same seed, and the manifest hashes then disagree with every
# prediction already scored against them.
#
# Measured 2026-08-22: installing environment-degrade.yml as written leaves BOTH
# opencv-python-headless 4.13.0.92 and opencv-python 5.0.0.93 present, because
# augraphy declares the GUI build as a hard requirement and pip honours it. `cv2`
# then resolves to 5.0.0.93, and 2 of 9 degraded images came out different. The
# environment file documents the uninstall that fixes it; this check is here
# because a documented step is one that can be skipped.
_PINNED_CV2 = "4.13.0"


class OpenCVVersionError(RuntimeError):
    """Raised when the active OpenCV cannot reproduce the shipped corpus."""


def check_opencv() -> None:
    """Refuse to degrade with an OpenCV that would produce a different corpus.

    Raises:
        OpenCVVersionError: The active cv2 is not the pinned build.
    """
    active = cv2.__version__
    if active.startswith(_PINNED_CV2):
        return
    raise OpenCVVersionError(
        "Cannot degrade: the active OpenCV would produce a different corpus.\n"
        f"  What:     cv2 resolves to {active}, not the pinned {_PINNED_CV2}. Geometry "
        "is not bit-stable across OpenCV versions, so the same seed yields different "
        "images — measured at 2 of 9 pages differing between 4.13.0 and 5.0.0.\n"
        "  Where:    the active conda environment; check with\n"
        "              pip list | grep -i opencv\n"
        "  Expected: ONLY opencv-python-headless==4.13.0.92. augraphy declares the GUI "
        "build opencv-python as a hard requirement, so a plain install of "
        "environment-degrade.yml leaves both present and the GUI build wins.\n"
        "  Recover:  pip uninstall -y opencv-python && pip install --no-deps "
        "augraphy==8.2.6\n"
        "            then re-check that pip list shows only the headless build."
    )


def _rot(point: list[float], cx: float, cy: float, degrees: float) -> list[float]:
    """Rotate a point about (cx, cy) by `degrees`."""
    theta = np.radians(degrees)
    x, y = point[0] - cx, point[1] - cy
    return [cx + x * np.cos(theta) - y * np.sin(theta), cy + x * np.sin(theta) + y * np.cos(theta)]


def skew_on_platen(image: Image.Image, geometry: dict, rng: np.random.Generator) -> Image.Image:
    """Rotate a flat page slightly, as a scanner feeds it a degree off square.

    No perspective and no drop shadow, both of which would be artefacts of
    photographing a page rather than scanning one. The bed shows as a near-white
    surround rather than a desk: a scanner lid is white or light grey, and
    modelling it as a desk would put a colour in the frame that the channel
    cannot produce.

    Args:
        image: The (already ink/paper-augmented) flat page.
        geometry: Tier geometry — `rotation_deg` and `margin`, each [min, max].
        rng: Seeded generator; all randomness is drawn from it.

    Returns:
        An RGB frame slightly larger than the input, the page rotated within it.
    """
    page = image.convert("RGB")
    w, h = page.size

    margin_lo, margin_hi = geometry["margin"]
    pad_x = max(1, int(w * rng.uniform(margin_lo, margin_hi)))
    pad_y = max(1, int(h * rng.uniform(margin_lo, margin_hi)))

    # The lid, not a desk: light and nearly neutral, with the faint vignette a
    # scanner lamp leaves at the edges of its travel.
    level = rng.uniform(232, 250)
    bed = np.ones((h + 2 * pad_y, w + 2 * pad_x, 3)) * level
    bed += rng.normal(0, 1.5, bed.shape)
    canvas = Image.fromarray(np.clip(bed, 0, 255).astype(np.uint8), "RGB")
    canvas.paste(page, (pad_x, pad_y))

    rot_lo, rot_hi = geometry["rotation_deg"]
    degrees = rng.uniform(rot_lo, rot_hi)
    return canvas.rotate(
        degrees,
        resample=Image.Resampling.BICUBIC,
        expand=False,
        fillcolor=(int(level), int(level), int(level)),
    )


def warp_to_photo(image: Image.Image, geometry: dict, rng: np.random.Generator) -> Image.Image:
    """Warp a flat page onto a desk background, as if photographed off-axis.

    Args:
        image: The (already ink/paper-augmented) flat page.
        geometry: Tier geometry — `foreshorten`, `rotation_deg` and `margin`,
            each a [min, max] pair.
        rng: Seeded generator; all randomness is drawn from it.

    Returns:
        An RGB frame larger than the input, with the page occupying a
        perspective-distorted sub-region over a desk background.
    """
    page = image.convert("RGB")
    w, h = page.size

    margin_lo, margin_hi = geometry["margin"]
    pad_x = int(w * rng.uniform(margin_lo, margin_hi))
    pad_y = int(h * rng.uniform(margin_lo, margin_hi))
    cw, ch = w + 2 * pad_x, h + 2 * pad_y

    # Flat desk: muted tone, gentle lighting gradient, faint noise.
    base = np.array([rng.uniform(150, 200), rng.uniform(140, 190), rng.uniform(125, 175)])
    bg = np.ones((ch, cw, 3)) * base
    gx = np.linspace(rng.uniform(-25, 0), rng.uniform(0, 25), cw)[None, :, None]
    gy = np.linspace(rng.uniform(-20, 0), rng.uniform(0, 20), ch)[:, None, None]
    bg = np.clip(bg + gx + gy + rng.normal(0, 3, (ch, cw, 3)), 0, 255)

    # Destination quad: foreshorten one edge, then rotate the whole page.
    fore_lo, fore_hi = geometry["foreshorten"]
    f = rng.uniform(fore_lo, fore_hi)
    edge = int(rng.integers(0, 4))
    q = [[0.0, 0.0], [float(w), 0.0], [float(w), float(h)], [0.0, float(h)]]  # TL TR BR BL
    if edge == 0:  # top edge away
        q[0][0] += w * f
        q[1][0] -= w * f
    elif edge == 1:  # right edge away
        q[1][1] += h * f
        q[2][1] -= h * f
    elif edge == 2:  # bottom edge away
        q[3][0] += w * f
        q[2][0] -= w * f
    else:  # left edge away
        q[0][1] += h * f
        q[3][1] -= h * f

    rot_lo, rot_hi = geometry["rotation_deg"]
    degrees = rng.uniform(rot_lo, rot_hi)
    q = [_rot(p, w / 2, h / 2, degrees) for p in q]

    ox = pad_x + rng.uniform(-pad_x * 0.3, pad_x * 0.3)
    oy = pad_y + rng.uniform(-pad_y * 0.3, pad_y * 0.3)
    dst = np.array([[x + ox, y + oy] for x, y in q], dtype=np.float32)
    src = np.array([[0, 0], [w, 0], [w, h], [0, h]], dtype=np.float32)

    m = cv2.getPerspectiveTransform(src, dst)
    rgba = np.dstack([np.array(page), np.full((h, w), 255, np.uint8)])
    warped = cv2.warpPerspective(
        rgba,
        m,
        (cw, ch),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(0, 0, 0, 0),
    )
    alpha = (warped[:, :, 3].astype(np.float32) / 255.0)[:, :, None]

    # Drop shadow under the page.
    shadow = cv2.GaussianBlur(warped[:, :, 3], (0, 0), max(w, h) * 0.02) * 0.45
    offset = int(max(w, h) * 0.015)
    shadow = np.roll(np.roll(shadow, offset, axis=0), offset, axis=1)[:, :, None] / 255.0
    bg = bg * (1 - shadow) + np.array([25, 22, 20]) * shadow

    composite = bg * (1 - alpha) + warped[:, :, :3].astype(np.float32) * alpha
    return Image.fromarray(np.clip(composite, 0, 255).astype(np.uint8), "RGB")


class MarkError(RuntimeError):
    """Raised when a tier names a mark that is not registered."""


def roller_streaks(image: Image.Image, spec: dict, rng: np.random.Generator) -> Image.Image:
    """Bands of uneven density down the feed direction, as a worn ADF leaves.

    A sheet-fed scanner drags the page past a fixed sensor on rollers. A roller
    with a flat spot or a speck of toner marks the same column of pixels the
    whole way down, so the artefact is a full-height vertical band rather than a
    blotch — which is what makes it recognisable, and what makes it dangerous
    for a table: a band landing on an amount column dims every figure in it.

    Replaces Augraphy's `DirtyRollers`, which produces the same artefact and is
    not reproducible. See the note on `AUGMENTATIONS` in augment.py.

    Args:
        image: The flat page.
        spec: `count`, `width` and `strength`, each a [min, max] pair.
        rng: Seeded generator; all randomness is drawn from it.

    Returns:
        The page with vertical density bands.
    """
    arr = np.array(image.convert("RGB")).astype(np.float32)
    height, width = arr.shape[:2]

    count_lo, count_hi = spec["count"]
    for _ in range(int(rng.integers(count_lo, count_hi + 1))):
        band_lo, band_hi = spec["width"]
        band = int(rng.integers(band_lo, band_hi + 1))
        x = int(rng.integers(0, max(1, width - band)))

        strength_lo, strength_hi = spec["strength"]
        strength = rng.uniform(strength_lo, strength_hi)
        # Either a dirty roller (darker) or one that has lifted ink (lighter).
        sign = -1.0 if rng.random() < 0.7 else 1.0

        # Soft edges: a hard-edged band reads as a drawn rectangle rather than
        # as contact pressure, which falls off across the roller's width.
        profile = np.hanning(band + 2)[1:-1] if band > 2 else np.ones(band)
        delta = (sign * strength * 255.0 * profile)[None, :, None]
        arr[:, x : x + band, :] = np.clip(arr[:, x : x + band, :] + delta, 0, 255)

        # A faint full-height jitter alongside, so the band is not perfectly
        # uniform down the page — the roller wobbles.
        wobble = rng.normal(0, strength * 12.0, (height, 1, 1))
        arr[:, x : x + band, :] = np.clip(arr[:, x : x + band, :] + wobble, 0, 255)

    return Image.fromarray(arr.astype(np.uint8), "RGB")


def fold_ridges(image: Image.Image, spec: dict, rng: np.random.Generator) -> Image.Image:
    """Horizontal creases from a page that has been folded and flattened again.

    A crease catches the light on one side and shades on the other, so it is a
    signed luminance ridge rather than a line. It is drawn on the flat page and
    then transformed with it, because the fold belongs to the paper.

    Replaces Augraphy's `Folding`, which produces the same artefact and is not
    reproducible. See the note on `AUGMENTATIONS` in augment.py.

    Args:
        image: The flat page.
        spec: `count` and `strength`, each a [min, max] pair.
        rng: Seeded generator; all randomness is drawn from it.

    Returns:
        The page with horizontal creases.
    """
    arr = np.array(image.convert("RGB")).astype(np.float32)
    height, width = arr.shape[:2]

    count_lo, count_hi = spec["count"]
    folds = int(rng.integers(count_lo, count_hi + 1))
    if folds < 1:
        return image.copy()

    # Spread the folds over the page rather than placing them independently: a
    # sheet folded twice creates evenly spaced creases, not two at random.
    for index in range(folds):
        centre = int(height * (index + 1) / (folds + 1) + rng.normal(0, height * 0.02))
        centre = int(np.clip(centre, 1, height - 2))

        strength_lo, strength_hi = spec["strength"]
        strength = rng.uniform(strength_lo, strength_hi) * 255.0
        spread = max(2.0, height * 0.004)

        rows = np.arange(height, dtype=np.float32) - centre
        # An odd (antisymmetric) profile: lit above the crease, shaded below.
        ridge = -rows / spread * np.exp(-0.5 * (rows / spread) ** 2) * strength
        arr += ridge[:, None, None]

    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB")


# YAML name -> implementation. Registered here rather than in augment.py because
# these are this project's own effects, applied after Augraphy and before the
# geometry, and they exist precisely because the Augraphy equivalents could not
# be made reproducible.
MARKS = {
    "roller_streaks": roller_streaks,
    "fold_ridges": fold_ridges,
}


def apply_marks(image: Image.Image, tier, rng: np.random.Generator) -> Image.Image:
    """Apply a tier's own paper marks, in declared order.

    Args:
        image: The flat page, already through Augraphy's phases.
        tier: The tier supplying `marks`.
        rng: Seeded generator; all randomness is drawn from it.

    Returns:
        The marked page, at the same dimensions as the input.

    Raises:
        MarkError: A mark entry is malformed or names an unregistered mark.
    """
    result = image
    for spec in tier.marks:
        where = f"families.{tier.family}.tiers[{tier.name}].marks"
        name = spec.get("mark")
        if name is None:
            raise MarkError(
                "Invalid mark spec.\n"
                f"  What:     a marks entry of tier '{tier.label}' has no 'mark:' key, so "
                "there is nothing to apply.\n"
                f"  Where:    config/degradation.yml -> {where}\n"
                f"  Expected: every entry to name one of {sorted(MARKS)}, e.g.\n"
                "              {mark: roller_streaks, count: [3, 6], width: [4, 12], "
                "strength: [0.04, 0.10]}\n"
                "  Recover:  add a 'mark:' key to the entry."
            )
        function = MARKS.get(str(name))
        if function is None:
            raise MarkError(
                "Unknown mark.\n"
                f"  What:     tier '{tier.label}' names mark '{name}', which is not "
                "registered.\n"
                f"  Where:    config/degradation.yml -> {where}\n"
                f"  Expected: one of {sorted(MARKS)}.\n"
                "  Recover:  use a registered mark, or add the function to MARKS in "
                "generators/degradation/geometry.py."
            )
        result = function(result, spec, rng)
    return result


def apply_photometrics(image: Image.Image, camera: dict, rng: np.random.Generator) -> Image.Image:
    """Apply lens and sensor artefacts to a whole frame.

    Runs after the geometry: blur, sensor noise and JPEG blocking are properties
    of the sensor and the file, not of the paper.

    Args:
        image: The composited frame.
        camera: Tier camera parameters — `blur`, `noise_sigma` and `jpeg`, each
            a [min, max] pair.
        rng: Seeded generator; all randomness is drawn from it.

    Returns:
        The captured-looking frame, same dimensions as the input.
    """
    frame = image.convert("RGB")
    frame = ImageEnhance.Brightness(frame).enhance(rng.uniform(0.92, 1.05))
    frame = ImageEnhance.Contrast(frame).enhance(rng.uniform(0.90, 1.0))

    blur_lo, blur_hi = camera["blur"]
    frame = frame.filter(ImageFilter.GaussianBlur(rng.uniform(blur_lo, blur_hi)))

    noise_lo, noise_hi = camera["noise_sigma"]
    sigma = rng.uniform(noise_lo, noise_hi)
    arr = np.array(frame).astype(np.int16)
    arr = arr + rng.normal(0, sigma, arr.shape).astype(np.int16)
    frame = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB")

    jpeg_lo, jpeg_hi = camera["jpeg"]
    buf = io.BytesIO()
    frame.save(buf, format="JPEG", quality=int(rng.integers(jpeg_lo, jpeg_hi + 1)))
    buf.seek(0)
    return Image.open(buf).convert("RGB")
