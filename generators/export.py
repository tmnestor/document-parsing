"""Assemble the dated deliverable directory.

Pure assembly: this module renders nothing and re-serialises nothing. It
copies what `generate` and `serialise` already produced, adds the three
artifacts that make a corpus interpretable away from this checkout — a hashed
manifest, the policy that produced the transcripts, and the prompt they
assume — and projects each page's captured events to the layout and table
artifacts that ship beside it (`layout/*.json` via `generators.layout`,
`tables/*.html` via `generators.tables`).

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

import yaml

from generators.serialise import load_serialisation_policy
from generators.tables import table_html

# `generators.layout` is imported inside `export_corpus`, not here: it reaches
# `generators.layout_dsl`, whose package `__init__` eagerly imports the render
# engine and, through it, `content_engine` (Faker). `docparse-degrade` imports
# this module for `manifest_record` alone, in an environment that deliberately
# does not have Faker — a module-level import here would break that boundary
# for a function `degrade` never calls.

_CHUNK = 1024 * 1024
_SCORING_POLICY_PATH = Path("config/scoring.yml")

_NORMALISATION_STEPS: tuple[tuple[str, str], ...] = (
    ("collapse_whitespace", "collapse whitespace runs"),
    ("fold_dashes", "fold dashes to ASCII"),
    ("fold_quotes", "fold quotes to ASCII"),
    ("strip_markdown", "strip Markdown syntax"),
)


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


def _normalisation_sentence(policy: dict) -> str:
    """Describe the normalisation policy in the prose the corpus ships.

    Generated rather than written out, so the shipped README and
    `config/scoring.yml` cannot drift apart about what "normalised" means. Read
    as plain YAML: `scoring/` lives in another environment and is deliberately
    not importable from here.

    Args:
        policy: The parsed `config/scoring.yml`.

    Returns:
        One sentence naming the Unicode form, the enabled steps, and the
        case-folding decision.

    Raises:
        ExportError: The policy has no `normalisation` block, or the block is
            missing a key this sentence needs.
    """
    try:
        rules = policy["normalisation"]
        steps = [label for key, label in _NORMALISATION_STEPS if rules[key]]
        joined = ", ".join(steps) if steps else "apply no further steps"
        tail = "fold case" if rules["fold_case"] else "do not fold case"
        return f"Unicode {rules['unicode_form']}, {joined}, and {tail}"
    except KeyError as err:
        (key,) = err.args
        raise ExportError(
            "Cannot export: config/scoring.yml is missing a key the README needs.\n"
            f"  What:     '{key}' is absent from its 'normalisation:' block.\n"
            f"  Where:    {_SCORING_POLICY_PATH.resolve()} -> normalisation.{key}\n"
            "  Expected: every key of normalisation.unicode_form, "
            "collapse_whitespace, fold_dashes, fold_quotes, strip_markdown, "
            "fold_case, e.g.\n"
            "              normalisation:\n"
            "                unicode_form: NFKC\n"
            "                collapse_whitespace: true\n"
            "  Recover:  restore config/scoring.yml from version control."
        ) from err


def _load_scoring_policy() -> dict:
    """Read `config/scoring.yml`, cwd-relative like the rest of this module.

    Returns:
        The parsed policy mapping.

    Raises:
        ExportError: The file does not exist — e.g. `export` was run from
            somewhere other than the repository root.
    """
    if not _SCORING_POLICY_PATH.exists():
        raise ExportError(
            "Cannot export: the scoring policy used to describe normalisation "
            "is missing.\n"
            f"  What:     {_SCORING_POLICY_PATH} does not exist.\n"
            f"  Where:    {_SCORING_POLICY_PATH.resolve()}\n"
            "  Expected: config/scoring.yml, present relative to the current "
            "working directory.\n"
            "  Recover:  run `python -m generators.pipeline export` from the "
            "repository root, or restore config/scoring.yml."
        )
    return yaml.safe_load(_SCORING_POLICY_PATH.read_text(encoding="utf-8")) or {}


def readme_text(
    date_stamp: str,
    counts: dict[str, int],
    *,
    tabled: int,
    multi_tabled: int,
    categories: dict[str, int],
    span_categories: dict[str, int],
) -> str:
    """Build the README that ships with the corpus.

    Written for someone with no access to this repo: what the corpus is, what
    each file is for, how to read the layout annotations, and — the part that
    matters — that the manifest hashes should be verified before scoring.

    Every number in the body is counted from the export being assembled, for
    the same reason `_normalisation_sentence` is generated: a figure written
    out by hand describes whichever vintage the author last looked at, and
    nothing fails when the corpus moves on. `(14 of 165)` sat in this template
    across a corpus that had grown to 177 pages. The same rule is why the
    category coverage claim counts `categories` against `BLOCK_CATEGORIES`
    rather than saying "3 of 18": a document type that introduces a figure
    should move that sentence without anyone remembering to.

    Args:
        date_stamp: The corpus date, YYYYMMDD.
        counts: Documents per document type.
        tabled: Pages that ship a `tables/` file.
        multi_tabled: Pages whose `tables/` file holds more than one table.
        categories: Block `category_type` -> number of annotations.
        span_categories: Span `category_type` -> number of spans.

    Returns:
        The README body.

    Raises:
        ExportError: `config/scoring.yml` is missing, or lacks a key the
            normalisation sentence needs.
    """
    # Lazy for the import-boundary reason at the top of this module: this
    # submodule's package `__init__` eagerly imports the render engine, and
    # `docparse-degrade` imports `manifest_record` from here without Faker.
    from generators.layout_dsl.categories import BLOCK_CATEGORIES, SPAN_CATEGORIES

    total = sum(counts.values())
    rows = "\n".join(f"| {doc_type} | {count} |" for doc_type, count in sorted(counts.items()))
    normalisation = _normalisation_sentence(_load_scoring_policy())
    annotations = sum(categories.values())
    category_rows = "\n".join(f"| `{name}` | {count} |" for name, count in sorted(categories.items()))
    span_rows = "\n".join(f"| `{name}` | {count} |" for name, count in sorted(span_categories.items()))
    # Stated rather than left to be discovered: a consumer who filters on
    # `ignore` should know whether it ever fires here before concluding their
    # filter works.
    ignore_note = (
        f"`true` marks real ink that no metric should score — {categories.get('abandon', 0)} block(s) here."
        if "abandon" in categories
        else "`true` marks real ink that no metric should score. Always `false` in this corpus."
    )
    coverage = (
        f"That is {len(categories)} of OmniDocBench's {len(BLOCK_CATEGORIES)} block categories "
        f"and {len(span_categories)} of its {len(SPAN_CATEGORIES)} span categories."
    )
    # Dropped entirely rather than printed as "(0 of N)": the paragraph exists
    # to warn a scorer about a file shape, and this corpus has none.
    multi_table_note = (
        f"""
