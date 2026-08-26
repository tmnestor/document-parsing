"""Score predictions against a corpus, one JSONL row per page.

Scoring and aggregation are separate commands for the same reason `generate` and
`serialise` are: capture once, interpret many times. Re-slicing a comparison by
document type, tier or model then costs seconds instead of re-reading a thousand
pages.
"""

import json
import re
from pathlib import Path
from typing import Annotated

import typer
from rich import print as rprint

from scoring.corpus import Corpus, load_corpus, verify_images
from scoring.errors import ScoringError, diagnostic
from scoring.metrics import character_error_rate, edit_distance, is_degenerate, word_error_rate
from scoring.normalise import normalise
from scoring.policy import load_scoring_policy
from scoring.predictions import PredictionSet, load_predictions
from scoring.tables import score_tables

app = typer.Typer(add_completion=False, help="Score model predictions against an exported corpus.")

_DEFAULT_POLICY = Path("config/scoring.yml")

# Tiers are named `{corpus}_{family}-{severity}` (spec §5.2, generators/degradation/cli.py).
# A directory whose name doesn't carry that suffix is the clean, undegraded export.
_TIER_SUFFIX = re.compile(r"^(?P<corpus>.+)_(?P<family>[^-_]+)-(?P<severity>[^-_]+)$")


def derive_family_severity(corpus_dir: Path) -> tuple[str, str]:
    """Recover a corpus's family and severity from its directory name.

    `--corpus` scores one directory at a time with no matrix row to read
    `family`/`severity` from, so scoring a tier this way used to hardcode
    `clean`/`none` for every corpus, tier or not (#N1). Tier directories
    follow one stated naming convention; this parses it back.

    Args:
        corpus_dir: The corpus directory passed to `--corpus`.

    Returns:
        `(family, severity)` parsed from the `_{family}-{severity}` suffix, or
        `("clean", "none")` when the name carries no such suffix.
    """
    match = _TIER_SUFFIX.match(corpus_dir.name)
    if match is None:
        return "clean", "none"
    return match.group("family"), match.group("severity")


def score_page(reference: str, prediction: str | None, policy: dict, *, verified: bool) -> dict:
    """Score one prediction against one reference.

    Args:
        reference: The corpus transcript.
        prediction: The model's output, or None when the file was absent.
        policy: The whole validated policy mapping.
        verified: Whether the corpus hashes were checked for this run.

    Returns:
        The metric fields of a row. Every distance is None when the prediction
        is absent, so an absence is carried in the data rather than dropped.
    """
    if prediction is None:
        return {
            "ref_chars": len(reference),
            "pred_chars": None,
            "prediction_present": False,
            "verified": verified,
            "strict_edit_distance": None,
            "strict_cer": None,
            "normalised_edit_distance": None,
            "normalised_cer": None,
            "normalised_wer": None,
            "degenerate": False,
            "table_cell_error_rate": None,
            "table_cells_compared": None,
            "table_cells_correct": None,
            "table_cells_misplaced": None,
            "table_rows_missing": None,
            "table_rows_spurious": None,
            "table_count_ref": None,
            "table_count_pred": None,
        }

    rules = policy["normalisation"]
    ref_norm = normalise(reference, rules)
    pred_norm = normalise(prediction, rules)
    return {
        "ref_chars": len(reference),
        "pred_chars": len(prediction),
        "prediction_present": True,
        "verified": verified,
        "strict_edit_distance": edit_distance(reference, prediction),
        "strict_cer": character_error_rate(reference, prediction),
        "normalised_edit_distance": edit_distance(ref_norm, pred_norm),
        "normalised_cer": character_error_rate(ref_norm, pred_norm),
        "normalised_wer": word_error_rate(ref_norm, pred_norm),
        "degenerate": is_degenerate(
            reference, prediction, float(policy["reporting"]["degenerate_length_multiple"])
        ),
        **score_tables(reference, prediction, policy),
    }


def score_corpus(
    corpus: Corpus,
    predictions: PredictionSet,
    policy: dict,
    *,
    family: str,
    severity: str,
    verified: bool,
) -> list[dict]:
    """Score every page of one corpus for one model.

    Args:
        corpus: The loaded corpus.
        predictions: The verified prediction set.
        policy: The whole validated policy mapping.
        family: Intake family from the matrix row.
        severity: Tier severity from the matrix row.
        verified: Whether image hashes were checked.

    Returns:
        One row per page, in manifest order.
    """
    rows = []
    for page in corpus.pages:
        reference = page.transcript.read_text(encoding="utf-8")
        row = {
            "model": predictions.run.model_id,
            "corpus": corpus.root.name,
            "case_id": page.case_id,
            "doc_type": page.doc_type,
            "family": family,
            "severity": severity,
        }
        row.update(score_page(reference, predictions.texts[page.stem], policy, verified=verified))
        rows.append(row)
    return rows


def _matrix_rows(matrix_path: Path) -> list[dict]:
    """Read matrix.jsonl, failing fast when it is absent.

    Args:
        matrix_path: Path to `matrix.jsonl`.

    Returns:
        The matrix rows.

    Raises:
        ScoringError: The file does not exist.
    """
    if not matrix_path.exists():
        raise diagnostic(
            f"{matrix_path.name} does not exist.",
            path=matrix_path.resolve(),
            key="matrix.jsonl",
            expected="the index written by `degrade`, listing the clean corpus and its tiers.",
            recover="run `python -m generators.degradation.cli --corpus <clean export>` "
            "to build it, or pass --corpus to score a single corpus.",
        )
    return [json.loads(line) for line in matrix_path.read_text(encoding="utf-8").splitlines() if line]


