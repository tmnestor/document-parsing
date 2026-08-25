"""Image degradation: Augraphy paper damage, then the intake channel's geometry.

Production receives scanned documents, so a corpus of clean renders measures a
condition production does not have. This package degrades a rendered page to a
declared severity within a declared intake family.

**The transcript does not change.** Degradation alters how legible the page is,
never what it says, so a degraded corpus reuses the clean transcripts byte for
byte. That is what makes the whole exercise cheap: no re-rendering, no
re-serialising, no new ground truth to validate — and it means any score
difference is attributable to image quality alone. `tests/test_degradation.py`
pins it.

Ordering is load-bearing, and is enforced here rather than in the config:
Augraphy's ink and paper phases run on the flat page, before the geometry,
because a crease belongs to the paper and must be transformed *with* it;
painting one across an already skewed page reads as a defect in the file rather
than in the document. Blur, sensor noise and compression run last, because they
belong to the sensor and the file.

This package is deliberately absent from the `docparse` environment, which stays
free of numpy and opencv so that the runner tests can import `runners/` where no
parser is installed. See `environment-degrade.yml`.
"""

import hashlib
from typing import TYPE_CHECKING

from generators.degradation.tiers import Tier, TierConfigError, load_tiers

if TYPE_CHECKING:  # pragma: no cover - import-time typing only
    from PIL import Image

# numpy, opencv and augraphy are imported inside `degrade_page`, not here, and
# the same rule `runners/common.py` follows for parser imports applies for the
# same reason: `config/degradation.yml` must be loadable — and therefore
# validatable in CI — from an environment that has none of them. Importing them
# at module scope would make `from generators.degradation import load_tiers`
# fail in `docparse`, which is where the config tests run.

__all__ = [
    "Tier",
    "TierConfigError",
    "degrade_page",
    "load_tiers",
    "page_seed",
]


def page_seed(stem: str, tier: Tier) -> int:
    """Derive a stable seed for one (page, tier) pair.

    Hashed rather than arithmetic on a case number, because the corpus is
    addressed by filename stem and a stem is not a number. Hashing also spaces
    neighbouring tiers far apart in the generator's sequence, so `scan-light`
    and `scan-moderate` of one page share no draws — adjacent seeds would
    otherwise produce visibly correlated damage and understate the variety a
    severity ladder is meant to provide.

    Masked to 31 bits because Augraphy hands the seed to `cv2.setRNGSeed`,
    which takes a signed C int and raises `ValueError: integer won't fit into a
    C int` above 2**31 - 1. A full 32-bit hash overflows it about half the time,
    so the failure is intermittent across pages and looks like a bad page rather
    than a bad seed.

    Args:
        stem: The page's filename stem, e.g. "CASE001_bank_statements".
        tier: The tier being applied.

    Returns:
        A seed unique to this pair and stable across runs and machines.
    """
    key = f"{stem}|{tier.family}|{tier.name}".encode()
    return int.from_bytes(hashlib.sha256(key).digest()[:4], "big") & 0x7FFF_FFFF


def degrade_page(image: "Image.Image", tier: Tier, seed: int) -> "Image.Image":
    """Degrade one clean render to one tier's severity.

    Args:
        image: The clean rendered page.
        tier: The severity tier to apply.
        seed: Seed for this (page, tier) pair — see `page_seed`.

    Returns:
        An RGB frame of the page as the tier's intake channel would deliver it.

    Raises:
        AugmentationError: The tier names an unregistered augmentation.
        MarkError: The tier names an unregistered mark.
    """
    import numpy as np

    from generators.degradation.augment import apply_augraphy
    from generators.degradation.geometry import (
        apply_marks,
        apply_photometrics,
        check_opencv,
        skew_on_platen,
        warp_to_photo,
    )

    # Before any work: a wrong OpenCV produces a corpus that looks right and
    # hashes differently, which is the failure this whole package is built to
    # avoid.
    check_opencv()

    augmented = apply_augraphy(image, tier, seed)
    rng = np.random.default_rng(seed)

    # Our own paper effects, after Augraphy and still on the flat page, so a
    # streak or a crease is transformed with the paper rather than painted
    # across an image that has already been skewed.
    augmented = apply_marks(augmented, tier, rng)

    mode = tier.geometry["mode"]
    if mode == "skew":
        placed = skew_on_platen(augmented, tier.geometry, rng)
    else:
        placed = warp_to_photo(augmented, tier.geometry, rng)

    return apply_photometrics(placed, tier.camera, rng)
