"""Regression tests for preflight-bound external smoke execution."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aletheia_lab.benchmark.generator import generate_p1
from aletheia_lab.diagnosis.external_pilot import run_openai_smoke, validate_openai_smoke
from aletheia_lab.diagnosis.openai_preflight import (
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


@pytest.fixture
def smoke_inputs(p1_generator_config: Path, tmp_path: Path) -> tuple[Path, Path, Path]:
    cases = tmp_path / "cases"
    store = tmp_path / "evidence-store"
    generate_p1(p1_generator_config, cases)
    generate_p1_evidence_store(cases, store)
    report = build_openai_preflight(store, load_openai_pilot_config(CONFIG_PATH))
    preflight = tmp_path / "preflight.json"
    write_openai_preflight(report, preflight)
    return cases, store, preflight


class _BoundAdapter:
    def __init__(self, identity: ProviderIdentity) -> None:
        self._identity = identity
        self.calls = 0

    @property
    def identity(self) -> ProviderIdentity:
        return self._identity

    def complete(self, request: DiagnosisRequest) -> ProviderResponse:
        self.calls += 1
        evidence_id = request.diagnosis_view.items[0].evidence_id
        raw = canonical_json(
            {
                "schema_version": "diagnosis-output/1",
                "root_cause_hypothesis": "The evidence supports a bounded comparison.",
                "claim_strength": "comparison",
                "supporting_evidence_ids": [evidence_id],
                "counterevidence_ids": [],
                "missing_evidence": [],
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


def test_external_smoke_executes_only_exact_preflight_set_and_validates(
    smoke_inputs: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    cases, store, preflight = smoke_inputs
    config = load_openai_pilot_config(CONFIG_PATH)
    report = build_openai_preflight(store, config)
    adapter = _BoundAdapter(config.provider_identity)
    output = tmp_path / "smoke"

    manifest = run_openai_smoke(
        store,
        config,
        preflight,
        output,
        confirmed_preflight_sha256=openai_preflight_sha256(report),
        adapter=adapter,
    )

    assert adapter.calls == 8
    assert (manifest.context_count, manifest.variant_count, manifest.run_count) == (4, 2, 8)
    assert {entry.request_id for entry in manifest.entries} == set(report.smoke_request_ids)
    assert manifest.success_count == 8
    assert validate_openai_smoke(output, store, config, preflight) == manifest
    evaluation = evaluate_matched_pilot(
        output,
        store,
        cases,
        openai_config_path=CONFIG_PATH,
        preflight_path=preflight,
    )
    assert evaluation.summary.run_count == 8
    assert len(evaluation.paired_sensitivity) == 4
    assert evaluation.summary.complete_paired_family_count == 0


def test_wrong_confirmation_or_adapter_identity_fails_before_provider_call(
    smoke_inputs: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    _, store, preflight = smoke_inputs
    config = load_openai_pilot_config(CONFIG_PATH)
    adapter = _BoundAdapter(config.provider_identity)
    with pytest.raises(ValueError, match="confirmation"):
        run_openai_smoke(
            store,
            config,
            preflight,
            tmp_path / "not-created",
            confirmed_preflight_sha256="0" * 64,
            adapter=adapter,
        )
    assert adapter.calls == 0
    assert not (tmp_path / "not-created").exists()

    wrong = _BoundAdapter(ProviderIdentity(provider="openai", model="gpt-4.1", version="latest"))
    with pytest.raises(ValueError, match="adapter identity"):
        run_openai_smoke(
            store,
            config,
            preflight,
            tmp_path / "also-not-created",
            confirmed_preflight_sha256=openai_preflight_sha256(
                build_openai_preflight(store, config)
            ),
            adapter=wrong,
        )
    assert wrong.calls == 0


def test_tampered_preflight_and_raw_artifact_fail_closed(
    smoke_inputs: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    _, store, preflight = smoke_inputs
    config = load_openai_pilot_config(CONFIG_PATH)
    report = build_openai_preflight(store, config)
    tampered = json.loads(preflight.read_text("utf-8"))
    tampered["request_set_sha256"] = "0" * 64
    tampered_path = tmp_path / "tampered-preflight.json"
    tampered_path.write_text(json.dumps(tampered), encoding="utf-8")
    adapter = _BoundAdapter(config.provider_identity)
    with pytest.raises(ValueError, match="differs"):
        run_openai_smoke(
            store,
            config,
            tampered_path,
            tmp_path / "not-created",
            confirmed_preflight_sha256=openai_preflight_sha256(report),
            adapter=adapter,
        )
    assert adapter.calls == 0

    output = tmp_path / "smoke"
    run_openai_smoke(
        store,
        config,
        preflight,
        output,
        confirmed_preflight_sha256=openai_preflight_sha256(report),
        adapter=adapter,
    )
    raw = next((output / "raw").rglob("*.txt"))
    raw.write_text("tampered", encoding="utf-8")
    with pytest.raises(ValueError, match="raw response hash mismatch"):
        validate_openai_smoke(output, store, config, preflight)


def test_tampered_cost_budget_fails_closed_and_legacy_preflight_still_validates(
    smoke_inputs: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    """New budgets are bound, while a genuine pre-budget artifact remains usable."""

    _, store, preflight = smoke_inputs
    config = load_openai_pilot_config(CONFIG_PATH)
    report = build_openai_preflight(store, config)
    payload = json.loads(preflight.read_text("utf-8"))
    payload["cost_estimates"]["smoke_retry_ceiling"]["estimated_cost_usd"] += 0.01
    tampered = tmp_path / "tampered-cost.json"
    tampered.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="retry-ceiling cost projection is not derived"):
        run_openai_smoke(
            store,
            config,
            tampered,
            tmp_path / "tampered-not-created",
            confirmed_preflight_sha256=openai_preflight_sha256(report),
            adapter=_BoundAdapter(config.provider_identity),
        )

    legacy_payload = report.model_dump(mode="json")
    legacy_payload.pop("cost_estimates")
    legacy = tmp_path / "legacy-preflight.json"
    legacy.write_text(json.dumps(legacy_payload), encoding="utf-8")
    legacy_report = load_openai_preflight(legacy)
    adapter = _BoundAdapter(config.provider_identity)
    output = tmp_path / "legacy-smoke"
    manifest = run_openai_smoke(
        store,
        config,
        legacy,
        output,
        confirmed_preflight_sha256=openai_preflight_sha256(legacy_report),
        adapter=adapter,
    )
    assert manifest.success_count == 8
    assert validate_openai_smoke(output, store, config, legacy) == manifest


def test_incomplete_execution_directory_cannot_validate(
    smoke_inputs: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    _, store, preflight = smoke_inputs
    output = tmp_path / "incomplete"
    output.mkdir()
    (output / "execution-authorization.json").write_text("{}", encoding="utf-8")
    with pytest.raises((ValueError, FileNotFoundError)):
        validate_openai_smoke(
            output, store, load_openai_pilot_config(CONFIG_PATH), preflight
        )