@app.command()
def score(
    matrix: Annotated[
        Path | None, typer.Option("--matrix", help="matrix.jsonl; scores every corpus it lists.")
    ] = None,
    corpus_dir: Annotated[
        Path | None, typer.Option("--corpus", help="A single exported corpus to score.")
    ] = None,
    predictions_root: Annotated[
        Path | None,
        typer.Option("--predictions-root", help="Holds <model>/<corpus>/ directories."),
    ] = None,
    predictions: Annotated[
        Path | None, typer.Option("--predictions", help="One model's predictions for --corpus.")
    ] = None,
    family: Annotated[
        str | None,
        typer.Option(
            "--family",
            help="Intake family for --corpus, overriding the name derived from its directory.",
        ),
    ] = None,
    severity: Annotated[
        str | None,
        typer.Option(
            "--severity",
            help="Tier severity for --corpus, overriding the name derived from its directory.",
        ),
    ] = None,
    out: Annotated[Path, typer.Option("--out", help="Where rows are written.")] = Path("rows.jsonl"),
    policy_path: Annotated[Path, typer.Option("--policy", help="Path to scoring.yml")] = _DEFAULT_POLICY,
    allow_missing: Annotated[
        bool, typer.Option("--allow-missing", help="Record absent predictions instead of failing.")
    ] = False,
    skip_verify: Annotated[
        bool, typer.Option("--skip-verify", help="Skip image hash verification (records it).")
    ] = False,
) -> None:
    """Score one corpus, or every corpus in a matrix.

    Raises:
        typer.Exit: With code 1 on any verification or configuration failure.
    """
    try:
        policy = load_scoring_policy(policy_path)
        rows: list[dict] = []

        if matrix is not None:
            if predictions_root is None:
                raise diagnostic(
                    "--matrix was given without --predictions-root.",
                    path=matrix.resolve(),
                    key="--predictions-root",
                    expected="a directory holding <model>/<corpus>/ prediction directories.",
                    recover="pass --predictions-root, or score one corpus with --corpus.",
                )
            if not predictions_root.is_dir():
                raise diagnostic(
                    f"{predictions_root} does not exist, or is not a directory.",
                    path=predictions_root.resolve(),
                    key="--predictions-root",
                    expected="a directory holding <model>/<corpus>/ prediction directories.",
                    recover="check the path, or run the model(s) first.",
                )

            matrix_entries = _matrix_rows(matrix)
            models_found: set[str] = set()
            for entry in matrix_entries:
                loaded = load_corpus(matrix.parent / entry["corpus"])
                if loaded.manifest_sha256 != entry["manifest_sha256"]:
                    raise diagnostic(
                        f"{entry['corpus']} has been re-exported since {matrix.name} was "
                        "built — its manifest hash no longer matches the matrix row.",
                        path=(matrix.parent / entry["corpus"]).resolve(),
                        key="manifest_sha256",
                        expected=f"manifest_sha256 == {entry['manifest_sha256'][:12]}… "
                        "(the vintage the matrix was built from).",
                        recover="rebuild the matrix with `degrade`, or re-score against "
                        "the corpus vintage the matrix describes.",
                    )
                if not skip_verify:
                    verify_images(loaded)
                for model_dir in sorted(p for p in predictions_root.iterdir() if p.is_dir()):
                    models_found.add(model_dir.name)
                    set_dir = model_dir / entry["corpus"]
                    if not set_dir.is_dir():
                        continue
                    loaded_predictions = load_predictions(set_dir, loaded, allow_missing=allow_missing)
                    rows.extend(
                        score_corpus(
                            loaded,
                            loaded_predictions,
                            policy,
                            family=str(entry["family"]),
                            severity=str(entry["severity"]),
                            verified=not skip_verify,
                        )
                    )

            if not rows:
                wanted_corpora = sorted({str(e["corpus"]) for e in matrix_entries})
                raise diagnostic(
                    f"{predictions_root} matched no <model>/<corpus>/ pair from {matrix.name}, "
                    "so there is nothing to write.",
                    path=predictions_root.resolve(),
                    key="--predictions-root",
                    expected=f"a <model>/<corpus>/ directory for each corpus in {wanted_corpora}, "
                    f"under one of the model directories found: {sorted(models_found) or '(none)'}.",
                    recover="check --predictions-root and the model/corpus directory names "
                    "against matrix.jsonl.",
                )
        else:
            if corpus_dir is None or predictions is None:
                raise diagnostic(
                    "neither --matrix nor --corpus/--predictions were given.",
                    path=Path.cwd(),
                    key="--matrix | --corpus",
                    expected="either --matrix with --predictions-root, or --corpus with --predictions.",
                    recover="pass one of those pairs.",
                )
            loaded = load_corpus(corpus_dir)
            if not skip_verify:
                verify_images(loaded)
            loaded_predictions = load_predictions(predictions, loaded, allow_missing=allow_missing)
            derived_family, derived_severity = derive_family_severity(corpus_dir)
            rows = score_corpus(
                loaded,
                loaded_predictions,
                policy,
                family=family if family is not None else derived_family,
                severity=severity if severity is not None else derived_severity,
                verified=not skip_verify,
            )
    except ScoringError as exc:
        rprint(f"[red]{exc}[/red]")
        raise typer.Exit(1) from None

    out.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    missing = sum(1 for row in rows if not row["prediction_present"])
    rprint(f"[green]Scored {len(rows)} page(s) into {out}.[/green]")
    if missing:
        rprint(f"[yellow]{missing} page(s) had no prediction and were recorded as absent.[/yellow]")
    if skip_verify:
        rprint("[yellow]Image hashes were NOT verified; rows record verified=false.[/yellow]")


if __name__ == "__main__":
    app()
