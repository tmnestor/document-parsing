"""Registry and runner for the ink and paper effects phase.

Only the augmentations this project declares are registered. An allow-list
rather than a passthrough to a whole catalogue is deliberate: it turns a YAML
typo into a startup diagnostic naming the valid options, and it records which
effects were chosen and which were rejected.

Ported from the predecessor repo, with the registry widened for the scanner
family. That repo excluded `DirtyRollers` and `DirtyDrum` as "photocopier
damage, not phone photography" — correct there, since it modelled only phone
photos. `DirtyDrum` would be exactly right for the `scan` ladder, but it has no
portable replacement yet, so it stays unregistered alongside `DirtyRollers`.

The classes registered below (`generators.degradation.effects`) are this
project's own re-derivation, not Augraphy's: they were measured to produce
different pixels on arm64 macOS and x86_64 Linux at identical library
versions, because Augraphy evaluates transcendental functions and IEEE-754
does not constrain those across platforms. See `effects.py`.
"""

from collections.abc import Callable

import numpy as np
from PIL import Image

from generators.degradation.effects import InkBleed, LightingGradient, ShadowCast, ThermalFade
from generators.degradation.tiers import Tier

# What a constructed effect looks like: called with the frame and the phase's
# shared seeded generator, returning the degraded frame.
Effect = Callable[[np.ndarray, np.random.Generator], np.ndarray]

# YAML name -> effect class. Every geometric augmentation is deliberately
# excluded: geometry.py owns geometry, so that skew and warp are one
# implementation rather than two that can disagree.
#
# `ThermalFade` is the one effect here that is not meant for every document
# type -- a faded bank statement is nonsense -- which is what `doc_types:`
# on every spec (see `_build` and `_effects_for` below) exists to gate.
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
AUGMENTATIONS: dict[str, Callable[..., Effect]] = {
    "InkBleed": InkBleed,
    "LightingGradient": LightingGradient,
    "ShadowCast": ShadowCast,
    "ThermalFade": ThermalFade,
}

# YAML key -> the constructor keyword each class expects. The YAML uses short
# readable names; kept distinct from the re-derived classes' own keyword names
# for the same reason Augraphy's differed — a stable YAML vocabulary that does
# not have to track whichever implementation is registered.
#
# `doc_types` is deliberately absent from every mapping here: it is not a
# constructor keyword, it is gating metadata `_build` strips out and
# `_effects_for` reads directly from the spec. See both below.
_PARAM_NAMES: dict[str, dict[str, str]] = {
    "InkBleed": {"intensity": "intensity_range", "kernel": "kernel_size"},
    "LightingGradient": {"max_brightness": "max_brightness", "direction": "direction"},
    "ShadowCast": {"side": "shadow_side", "opacity": "shadow_opacity_range"},
    "ThermalFade": {"strength": "strength_range", "direction": "direction"},
}

# Meta-keys every spec may carry that are not constructor parameters:
# "augmentation" names the class, "doc_types" gates which document types the
# effect applies to.
_META_KEYS = frozenset({"augmentation", "doc_types"})

# InkBleed wants a (w, h) kernel; the YAML declares a single int, since a
# non-square ink-bleed kernel has no physical meaning.
_SQUARE_KERNEL_KEYS = frozenset({"kernel_size"})


class AugmentationError(RuntimeError):
    """Raised when a tier names an augmentation that is not registered."""


