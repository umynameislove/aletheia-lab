"""Regression tests for deterministic, offline-only P1 closeout reporting."""

from __future__ import annotations

import json
import shutil
from collections import Counter
from pathlib import Path

import pytest
from typer.testing import CliRunner

from aletheia_lab.benchmark.generator import generate_p1
from aletheia_lab.cli import app
from aletheia_lab.diagnosis.adapters import OpenAIChatCompletionsAdapter
from aletheia_lab.diagnosis.external_pilot import run_openai_full
from aletheia_lab.diagnosis.openai_preflight import (
    build_openai_preflight,
    load_openai_pilot_config,
    openai_preflight_sha256,
    write_openai_preflight,
)
from aletheia_lab.diagnosis.schema import (
    DiagnosisRequest,
    ProviderIdentity,
    ProviderResponse,
    UsageRecord,
)
from aletheia_lab.evaluation.closeout import (
    generate_p1_closeout,
    validate_p1_closeout,
)
from aletheia_lab.evaluation.pilot import evaluate_matched_pilot, write_evaluation_report
from aletheia_lab.evaluation.result_lock import build_p1_result_lock, write_p1_result_lock
from aletheia_lab.evidence.p1 import generate_p1_evidence_store
from aletheia_lab.evidence.schema import canonical_json

CONFIG_PATH = Path("configs/evaluation/openai_pilot.yaml")
runner = CliRunner()


class _OfflineFixtureAdapter:
    def __init__(self, identity: ProviderIdentity) -> None:
        self._identity = identity
        self.calls_by_request: Counter[str] = Counter()

    @property
    def identity(self) -> ProviderIdentity:
        return self._identity

    def complete(self, request: DiagnosisRequest) -> ProviderResponse:
        self.calls_by_request[request.request_id] += 1
        evidence_id = request.diagnosis_view.items[0].evidence_id
        raw = canonical_json(
            {
                "schema_version": "diagnosis-output/1",
                "root_cause_hypothesis": "The visible evidence supports an observation.",
                "claim_strength": "observation",
                "supporting_evidence_ids": [evidence_id],
                "counterevidence_ids": [],
                "missing_evidence": ["Reference and metric comparison may be required."],
                "confidence": 0.4,
                "abstain": False,
            }
        )
        return ProviderResponse(
            response_id=f"fixture-{request.request_id}",
            provider_identity=self.identity,
            raw_text=raw,
            usage=UsageRecord(
                input_tokens=20,
                output_tokens=10,
                estimated_cost_usd=0.00012,
            ),
            latency_ms=1.0,
        )


@pytest.fixture
def closeout_inputs(
    p1_generator_config: Path, tmp_path: Path
) -> dict[str, Path]:
    cases = tmp_path / "cases"
    store = tmp_path / "evidence-store"
    pilot = tmp_path / "full"
    preflight = tmp_path / "preflight.json"
    evaluation = tmp_path / "evaluation.json"
    lock = tmp_path / "result-lock.json"
    generate_p1(p1_generator_config, cases)
    generate_p1_evidence_store(cases, store)
    config = load_openai_pilot_config(CONFIG_PATH)
    preflight_report = build_openai_preflight(store, config)
    write_openai_preflight(preflight_report, preflight)
    assert preflight_report.cost_estimates is not None
    run_openai_full(
        store,
        config,
        preflight,
        pilot,
        confirmed_preflight_sha256=openai_preflight_sha256(preflight_report),
        confirmed_estimated_full_retry_ceiling_usd=(
            preflight_report.cost_estimates.full_retry_ceiling.estimated_cost_usd
        ),
        adapter=_OfflineFixtureAdapter(config.provider_identity),
    )
    report = evaluate_matched_pilot(
        pilot,
        store,
        cases,
        openai_config_path=CONFIG_PATH,
        preflight_path=preflight,
    )
    write_evaluation_report(report, evaluation)
    result_lock = build_p1_result_lock(
        pilot,
        store,
        cases,
        CONFIG_PATH,
        preflight,
        evaluation,
        execution_commit_sha="a" * 40,
        evaluation_commit_sha="b" * 40,
    )
    write_p1_result_lock(result_lock, lock)
    return {
        "cases": cases,
        "store": store,
        "pilot": pilot,
        "preflight": preflight,
        "evaluation": evaluation,
        "lock": lock,
        "root": tmp_path,
    }


def _args(paths: dict[str, Path], output: Path) -> list[str]:
    return [
        "--lock",
        str(paths["lock"]),
        "--pilot-dir",
        str(paths["pilot"]),
        "--store-dir",
        str(paths["store"]),
        "--cases-dir",
        str(paths["cases"]),
        "--config",
        str(CONFIG_PATH),
        "--preflight",
        str(paths["preflight"]),
        "--evaluation",
        str(paths["evaluation"]),
        "--output-dir",
        str(output),
    ]


def _relative_payloads(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and not path.is_symlink()
    }


