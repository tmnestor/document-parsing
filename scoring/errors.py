"""The four-element fail-fast diagnostic, shared across the scoring package.

Mirrors `generators/serialise.py`'s `_err`, deliberately rather than importing
it: `scoring/` reads an exported directory and imports nothing from
`generators/` (see tests/scoring/test_boundaries.py). Twelve duplicated lines
are the price of that boundary, and a cheap one.
"""

from pathlib import Path


class ScoringError(RuntimeError):
    """Raised when a corpus, a prediction set or the policy is unusable."""


def diagnostic(what: str, *, path: Path | str, key: str, expected: str, recover: str) -> ScoringError:
    """Build a four-element fail-fast diagnostic.

    Args:
        what: What is wrong.
        path: Absolute path of the offending file.
        key: The dotted key or filename inside it.
        expected: A concrete example of a valid value.
        recover: A one-line remediation step.

    Returns:
        The error, ready to raise.
    """
    return ScoringError(
        "Cannot score.\n"
        f"  What:     {what}\n"
        f"  Where:    {path} -> {key}\n"
        f"  Expected: {expected}\n"
        f"  Recover:  {recover}"
    )
