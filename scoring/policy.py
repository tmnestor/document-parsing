"""Load and validate config/scoring.yml."""

import unicodedata
from pathlib import Path

import yaml

from scoring.errors import diagnostic

REQUIRED_POLICY_KEYS: tuple[str, ...] = (
    "normalisation.unicode_form",
    "normalisation.collapse_whitespace",
    "normalisation.fold_dashes",
    "normalisation.fold_quotes",
    "normalisation.strip_markdown",
    "normalisation.fold_case",
    "reporting.degenerate_length_multiple",
    "reporting.percentiles",
)

_UNICODE_FORMS = ("NFC", "NFD", "NFKC", "NFKD")
_BOOL_KEYS = (
    "collapse_whitespace",
    "fold_dashes",
    "fold_quotes",
    "strip_markdown",
    "fold_case",
)
_EXAMPLES: dict[str, str] = {
    "normalisation.unicode_form": "NFKC",
    "normalisation.collapse_whitespace": "true",
    "normalisation.fold_dashes": "true",
    "normalisation.fold_quotes": "true",
    "normalisation.strip_markdown": "true",
    "normalisation.fold_case": "false",
    "reporting.degenerate_length_multiple": "3.0",
    "reporting.percentiles": "[50, 90, 100]",
}


def load_scoring_policy(path: Path) -> dict:
    """Read and validate the scoring convention.

    Args:
        path: Path to `scoring.yml`.

    Returns:
        The validated policy mapping.

    Raises:
        ScoringError: The file is missing or unparseable, a key is absent, or a
            value is outside what this scorer implements.
    """
    resolved = path.resolve()
    if not path.exists():
        raise diagnostic(
            f"{path} does not exist.",
            path=resolved,
            key="(whole file)",
            expected=f"a YAML mapping declaring every key of {list(REQUIRED_POLICY_KEYS)}, e.g.\n"
            "              normalisation:\n                unicode_form: NFKC",
            recover="create config/scoring.yml.",
        )

    try:
        policy = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as err:
        raise diagnostic(
            f"the file is not valid YAML: {err}",
            path=resolved,
            key="(whole file)",
            expected="parseable YAML, e.g.\n              normalisation:\n                fold_case: false",
            recover="fix the syntax error at the line named above.",
        ) from err

    if not isinstance(policy, dict):
        raise diagnostic(
            f"expected a mapping, got {type(policy).__name__}.",
            path=resolved,
            key="(document root)",
            expected="a top-level mapping of the two policy sections, e.g.\n"
            "              normalisation:\n                unicode_form: NFKC\n"
            "              reporting:\n                percentiles: [50, 90, 100]",
            recover="wrap the settings in a top-level mapping.",
        )

    for dotted in REQUIRED_POLICY_KEYS:
        section, _, key = dotted.partition(".")
        if not isinstance(policy.get(section), dict) or key not in policy[section]:
            raise diagnostic(
                f"'{dotted}' is not declared.",
                path=resolved,
                key=dotted,
                expected=f"every key of {list(REQUIRED_POLICY_KEYS)} present — none has a Python "
                f"default, e.g.\n              {section}:\n                {key}: {_EXAMPLES[dotted]}",
                recover=f"add '{key}:' under '{section}:' in {path}.",
            )

    _validate_values(policy, path=path, resolved=resolved)
    return policy


def _validate_values(policy: dict, *, path: Path, resolved: Path) -> None:
    """Range- and type-check every declared value.

    Args:
        policy: The policy mapping, known to declare every required key.
        path: The path as given, used in the recovery step.
        resolved: The absolute path, used to locate the file.

    Raises:
        ScoringError: A value is the wrong type or outside the allowed range.
    """
    form = policy["normalisation"]["unicode_form"]
    if form not in _UNICODE_FORMS:
        raise diagnostic(
            f"'unicode_form' is {form!r}, which is not a Unicode normalisation form.",
            path=resolved,
            key="normalisation.unicode_form",
            expected=f"one of {list(_UNICODE_FORMS)}, e.g.\n              unicode_form: NFKC",
            recover=f"set 'normalisation.unicode_form:' in {path} to one of those forms.",
        )
    unicodedata.normalize(form, "")

    for key in _BOOL_KEYS:
        value = policy["normalisation"][key]
        if not isinstance(value, bool):
            raise diagnostic(
                f"'{key}' is {value!r}, which is not a boolean.",
                path=resolved,
                key=f"normalisation.{key}",
                expected=f"true or false, e.g.\n              {key}: true",
                recover=f"set 'normalisation.{key}:' in {path} to true or false.",
            )

    multiple = policy["reporting"]["degenerate_length_multiple"]
    if isinstance(multiple, bool) or not isinstance(multiple, int | float) or multiple <= 1:
        raise diagnostic(
            f"'degenerate_length_multiple' is {multiple!r}, which is not a number above 1.",
            path=resolved,
            key="reporting.degenerate_length_multiple",
            expected="a number greater than 1 — at or below 1 every correct prediction counts "
            "as degenerate, e.g.\n              degenerate_length_multiple: 3.0",
            recover=f"raise 'reporting.degenerate_length_multiple:' in {path} above 1.",
        )

    percentiles = policy["reporting"]["percentiles"]
    if (
        not isinstance(percentiles, list)
        or not percentiles
        or not all(not isinstance(p, bool) and isinstance(p, int) and 0 < p <= 100 for p in percentiles)
    ):
        raise diagnostic(
            f"'percentiles' is {percentiles!r}, which is not a non-empty list of 1-100 integers.",
            path=resolved,
            key="reporting.percentiles",
            expected="a non-empty list of integers in 1..100, e.g.\n"
            "              percentiles: [50, 90, 100]",
            recover=f"set 'reporting.percentiles:' in {path} to such a list.",
        )
