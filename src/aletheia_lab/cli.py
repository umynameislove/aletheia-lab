"""Command line interface for Aletheia Lab."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from aletheia_lab.baseline.cli import baseline_app
from aletheia_lab.benchmark.manifest import load_case
from aletheia_lab.config import load_yaml
from aletheia_lab.evaluation.metrics import binary_score
from aletheia_lab.evidence.leakage import find_forbidden_terms

app = typer.Typer(help="Aletheia Lab research/evaluation toolkit.")
console = Console()

app.add_typer(baseline_app, name="baseline")


@app.command()
def plan(config: Path = Path("configs/project.yaml")) -> None:
    """Print the active project plan summary."""

    data = load_yaml(config)
    project = data.get("project", {})
    vertical_slice = data.get("vertical_slice", {})
    scope = data.get("scope", {})

    table = Table(title="Aletheia Lab Plan")
    table.add_column("Field")
    table.add_column("Value")
    table.add_row("Project", str(project.get("name", "unknown")))
    table.add_row("Mode", str(project.get("mode", "unknown")))
    table.add_row("P1 fault type", str(vertical_slice.get("fault_type", "unknown")))
    table.add_row("P1 cases", str(vertical_slice.get("target_cases", "unknown")))
    table.add_row("Official case goal", str(scope.get("official_case_goal", "unknown")))
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
