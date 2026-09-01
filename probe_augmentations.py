"""Which individual augraphy augmentation is not portable across machines?

`probe_phases.py` localised cross-machine divergence to the augraphy phase --
the first thing that runs, before marks, geometry or camera. This narrows it
further, applying each registered augmentation ALONE to the same clean page.

The point is to size a fix. `config/degradation.yml` already records that
`Folding` and `DirtyRollers` were dropped as not reproducible and reimplemented
in this repository as `marks:`; an augmentation that diverges here is a
candidate for the same treatment. One that agrees across machines can stay.

    python probe_augmentations.py CLEAN_PAGE.png
"""

import hashlib
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import numpy as np
from PIL import Image

REPO = Path(__file__).resolve().parent
SEED = 12345

# One representative parameter set per augmentation, taken from the shipped
# tiers so the probe exercises what the corpus actually uses.
CASES: list[tuple[str, dict]] = [
    ("InkBleed", {"intensity_range": (0.25, 0.45), "kernel_size": (5, 5)}),
    ("LightingGradient", {"max_brightness": 235, "direction": 45}),
    ("ShadowCast", {"shadow_side": "top", "shadow_opacity_range": (0.35, 0.55)}),
]


def _digest(array: np.ndarray) -> str:
    return hashlib.sha256(np.ascontiguousarray(array).tobytes()).hexdigest()[:16]


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2

    from generators.degradation.augment import AUGMENTATIONS

    with Image.open(Path(sys.argv[1])) as handle:
        clean = np.asarray(handle.convert("RGB"))
    print(f"clean input   {_digest(clean)}\n")
    print(f"{'augmentation':20} {'output':>18}")

    for name, kwargs in CASES:
        factory = AUGMENTATIONS.get(name)
        if factory is None:
            print(f"{name:20} {'(not registered)':>18}")
            continue
        # Augraphy augmentations read numpy's legacy global RNG; seed it
        # immediately before each call so one result cannot depend on another.
        np.random.seed(SEED)
        try:
            build = cast(Callable[..., Any], factory)
            result = build(**kwargs)(clean.copy())
        except Exception as exc:
            print(f"{name:20} failed: {exc}")
            continue
        print(f"{name:20} {_digest(np.asarray(result)):>18}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
