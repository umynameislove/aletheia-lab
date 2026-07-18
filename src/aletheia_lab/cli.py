"""Command line interface for Aletheia Lab."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from aletheia_lab.baseline.cli import baseline_app
from aletheia_lab.benchmark.cli import benchmark_app
from aletheia_lab.benchmark.manifest import load_case
from aletheia_lab.config import load_yaml
from aletheia_lab.evaluation.metrics import binary_score
from aletheia_lab.evidence.leakage import find_forbidden_terms

app = typer.Typer(help="Aletheia Lab research/evaluation toolkit.")
console = Console()

app.add_typer(baseline_app, name="baseline")
app.add_typer(benchmark_app, name="benchmark")


@app.command()
def info(config: Path = Path("configs/project.yaml")) -> None:
    """Print the active benchmark configuration."""

    data = load_yaml(config)
    project = data.get("project", {})
    dataset = data.get("dataset", {})
    benchmark = data.get("benchmark", {})

    table = Table(title="Aletheia Lab Configuration")
    table.add_column("Field")
    table.add_column("Value")
    table.add_row("Project", str(project.get("name", "unknown")))
    table.add_row("Version", str(project.get("version", "unknown")))
    table.add_row("Dataset", str(dataset.get("id", "unknown")))
    table.add_row("Fault type", str(benchmark.get("fault_type", "unknown")))
    table.add_row("Benchmark cases", str(benchmark.get("target_cases", "unknown")))
    console.print(table)


@app.command("validate-case")
def validate_case(path: Path) -> None:
    """Validate one benchmark case JSON file."""

    case = load_case(path)
    console.print(f"[green]OK[/green] {case.case_id} ({case.fault_type})")


@app.command("leakage-check")
def leakage_check(text: str, forbidden: list[str] = typer.Option([])) -> None:
    """Quickly scan text for forbidden leakage terms."""

    matches = find_forbidden_terms(text, forbidden)
    if matches:
        console.print(f"[red]Potential leakage:[/red] {', '.join(matches)}")
        raise typer.Exit(code=1)
    console.print("[green]No forbidden terms found.[/green]")


@app.command("score-example")
def score_example(predicted: str, expected: str) -> None:
    """Print a simple exact-match binary score."""

    score = binary_score(predicted.strip().lower() == expected.strip().lower())
    console.print({"score": score})
