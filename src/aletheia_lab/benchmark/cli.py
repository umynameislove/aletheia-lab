"""Typer sub-app for P1 benchmark case generation and validation."""

from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.console import Console

from aletheia_lab.baseline.loader import DatasetSchemaError
from aletheia_lab.benchmark.case_validation import validate_p1_cases
from aletheia_lab.benchmark.generator import GeneratorConfigError, generate_p1

benchmark_app = typer.Typer(help="Generate and validate P1 benchmark cases.")
console = Console()


@benchmark_app.command("generate-p1")
def generate_p1_cmd(
    config: Path = typer.Option(Path("configs/project.yaml"), "--config"),
    output_dir: Path = typer.Option(Path("experiments/p1/cases"), "--output-dir"),
) -> None:
    """Generate the 15 P1 data-drift cases (5 settings x 3 evidence conditions)."""

    try:
        summary = generate_p1(config, output_dir)
    except (GeneratorConfigError, DatasetSchemaError) as exc:
        console.print(f"[red]FAIL[/red] {exc}")
        raise typer.Exit(code=1) from exc
    report = validate_p1_cases(output_dir)
    summary["validation_passed"] = report.passed
    console.print_json(json.dumps(summary))
    if not (report.passed and summary["leakage_total"] == 0):
        console.print(f"[red]Validation FAILED[/red] {report.as_dict()}")
        raise typer.Exit(code=1)
    console.print("[green]Generated 15 cases, validation PASS, leakage 0[/green]")


@benchmark_app.command("validate-p1")
def validate_p1_cmd(
    cases_dir: Path = typer.Option(Path("experiments/p1/cases"), "--cases-dir"),
) -> None:
    """Validate a generated P1 case directory."""

    report = validate_p1_cases(cases_dir)
    console.print_json(json.dumps(report.as_dict()))
    if not report.passed:
        console.print("[red]Validation FAILED[/red]")
        raise typer.Exit(code=1)
    console.print("[green]Validation PASS[/green]")
