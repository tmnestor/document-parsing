"""Load a model's predictions, bound to one prompt and one corpus vintage.

Two hashes are checked, and they close different holes. `prompt_sha256` catches
a prediction produced with a different prompt: prompt and transcripts are a
matched pair, and a model told to do something else is not being measured on
this benchmark. `corpus_manifest_sha256` is recorded by the runner on the remote
host and catches a prediction produced against a different corpus vintage --
verifying local images proves only that the local copy is intact, which says
nothing about what the GPU box actually read.
"""

import json
from dataclasses import dataclass
from pathlib import Path

from scoring.corpus import Corpus
from scoring.errors import diagnostic

_REQUIRED_RUN_KEYS: tuple[str, ...] = (
    "model_id",
    "model_revision",
    "prompt_sha256",
    "corpus",
    "corpus_manifest_sha256",
    "generated_at",
    "host",
)


@dataclass(frozen=True)
class RunMetadata:
    """Provenance for one model's pass over one corpus.

    Attributes:
        model_id: The model identifier used in reports.
        model_revision: Checkpoint or revision, for reproducibility.
        prompt_sha256: Hash of the prompt the runner used.
        corpus: Name of the corpus directory the runner read.
        corpus_manifest_sha256: Hash of that corpus's manifest, from the runner.
        generated_at: ISO-8601 timestamp of the run.
        host: Where inference ran.
    """

    model_id: str
    model_revision: str
    prompt_sha256: str
    corpus: str
    corpus_manifest_sha256: str
    generated_at: str
    host: str


@dataclass(frozen=True)
class PredictionSet:
    """One model's output for one corpus.

    Attributes:
        run: The run's provenance.
        texts: Prediction text by page stem; None where the file was absent and
            `allow_missing` was set.
    """

    run: RunMetadata
    texts: dict[str, str | None]


def load_predictions(root: Path, corpus: Corpus, *, allow_missing: bool = False) -> PredictionSet:
    """Load and verify a prediction directory against a corpus.

    Args:
        root: Directory holding `run.json` and one `.md` per page stem.
        corpus: The corpus these predictions claim to be for.
        allow_missing: Record an absent prediction as None instead of failing.

    Returns:
        The verified prediction set.

    Raises:
        ScoringError: `run.json` is absent or incomplete, either hash disagrees
            with the corpus, or a prediction is missing without `allow_missing`.
    """
    run_path = root / "run.json"
    if not run_path.exists():
        raise diagnostic(
            "run.json does not exist, so this prediction set carries no provenance.",
            path=root.resolve(),
            key="run.json",
            expected="a JSON object with "
            f"{list(_REQUIRED_RUN_KEYS)}, written by the runner beside its predictions.",
            recover="have the runner emit run.json, recording the prompt and corpus "
            "manifest hashes it actually read.",
        )

    data = json.loads(run_path.read_text(encoding="utf-8"))
    for key in _REQUIRED_RUN_KEYS:
        if key not in data:
            raise diagnostic(
                f"'{key}' is missing from run.json.",
                path=run_path.resolve(),
                key=key,
                expected=f"every key of {list(_REQUIRED_RUN_KEYS)}; provenance is not optional, "
                f'e.g.\n              "{key}": "…"',
                recover=f"add '{key}' to run.json in the runner.",
            )
        value = data[key]
        if not isinstance(value, str) or not value:
            raise diagnostic(
                f"'{key}' is {value!r} ({type(value).__name__}), not a non-empty string.",
                path=run_path.resolve(),
                key=key,
                expected=f'a non-empty string, e.g.\n              "{key}": "…"',
                recover=f"fix the runner so it writes '{key}' as a plain string in run.json.",
            )
    run = RunMetadata(**{key: data[key] for key in _REQUIRED_RUN_KEYS})

    if run.prompt_sha256 != corpus.prompt_sha256:
        raise diagnostic(
            "the predictions were produced with a different prompt than this corpus ships.",
            path=run_path.resolve(),
            key="prompt_sha256",
            expected=f"{corpus.prompt_sha256[:12]}… — the hash of {corpus.root.name}/prompt.md; "
            f"found {run.prompt_sha256[:12]}….",
            recover="re-run the model with the prompt shipped in the corpus; prompt and "
            "transcripts are a matched pair and scoring across them measures something else.",
        )

    if run.corpus_manifest_sha256 != corpus.manifest_sha256:
        raise diagnostic(
            "the predictions were produced against a different corpus vintage.",
            path=run_path.resolve(),
            key="corpus_manifest_sha256",
            expected=f"{corpus.manifest_sha256[:12]}… — the hash of {corpus.root.name}/"
            f"manifest.jsonl; found {run.corpus_manifest_sha256[:12]}….",
            recover="score against the corpus the runner actually read, or re-run the model "
            "against this one.",
        )

    texts: dict[str, str | None] = {}
    for page in corpus.pages:
        path = root / f"{page.stem}.md"
        if path.exists():
            texts[page.stem] = path.read_text(encoding="utf-8")
            continue
        if not allow_missing:
            raise diagnostic(
                f"no prediction for {page.stem}.",
                path=root.resolve(),
                key=f"{page.stem}.md",
                expected="one prediction file per page in the corpus — scoring a subset and "
                "reporting the mean flatters a model that failed to answer.",
                recover="produce the missing prediction, or pass --allow-missing to record "
                "the absence in the output.",
            )
        texts[page.stem] = None

    return PredictionSet(run=run, texts=texts)
