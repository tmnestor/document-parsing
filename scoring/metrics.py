"""Edit-distance metrics over normalised or raw text.

CER is deliberately NOT capped at 1.0. A model that emits 128,768 characters for
a 2,400-character page has failed in a way a capped score would render identical
to ordinary transcription error, and that difference is exactly what a
comparison needs to surface. `report` handles the consequence by leading with
the median rather than the mean.
"""

from rapidfuzz.distance import Levenshtein


def edit_distance(a: str, b: str) -> int:
    """Return the Levenshtein distance between two strings.

    Args:
        a: First string.
        b: Second string.

    Returns:
        The number of single-character insertions, deletions and substitutions.
    """
    return int(Levenshtein.distance(a, b))


def character_error_rate(reference: str, prediction: str) -> float:
    """Return edits per reference character.

    Args:
        reference: The ground-truth transcript.
        prediction: The model's output.

    Returns:
        `edit_distance / len(reference)`, uncapped. When the reference is empty:
        0.0 if the prediction is also empty, else 1.0 — there is nothing to
        divide by, and any output against no reference is wholly wrong.
    """
    if not reference:
        return 0.0 if not prediction else 1.0
    return edit_distance(reference, prediction) / len(reference)


def word_error_rate(reference: str, prediction: str) -> float:
    """Return word-level edits per reference word.

    Args:
        reference: The ground-truth transcript.
        prediction: The model's output.

    Returns:
        Word-level Levenshtein distance over the reference's word count, with
        the same empty-reference rule as `character_error_rate`.
    """
    ref_words = reference.split()
    pred_words = prediction.split()
    if not ref_words:
        return 0.0 if not pred_words else 1.0
    return int(Levenshtein.distance(ref_words, pred_words)) / len(ref_words)


def is_degenerate(reference: str, prediction: str, multiple: float) -> bool:
    """Report whether a prediction has run away rather than merely erred.

    Args:
        reference: The ground-truth transcript.
        prediction: The model's output.
        multiple: `reporting.degenerate_length_multiple` from the policy.

    Returns:
        True when the prediction is longer than `multiple` times the reference.
        An empty prediction is never degenerate — that is a total miss, already
        recorded by the error rate.
    """
    if not prediction:
        return False
    if not reference:
        return True
    return len(prediction) > multiple * len(reference)
