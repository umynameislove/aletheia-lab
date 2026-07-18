"""Typer sub-app for P1 benchmark case generation and validation."""

from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.console import Console

from aletheia_lab.baseline.loader import DatasetSchemaError
from aletheia_lab.benchmark.case_validation import validate_p1_cases
from aletheia_lab.benchmark.generator import GeneratorConfigError, generate_p1
from aletheia_lab.diagnosis.adapters import (
    AdapterError,
    DeterministicMockAdapter,
    OpenAIChatCompletionsAdapter,
)
from aletheia_lab.diagnosis.external_pilot import (
    authorize_openai_full,
    authorize_openai_smoke,
    run_openai_full,
    run_openai_smoke,
    validate_openai_full,
    validate_openai_smoke,
)
from aletheia_lab.diagnosis.openai_preflight import (
    build_openai_preflight,
    load_openai_pilot_config,
    load_openai_preflight,
    openai_preflight_sha256,
    write_openai_preflight,
)
from aletheia_lab.diagnosis.pilot import run_p1_matched_pilot, validate_p1_matched_pilot
from aletheia_lab.evaluation.pilot import evaluate_matched_pilot, write_evaluation_report
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
        Path("configs/evaluation/openai_pilot.yaml"), "--config"
    ),
    output: Path = typer.Option(
        Path("experiments/p1/outputs/openai-preflight.json"), "--output"
    ),
) -> None:
    """Build and persist the complete OpenAI request set without an external send."""

    try:
        frozen_config = load_openai_pilot_config(config)
        report = build_openai_preflight(store_dir, frozen_config)
        write_openai_preflight(report, output)
    except (FileExistsError, FileNotFoundError, OSError, ValueError) as exc:
        console.print(f"[red]FAIL[/red] {exc}")
        raise typer.Exit(code=1) from exc
    console.print_json(json.dumps(report.model_dump(mode="json")))
    if report.cost_estimates is None:  # pragma: no cover - newly built reports always include it
        raise typer.Exit(code=1)
    costs = report.cost_estimates
    console.print(
        "[green]OpenAI preflight PASS[/green]: "
        f"{report.matched_pair_count} matched pairs / {report.request_count} requests; "
        "eight-request smoke plan frozen. Estimated costs: "
        f"smoke one attempt ${costs.smoke_one_attempt.estimated_cost_usd:.4f}; "
        f"smoke retry ceiling ${costs.smoke_retry_ceiling.estimated_cost_usd:.4f}; "
        f"full one attempt ${costs.full_one_attempt.estimated_cost_usd:.4f}; "
        f"full retry ceiling ${costs.full_retry_ceiling.estimated_cost_usd:.4f}. "
        "No external request was sent. "
        f"Confirmation SHA-256: {openai_preflight_sha256(report)}"
    )


@benchmark_app.command("run-p1-openai-smoke")
def run_p1_openai_smoke_cmd(
    store_dir: Path = typer.Option(Path("experiments/p1/evidence-store"), "--store-dir"),
    config: Path = typer.Option(Path("configs/evaluation/openai_pilot.yaml"), "--config"),
    preflight: Path = typer.Option(
        Path("experiments/p1/outputs/openai-preflight.json"), "--preflight"
    ),
    output_dir: Path = typer.Option(
        Path("experiments/p1/outputs/openai-smoke"), "--output-dir"
    ),
    confirm_preflight_sha256: str = typer.Option(..., "--confirm-preflight-sha256"),
) -> None:
    """Run exactly eight externally billed requests after exact SHA confirmation."""

    try:
        frozen_config = load_openai_pilot_config(config)
        persisted = load_openai_preflight(preflight)
        # Complete all no-network authorization checks before even reading the API key.
        authorize_openai_smoke(
            store_dir,
            frozen_config,
            persisted,
            confirm_preflight_sha256,
        )
        adapter = OpenAIChatCompletionsAdapter.from_environment()
        manifest = run_openai_smoke(
            store_dir,
            frozen_config,
            preflight,
            output_dir,
            confirmed_preflight_sha256=confirm_preflight_sha256,
            adapter=adapter,
        )
    except (AdapterError, FileExistsError, FileNotFoundError, OSError, ValueError) as exc:
        console.print(f"[red]FAIL[/red] {exc}")
        raise typer.Exit(code=1) from exc
    console.print_json(json.dumps(manifest.model_dump(mode="json")))
    console.print(
        "[green]OpenAI smoke execution recorded[/green]: "
        f"{manifest.run_count} requests, {manifest.success_count} parsed, "
        f"{manifest.unresolved_count} unresolved."
    )


