"""Regression tests for dual-confirmed 30-request OpenAI execution."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest
from typer.testing import CliRunner

from aletheia_lab.benchmark.generator import generate_p1
from aletheia_lab.cli import app
from aletheia_lab.diagnosis.adapters import OpenAIChatCompletionsAdapter
from aletheia_lab.diagnosis.external_pilot import (
    run_openai_full,
    validate_openai_full,
)
from aletheia_lab.diagnosis.openai_preflight import (
    OpenAIPilotConfig,
    OpenAIPreflightReport,
    build_openai_preflight,
    load_openai_pilot_config,
    load_openai_preflight,
    openai_preflight_sha256,
    write_openai_preflight,
)
from aletheia_lab.diagnosis.schema import (
    DiagnosisRequest,
    ProviderIdentity,
    ProviderResponse,
    UsageRecord,
)
from aletheia_lab.evaluation.pilot import evaluate_matched_pilot
from aletheia_lab.evidence.p1 import generate_p1_evidence_store
from aletheia_lab.evidence.schema import canonical_json

CONFIG_PATH = Path("configs/evaluation/openai_pilot.yaml")
runner = CliRunner()


@pytest.fixture
def full_inputs(p1_generator_config: Path, tmp_path: Path) -> tuple[Path, Path, Path]:
    cases = tmp_path / "cases"
    store = tmp_path / "evidence-store"
    generate_p1(p1_generator_config, cases)
    generate_p1_evidence_store(cases, store)
    report = build_openai_preflight(store, load_openai_pilot_config(CONFIG_PATH))
    preflight = tmp_path / "preflight.json"
    write_openai_preflight(report, preflight)
    return cases, store, preflight


class _FullAdapter:
    def __init__(
        self,
        identity: ProviderIdentity,
        *,
        fail_first_attempt: bool = False,
    ) -> None:
        self._identity = identity
        self.fail_first_attempt = fail_first_attempt
        self.calls = 0
        self.calls_by_request: Counter[str] = Counter()

    @property
    def identity(self) -> ProviderIdentity:
        return self._identity

    def complete(self, request: DiagnosisRequest) -> ProviderResponse:
        self.calls += 1
        self.calls_by_request[request.request_id] += 1
        attempt = self.calls_by_request[request.request_id]
        if self.fail_first_attempt and attempt == 1:
            raw = "not valid diagnosis JSON"
        else:
            evidence_id = request.diagnosis_view.items[0].evidence_id
            raw = canonical_json(
                {
                    "schema_version": "diagnosis-output/1",
                    "root_cause_hypothesis": "The visible evidence supports a comparison.",
                    "claim_strength": "comparison",
                    "supporting_evidence_ids": [evidence_id],
                    "counterevidence_ids": [],
                    "missing_evidence": ["Additional comparison evidence may be required."],
                    "confidence": 0.5,
                    "abstain": False,
                }
            )
        return ProviderResponse(
            response_id=f"chatcmpl-{self.calls}",
            provider_identity=self.identity,
            raw_text=raw,
            usage=UsageRecord(input_tokens=20, output_tokens=10, estimated_cost_usd=0.00012),
            latency_ms=1.0,
        )


def _confirmations(
    store: Path,
) -> tuple[OpenAIPilotConfig, OpenAIPreflightReport, str, float]:
    config = load_openai_pilot_config(CONFIG_PATH)
    report = build_openai_preflight(store, config)
    assert report.cost_estimates is not None
    return (
        config,
        report,
        openai_preflight_sha256(report),
        report.cost_estimates.full_retry_ceiling.estimated_cost_usd,
    )


def test_full_execution_preserves_15x2_census_validates_and_evaluates(
    full_inputs: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    cases, store, preflight = full_inputs
    config, report, digest, ceiling = _confirmations(store)
    adapter = _FullAdapter(config.provider_identity)
    output = tmp_path / "full"

    manifest = run_openai_full(
        store,
        config,
        preflight,
        output,
        confirmed_preflight_sha256=digest,
        confirmed_estimated_full_retry_ceiling_usd=ceiling,
        adapter=adapter,
    )

    assert adapter.calls == 30
    assert (manifest.context_count, manifest.variant_count, manifest.run_count) == (15, 2, 30)
    assert manifest.success_count == 30
    assert validate_openai_full(output, store, config, preflight) == manifest
    authorization = json.loads((output / "execution-authorization.json").read_text("utf-8"))
    assert authorization["schema_version"] == "external-full-authorization/1"
    assert authorization["confirmed_estimated_full_retry_ceiling_usd"] == ceiling
    assert len(authorization["full_request_ids"]) == 30
    assert "smoke_request_ids" not in authorization
    assert set(authorization["full_request_ids"]) == {
        entry.request_id for entry in manifest.entries
    }

    evaluation = evaluate_matched_pilot(
        output,
        store,
        cases,
        openai_config_path=CONFIG_PATH,
        preflight_path=preflight,
    )
    assert evaluation.summary.run_count == 30
    assert len(evaluation.paired_sensitivity) == 10
    assert evaluation.summary.complete_paired_family_count == 10
    assert all(item.complete_three_condition_family for item in evaluation.paired_sensitivity)

    with pytest.raises(FileExistsError):
        run_openai_full(
            store,
            config,
            preflight,
            output,
            confirmed_preflight_sha256=digest,
            confirmed_estimated_full_retry_ceiling_usd=ceiling,
            adapter=adapter,
        )
    assert adapter.calls == 30
    assert report.request_count == 30


@pytest.mark.parametrize("bad_ceiling", [0.0, 0.384621, float("nan"), float("inf")])
def test_wrong_full_confirmation_fails_before_provider_call(
    full_inputs: tuple[Path, Path, Path], tmp_path: Path, bad_ceiling: float
) -> None:
    _, store, preflight = full_inputs
    config, _, digest, ceiling = _confirmations(store)
    assert bad_ceiling != ceiling
    adapter = _FullAdapter(config.provider_identity)
    with pytest.raises(ValueError, match="cost confirmation"):
        run_openai_full(
            store,
            config,
            preflight,
            tmp_path / "not-created",
            confirmed_preflight_sha256=digest,
            confirmed_estimated_full_retry_ceiling_usd=bad_ceiling,
            adapter=adapter,
        )
    assert adapter.calls == 0
    assert not (tmp_path / "not-created").exists()


def test_wrong_digest_identity_and_legacy_preflight_fail_before_send(
    full_inputs: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    _, store, preflight = full_inputs
    config, report, digest, ceiling = _confirmations(store)
    adapter = _FullAdapter(config.provider_identity)
    with pytest.raises(ValueError, match="confirmation"):
        run_openai_full(
            store,
            config,
            preflight,
            tmp_path / "bad-digest",
            confirmed_preflight_sha256="0" * 64,
            confirmed_estimated_full_retry_ceiling_usd=ceiling,
            adapter=adapter,
        )
    assert adapter.calls == 0

    wrong_identity = _FullAdapter(
        ProviderIdentity(provider="openai", model="gpt-4.1", version="latest")
    )
    with pytest.raises(ValueError, match="adapter identity"):
        run_openai_full(
            store,
            config,
            preflight,
            tmp_path / "bad-identity",
            confirmed_preflight_sha256=digest,
            confirmed_estimated_full_retry_ceiling_usd=ceiling,
            adapter=wrong_identity,
        )
    assert wrong_identity.calls == 0

    legacy_payload = report.model_dump(mode="json")
    legacy_payload.pop("cost_estimates")
    legacy = tmp_path / "legacy.json"
    legacy.write_text(json.dumps(legacy_payload), encoding="utf-8")
    legacy_report = load_openai_preflight(legacy)
    with pytest.raises(ValueError, match="four-budget preflight"):
        run_openai_full(
            store,
            config,
            legacy,
            tmp_path / "legacy-not-created",
            confirmed_preflight_sha256=openai_preflight_sha256(legacy_report),
            confirmed_estimated_full_retry_ceiling_usd=ceiling,
            adapter=adapter,
        )
    assert adapter.calls == 0


def test_cli_rejects_bad_confirmation_before_reading_api_credentials(
    full_inputs: tuple[Path, Path, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, store, preflight = full_inputs
    _, _, _, ceiling = _confirmations(store)
    credential_reads = 0

    def credentials_forbidden() -> None:
        nonlocal credential_reads
        credential_reads += 1
        raise AssertionError("CLI read credentials before authorization completed")

    monkeypatch.setattr(
        OpenAIChatCompletionsAdapter,
        "from_environment",
        staticmethod(credentials_forbidden),
    )
    result = runner.invoke(
        app,
        [
            "benchmark",
            "run-p1-openai-full",
            "--store-dir",
            str(store),
            "--config",
            str(CONFIG_PATH),
            "--preflight",
            str(preflight),
            "--output-dir",
            str(tmp_path / "not-created"),
            "--confirm-preflight-sha256",
            "0" * 64,
            "--confirm-estimated-full-retry-ceiling-usd",
            str(ceiling),
        ],
    )
    assert result.exit_code == 1
    assert "confirmation" in result.output
    assert credential_reads == 0
    assert not (tmp_path / "not-created").exists()


def test_full_retry_ceiling_is_bounded_and_every_attempt_validates(
    full_inputs: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    _, store, preflight = full_inputs
    config, _, digest, ceiling = _confirmations(store)
    adapter = _FullAdapter(config.provider_identity, fail_first_attempt=True)
    output = tmp_path / "retried-full"

    manifest = run_openai_full(
        store,
        config,
        preflight,
        output,
        confirmed_preflight_sha256=digest,
        confirmed_estimated_full_retry_ceiling_usd=ceiling,
        adapter=adapter,
    )

    assert adapter.calls == 60
    assert set(adapter.calls_by_request.values()) == {2}
    assert manifest.success_count == 30
    records = [json.loads(path.read_text("utf-8")) for path in (output / "runs").glob("*.json")]
    assert len(records) == 30
    assert all([attempt["status"] for attempt in row["attempts"]] == [
        "parse_failure",
        "success",
    ] for row in records)
    assert validate_openai_full(output, store, config, preflight) == manifest


def test_tampered_full_authorization_and_artifact_fail_closed(
    full_inputs: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    _, store, preflight = full_inputs
    config, _, digest, ceiling = _confirmations(store)
    output = tmp_path / "full"
    run_openai_full(
        store,
        config,
        preflight,
        output,
        confirmed_preflight_sha256=digest,
        confirmed_estimated_full_retry_ceiling_usd=ceiling,
        adapter=_FullAdapter(config.provider_identity),
    )

    authorization_path = output / "execution-authorization.json"
    authorization = json.loads(authorization_path.read_text("utf-8"))
    authorization["full_request_ids"] = list(reversed(authorization["full_request_ids"]))
    authorization_path.write_text(json.dumps(authorization), encoding="utf-8")
    with pytest.raises(ValueError, match="authorization differs"):
        validate_openai_full(output, store, config, preflight)

    authorization["full_request_ids"] = list(reversed(authorization["full_request_ids"]))
    authorization_path.write_text(json.dumps(authorization), encoding="utf-8")
    raw = next((output / "raw").rglob("*.txt"))
    raw.write_text("tampered", encoding="utf-8")
    with pytest.raises(ValueError, match="raw response hash mismatch"):
        validate_openai_full(output, store, config, preflight)