A page with more than one table ({multi_tabled} of {total}) writes every table
into that one `tables/{{stem}}.html` file, one root `<table>` after another in
page order. `layout/{{stem}}.json` is authoritative per table in that case —
each `table` annotation there carries its own `html`.
"""
        if multi_tabled
        else ""
    )
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
| `layout/` | OmniDocBench-shaped `layout_dets` per page: boxes, categories, reading order. |
| `tables/` | Table HTML for TEDS, one file per page with a table ({tabled} of {total}; absent otherwise). |
| `manifest.jsonl` | One row per case: image, transcript, doc_type, sha256, layout, tables. |
| `prompt.md` | The prompt these transcripts assume. |
| `serialisation.yml` | The exact policy that produced these transcripts. |
{multi_table_note}
## The layout annotations

`layout/{{stem}}.json` is a single object, `{{"layout_dets": [...]}}`, holding one
annotation per block on that page ({annotations} across the corpus). The
field names are OmniDocBench's, verbatim, so if you already work in that
vocabulary you need no translation table.

| Field | What it holds |
| --- | --- |
| `category_type` | The element class — see the table below. |
| `poly` | 8 numbers: TL, TR, BR, BL as `(x, y)` pairs. |
| `anno_id` | The block's unique id. **Not an array index** — see below. |
| `order` | Reading order: dense, `0`-based, gap-free. |
| `text` | The block's text, **pre-wrap**. |
| `html` | Table structure. Present on `table` blocks only. |
| `ignore` | {ignore_note} |
| `line_with_spans` | The drawn lines within the block, each with its own `poly` and text. |
| `attribute` | `doc_type`, `layout_id`, `tier`. |

Five things worth knowing before you consume these.

**`anno_id` is sparse; `order` is not.** `anno_id` is the block's capture
sequence number, and the walk that produced it also numbers containers, column
markers and spacers, none of which draw annotatable ink. Gaps are therefore
normal, and a page's highest `anno_id` is routinely several times its block
count. Join on `anno_id`, but index and sort on `order`, which counts only
annotations and runs `0..n-1`. The array ships already sorted by `order`.

**`text` is pre-wrap; `poly` is post-wrap.** Line wrapping is an artifact of font
size and column width, not of content, so a block's `text` is the unwrapped
string — byte-identical to its line in the Markdown transcript. Its `poly` is
where the ink actually landed. A two-line wrapped address is therefore one
`text_block` carrying the whole string, with two `text_span`s inside
`line_with_spans` carrying a line each. Both readings are true and neither is
lossy; pick the one your metric needs.

**Boxes hug ink, not layout regions.** A block's `poly` is the union of its
spans' boxes, measured from the glyphs drawn, so a right-aligned amount in a wide
column yields a narrow box rather than a column-width one. A parser that predicts
region-shaped blocks will score lower IoU against these for a difference that is
not a reading error. That trade is deliberate — an ink-shaped box is verifiable
against the page, a region-shaped one only against the layout that produced it —
but it is worth knowing before you read an IoU number as a layout failure.

**`table` blocks carry `html`, and their `text` is empty.** A table's content
lives in `html` for TEDS; its `text` is `""` and its `line_with_spans` is empty.
There are no cell boxes: OmniDocBench has no `table_cell` category and scores
tables by TEDS rather than by cell geometry, so cell boxes would be data no
metric here consumes. Cell structure is preserved where it is scored — in the
HTML.

**Coordinates are pixels in that page's own image, origin top-left.** Page size
is not constant across the corpus — receipts are cropped to their content height
— so normalise against the image you were handed, never against a fixed page
size.

### Categories present

| `category_type` | Annotations |
| --- | --- |
{category_rows}

| Span `category_type` | Spans |
| --- | --- |
{span_rows}

{coverage}

This corpus is three Australian business document types: it holds no figures, no
formulas, no captions, no headers or footers, no page numbers, no code and no
references. Those categories are **absent, not empty** — read their absence as a
property of the corpus, not as a gap in the annotation.

**There is no composite score here.** OmniDocBench's headline number is
`((1 − text edit distance) × 100 + TEDS + CDM) / 3`. CDM is a formula metric and
this corpus has no formulas, so that average is not computable on it. Report the
per-task numbers instead: a three-way mean missing one of its terms is a
different statistic wearing the same name, and publishing it invites exactly the
cross-benchmark comparison it cannot support.

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
   ({normalisation}), then compute normalised edit distance and
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

    Beside each image and transcript, this also writes the page's OmniDocBench
    annotations (`layout/{stem}.json`, always) and its tables' HTML
    (`tables/{stem}.html`, only when the page has one — absence is expressed
    by omission, not by an empty file).

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
        ExportError: An image, transcript, the policy or the prompt is missing,
            or a non-empty event stream yields zero layout annotations (a
            record carried over from before layout capture).
        LayoutError: A page's annotations cannot be trusted — a degenerate
            box, an incomplete reading order, or an unbalanced table stream.
    """
    from generators.layout import layout_dets  # see the import-boundary note above

    for label, path, command in (
        ("serialisation policy", policy_path, "serialise"),
        ("prompt", prompt_path, "serialise"),
    ):
        if not path.exists():
            raise _missing(label, path, recover_command=command)

    policy = load_serialisation_policy(policy_path)

    root = target / f"parsing_{date_stamp}"
    (root / "images").mkdir(parents=True, exist_ok=True)
    (root / "transcripts").mkdir(parents=True, exist_ok=True)
    (root / "layout").mkdir(parents=True, exist_ok=True)

    manifest: list[dict] = []
    counts: dict[str, int] = {}
    tabled = 0
    multi_tabled = 0
    # Counted from the annotations actually written, so the README's category
    # tables describe this export rather than a remembered corpus shape.
    categories: dict[str, int] = {}
    span_categories: dict[str, int] = {}
    for record in records:
        doc_type = record["doc_type"]
        image_name = record["image_file"]
        source_image = _find_image(images_root, image_name, doc_type)
        source_transcript = transcripts_dir / (Path(image_name).stem + ".md")

        row = manifest_record(source_image, source_transcript, doc_type)
        shutil.copy2(source_image, root / "images" / image_name)
        shutil.copy2(source_transcript, root / "transcripts" / source_transcript.name)

        stem = Path(image_name).stem
        # "tier" is hardcoded here: `degrade` copies these artifacts verbatim
        # into every tier corpus, exactly as it copies transcripts, because a
        # degraded page says the same thing and its elements sit in the same
        # places. Rewriting it per tier is a follow-up, not this task.
        attribute = {
            "doc_type": doc_type,
            "layout_id": str(record.get("layout_id", "")),
            "tier": "clean",
        }
        annotations = layout_dets(record["events"], attribute=attribute, policy=policy)
        if record["events"] and not annotations["layout_dets"]:
            # `_merge_event_records` (pipeline.py) deliberately carries a
            # record over from an earlier `generate` run when its image is
            # still on disk. A record captured before this branch has events
            # but no `category_type` on any of them, so `layout_dets` returns
            # `{"layout_dets": []}` — a page that would ship an empty layout
            # file beside a populated table file, with the manifest
            # advertising both. Every real page in this corpus produces at
            # least 7 annotations, so a non-empty stream with zero is
            # unambiguous.
            raise ExportError(
                "Cannot export: a record's event stream yields zero layout annotations.\n"
                f"  What:     {stem} ({doc_type}) carries {len(record['events'])} event(s) but "
                "0 layout_dets.\n"
                f"  Where:    {transcripts_dir.parent / 'events.jsonl'} -> case_id="
                f"{record.get('case_id')!r}, doc_type={doc_type!r}\n"
                "  Expected: a non-empty event stream to carry a category_type on at least one "
                'event, e.g.\n              {"kind": "title", "category_type": "title", ...}\n'
                "  Recover:  re-run `python -m generators.pipeline generate` for this case — "
                "this record predates layout capture, so its events carry no category_type and "
                "export cannot derive annotations for it."
            )
        (root / "layout" / f"{stem}.json").write_text(
            json.dumps(annotations, indent=2) + "\n", encoding="utf-8"
        )
        row["layout"] = f"layout/{stem}.json"

        for det in annotations["layout_dets"]:
            categories[det["category_type"]] = categories.get(det["category_type"], 0) + 1
            for span in det.get("line_with_spans", ()):
                kind = span["category_type"]
                span_categories[kind] = span_categories.get(kind, 0) + 1

        tables = table_html(record["events"], policy)
        if tables:
            (root / "tables").mkdir(parents=True, exist_ok=True)
            (root / "tables" / f"{stem}.html").write_text("\n".join(tables) + "\n", encoding="utf-8")
            row["tables"] = f"tables/{stem}.html"
            tabled += 1
            multi_tabled += len(tables) > 1

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
    (root / "README.md").write_text(
        readme_text(
            date_stamp,
            counts,
            tabled=tabled,
            multi_tabled=multi_tabled,
            categories=categories,
            span_categories=span_categories,
        ),
        encoding="utf-8",
    )
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
