"""Turn an exported clean corpus into one degraded corpus per tier.

Runs in `docparse`, the one environment. It imports numpy, opencv and
augraphy; the first two are pinned in `environment.yml`, and `build_corpus.sh`
installs augraphy itself, with `--no-deps`, and verifies the result — see
`generators/degradation/augment.py` for why a plain install is unsafe.

**It consumes an exported corpus and produces exported corpora.** The output of
each tier is a complete `parsing_*/` directory — images, transcripts, layout
annotations, table HTML, manifest, prompt, serialisation policy, README — so
`score` and both parser runners work on it with no change and no new flags. The
alternative, degrading `output/` and teaching every downstream tool about
tiers, would have put the same knowledge in four places.

**Transcripts, layout annotations and table HTML are copied byte for byte.**
Degradation changes how legible a page is, never what it says or where its
elements sit, so the ground truth is identical and any score difference is
attributable to image quality alone. The manifest is rebuilt because it hashes
images, and those have changed — which is what stops a degraded prediction
being scored against clean truth by accident.

Usage:
    python -m generators.degradation.cli --corpus parsing_20260820 \\
        --family scan --type bank_statements
"""

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import typer
from PIL import Image
from rich import print as rprint
from rich.progress import Progress

from generators.degradation import degrade_page, load_tiers, page_seed
from generators.degradation.matrix import matrix_row, write_matrix
from generators.degradation.tiers import load_corpus_selection
from generators.export import manifest_record
from generators.loader import load_generation_config

app = typer.Typer(add_completion=False)

# Copied into every degraded corpus beside the images they describe. Anything a
# scorer needs to interpret the corpus must travel with it, exactly as `export`
# ships the prompt and the policy with the clean one.
_CARRIED = ("prompt.md", "serialisation.yml", "README.md")


def _fail(what: str, *, where: str, expected: str, recover: str) -> None:
    """Print a four-element diagnostic and exit."""
    rprint(f"[red]Cannot degrade the corpus.[/red]\n  What:     {what}")
    rprint(f"  Where:    {where}\n  Expected: {expected}\n  Recover:  {recover}")
    raise typer.Exit(1)


@dataclass(frozen=True)
class RunPlan:
    """What a degrade invocation will actually do.

    Attributes:
        families: Intake families to render.
        doc_types: Document types to include.
        out: Directory the tier corpora are written into.
    """

    families: tuple[str, ...]
    doc_types: tuple[str, ...]
    out: Path


def resolve_run(
    config_path: Path,
    generation_config_path: Path,
    *,
    family: list[str] | None,
    doc_type: list[str] | None,
    out: Path,
) -> RunPlan:
    """Fold flags over configuration to decide what this run covers.

    Extracted from the command body so it can be tested in `docparse`, where
    augraphy is absent and the degrade loop itself cannot run.

    Args:
        config_path: Path to `degradation.yml`.
        generation_config_path: Path to `generation_config.yml`, which resolves
            where generated data lives.
        family: `--family` values, or None to use the configured families.
        doc_type: `--type` values, or None to use the configured types.
        out: `--out`. Required — the destination of a deliverable has one
            owner, and it is the caller.

    Returns:
        The resolved plan.
    """
    selection = load_corpus_selection(config_path)
    # Loaded for its validation, not for a path: a run that is about to write a
    # corpus should fail on a malformed generation config here rather than
    # midway through the degrade loop.
    load_generation_config(generation_config_path)
    return RunPlan(
        families=tuple(family) if family else selection.families,
        doc_types=tuple(doc_type) if doc_type else selection.document_types,
        out=out,
    )


