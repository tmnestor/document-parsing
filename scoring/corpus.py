"""Read an exported corpus, and prove it is the vintage it claims to be.

The shipped corpus README instructs a human to verify image hashes before
scoring. Doing it here instead is the entire point of shipping the hashes: a
score computed against the wrong vintage is not merely wrong, it is plausible.
"""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from scoring.errors import diagnostic


@dataclass(frozen=True)
class CorpusPage:
    """One page and its reference transcript.

    Attributes:
        case_id: The case identifier, e.g. `CASE001`.
        doc_type: The document type, e.g. `bank_statements`.
        image: Absolute path to the page image.
        transcript: Absolute path to the reference transcript.
        sha256: The image hash recorded in the manifest.
        stem: The shared filename stem, which pairs a prediction to this page.
    """

    case_id: str
    doc_type: str
    image: Path
    transcript: Path
    sha256: str
    stem: str


@dataclass(frozen=True)
class Corpus:
    """An exported corpus directory.

    Attributes:
        root: The corpus directory.
        pages: Its pages, in manifest order.
        prompt_sha256: Hash of the shipped `prompt.md`.
        manifest_sha256: Hash of `manifest.jsonl`, identifying the vintage.
    """

    root: Path
    pages: tuple[CorpusPage, ...]
    prompt_sha256: str
    manifest_sha256: str


def _sha256(path: Path) -> str:
    """Return the hex sha256 of a file, read in chunks."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_corpus(root: Path) -> Corpus:
    """Load an exported corpus from its manifest.

    Args:
        root: The corpus directory.

    Returns:
        The loaded corpus. Images are not read here; call `verify_images`.

    Raises:
        ScoringError: The directory holds no manifest or no prompt.
    """
    manifest = root / "manifest.jsonl"
    if not manifest.exists():
        raise diagnostic(
            f"{manifest.name} does not exist, so {root.name} is not an exported corpus.",
            path=root.resolve(),
            key="manifest.jsonl",
            expected="a directory written by `export` or `degrade`, holding images/, "
            "transcripts/, prompt.md and manifest.jsonl.",
            recover="point --corpus at an export, or run "
            "`python -m generators.pipeline export` to create one.",
        )

    prompt = root / "prompt.md"
    if not prompt.exists():
        raise diagnostic(
            "prompt.md does not exist, so the prompt a prediction used cannot be checked.",
            path=root.resolve(),
            key="prompt.md",
            expected="the prompt shipped with the corpus — prompt and transcripts are a "
            "matched pair and travel together.",
            recover="re-export the corpus, which copies config/prompt.md into it.",
        )

    pages = []
    for line in manifest.read_text(encoding="utf-8").splitlines():
        if not line:
            continue
        record = json.loads(line)
        stem = Path(record["image"]).stem
        pages.append(
            CorpusPage(
                case_id=stem.split("_")[0],
                doc_type=str(record["doc_type"]),
                image=root / record["image"],
                transcript=root / record["transcript"],
                sha256=str(record["sha256"]),
                stem=stem,
            )
        )

    return Corpus(
        root=root,
        pages=tuple(pages),
        prompt_sha256=_sha256(prompt),
        manifest_sha256=_sha256(manifest),
    )


def verify_images(corpus: Corpus) -> None:
    """Check every image against its manifest hash.

    Args:
        corpus: The loaded corpus.

    Raises:
        ScoringError: An image is missing, or its bytes do not match.
    """
    for page in corpus.pages:
        if not page.image.exists():
            raise diagnostic(
                f"{page.image.name} is named in the manifest but is not on disk.",
                path=corpus.root.resolve(),
                key=f"images/{page.image.name}",
                expected="every image the manifest lists, present and unmodified.",
                recover="restore the missing page, or re-export the corpus.",
            )
        actual = _sha256(page.image)
        if actual != page.sha256:
            raise diagnostic(
                f"{page.image.name} does not match its manifest hash — this is a different "
                f"corpus vintage. Expected {page.sha256[:12]}…, found {actual[:12]}….",
                path=corpus.root.resolve(),
                key=f"images/{page.image.name}",
                expected="image bytes matching the sha256 recorded in manifest.jsonl.",
                recover="score against the corpus the predictions were produced from; a "
                "score across vintages is meaningless.",
            )
