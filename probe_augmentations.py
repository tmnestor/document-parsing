"""Which individual effect is not reproducible on one machine?

`probe_phases.py` localised cross-machine divergence to the effects phase --
the first thing that runs, before marks, geometry or camera. This narrows it
further, running each effect ALONE through the real `apply_effects` path.

Going through `apply_effects` is essential rather than convenient: it is the
same entry point `degrade_page` calls, so a probe that constructs an effect
some other way could pass while the real pipeline still diverges. `InkBleed`,
`LightingGradient` and `ShadowCast` are this project's own re-derivation --
portable arithmetic seeded only from the caller's `np.random.Generator`, with
no global RNG state anywhere in the chain (see
`generators/degradation/effects.py`).

Each case therefore runs twice. If the two disagree the effect is not
reproducible even on one machine, and the cross-machine question does not arise
for it yet.

The point is to size a fix. `config/degradation.yml` already records that the
library this project used to depend on had two augmentations, `Folding` and
`DirtyRollers`, that were not seedable at all and were reimplemented here as
`marks:`; an effect that diverges across machines is a candidate for the same
treatment.

    python probe_augmentations.py CLEAN_PAGE.png
"""

import hashlib
import sys
from pathlib import Path

import numpy as np
from PIL import Image

REPO = Path(__file__).resolve().parent
SEED = 12345

# One representative spec per augmentation, in the shape the YAML declares and
# taken from the shipped tiers, so the probe exercises what the corpus uses.
CASES: list[tuple[str, str, dict]] = [
    ("InkBleed", "ink", {"augmentation": "InkBleed", "intensity": [0.25, 0.45], "kernel": 5}),
    (
        "LightingGradient",
        "paper",
        {"augmentation": "LightingGradient", "max_brightness": 235, "direction": 45},
    ),
    (
        "ShadowCast",
        "paper",
        {"augmentation": "ShadowCast", "side": "top", "opacity": [0.35, 0.55]},
    ),
]


def _digest(image: Image.Image) -> str:
    return hashlib.sha256(np.asarray(image.convert("RGB")).tobytes()).hexdigest()[:16]


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2

    from generators.degradation.augment import apply_effects
    from generators.degradation.tiers import Tier

    with Image.open(Path(sys.argv[1])) as handle:
        clean = handle.convert("RGB").copy()
    print(f"clean input   {_digest(clean)}\n")
    print(f"{'augmentation':20} {'output':>18}  stable locally?")

    for name, phase, spec in CASES:
        tier = Tier(
            family="probe",
            name="probe",
            suffix="probe",
            description=f"{name} alone",
            ink=[spec] if phase == "ink" else [],
            paper=[spec] if phase == "paper" else [],
            marks=[],
            geometry={"mode": "skew", "rotation_deg": [0, 0], "margin": [0, 0]},
            camera={"blur": [0, 0], "noise_sigma": [0, 0], "jpeg": [100, 100]},
        )
        try:
            first = _digest(apply_effects(clean.copy(), tier, SEED))
            second = _digest(apply_effects(clean.copy(), tier, SEED))
        except Exception as exc:
            print(f"{name:20} failed: {exc}")
            continue
        stable = "yes" if first == second else "NO -- not reproducible on one machine"
        print(f"{name:20} {first:>18}  {stable}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