@benchmark_app.command("validate-p1-openai-smoke")
def validate_p1_openai_smoke_cmd(
    output_dir: Path = typer.Option(
        Path("experiments/p1/outputs/openai-smoke"), "--output-dir"
    ),
    store_dir: Path = typer.Option(Path("experiments/p1/evidence-store"), "--store-dir"),
    config: Path = typer.Option(Path("configs/evaluation/openai_pilot.yaml"), "--config"),
    preflight: Path = typer.Option(
        Path("experiments/p1/outputs/openai-preflight.json"), "--preflight"
    ),
) -> None:
    """Verify external smoke authorization, source binding and immutable artifacts."""

    try:
        manifest = validate_openai_smoke(
            output_dir, store_dir, load_openai_pilot_config(config), preflight
        )
    except (FileNotFoundError, OSError, ValueError) as exc:
        console.print(f"[red]FAIL[/red] {exc}")
        raise typer.Exit(code=1) from exc
    console.print_json(json.dumps(manifest.model_dump(mode="json")))
    console.print("[green]OpenAI smoke validation PASS[/green]")


@benchmark_app.command("run-p1-openai-full")
def run_p1_openai_full_cmd(
    store_dir: Path = typer.Option(Path("experiments/p1/evidence-store"), "--store-dir"),
    config: Path = typer.Option(Path("configs/evaluation/openai_pilot.yaml"), "--config"),
    preflight: Path = typer.Option(
        Path("experiments/p1/outputs/openai-preflight.json"), "--preflight"
    ),
    output_dir: Path = typer.Option(
        Path("experiments/p1/outputs/openai-full"), "--output-dir"
    ),
    confirm_preflight_sha256: str = typer.Option(..., "--confirm-preflight-sha256"),
    confirm_estimated_full_retry_ceiling_usd: float = typer.Option(
        ..., "--confirm-estimated-full-retry-ceiling-usd"
    ),
) -> None:
    """Run all 30 billed requests after exact plan and cost confirmation."""

    try:
        frozen_config = load_openai_pilot_config(config)
        persisted = load_openai_preflight(preflight)
        # Finish every no-network gate before the API key is read.
        authorize_openai_full(
            store_dir,
            frozen_config,
            persisted,
            confirm_preflight_sha256,
            confirm_estimated_full_retry_ceiling_usd,
        )
        adapter = OpenAIChatCompletionsAdapter.from_environment()
        manifest = run_openai_full(
            store_dir,
            frozen_config,
            preflight,
            output_dir,
            confirmed_preflight_sha256=confirm_preflight_sha256,
            confirmed_estimated_full_retry_ceiling_usd=(
                confirm_estimated_full_retry_ceiling_usd
            ),
            adapter=adapter,
        )
    except (AdapterError, FileExistsError, FileNotFoundError, OSError, ValueError) as exc:
        console.print(f"[red]FAIL[/red] {exc}")
        raise typer.Exit(code=1) from exc
    console.print_json(json.dumps(manifest.model_dump(mode="json")))
    console.print(
        "[green]OpenAI full execution recorded[/green]: "
        f"{manifest.run_count} requests, {manifest.success_count} parsed, "
        f"{manifest.unresolved_count} unresolved."
    )


@benchmark_app.command("validate-p1-openai-full")
def validate_p1_openai_full_cmd(
    output_dir: Path = typer.Option(
        Path("experiments/p1/outputs/openai-full"), "--output-dir"
    ),
    store_dir: Path = typer.Option(Path("experiments/p1/evidence-store"), "--store-dir"),
    config: Path = typer.Option(Path("configs/evaluation/openai_pilot.yaml"), "--config"),
    preflight: Path = typer.Option(
        Path("experiments/p1/outputs/openai-preflight.json"), "--preflight"
    ),
) -> None:
    """Verify full authorization, 30-request census and immutable artifacts."""

    try:
        manifest = validate_openai_full(
            output_dir, store_dir, load_openai_pilot_config(config), preflight
        )
    except (FileNotFoundError, OSError, ValueError) as exc:
        console.print(f"[red]FAIL[/red] {exc}")
        raise typer.Exit(code=1) from exc
    console.print_json(json.dumps(manifest.model_dump(mode="json")))
    console.print("[green]OpenAI full validation PASS[/green]")


@benchmark_app.command("evaluate-p1-pilot")
def evaluate_p1_pilot_cmd(
    pilot_dir: Path = typer.Option(..., "--pilot-dir"),
    store_dir: Path = typer.Option(Path("experiments/p1/evidence-store"), "--store-dir"),
    cases_dir: Path = typer.Option(Path("experiments/p1/cases"), "--cases-dir"),
    output: Path = typer.Option(..., "--output"),
    openai_config: Path | None = typer.Option(None, "--openai-config"),
    preflight: Path | None = typer.Option(None, "--preflight"),
) -> None:
    """Score correctness, evidence support, behavior and paired sensitivity."""

    try:
        report = evaluate_matched_pilot(
            pilot_dir,
            store_dir,
            cases_dir,
            openai_config_path=openai_config,
            preflight_path=preflight,
        )
        write_evaluation_report(report, output)
    except (FileExistsError, FileNotFoundError, OSError, ValueError) as exc:
        console.print(f"[red]FAIL[/red] {exc}")
        raise typer.Exit(code=1) from exc
    console.print_json(json.dumps(report.summary.model_dump(mode="json")))
    console.print(
        "[green]Pilot evaluation written[/green]. "
        "The locked lexical correctness score still requires final human semantic review."
    )