@app.command()
def degrade(
    corpus: Annotated[Path, typer.Option("--corpus", help="An exported clean corpus.")],
    # Required, like `export --target`, and for the same reason: the tiers are a
    # deliverable, so the caller names where they land. Config still decides
    # *what* is degraded -- families, types, tiers -- just not where it goes.
    out: Annotated[
        Path,
        typer.Option(
            "--out",
            help="Where the degraded corpora are written. Required — there is no configured default.",
        ),
    ],
    config: Annotated[Path, typer.Option("--config", help="Tier declarations.")] = Path(
        "config/degradation.yml"
    ),
    generation_config: Annotated[
        Path, typer.Option("--generation-config", help="Path to generation_config.yml")
    ] = Path("config/generation_config.yml"),
    family: Annotated[
        list[str] | None,
        typer.Option("--family", help="Intake families to render; repeatable. Default: all."),
    ] = None,
    doc_type: Annotated[
        list[str] | None,
        typer.Option("--type", help="Restrict to these document types; repeatable."),
    ] = None,
    limit: Annotated[int | None, typer.Option("--limit", help="First N pages, for a smoke test.")] = None,
) -> None:
    """Write one complete degraded corpus per declared tier."""
    manifest_path = corpus / "manifest.jsonl"
    if not manifest_path.exists():
        _fail(
            f"{corpus}/manifest.jsonl does not exist, so this is not an exported corpus.",
            where=str(corpus.resolve()),
            expected="a directory written by `export`, holding images/, transcripts/ and "
            "manifest.jsonl, e.g.\n              parsing_20260820",
            recover="run `python -m generators.pipeline export` first, or point --corpus at "
            "an existing export.",
        )

    all_records = [json.loads(line) for line in manifest_path.read_text().splitlines() if line]
    missing_layout = [r for r in all_records if "layout" not in r]
    if missing_layout:
        # Reading `record["layout"]` unguarded below (mirroring the `if
        # "tables" in record` guard that already exists) would crash mid-loop
        # with a bare KeyError, after the first degraded JPEG had already been
        # written — leaving a partial tier directory. Failing here, before any
        # image is written, catches every corpus exported before layout
        # capture in one place rather than per record.
        _fail(
            f"{len(missing_layout)} of {len(all_records)} row(s) in {corpus.name}/manifest.jsonl "
            "have no 'layout' key.",
            where=str(manifest_path.resolve()),
            expected="every manifest row carrying a 'layout' path, written by `export` on this "
            'branch, e.g.\n              {"image": "images/CASE001_invoices.png", '
            '"layout": "layout/CASE001_invoices.json", ...}',
            recover=f"re-export {corpus.name} with `python -m generators.pipeline export` — "
            "this corpus predates layout capture, so `degrade` cannot carry through "
            "annotations it does not have.",
        )

    plan = resolve_run(config, generation_config, family=family, doc_type=doc_type, out=out)
    tiers = load_tiers(config, families=list(plan.families))
    records = [r for r in all_records if r["doc_type"] in plan.doc_types]
    if limit:
        records = records[:limit]
    if not records:
        _fail(
            "no pages selected.",
            where=str((corpus / "manifest.jsonl").resolve()),
            expected="at least one manifest row matching the filters, with --type naming "
            "one of the corpus's document types.",
            recover="drop --type, or name a document type the manifest contains.",
        )

    rprint(
        f"[bold]{corpus.name}[/bold]: {len(records)} page(s) x {len(tiers)} tier(s) "
        f"= {len(records) * len(tiers)} degraded page(s)"
    )

    plan.out.mkdir(parents=True, exist_ok=True)
    for tier in tiers:
        target = plan.out / f"{corpus.name}_{tier.family}-{tier.name}"
        (target / "images").mkdir(parents=True, exist_ok=True)
        (target / "transcripts").mkdir(parents=True, exist_ok=True)
        (target / "layout").mkdir(parents=True, exist_ok=True)

        manifest: list[dict] = []
        with Progress(transient=True) as progress:
            task = progress.add_task(f"{tier.label}", total=len(records))
            for record in records:
                stem = Path(record["image"]).stem

                # JPEG, not PNG: every intake channel this models delivers a
                # compressed file, and the tier already declares the quality.
                # Writing PNG would hand the model a cleaner image than
                # production ever produces.
                image_name = f"{stem}.jpg"
                degraded = degrade_page(
                    Image.open(corpus / record["image"]),
                    tier,
                    page_seed(stem, tier),
                )
                degraded.save(target / "images" / image_name, quality=95, subsampling=0)

                # Byte for byte: the page says the same thing however badly it
                # was scanned, and its elements sit in the same places.
                transcript_name = Path(record["transcript"]).name
                shutil.copyfile(corpus / record["transcript"], target / "transcripts" / transcript_name)

                row = manifest_record(
                    target / "images" / image_name,
                    target / "transcripts" / transcript_name,
                    record["doc_type"],
                    family=tier.family,
                    severity=tier.name,
                )

                layout_name = Path(record["layout"]).name
                shutil.copyfile(corpus / record["layout"], target / "layout" / layout_name)
                row["layout"] = f"layout/{layout_name}"

                if "tables" in record:
                    (target / "tables").mkdir(parents=True, exist_ok=True)
                    tables_name = Path(record["tables"]).name
                    shutil.copyfile(corpus / record["tables"], target / "tables" / tables_name)
                    row["tables"] = f"tables/{tables_name}"

                manifest.append(row)
                progress.advance(task)

        (target / "manifest.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in manifest), encoding="utf-8"
        )
        for name in _CARRIED:
            source = corpus / name
            if source.exists():
                shutil.copyfile(source, target / name)
        (target / "DEGRADATION.md").write_text(_note(corpus, tier, len(manifest)), encoding="utf-8")

        rprint(f"  [green]{tier.label:16}[/green] {len(manifest):4d} page(s) -> {target}")

    # The clean corpus is a row like any other: without that baseline a
    # comparison cannot separate a weak model from one the degradation hurt.
    rows = [matrix_row(corpus, family="clean", severity="none")]
    rows.extend(
        matrix_row(
            plan.out / f"{corpus.name}_{tier.family}-{tier.name}", family=tier.family, severity=tier.name
        )
        for tier in tiers
    )
    matrix_path = write_matrix(rows, plan.out)
    rprint(f"[green]Matrix written: {matrix_path} ({len(rows)} corpora)[/green]")


def _note(corpus: Path, tier, pages: int) -> str:
    """Describe, inside the corpus, what was done to it and what was not."""
    return f"""# Degraded corpus: {tier.family}-{tier.name}

{pages} page(s), derived from `{corpus.name}` by
`python -m generators.degradation.cli`.

{tier.description.strip()}

## What changed

The **images** only. Every transcript is copied byte for byte from the clean
corpus, because degradation changes how legible a page is and never what it
says. Any difference in score against the clean corpus is therefore
attributable to image quality alone.

Images are JPEG rather than PNG. Every intake channel modelled here delivers a
compressed file, and the tier declares the quality; shipping PNG would hand a
system a cleaner image than production produces.

## Vintage

`manifest.jsonl` has been rebuilt, so its image hashes differ from the clean
corpus. That is deliberate: `score` refuses to score predictions against a
corpus whose hashes do not match, which makes scoring a degraded prediction
against clean ground truth impossible rather than merely discouraged.

## Reproducing it

Deterministic. Each page's seed is derived from its filename stem and this
tier's name, so re-running writes byte-identical images. The declaration lives
in `config/degradation.yml` in the generating repository.
"""


if __name__ == "__main__":
    app()
