"""Registry and runner for the Augraphy phase pipeline.

Only the augmentations this project declares are registered. An allow-list
rather than a passthrough to Augraphy's whole catalogue is deliberate: it turns
a YAML typo into a startup diagnostic naming the valid options, and it records
which effects were chosen and which were rejected.

Ported from the predecessor repo, with the registry widened for the scanner
family. That repo excluded `DirtyRollers` and `DirtyDrum` as "photocopier
damage, not phone photography" — correct there, since it modelled only phone
photos. Here they are exactly right for the `scan` ladder and still absent from
`photo`, which is what a per-family allow-list is for.
"""

import contextlib
import tempfile
from collections.abc import Callable

import numpy as np
from PIL import Image

from generators.degradation.tiers import Tier

try:
    from augraphy import (
        AugraphyPipeline,
        DirtyDrum,
        InkBleed,
        LightingGradient,
        ShadowCast,
    )
except ImportError as err:  # pragma: no cover - environment failure, not logic
    raise ImportError(
        "Augraphy is not installed.\n"
        f"  What:     image degradation needs augraphy, which failed to import: {err}.\n"
        "  Where:    environment-degrade.yml -> dependencies.pip\n"
        "  Expected: augraphy==8.2.6 installed WITHOUT its declared dependencies, since "
        "it requires `opencv-python` (the full GUI build) which would displace the pinned "
        "opencv-python-headless. numpy must also be <= 2.4, the ceiling numba imposes.\n"
        "  Recover:  conda env create -f environment-degrade.yml, then "
        "`pip uninstall -y opencv-python && pip install --no-deps augraphy==8.2.6` if the "
        "full opencv was pulled in. This package is deliberately absent from `docparse`, "
        "which stays free of numpy and opencv."
    ) from err

# YAML name -> Augraphy class. Every geometric augmentation is deliberately
# excluded: geometry.py owns geometry, so that skew and warp are one
# implementation rather than two that can disagree.
#
# `Folding` and `DirtyRollers` were registered and then REMOVED, on 2026-08-22,
# because neither is reproducible. With `p=1`, and with `random`, `np.random`,
# `cv2`'s RNG and numba's all seeded immediately before the call, five runs of
# either at one seed produce five different images: both delegate to
# numba-compiled helpers (`warp_fold`, `apply_scanline_mask`) carrying RNG state
# that no documented seeding reaches. Every other registered augmentation here
# is byte-identical under the same test.
#
# That is disqualifying rather than untidy. The export manifest hashes images and
# `score` refuses to score a prediction against a different vintage, so a corpus
# that cannot be regenerated from a clean checkout cannot be re-derived if it is
# ever lost, and a reviewer cannot confirm the images are what the config says.
# The two artefacts they provided -- ADF roller streaks and a folded page -- are
# real and worth having, so they are reimplemented as seeded `marks` in
# geometry.py instead. See `MARKS` there.
AUGMENTATIONS: dict[str, Callable[..., object]] = {
    "InkBleed": InkBleed,
    "LightingGradient": LightingGradient,
    "ShadowCast": ShadowCast,
    "DirtyDrum": DirtyDrum,
}

# YAML key -> the constructor keyword each Augraphy class expects. The YAML uses
# short readable names; Augraphy's own parameter names are longer and
# inconsistent between classes. Verified against augraphy 8.2.6 — if the pin
# moves, re-check with inspect.signature(<Class>.__init__).parameters.
_PARAM_NAMES: dict[str, dict[str, str]] = {
    "InkBleed": {"intensity": "intensity_range", "kernel": "kernel_size"},
    "LightingGradient": {"max_brightness": "max_brightness", "direction": "direction"},
    "ShadowCast": {"side": "shadow_side", "opacity": "shadow_opacity_range"},
    "DirtyDrum": {"line_width": "line_width_range", "line_concentration": "line_concentration"},
}

# InkBleed wants a (w, h) kernel; the YAML declares a single int, since a
# non-square ink-bleed kernel has no physical meaning.
_SQUARE_KERNEL_KEYS = frozenset({"kernel_size"})


class AugmentationError(RuntimeError):
    """Raised when a tier names an augmentation that is not registered."""


