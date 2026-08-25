"""Corpus pipeline: validate configuration, render pages.

Usage:
    python -m generators.pipeline validate
    python -m generators.pipeline generate --type invoices --limit 3
    python -m generators.pipeline serialise
    python -m generators.pipeline preview CASE001
    python -m generators.pipeline export

The commands are ordered: `generate` renders and captures, `serialise` turns
captured events into transcripts, `export` packages what those two produced.
Each reads only the previous one's output, so a convention change re-emits every
transcript without re-rendering an image (design §6).

The predecessor's `derive` and `eval-set` commands do not cross — both project
extraction ground truth, which belongs to that repo.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Annotated

import typer
from rich import print as rprint

from generators.bank_statement import render_bank_statement
from generators.common import FitError
from generators.content_engine import load_pools, reachable_blocked_names
from generators.export import ExportError, export_corpus
from generators.invoice import render_invoice
from generators.layout_dsl.schema import LayoutSchemaError, validate_layout
from generators.loader import load_generation_config, load_ground_truth, load_layout_registry
from generators.overflow_check import build_overflow_error, check_overflow
from generators.receipt import render_receipt
from generators.schema import field_names_for, layout_field_names_for, validate_entry
from generators.serialise import load_serialisation_policy
from generators.serialise import serialise as serialise_events

app = typer.Typer(add_completion=False, help="Synthetic document parsing corpus pipeline.")

_RENDERERS = {
    "bank_statements": render_bank_statement,
    "receipts": render_receipt,
    "invoices": render_invoice,
}

_DEFAULT_CONFIG = Path("config/generation_config.yml")
_DEFAULT_POLICY = Path("config/serialisation.yml")
_DEFAULT_PROMPT = Path("config/prompt.md")
_DEFAULT_SCORING = Path("config/scoring.yml")


def _validate_layouts(layouts: dict, *, doc_type: str, layout_path: str) -> list[str]:
    """Structurally validate every DSL body in a layout registry.

    Args:
        layouts: Layout registry (layout id -> layout dict).
        doc_type: Document type, used to resolve the legal field names.
        layout_path: Path to the layout YAML, used in diagnostics.

    Returns:
        One diagnostic string per invalid layout; empty when all are well formed.
    """
    known = set(field_names_for(doc_type))
    errors: list[str] = []
    for layout_id, layout in layouts.items():
        if "body" not in layout:
            continue
        # A layout may draw a block no other layout has, whose data is authored
        # per case under `layout_fields` rather than required of every entry of
        # the type. Those names are legal references for that layout only.
        allowed = known | set(layout_field_names_for(layout_id))
        try:
            validate_layout(layout, layout_id=layout_id, layout_path=layout_path, known_fields=allowed)
        except LayoutSchemaError as exc:
            errors.append(str(exc))
    return errors


@app.command()
def validate(
    config: Annotated[Path, typer.Option(help="Path to generation_config.yml")] = _DEFAULT_CONFIG,
) -> None:
    """Check every ground-truth entry and layout before anything is rendered.

    Covers required fields per document type, ABN checksums, date and amount
    formats, equal item counts across parallel pipe-delimited fields, GST as one
    eleventh of a GST-inclusive total, that each `layout:` names a layout the
    registry actually holds, that the DSL bodies are well formed, that no text
    overflows its budget, and that the business-name grammar cannot emit a real
    company.

    Errors are collected rather than raised one at a time, so a single run
    reports every problem in the corpus.

    Raises:
        typer.Exit: With code 1 when any check fails.
    """
    cfg = load_generation_config(config)
    all_errors: list[str] = []

    for doc_type, doc_cfg in cfg["document_types"].items():
        gt_path = Path(doc_cfg["ground_truth"])
        if not gt_path.exists():
            all_errors.append(f"{doc_type}: ground truth not found at {gt_path}")
            continue

        gt_data = load_ground_truth(gt_path)
        layout_path = Path(doc_cfg["layouts"])
        layouts = load_layout_registry(layout_path) if layout_path.exists() else {}

        for case_id, entry in gt_data.items():
            all_errors.extend(validate_entry(str(case_id), entry))
            layout_ref = entry.get("layout", "")
            if layouts and layout_ref not in layouts:
                all_errors.append(
                    f"{case_id}: layout '{layout_ref}' not found in {layout_path}. "
                    f"Available layouts: {sorted(layouts)}"
                )

        # Every layout body is structurally validated before any rendering, so a
        # malformed primitive or unknown field reference fails here rather than
        # part-way through a several-hundred-image generate run.
        layout_errors = (
            _validate_layouts(layouts, doc_type=doc_type, layout_path=str(layout_path)) if layouts else []
        )
        all_errors.extend(layout_errors)

        # Overflow backstop: render each entry and surface any content that cannot
        # fit its box even after lossless wrap/shrink (a real design error).
        # Skipped when layout validation already failed — rendering a known-broken
        # body raises an unrelated exception instead of a useful fit diagnostic.
        renderer = _RENDERERS.get(doc_type)
        if renderer and layouts and not layout_errors:
            all_errors.extend(check_overflow(gt_data, layouts, renderer))

    # A pool edit must not make a real business name emittable. The runtime
    # blocklist in fictional_business_name() would still catch it, but only as a
    # retry during seeding; checked here it is a configuration error raised at
    # the moment someone widens business_name_parts.
    for name in reachable_blocked_names(load_pools()):
        all_errors.append(
            f"the business-name grammar can now emit '{name}', a real business on the "
            f"blocklist. A prefix in business_name_parts.surnames or .suburb_prefixes "
            f"combined with a noun in .category_nouns produces it exactly. Rename or "
            f"remove the offending part in config/data_pools.yml so no real name is "
            f"reachable."
        )

    if all_errors:
        rprint(f"[red]Validation failed with {len(all_errors)} error(s):[/red]")
        for err in all_errors:
            rprint(f"  [red]- {err}[/red]")
        raise typer.Exit(1) from None

    rprint("[green]Validation passed.[/green]")


@app.command()
def generate(
    config: Annotated[Path, typer.Option(help="Path to generation_config.yml")] = _DEFAULT_CONFIG,
    doc_type: Annotated[str | None, typer.Option("--type", help="Render only this document type.")] = None,
    limit: Annotated[
        int | None, typer.Option("--limit", help="Render at most N documents per type.")
    ] = None,
    output: Annotated[
        Path | None, typer.Option("--output", help="Override the configured output directory.")
    ] = None,
    derived: Annotated[
        Path | None, typer.Option("--derived", help="Override the configured derived directory.")
    ] = None,
) -> None:
    """Render page images from ground truth, capturing a transcript per page.

    Filenames are `{case_id}_{doc_type}.png`, never `{case_id}_{layout_id}.png`
    (design §6.1): a model must not be able to infer the layout template from
    the filename before it has read a pixel.

    The §8.2 coverage invariant runs here, not only under pytest: if a primitive
    puts text on the page without emitting an event, this command fails and
    names it, rather than writing a quietly incomplete transcript.

    Raises:
        typer.Exit: With code 1 on an unknown `--type` or an invalid layout.
        CoverageError: A primitive drew text without emitting an event.
    """
    cfg = load_generation_config(config)
    output_dir = output if output is not None else Path(cfg["output_dir"])
    records: list[dict] = []

    # Kept unfiltered: a partial run still has to resolve where *every* type's
    # images live, to tell a carried-over event record from a stale one.
    all_doc_types = cfg["document_types"]
    doc_types = all_doc_types
    if doc_type:
        if doc_type not in doc_types:
            rprint(f"[red]Unknown document type '{doc_type}'. Available: {sorted(doc_types)}[/red]")
            raise typer.Exit(1) from None
        doc_types = {doc_type: doc_types[doc_type]}

    for dtype, doc_cfg in doc_types.items():
        renderer = _RENDERERS[dtype]
        gt_data = load_ground_truth(Path(doc_cfg["ground_truth"]))
        layout_path = Path(doc_cfg["layouts"])
        layouts = load_layout_registry(layout_path)

        layout_errors = _validate_layouts(layouts, doc_type=dtype, layout_path=str(layout_path))
        if layout_errors:
            rprint(f"[red]{dtype}: layout validation failed.[/red]")
            for err in layout_errors:
                rprint(f"[red]{err}[/red]")
            raise typer.Exit(1) from None

        target = output_dir / doc_cfg["output_subdir"] if output is None else output_dir
        target.mkdir(parents=True, exist_ok=True)

        count = 0
        for case_id, entry in gt_data.items():
            if limit is not None and count >= limit:
                break
            layout_ref = entry.get("layout", "")
            layout = layouts.get(layout_ref)
            if not layout:
                rprint(f"[yellow]Skipping {case_id}: layout '{layout_ref}' not found.[/yellow]")
                continue

            entry["case_id"] = str(case_id)
            try:
                image, recorder = renderer(entry, layout)
            except FitError as exc:
                raise build_overflow_error(
                    [f"{case_id} / {layout_ref}: {str(exc).splitlines()[0]}"]
                ) from None

            image_file = f"{case_id}_{dtype}.png"
            image.save(target / image_file)
            records.append(
                {
                    "case_id": str(case_id),
                    "doc_type": dtype,
                    "image_file": image_file,
                    "events": [event.as_dict() for event in recorder.events],
                }
            )
            count += 1

        rprint(f"[green]{dtype}: generated {count} documents into {target}.[/green]")

    derived_dir = derived if derived is not None else Path(cfg["derived_dir"])
    derived_dir.mkdir(parents=True, exist_ok=True)
    events_path = derived_dir / "events.jsonl"

    image_dir_for = {
        dtype: output_dir if output is not None else output_dir / doc_cfg["output_subdir"]
        for dtype, doc_cfg in all_doc_types.items()
    }
    merged, dropped = _merge_event_records(events_path, records, image_dir_for=image_dir_for)
    with events_path.open("w", encoding="utf-8") as handle:
        for record in merged:
            handle.write(json.dumps(record) + "\n")

    for image_file in dropped:
        rprint(f"[yellow]Dropped stale events for {image_file}: no such image on disk.[/yellow]")
    carried = len(merged) - len(records)
    if carried:
        rprint(
            f"[green]Events written: {events_path} ({len(merged)} documents: "
            f"{len(records)} from this run, {carried} carried over from earlier runs)[/green]"
        )
    else:
        rprint(f"[green]Events written: {events_path} ({len(merged)} documents)[/green]")


def _merge_event_records(
    events_path: Path,
    fresh: list[dict],
    *,
    image_dir_for: dict[str, Path],
) -> tuple[list[dict], list[str]]:
    """Fold this run's records into whatever `events.jsonl` already holds.

    `events.jsonl` must mirror what is on disk in the output directory, and a
    partial run — `--type`, `--limit`, or both — only ever rewrites part of
    that directory. Truncating the file to this run's records therefore threw
    away the events for pages still sitting on disk: a `generate --type
    bank_statements` followed by `serialise` wrote 55 fresh transcripts beside
    110 stale ones, with nothing on either side recording that the corpus was
    now a mixture. Images already survive a partial run — they live in per-type
    subdirectories and only the regenerated ones are overwritten — so merging
    brings the event stream in line with how the images have always behaved,
    rather than inventing a new rule.

    A record is keyed by `(case_id, doc_type)`, the same granularity a partial
    run rewrites at. Fresh records replace matching ones in place, which keeps
    file order stable across runs; unmatched fresh records append. Carried-over
    records whose image has since left the disk are dropped rather than
    preserved, because a record with no page is precisely the divergence this
    file exists to prevent.

    Args:
        events_path: The `events.jsonl` this run is about to write.
        fresh: The records this run captured, in render order.
        image_dir_for: Directory holding each doc type's images, keyed by type
            — the per-type output subdirectory, or the flat override directory.

    Returns:
        The merged records to write, and the `image_file` of every carried-over
        record dropped for having no page on disk.
    """
    if not events_path.exists():
        return fresh, []

    existing = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines() if line]
    by_key = {(r["case_id"], r["doc_type"]): r for r in fresh}

    dropped: list[str] = []
    merged: list[dict] = []
    replaced: set[tuple[str, str]] = set()
    for record in existing:
        key = (record["case_id"], record["doc_type"])
        replacement = by_key.get(key)
        if replacement is not None:
            merged.append(replacement)
            replaced.add(key)
            continue
        directory = image_dir_for.get(record["doc_type"])
        if directory is None or not (directory / record["image_file"]).exists():
            dropped.append(record["image_file"])
            continue
        merged.append(record)

    merged.extend(r for r in fresh if (r["case_id"], r["doc_type"]) not in replaced)
    return merged, dropped


def _load_event_records(derived_dir: Path) -> list[dict]:
    """Read `events.jsonl` from a derived directory.

    Args:
        derived_dir: The directory `generate` wrote its events into.

    Returns:
        One record per rendered document.

    Raises:
        typer.Exit: With code 1 when the file is absent.
    """
    events_path = derived_dir / "events.jsonl"
    if not events_path.exists():
        rprint("[red]No captured events found.[/red]")
        rprint(f"[red]  What:     {events_path} does not exist.[/red]")
        rprint(f"[red]  Where:    {events_path.resolve()}[/red]")
        rprint("[red]  Expected: the event stream `generate` writes as it renders.[/red]")
        rprint("[red]  Recover:  run `python -m generators.pipeline generate` first.[/red]")
        raise typer.Exit(1) from None
    return [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines() if line]


@app.command()
def serialise(
    config: Annotated[Path, typer.Option(help="Path to generation_config.yml")] = _DEFAULT_CONFIG,
    policy: Annotated[Path, typer.Option("--policy", help="Path to serialisation.yml")] = _DEFAULT_POLICY,
    derived: Annotated[
        Path | None, typer.Option("--derived", help="Override the configured derived directory.")
    ] = None,
) -> None:
    """Turn captured events into Markdown transcripts.

    A pure function of events and policy: it renders nothing and imports no
    renderer. That is why it is a separate command (design §6) — the
    convention is the risky part of this design, so it can change and every
    transcript re-emit in seconds without re-rendering a single image.

    Raises:
        typer.Exit: With code 1 when no events have been captured.
    """
    cfg = load_generation_config(config)
    derived_dir = derived if derived is not None else Path(cfg["derived_dir"])
    records = _load_event_records(derived_dir)
    convention = load_serialisation_policy(policy)

    transcripts_dir = derived_dir / "transcripts"
    transcripts_dir.mkdir(parents=True, exist_ok=True)
    for record in records:
        target = transcripts_dir / (Path(record["image_file"]).stem + ".md")
        target.write_text(serialise_events(record["events"], convention), encoding="utf-8")

    rprint(f"[green]Serialised {len(records)} transcripts into {transcripts_dir}.[/green]")


@app.command()
def preview(
    case_id: Annotated[str, typer.Argument(help="The case to preview, e.g. CASE001")],
    config: Annotated[Path, typer.Option(help="Path to generation_config.yml")] = _DEFAULT_CONFIG,
    policy: Annotated[Path, typer.Option("--policy", help="Path to serialisation.yml")] = _DEFAULT_POLICY,
    derived: Annotated[
        Path | None, typer.Option("--derived", help="Override the configured derived directory.")
    ] = None,
) -> None:
    """Print one document's transcript beside its image path.

    Exists so the design §8.5 visual check has something to check against: a
    transcription corpus's correctness is ultimately visual, and no automated
    check catches a transcript that parses cleanly but describes the wrong page.

    Raises:
        typer.Exit: With code 1 when the case has no captured events.
    """
    cfg = load_generation_config(config)
    derived_dir = derived if derived is not None else Path(cfg["derived_dir"])
    records = _load_event_records(derived_dir)
    convention = load_serialisation_policy(policy)

    matches = [record for record in records if record["case_id"] == case_id]
    if not matches:
        rprint(f"[red]No captured events for case '{case_id}'.[/red]")
        rprint(f"[red]  Known cases: {sorted({r['case_id'] for r in records})[:8]} ...[/red]")
        raise typer.Exit(1) from None

    for record in matches:
        subdir = cfg["document_types"][record["doc_type"]]["output_subdir"]
        image_path = Path(cfg["output_dir"]) / subdir / record["image_file"]
        rprint(f"[bold]{record['case_id']}[/bold]  ({record['doc_type']})")
        rprint(f"[cyan]image:[/cyan] {image_path}")
        rprint("[cyan]transcript:[/cyan]")
        print(serialise_events(record["events"], convention))


@app.command()
def export(
    config: Annotated[Path, typer.Option(help="Path to generation_config.yml")] = _DEFAULT_CONFIG,
    policy: Annotated[Path, typer.Option("--policy", help="Path to serialisation.yml")] = _DEFAULT_POLICY,
    prompt: Annotated[Path, typer.Option("--prompt", help="Path to prompt.md")] = _DEFAULT_PROMPT,
    derived: Annotated[
        Path | None, typer.Option("--derived", help="Override the configured derived directory.")
    ] = None,
    output: Annotated[
        Path | None, typer.Option("--output", help="Override the configured output directory.")
    ] = None,
    target: Annotated[
        Path, typer.Option("--target", help="Directory to create the export inside.")
    ] = Path(),
    date: Annotated[
        str | None, typer.Option("--date", help="Corpus date stamp, YYYYMMDD. Defaults to today.")
    ] = None,
) -> None:
    """Assemble the dated deliverable directory (design §6.1).

    Copies images and transcripts verbatim — it never re-renders or
    re-serialises — and adds the three artifacts that make the corpus
    interpretable away from this checkout: a hashed manifest, a copy of the
    policy that produced the transcripts, and the prompt they assume.

    Raises:
        typer.Exit: With code 1 when a needed artifact is missing.
    """
    cfg = load_generation_config(config)
    derived_dir = derived if derived is not None else Path(cfg["derived_dir"])
    output_dir = output if output is not None else Path(cfg["output_dir"])
    records = _load_event_records(derived_dir)
    date_stamp = date if date is not None else datetime.now().strftime("%Y%m%d")

    try:
        root = export_corpus(
            records,
            images_root=output_dir,
            transcripts_dir=derived_dir / "transcripts",
            policy_path=policy,
            prompt_path=prompt,
            target=target,
            date_stamp=date_stamp,
        )
    except ExportError as exc:
        rprint(f"[red]{exc}[/red]")
        raise typer.Exit(1) from None

    rprint(f"[green]Exported {len(records)} documents into {root}.[/green]")
    rprint("[cyan]Verify every image against its sha256 in manifest.jsonl before scoring.[/cyan]")


if __name__ == "__main__":
    app()
