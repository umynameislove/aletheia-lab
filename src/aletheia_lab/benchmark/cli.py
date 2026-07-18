"""Typer sub-app for P1 benchmark case generation and validation."""

from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.console import Console

from aletheia_lab.baseline.loader import DatasetSchemaError
from aletheia_lab.benchmark.case_validation import validate_p1_cases
from aletheia_lab.benchmark.generator import GeneratorConfigError, generate_p1
from aletheia_lab.diagnosis.adapters import DeterministicMockAdapter
from aletheia_lab.diagnosis.g6b import (
    build_openai_preflight,
    load_openai_pilot_config,
    write_openai_preflight,
)
from aletheia_lab.diagnosis.pilot import run_p1_matched_pilot, validate_p1_matched_pilot
from aletheia_lab.evidence.collectors import EvidenceCollectionError
from aletheia_lab.evidence.p1 import (
    generate_p1_evidence_store,
    validate_p1_evidence_store,
)

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


@benchmark_app.command("generate-p1-evidence")
def generate_p1_evidence_cmd(
    cases_dir: Path = typer.Option(Path("experiments/p1/cases"), "--cases-dir"),
    output_dir: Path = typer.Option(Path("experiments/p1/evidence-store"), "--output-dir"),
) -> None:
    """Collect, audit and immutably persist all 15 P1 EvidenceBundles."""

    try:
        manifest = generate_p1_evidence_store(cases_dir, output_dir)
    except (EvidenceCollectionError, FileExistsError, OSError, ValueError) as exc:
        console.print(f"[red]FAIL[/red] {exc}")
        raise typer.Exit(code=1) from exc
    report = validate_p1_evidence_store(output_dir, cases_dir)
    console.print_json(json.dumps(report.as_dict()))
    if not report.passed:
        console.print("[red]Evidence validation FAILED[/red]")
        raise typer.Exit(code=1)
    console.print(
        "[green]Generated and verified "
        f"{manifest.bundle_count} bundles; machine leakage PASS.[/green] "
        "Human review remains pending until an attested review record is supplied."
    )


@benchmark_app.command("validate-p1-evidence")
def validate_p1_evidence_cmd(
    store_dir: Path = typer.Option(Path("experiments/p1/evidence-store"), "--store-dir"),
    cases_dir: Path = typer.Option(Path("experiments/p1/cases"), "--cases-dir"),
    human_review: Path | None = typer.Option(None, "--human-review"),
) -> None:
    """Recompute store/audit integrity and optionally verify human sign-off."""

    report = validate_p1_evidence_store(store_dir, cases_dir, human_review_path=human_review)
    console.print_json(json.dumps(report.as_dict()))
    if not report.passed:
        console.print("[red]Evidence validation FAILED[/red]")
        raise typer.Exit(code=1)
    console.print(
        "[green]Evidence technical validation PASS[/green]; "
        f"human review status: {report.human_review_status}."
    )


@benchmark_app.command("run-p1-pilot-mock")
def run_p1_pilot_mock_cmd(
    store_dir: Path = typer.Option(Path("experiments/p1/evidence-store"), "--store-dir"),
    output_dir: Path = typer.Option(
        Path("experiments/p1/outputs/matched-pilot-mock"), "--output-dir"
    ),
) -> None:
    """Run the 15-context x 2-variant G6 contract pilot without an external send."""

    try:
        manifest = run_p1_matched_pilot(
            store_dir,
            output_dir,
            adapter=DeterministicMockAdapter(),
        )
    except (FileExistsError, OSError, ValueError) as exc:
        console.print(f"[red]FAIL[/red] {exc}")
        raise typer.Exit(code=1) from exc
    console.print_json(json.dumps(manifest.model_dump(mode="json")))
    console.print(
        "[green]Matched pilot contract PASS[/green]: "
        f"{manifest.run_count} runs, {manifest.success_count} parsed, "
        f"{manifest.unresolved_count} unresolved. "
        "Mock outputs are infrastructure evidence, not model-quality results."
    )


@benchmark_app.command("validate-p1-pilot")
def validate_p1_pilot_cmd(
    output_dir: Path = typer.Option(
        Path("experiments/p1/outputs/matched-pilot-mock"), "--output-dir"
    ),
    store_dir: Path = typer.Option(Path("experiments/p1/evidence-store"), "--store-dir"),
) -> None:
    """Recompute source binding, matchedness and all P1 pilot artifact hashes."""

    try:
        manifest = validate_p1_matched_pilot(output_dir, store_dir)
    except (FileNotFoundError, OSError, ValueError) as exc:
        console.print(f"[red]FAIL[/red] {exc}")
        raise typer.Exit(code=1) from exc
    console.print_json(json.dumps(manifest.model_dump(mode="json")))
    console.print("[green]P1 matched pilot validation PASS[/green]")


@benchmark_app.command("preflight-p1-openai")
def preflight_p1_openai_cmd(
    store_dir: Path = typer.Option(Path("experiments/p1/evidence-store"), "--store-dir"),
    config: Path = typer.Option(
        Path("configs/evaluation/p1_g6b_openai.yaml"), "--config"
    ),
    output: Path = typer.Option(
        Path("experiments/p1/outputs/g6b-openai-preflight.json"), "--output"
    ),
) -> None:
    """Build and persist the complete G6B request plan without an external send."""

    try:
        frozen_config = load_openai_pilot_config(config)
        report = build_openai_preflight(store_dir, frozen_config)
        write_openai_preflight(report, output)
    except (FileExistsError, FileNotFoundError, OSError, ValueError) as exc:
        console.print(f"[red]FAIL[/red] {exc}")
        raise typer.Exit(code=1) from exc
    console.print_json(json.dumps(report.model_dump(mode="json")))
    console.print(
        "[green]G6B OpenAI preflight PASS[/green]: "
        f"{report.matched_pair_count} matched pairs / {report.request_count} requests; "
        f"eight-request smoke plan frozen; estimated maximum cost "
        f"${report.estimated_max_cost_usd:.4f}. No external request was sent."
    )