def _build(spec: dict, *, tier: Tier, phase: str) -> object:
    """Instantiate one augmentation from its YAML spec.

    Args:
        spec: The YAML mapping, carrying `augmentation:` plus its parameters.
        tier: Owning tier, for diagnostics.
        phase: "ink" or "paper", for diagnostics.

    Returns:
        The constructed Augraphy augmentation.

    Raises:
        AugmentationError: No `augmentation:` key, an unregistered name, or a
            parameter the registered class does not accept.
    """
    where = f"families.{tier.family}.tiers[{tier.name}].{phase}"
    name = spec.get("augmentation")
    if name is None:
        raise AugmentationError(
            "Invalid augmentation spec.\n"
            f"  What:     a {phase}-phase entry of tier '{tier.label}' has no "
            "'augmentation:' key, so there is nothing to construct.\n"
            f"  Where:    config/degradation.yml -> {where}\n"
            f"  Expected: every entry to name one of {sorted(AUGMENTATIONS)}, e.g.\n"
            "              {augmentation: InkBleed, intensity: [0.05, 0.15], kernel: 3}\n"
            f"  Recover:  add an 'augmentation:' key to the {phase} entry."
        )

    factory = AUGMENTATIONS.get(str(name))
    if factory is None:
        raise AugmentationError(
            "Unknown augmentation.\n"
            f"  What:     tier '{tier.label}' names '{name}' in its {phase} phase, which "
            "is not registered.\n"
            f"  Where:    config/degradation.yml -> {where}\n"
            f"  Expected: one of {sorted(AUGMENTATIONS)}.\n"
            "  Recover:  use a registered augmentation, or add the class to AUGMENTATIONS "
            "in generators/degradation/augment.py together with its parameter mapping."
        )

    mapping = _PARAM_NAMES[str(name)]
    kwargs: dict[str, object] = {}
    for key, value in spec.items():
        if key == "augmentation":
            continue
        param = mapping.get(key)
        if param is None:
            raise AugmentationError(
                "Unknown augmentation parameter.\n"
                f"  What:     tier '{tier.label}' passes '{key}' to {name}, which does not "
                "accept it.\n"
                f"  Where:    config/degradation.yml -> {where}\n"
                f"  Expected: one of {sorted(mapping)} for {name}.\n"
                f"  Recover:  remove '{key}', or add it to _PARAM_NAMES['{name}'] in "
                "generators/degradation/augment.py with the Augraphy keyword it maps to."
            )
        if param in _SQUARE_KERNEL_KEYS:
            kwargs[param] = (int(value), int(value))
        elif isinstance(value, list):
            kwargs[param] = tuple(value)  # Augraphy wants tuples for its *_range params
        else:
            kwargs[param] = value
    return factory(**kwargs)


def apply_augraphy(image: Image.Image, tier: Tier, seed: int) -> Image.Image:
    """Apply a tier's ink and paper phases to the flat page.

    Runs before any geometry: these model damage to the paper itself, which must
    then be transformed *with* the page rather than painted across an image that
    has already been skewed or warped.

    Args:
        image: The clean, flat rendered page.
        tier: The severity tier supplying the phase specs.
        seed: Seed making this tier's output reproducible.

    Returns:
        The augmented page, at the same dimensions as the input.

    Raises:
        AugmentationError: A phase entry is malformed or names an unknown
            augmentation.
    """
    ink = [_build(spec, tier=tier, phase="ink") for spec in tier.ink]
    paper = [_build(spec, tier=tier, phase="paper") for spec in tier.paper]

    if not ink and not paper:
        return image.copy()

    pipeline = AugraphyPipeline(
        ink_phase=ink,
        paper_phase=paper,
        post_phase=[],
        save_outputs=False,
        log=False,
        random_seed=seed,
    )
    # Augraphy samples from NumPy's global RNG in places its own random_seed does
    # not reach, so both are set. This is the one spot where global random state
    # is unavoidable; it is contained here and covered by a byte-identity test.
    np.random.seed(seed)

    # AugraphyPipeline.__call__ unconditionally writes its input into
    # `os.getcwd()/augraphy_cache/` — a ring buffer written whatever
    # `save_outputs` says, with no setting to disable or relocate it. Running in
    # a throwaway directory sends it somewhere harmless.
    #
    # This is containment, not tidiness. Augraphy's PageBorder, BleedThrough and
    # BookBinding *read* from that cache, so a pipeline including them would
    # composite whatever images the last run left behind — output depending on
    # directory state rather than on the seed. None of the registered
    # augmentations read it, and pointing the cache at a fresh empty directory
    # each call makes that structurally true rather than true-by-inspection.
    with tempfile.TemporaryDirectory() as cache_dir, contextlib.chdir(cache_dir):
        result = pipeline(np.array(image.convert("RGB")))
    return Image.fromarray(np.asarray(result, dtype=np.uint8), "RGB")