def _build(spec: dict, *, tier: Tier, phase: str) -> Effect:
    """Instantiate one effect from its YAML spec.

    Args:
        spec: The YAML mapping, carrying `augmentation:` plus its parameters.
        tier: Owning tier, for diagnostics.
        phase: "ink" or "paper", for diagnostics.

    Returns:
        The constructed effect, callable as `effect(image_array, rng)`.

    Raises:
        AugmentationError: No `augmentation:` key, no `doc_types:` key, an
            unregistered name, or a parameter the registered class does not
            accept.
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
            "              {augmentation: InkBleed, intensity: [0.05, 0.15], kernel: 3,\n"
            "               doc_types: [bank_statements, receipts, invoices]}\n"
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

    doc_types = spec.get("doc_types")
    if not isinstance(doc_types, list) or not doc_types:
        raise AugmentationError(
            "Invalid augmentation spec.\n"
            f"  What:     the {name} entry in the {phase} phase of tier '{tier.label}' has "
            "no 'doc_types:' key, so there is no way to tell which document types it "
            "applies to. Every key is required, even one that would name every type.\n"
            f"  Where:    config/degradation.yml -> {where}\n"
            "  Expected: a non-empty list of document types, e.g.\n"
            "              {augmentation: ThermalFade, strength: [0.10, 0.25], "
            "direction: 90,\n"
            "               doc_types: [receipts]}\n"
            f"  Recover:  add 'doc_types:' to the {name} entry of tier '{tier.label}', "
            "naming which document types this effect applies to -- "
            "[bank_statements, receipts, invoices] for an effect that applies to all of them."
        )

    mapping = _PARAM_NAMES[str(name)]
    kwargs: dict[str, object] = {}
    for key, value in spec.items():
        if key in _META_KEYS:
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
                "generators/degradation/augment.py with the constructor keyword it maps to."
            )
        if param in _SQUARE_KERNEL_KEYS:
            kwargs[param] = (int(value), int(value))
        elif isinstance(value, list):
            kwargs[param] = tuple(value)  # the *_range params want tuples, not lists
        else:
            kwargs[param] = value
    return factory(**kwargs)


def _effects_for(specs: list[dict], *, tier: Tier, phase: str, doc_type: str) -> list[Effect]:
    """Build every effect in a phase, keeping only the ones this page's type gets.

    Every spec is built regardless of `doc_type` -- a malformed entry must fail
    even on a run that would never have applied it, rather than only surfacing
    when someone happens to render the one document type that exercises it.

    Args:
        specs: The phase's spec list, e.g. `tier.paper`.
        tier: Owning tier, for diagnostics.
        phase: "ink" or "paper", for diagnostics.
        doc_type: The page's document type, e.g. "receipts".

    Returns:
        The constructed effects whose `doc_types:` includes `doc_type`, in
        declared order.
    """
    effects = []
    for spec in specs:
        effect = _build(spec, tier=tier, phase=phase)
        if doc_type in spec["doc_types"]:
            effects.append(effect)
    return effects


def apply_effects(image: Image.Image, tier: Tier, seed: int, doc_type: str) -> Image.Image:
    """Apply a tier's ink and paper phases to the flat page.

    Runs before any geometry: these model damage to the paper itself, which must
    then be transformed *with* the page rather than painted across an image that
    has already been skewed or warped.

    Args:
        image: The clean, flat rendered page.
        tier: The severity tier supplying the phase specs.
        seed: Seed making this tier's output reproducible.
        doc_type: The page's document type, e.g. "receipts". Gates which specs
            apply -- `ThermalFade`, for instance, is declared with
            `doc_types: [receipts]` and is a no-op for every other type.

    Returns:
        The augmented page, at the same dimensions as the input.

    Raises:
        AugmentationError: A phase entry is malformed, names an unknown
            augmentation, or has no `doc_types:` key.
    """
    ink = _effects_for(tier.ink, tier=tier, phase="ink", doc_type=doc_type)
    paper = _effects_for(tier.paper, tier=tier, phase="paper", doc_type=doc_type)

    if not ink and not paper:
        return image.copy()

    # One seeded generator for the whole phase, passed explicitly. The previous
    # implementation set NumPy's global seed because Augraphy sampled from it in
    # places its own `random_seed` did not reach; nothing here reads a global.
    rng = np.random.default_rng(seed)
    frame = np.array(image.convert("RGB"))
    for effect in (*ink, *paper):
        frame = effect(frame, rng)
    return Image.fromarray(np.asarray(frame, dtype=np.uint8), "RGB")
