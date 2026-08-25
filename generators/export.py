"""Assemble the dated deliverable directory.

Pure assembly: this module renders nothing and serialises nothing. It copies
what `generate` and `serialise` already produced and adds the three artifacts
that make a corpus interpretable away from this checkout — a hashed manifest,
the policy that produced the transcripts, and the prompt they assume.

**Why the manifest carries a hash.** Design §6.1 records a real failure behind
this requirement: a scoring run pointed at the wrong-vintage ground truth
matched 22 of 165 filenames and still produced a plausible number. Filename
matching cannot detect that. A sha256 per image makes the mismatch impossible to
score around rather than merely detectable after the fact — and it hashes the
image, because the image is what a model reads.
"""

import hashlib
import json
import shutil
from pathlib import Path

_CHUNK = 1024 * 1024


class ExportError(RuntimeError):
    """Raised when the artifacts an export needs are missing or inconsistent."""


def sha256_of(path: Path) -> str:
    """Return the hex sha256 of a file, read in chunks.

    Args:
        path: File to hash.

    Returns:
        The hex digest.
    """
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _missing(kind: str, path: Path, *, recover_command: str) -> ExportError:
    """Build a four-element diagnostic for an artifact the export needs."""
    return ExportError(
        f"Cannot export: a {kind} is missing.\n"
        f"  What:     {path.name} does not exist.\n"
        f"  Where:    {path}\n"
        f"  Expected: one {kind} per rendered document, produced by "
        f"`python -m generators.pipeline {recover_command}`.\n"
        f"  Recover:  run `python -m generators.pipeline {recover_command}` before exporting."
    )


def manifest_record(image: Path, transcript: Path, doc_type: str) -> dict:
    """Build one manifest row.

    Paths are recorded relative to the export root, so the shipped corpus stays
    valid wherever it is unpacked.

    Args:
        image: The rendered page.
        transcript: Its Markdown transcript.
        doc_type: The document type key, e.g. "invoices".

    Returns:
        `{"image", "transcript", "doc_type", "sha256"}` per design §6.1.

    Raises:
        ExportError: Either file is missing.
    """
    if not image.exists():
        raise _missing("rendered page", image, recover_command="generate")
    if not transcript.exists():
        raise _missing("transcript", transcript, recover_command="serialise")
    return {
        "image": f"images/{image.name}",
        "transcript": f"transcripts/{transcript.name}",
        "doc_type": doc_type,
        "sha256": sha256_of(image),
    }


def readme_text(date_stamp: str, counts: dict[str, int]) -> str:
    """Build the README that ships with the corpus.

    Written for someone with no access to this repo: what the corpus is, what
    each file is for, and — the part that matters — that the manifest hashes
    should be verified before scoring.

    Args:
        date_stamp: The corpus date, YYYYMMDD.
        counts: Documents per document type.

    Returns:
        The README body.
    """
    total = sum(counts.values())
    rows = "\n".join(f"| {doc_type} | {count} |" for doc_type, count in sorted(counts.items()))
    return f"""# Document parsing corpus — {date_stamp}

{total} synthetic Australian business documents for benchmarking **full-page
transcription**. Each page ships with a canonical Markdown transcript produced
by the renderer at the moment it drew the page — the ground truth is authored,
never recovered by OCR or by hand.

| Document type | Pages |
| --- | --- |
{rows}
| **total** | **{total}** |

## What is here

| Path | What it is |
| --- | --- |
| `images/` | One pristine page render per case. |
| `transcripts/` | The canonical transcript for each page, same stem. |
| `manifest.jsonl` | One row per case: image, transcript, doc_type, sha256. |
| `prompt.md` | The prompt these transcripts assume. |
| `serialisation.yml` | The exact policy that produced these transcripts. |

## Verify before you score

Check every image against its `sha256` in `manifest.jsonl` before scoring.

This is not ceremony. Scoring a run against the wrong-vintage ground truth is
the failure this manifest exists to prevent: filenames alone can match well
enough to produce a plausible number while comparing the wrong pages. If a hash
does not match, the corpus and the predictions are not the same vintage, and any
score from them is meaningless.

## How to score

Ask the model to transcribe each page using `prompt.md` verbatim — the prompt
and these transcripts are a matched pair, and changing one without the other
silently measures something else.

Report two numbers:

1. **Normalised** — pass prediction and truth through the same normalisation
   (Unicode NFKC, collapse whitespace runs, fold dashes and quotes to ASCII,
   strip Markdown syntax), then compute normalised edit distance and
   character/word error rate. This measures *reading*.
2. **Strict** — the raw forms as shipped. This measures reading plus adherence
   to the conventions in `serialisation.yml`.

Neither is the real number on its own. **Do not fold case**: reading account
names and identifiers with correct case is legitimately part of transcription.

## What this corpus does not claim

Every page is a pristine render. These results bound parsing accuracy on **clean
renders**, not on photographed or scanned input. That is a coherent benchmark;
it simply wants stating rather than being read as general document-parsing
accuracy.
"""


