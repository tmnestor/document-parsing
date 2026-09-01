"""Which degradation phase first diverges between two machines?

The clean render is now portable (the layout-engine pin), but degraded pages
still differ in PIXELS across architectures. `cv2.setUseOptimized(False)` with a
single thread changes nothing, so it is not OpenCV's runtime SIMD dispatch --
it is inherent floating-point difference somewhere in the chain.

`degrade_page` runs four phases in a fixed order. This hashes the image after
each, so comparing two machines' output shows the FIRST phase whose digest
diverges. Everything downstream of that inherits the difference, so only the
first divergence names a cause:

    effects    ink and paper effects, this project's own portable
               re-derivation (numpy) -- see generators/degradation/effects.py
    marks      this repository's own roller streaks and fold ridges (numpy)
    geometry   skew or perspective warp (cv2.warpPerspective, INTER_CUBIC)
    camera     blur, noise, JPEG round-trip (PIL)

One page, every tier. Nothing is written and nothing is transferred.

    python probe_phases.py CLEAN_CORPUS/images/CASE001_bank_statements.png
"""

import hashlib
import sys
from pathlib import Path

import numpy as np
from PIL import Image

REPO = Path(__file__).resolve().parent
CONFIG = REPO / "config" / "degradation.yml"
SEED = 12345


def _digest(image: Image.Image) -> str:
    return hashlib.sha256(image.convert("RGB").tobytes()).hexdigest()[:16]


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    page_path = Path(sys.argv[1])

    from generators.degradation.augment import apply_effects
    from generators.degradation.geometry import (
        apply_marks,
        apply_photometrics,
        skew_on_platen,
        warp_to_photo,
    )
    from generators.degradation.tiers import load_tiers

    with Image.open(page_path) as handle:
        clean = handle.convert("RGB").copy()
    print(f"clean input      {_digest(clean)}   {page_path.name}\n")
    print(f"{'tier':16} {'effects':>17} {'marks':>17} {'geometry':>17} {'camera':>17}")

    for tier in load_tiers(CONFIG):
        effected = apply_effects(clean.copy(), tier, SEED)
        after_effects = _digest(effected)

        rng = np.random.default_rng(SEED)
        marked = apply_marks(effected, tier, rng)
        after_marks = _digest(marked)

        if tier.geometry["mode"] == "skew":
            placed = skew_on_platen(marked, tier.geometry, rng)
        else:
            placed = warp_to_photo(marked, tier.geometry, rng)
        after_geometry = _digest(placed)

        final = apply_photometrics(placed, tier.camera, rng)
        print(
            f"{tier.label:16} {after_effects:>17} {after_marks:>17} "
            f"{after_geometry:>17} {_digest(final):>17}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