def test_closeout_is_offline_deterministic_and_fails_closed(
    closeout_inputs: dict[str, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = tmp_path / "closeout-1"
    second = tmp_path / "closeout-2"
    package = generate_p1_closeout(
        closeout_inputs["lock"],
        closeout_inputs["pilot"],
        closeout_inputs["store"],
        closeout_inputs["cases"],
        CONFIG_PATH,
        closeout_inputs["preflight"],
        closeout_inputs["evaluation"],
        first,
    )
    assert package.canonical_result.run_count == 30
    assert package.canonical_result.independent_family_count == 5
    assert package.canonical_result.diagnosis_context_count == 15
    assert package.operational_report.actual_run_count == 30
    assert package.operational_report.actual_attempt_count == 30
    assert package.operational_report.actual_retry_count == 0
    assert package.operational_report.actual_input_tokens == 600
    assert package.operational_report.actual_output_tokens == 300
    assert package.operational_report.actual_estimated_cost_usd == pytest.approx(0.0036)
    assert package.manifest.artifact_count == 6
    assert set(_relative_payloads(first)) == {
        "canonical-result.json",
        "canonical-result.md",
        "closeout-manifest.json",
        "error-analysis-draft.json",
        "error-analysis-draft.md",
        "operational-report.json",
        "operational-report.md",
    }
    assert validate_p1_closeout(
        closeout_inputs["lock"],
        closeout_inputs["pilot"],
        closeout_inputs["store"],
        closeout_inputs["cases"],
        CONFIG_PATH,
        closeout_inputs["preflight"],
        closeout_inputs["evaluation"],
        first,
    ) == package

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    def credentials_forbidden() -> None:
        raise AssertionError("offline closeout attempted to read API credentials")

    monkeypatch.setattr(
        OpenAIChatCompletionsAdapter,
        "from_environment",
        staticmethod(credentials_forbidden),
    )
    generated = runner.invoke(
        app, ["benchmark", "generate-p1-closeout", *_args(closeout_inputs, second)]
    )
    assert generated.exit_code == 0, generated.output
    assert "no external request was sent" in generated.output
    validated = runner.invoke(
        app, ["benchmark", "validate-p1-closeout", *_args(closeout_inputs, second)]
    )
    assert validated.exit_code == 0, validated.output
    assert _relative_payloads(first) == _relative_payloads(second)

    with pytest.raises(FileExistsError):
        generate_p1_closeout(
            closeout_inputs["lock"],
            closeout_inputs["pilot"],
            closeout_inputs["store"],
            closeout_inputs["cases"],
            CONFIG_PATH,
            closeout_inputs["preflight"],
            closeout_inputs["evaluation"],
            first,
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("alter", "differs from recomputation"),
        ("missing", "artifact set differs"),
        ("extra", "artifact set differs"),
    ],
)
def test_closeout_output_tampering_fails(
    closeout_inputs: dict[str, Path],
    tmp_path: Path,
    mutation: str,
    message: str,
) -> None:
    clean = tmp_path / "clean"
    generate_p1_closeout(
        closeout_inputs["lock"],
        closeout_inputs["pilot"],
        closeout_inputs["store"],
        closeout_inputs["cases"],
        CONFIG_PATH,
        closeout_inputs["preflight"],
        closeout_inputs["evaluation"],
        clean,
    )
    tampered = tmp_path / f"tampered-{mutation}"
    shutil.copytree(clean, tampered)
    if mutation == "alter":
        (tampered / "canonical-result.md").write_text("tampered\n", encoding="utf-8")
    elif mutation == "missing":
        (tampered / "operational-report.md").unlink()
    else:
        (tampered / "unexpected.txt").write_text("unexpected\n", encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        validate_p1_closeout(
            closeout_inputs["lock"],
            closeout_inputs["pilot"],
            closeout_inputs["store"],
            closeout_inputs["cases"],
            CONFIG_PATH,
            closeout_inputs["preflight"],
            closeout_inputs["evaluation"],
            tampered,
        )


def test_closeout_rejects_stale_evaluation_and_symlink(
    closeout_inputs: dict[str, Path], tmp_path: Path
) -> None:
    stale = tmp_path / "stale-evaluation.json"
    payload = json.loads(closeout_inputs["evaluation"].read_text("utf-8"))
    payload["diagnosis_evaluations"][0]["correctness"]["rationale"] = "changed"
    stale.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="evaluation report"):
        generate_p1_closeout(
            closeout_inputs["lock"],
            closeout_inputs["pilot"],
            closeout_inputs["store"],
            closeout_inputs["cases"],
            CONFIG_PATH,
            closeout_inputs["preflight"],
            stale,
            tmp_path / "not-created",
        )
    assert not (tmp_path / "not-created").exists()

    clean = tmp_path / "closeout"
    generate_p1_closeout(
        closeout_inputs["lock"],
        closeout_inputs["pilot"],
        closeout_inputs["store"],
        closeout_inputs["cases"],
        CONFIG_PATH,
        closeout_inputs["preflight"],
        closeout_inputs["evaluation"],
        clean,
    )
    (clean / "link").symlink_to(clean / "canonical-result.json")
    with pytest.raises(ValueError, match="symlink"):
        validate_p1_closeout(
            closeout_inputs["lock"],
            closeout_inputs["pilot"],
            closeout_inputs["store"],
            closeout_inputs["cases"],
            CONFIG_PATH,
            closeout_inputs["preflight"],
            closeout_inputs["evaluation"],
            clean,
        )
