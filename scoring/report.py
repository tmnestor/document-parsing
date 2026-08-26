"""Aggregate scored rows into a comparison.

Median first, and the mean reported beside it rather than instead of it. CER is
`edits / reference length` and is unbounded above, so one runaway page can
dominate a mean; equally a mean can bury two catastrophic failures behind a
hundred good pages. Reporting the median, the percentiles, the mean and an
explicit degenerate count keeps both failure modes visible.

Absent predictions are counted separately and excluded from the distribution: a
page the model never answered is not a score of anything, but it must not
disappear either.
"""

import csv
import io
import json
from pathlib import Path
from typing import Annotated

import typer
from rich import print as rprint

from scoring.errors import ScoringError, diagnostic
from scoring.policy import load_scoring_policy

app = typer.Typer(add_completion=False, help="Aggregate scored rows into a comparison.")

_METRICS = ("normalised_cer", "strict_cer", "normalised_wer", "table_cell_error_rate")
_GROUP_KEYS = ("model", "corpus", "doc_type", "family", "severity")


def _percentile(values: list[float], p: int) -> float:
    """Return the p-th percentile by nearest-rank, on a sorted copy.

    Nearest-rank rather than interpolation: with as few as five pages in a
    group, an interpolated "worst case" is a value no page actually scored.

    Args:
        values: The sample; must be non-empty.
        p: Percentile in 1..100.

    Returns:
        The selected value.
    """
    ordered = sorted(values)
    rank = max(1, -(-p * len(ordered) // 100))
    return ordered[rank - 1]


def aggregate(rows: list[dict], group_by: tuple[str, ...], policy: dict) -> list[dict]:
    """Group rows and summarise each group.

    Args:
        rows: Rows emitted by `scoring.score`.
        group_by: Row keys to group on, e.g. `("model", "doc_type")`.
        policy: The validated policy, read for `reporting.percentiles`.

    Returns:
        One summary per group, ordered by the group key tuple.
    """
    percentiles = [int(p) for p in policy["reporting"]["percentiles"]]
    buckets: dict[tuple, list[dict]] = {}
    for row in rows:
        buckets.setdefault(tuple(row[key] for key in group_by), []).append(row)

    groups = []
    for key, bucket in sorted(buckets.items(), key=lambda item: tuple(str(v) for v in item[0])):
        scored = [r for r in bucket if r["prediction_present"]]
        group: dict = dict(zip(group_by, key, strict=True))
        group["pages"] = len(bucket)
        group["missing"] = len(bucket) - len(scored)
        group["degenerate"] = sum(1 for r in bucket if r["degenerate"])
        group["verified"] = all(r["verified"] for r in bucket)
        for metric in _METRICS:
            values = [float(r[metric]) for r in scored if r[metric] is not None]
            for p in percentiles:
                group[f"{metric}_p{p}"] = _percentile(values, p) if values else None
            group[f"{metric}_mean"] = sum(values) / len(values) if values else None
        groups.append(group)
    return groups


def _columns(group_by: tuple[str, ...], groups: list[dict]) -> list[str]:
    """Return the column order: group keys first, then every summary field."""
    if not groups:
        return list(group_by)
    return list(group_by) + [k for k in groups[0] if k not in group_by]


def _cell(value: object) -> str:
    """Format one cell: floats to four places, None as an em dash."""
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def render_markdown(groups: list[dict], group_by: tuple[str, ...]) -> str:
    """Render groups as a Markdown pipe table.

    Args:
        groups: Summaries from `aggregate`.
        group_by: The keys grouped on, rendered as the leading columns.

    Returns:
        The table, header rule included.
    """
    columns = _columns(group_by, groups)
    lines = ["| " + " | ".join(columns) + " |", "| " + " | ".join("---" for _ in columns) + " |"]
    lines += ["| " + " | ".join(_cell(g.get(c)) for c in columns) + " |" for g in groups]
    return "\n".join(lines) + "\n"


def render_csv(groups: list[dict], group_by: tuple[str, ...]) -> str:
    """Render groups as CSV.

    Args:
        groups: Summaries from `aggregate`.
        group_by: The keys grouped on, rendered as the leading columns.

    Returns:
        CSV text with a header row.
    """
    columns = _columns(group_by, groups)
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for group in groups:
        writer.writerow({c: group.get(c) for c in columns})
    return buffer.getvalue()


@app.command()
def report(
    rows_paths: Annotated[list[Path], typer.Option("--rows", help="Row files; repeatable.")],
    group_by: Annotated[
        str, typer.Option("--group-by", help="Comma-separated row keys.")
    ] = "model,doc_type,family,severity",
    output_format: Annotated[str, typer.Option("--format", help="markdown or csv")] = "markdown",
    policy_path: Annotated[Path, typer.Option("--policy", help="Path to scoring.yml")] = Path(
        "config/scoring.yml"
    ),
    out: Annotated[Path | None, typer.Option("--out", help="Write here instead of stdout.")] = None,
) -> None:
    """Aggregate one or more row files into a comparison table.

    Raises:
        typer.Exit: With code 1 on an unknown group key or format.
    """
    try:
        policy = load_scoring_policy(policy_path)
        keys = tuple(k.strip() for k in group_by.split(",") if k.strip())
        unknown = [k for k in keys if k not in _GROUP_KEYS]
        if unknown:
            raise diagnostic(
                f"--group-by names {unknown}, which are not row keys.",
                path=Path.cwd(),
                key="--group-by",
                expected=f"a comma-separated subset of {list(_GROUP_KEYS)}, e.g.\n"
                "              --group-by model,doc_type,family,severity",
                recover="use only those keys.",
            )
        if output_format not in ("markdown", "csv"):
            raise diagnostic(
                f"--format is {output_format!r}.",
                path=Path.cwd(),
                key="--format",
                expected="markdown or csv, e.g.\n              --format markdown",
                recover="pass one of those two values.",
            )

        rows: list[dict] = []
        for path in rows_paths:
            if not path.exists():
                raise diagnostic(
                    f"{path} does not exist.",
                    path=path.resolve(),
                    key="--rows",
                    expected="a JSONL file written by `python -m scoring.score --out`.",
                    recover="run scoring.score first, or correct the path.",
                )
            rows += [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    except ScoringError as exc:
        rprint(f"[red]{exc}[/red]")
        raise typer.Exit(1) from None

    groups = aggregate(rows, keys, policy)
    text = render_markdown(groups, keys) if output_format == "markdown" else render_csv(groups, keys)

    if out is not None:
        out.write_text(text, encoding="utf-8")
        rprint(f"[green]Wrote {len(groups)} group(s) to {out}.[/green]")
    else:
        print(text)

    if any(not g["verified"] for g in groups):
        rprint("[yellow]Some groups were scored without hash verification.[/yellow]")


if __name__ == "__main__":
    app()