def export_corpus(
    records: list[dict],
    *,
    images_root: Path,
    transcripts_dir: Path,
    policy_path: Path,
    prompt_path: Path,
    target: Path,
    date_stamp: str,
) -> Path:
    """Assemble the dated deliverable directory.

    Args:
        records: The event records `generate` wrote, one per document.
        images_root: Directory holding the rendered pages, possibly in
            per-type subdirectories.
        transcripts_dir: Directory holding the serialised transcripts.
        policy_path: The `serialisation.yml` that produced them.
        prompt_path: The prompt these transcripts assume.
        target: Directory to create the export inside.
        date_stamp: Corpus date, YYYYMMDD.

    Returns:
        The created export root.

    Raises:
        ExportError: An image, transcript, the policy or the prompt is missing.
    """
    for label, path, command in (
        ("serialisation policy", policy_path, "serialise"),
        ("prompt", prompt_path, "serialise"),
    ):
        if not path.exists():
            raise _missing(label, path, recover_command=command)

    root = target / f"parsing_{date_stamp}"
    (root / "images").mkdir(parents=True, exist_ok=True)
    (root / "transcripts").mkdir(parents=True, exist_ok=True)

    manifest: list[dict] = []
    counts: dict[str, int] = {}
    for record in records:
        doc_type = record["doc_type"]
        image_name = record["image_file"]
        source_image = _find_image(images_root, image_name, doc_type)
        source_transcript = transcripts_dir / (Path(image_name).stem + ".md")

        row = manifest_record(source_image, source_transcript, doc_type)
        shutil.copy2(source_image, root / "images" / image_name)
        shutil.copy2(source_transcript, root / "transcripts" / source_transcript.name)
        manifest.append(row)
        counts[doc_type] = counts.get(doc_type, 0) + 1

    with (root / "manifest.jsonl").open("w", encoding="utf-8") as handle:
        for row in manifest:
            handle.write(json.dumps(row) + "\n")

    # Copied verbatim, not regenerated: a transcript must stay interpretable
    # independently of this checkout (design §6.1), and the prompt is half of a
    # matched pair with the transcripts beside it.
    shutil.copy2(policy_path, root / "serialisation.yml")
    shutil.copy2(prompt_path, root / "prompt.md")
    (root / "README.md").write_text(readme_text(date_stamp, counts), encoding="utf-8")
    return root


def _find_image(images_root: Path, image_name: str, doc_type: str) -> Path:
    """Locate a rendered page under the output directory.

    `generate` writes into a per-type subdirectory by default but flattens when
    `--output` is given, so both shapes are accepted.

    Args:
        images_root: The output directory.
        image_name: The page's filename.
        doc_type: Its document type, used as the subdirectory name.

    Returns:
        The path that exists; the flat candidate if neither does, so
        `manifest_record` raises the diagnostic rather than this helper.
    """
    nested = images_root / doc_type / image_name
    if nested.exists():
        return nested
    return images_root / image_name
