"""Load and validate `config/degradation.yml`.

The declared tier lists *are* the variant counts — three tiers in a family
produce three degraded variants per page. There is deliberately no separate
count key, so the configuration cannot contradict itself.

Ported from the predecessor repo's `receipt_degradation` loader and generalised
from one severity ladder to a family of them, because a scanner and a phone
camera damage a page differently and merging them makes the question "which
intake channel breaks it?" unanswerable.
"""

from dataclasses import dataclass
from pathlib import Path

import yaml

_FAMILY_KEYS = ("description", "tiers")
_TIER_KEYS = ("name", "suffix", "description", "ink", "paper", "marks", "geometry", "camera")
_MODES = ("skew", "warp")

_EXAMPLE = """              families:
                scan:
                  description: Flatbed or sheet-fed scanner.
                  tiers:
                    - name: light
                      suffix: scan1
                      description: A well-maintained office scanner.
                      ink:   [{augmentation: InkBleed, intensity: [0.03, 0.10], kernel: 3}]
                      paper: [{augmentation: LightingGradient, max_brightness: 255, direction: 90}]
                      marks: [{mark: roller_streaks, count: [3, 6], width: [4, 12], strength: [0.04, 0.10]}]
                      geometry: {mode: skew, rotation_deg: [-0.8, 0.8], margin: [0.01, 0.02]}
                      camera:   {blur: [0.1, 0.3], noise_sigma: [1, 2], jpeg: [88, 96]}"""


class TierConfigError(RuntimeError):
    """Raised when the degradation config is missing or malformed."""


@dataclass(frozen=True)
class Tier:
    """One declared severity level within one intake family.

    Attributes:
        family: Which intake channel this belongs to, e.g. "scan".
        name: Severity label within the family, e.g. "light".
        suffix: Filename suffix distinguishing this variant, e.g. "scan1".
        description: Why this severity exists, for whoever reads the config.
        ink: Augraphy ink-phase specs, each naming a registered augmentation.
        paper: Augraphy paper-phase specs, same shape as `ink`.
        marks: This project's own seeded paper effects, applied after Augraphy
            and before the geometry. They exist because the Augraphy effects
            that produced the same artefacts are not reproducible.
        geometry: Geometry parameters; `mode` selects skew or warp.
        camera: Photometric parameters — blur, noise_sigma, jpeg.
    """

    family: str
    name: str
    suffix: str
    description: str
    ink: list[dict]
    paper: list[dict]
    marks: list[dict]
    geometry: dict
    camera: dict

    @property
    def label(self) -> str:
        """The name this tier is reported and exported under."""
        return f"{self.family}-{self.name}"


def _err(what: str, *, config_path: Path, key_path: str, expected: str, recover: str) -> TierConfigError:
    """Build a four-element fail-fast diagnostic."""
    return TierConfigError(
        f"Invalid degradation config.\n"
        f"  What:     {what}\n"
        f"  Where:    {config_path.resolve()} -> {key_path}\n"
        f"  Expected: {expected}\n"
        f"  Recover:  {recover}"
    )


