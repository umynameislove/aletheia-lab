"""Typer sub-app for the baseline: train, evaluate, verify."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from aletheia_lab.baseline.loader import DatasetSchemaError
from aletheia_lab.baseline.run import resolve_settings, train, verify

baseline_app = typer.Typer(help="Seeded dataset loader and deterministic baseline model.")
console = Console()


def _metrics_table(metrics: dict[str, Any]) -> Table:
    table = Table(title="Baseline metrics by split")
    table.add_column("Split")
    for col in ("n", "accuracy", "balanced_accuracy", "precision", "recall", "f1", "roc_auc"):
        table.add_column(col)
    for split_name, m in metrics["splits"].items():
        roc = "n/a" if m["roc_auc"] is None else f"{m['roc_auc']:.4f}"
        table.add_row(
            split_name,
            str(m["n"]),
            f"{m['accuracy']:.4f}",
            f"{m['balanced_accuracy']:.4f}",
            f"{m['precision']:.4f}",
            f"{m['recall']:.4f}",
            f"{m['f1']:.4f}",
            roc,
        )
    return table


@baseline_app.command("train")
def train_cmd(
    config: Path = typer.Option(Path("configs/project.yaml"), "--config"),
    output_dir: Path | None = typer.Option(None, "--output-dir"),
) -> None:
    """Train the baseline and write artifacts."""

    settings = resolve_settings(config)
    try:
        result = train(settings, output_dir)
    except DatasetSchemaError as exc:
        console.print(f"[red]FAIL[/red] {exc}")
        raise typer.Exit(code=1) from exc
    console.print(f"[green]Trained[/green] -> {result['run_dir']}")
    console.print(_metrics_table(result["metrics"]))


@baseline_app.command("evaluate")
def evaluate(
    run_dir: Path = typer.Option(..., "--run-dir"),
) -> None:
    """Print metrics from an existing run directory."""

    metrics_path = run_dir / "metrics.json"
    if not metrics_path.exists():
        console.print(f"[red]FAIL[/red] no metrics.json in {run_dir}")
        raise typer.Exit(code=1)
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    console.print(_metrics_table(metrics))


@baseline_app.command("verify")
def verify_cmd(
    config: Path = typer.Option(Path("configs/project.yaml"), "--config"),
    output_dir: Path | None = typer.Option(None, "--output-dir"),
) -> None:
    """Run training twice with the same seed and check reproducibility."""

    settings = resolve_settings(config)
    try:
        report = verify(settings, output_dir)
    except DatasetSchemaError as exc:
        console.print(f"[red]FAIL[/red] {exc}")
        raise typer.Exit(code=1) from exc
    console.print_json(json.dumps(report.as_dict()))
    if not report.passed:
        console.print("[red]Reproducibility FAILED[/red]")
        raise typer.Exit(code=1)
    console.print("[green]Reproducibility PASS[/green]")