def load_tiers(config_path: Path, *, families: list[str] | None = None) -> list[Tier]:
    """Load every declared tier, in YAML order.

    Args:
        config_path: Path to degradation.yml.
        families: Restrict to these families; None loads all of them.

    Returns:
        The declared tiers, family by family, in the order they appear.

    Raises:
        TierConfigError: The file is missing, malformed, or a tier omits a key.
    """
    if not config_path.exists():
        raise _err(
            f"{config_path.name} does not exist, so there is nothing to declare what "
            "a degraded page looks like.",
            config_path=config_path,
            key_path="(whole file)",
            expected=f"a YAML file declaring intake families and their tiers, e.g.\n{_EXAMPLE}",
            recover="restore config/degradation.yml, or pass --degradation pointing at it.",
        )

    document = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    declared = document.get("families")
    if not isinstance(declared, dict) or not declared:
        raise _err(
            "the top-level `families:` mapping is missing or empty.",
            config_path=config_path,
            key_path="families",
            expected=f"at least one intake family, each with `tiers:`, e.g.\n{_EXAMPLE}",
            recover="add a `families:` block naming at least one intake channel.",
        )

    wanted = list(declared) if families is None else families
    for family in wanted:
        if family not in declared:
            raise _err(
                f"family '{family}' was requested but is not declared.",
                config_path=config_path,
                key_path=f"families.{family}",
                expected=f"one of the declared families: {sorted(declared)}.",
                recover=f"declare '{family}', or pass --family with one of the names above.",
            )

    loaded: list[Tier] = []
    for family in wanted:
        block = declared[family]
        if not isinstance(block, dict):
            raise _err(
                f"family '{family}' is not a mapping.",
                config_path=config_path,
                key_path=f"families.{family}",
                expected=f"a mapping with keys {list(_FAMILY_KEYS)}, e.g.\n{_EXAMPLE}",
                recover=f"rewrite families.{family} as a mapping.",
            )
        for key in _FAMILY_KEYS:
            if key not in block:
                raise _err(
                    f"family '{family}' has no `{key}:` key.",
                    config_path=config_path,
                    key_path=f"families.{family}.{key}",
                    expected=f"every family to declare {list(_FAMILY_KEYS)}, e.g.\n{_EXAMPLE}",
                    recover=f"add `{key}:` to families.{family}.",
                )

        tiers = block["tiers"]
        if not isinstance(tiers, list) or not tiers:
            raise _err(
                f"family '{family}' declares no tiers.",
                config_path=config_path,
                key_path=f"families.{family}.tiers",
                expected=f"a non-empty list of tiers, e.g.\n{_EXAMPLE}",
                recover=f"add at least one tier to families.{family}.tiers.",
            )

        for index, entry in enumerate(tiers):
            where = f"families.{family}.tiers[{index}]"
            if not isinstance(entry, dict):
                raise _err(
                    f"tier {index} of family '{family}' is not a mapping.",
                    config_path=config_path,
                    key_path=where,
                    expected=f"a mapping with keys {list(_TIER_KEYS)}, e.g.\n{_EXAMPLE}",
                    recover=f"rewrite {where} as a mapping.",
                )
            for key in _TIER_KEYS:
                if key not in entry:
                    raise _err(
                        f"tier {index} of family '{family}' has no `{key}:` key. Every key "
                        "is required, including empty ones — `ink: []` states that a tier "
                        "leaves the ink alone, where an omission leaves a reader unable to "
                        "tell a decision from an oversight.",
                        config_path=config_path,
                        key_path=f"{where}.{key}",
                        expected=f"every tier to declare {list(_TIER_KEYS)}, e.g.\n{_EXAMPLE}",
                        recover=f"add `{key}:` to {where}.",
                    )

            mode = entry["geometry"].get("mode")
            if mode not in _MODES:
                raise _err(
                    f"tier '{entry['name']}' of family '{family}' declares geometry mode "
                    f"{mode!r}, which is not a mode this pipeline implements.",
                    config_path=config_path,
                    key_path=f"{where}.geometry.mode",
                    expected=f"one of {list(_MODES)} — `skew` holds the page flat and square "
                    "as a platen does, `warp` foreshortens it onto a desk as a camera does.",
                    recover=f"set {where}.geometry.mode to skew or warp.",
                )

            loaded.append(
                Tier(
                    family=family,
                    name=entry["name"],
                    suffix=entry["suffix"],
                    description=entry["description"],
                    ink=entry["ink"] or [],
                    paper=entry["paper"] or [],
                    marks=entry["marks"] or [],
                    geometry=entry["geometry"],
                    camera=entry["camera"],
                )
            )

    suffixes = [tier.suffix for tier in loaded]
    duplicated = sorted({s for s in suffixes if suffixes.count(s) > 1})
    if duplicated:
        raise _err(
            f"the suffix(es) {duplicated} are declared by more than one tier. Suffixes "
            "name the output files, so a collision would have one tier overwrite another "
            "and report a full run.",
            config_path=config_path,
            key_path="families.*.tiers[*].suffix",
            expected="every tier across every family to carry a distinct suffix, e.g. "
            "scan1, scan2, photo1.",
            recover="give each tier its own suffix.",
        )
    return loaded
