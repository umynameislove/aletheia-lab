"""Exact-model lock, OpenAI adapter and offline-preflight regression tests."""

from __future__ import annotations

import importlib.metadata
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from aletheia_lab.benchmark.generator import generate_p1
from aletheia_lab.diagnosis.adapters import (
    AdapterError,
    OpenAIChatCompletionsAdapter,
    openai_output_json_schema,
)
from aletheia_lab.diagnosis.openai_preflight import (
    MODEL_SNAPSHOT,
    OpenAIPilotConfig,
    build_openai_preflight,
    load_openai_pilot_config,
    openai_outbound_payload,
    write_openai_preflight,
)
from aletheia_lab.diagnosis.pilot import build_matched_requests
from aletheia_lab.diagnosis.schema import ProviderResponse
from aletheia_lab.evidence.p1 import generate_p1_evidence_store
from aletheia_lab.evidence.schema import canonical_json, project_diagnosis_evidence
from aletheia_lab.evidence.store import load_bundle_store

CONFIG_PATH = Path("configs/evaluation/openai_pilot.yaml")


@pytest.fixture
def p1_store(p1_generator_config: Path, tmp_path: Path) -> Path:
    cases = tmp_path / "cases"
    store = tmp_path / "evidence-store"
    generate_p1(p1_generator_config, cases)
    generate_p1_evidence_store(cases, store)
    return store


def _config_payload() -> dict[str, object]:
    return load_openai_pilot_config(CONFIG_PATH).model_dump(mode="json")


@pytest.mark.parametrize("model", ["gpt-4.1", "gpt-4.1-mini", "gpt-4.1-latest"])
def test_config_rejects_alias_or_unapproved_model(model: str) -> None:
    payload = _config_payload()
    payload["model_snapshot"] = model
    with pytest.raises(ValidationError, match="model_snapshot"):
        OpenAIPilotConfig.model_validate(payload)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("capabilities", "tools"), True),
        (("capabilities", "web_search"), True),
        (("capabilities", "retrieval"), True),
        (("execution", "preflight_only"), False),
        (("settings", "temperature"), 0.1),
        (("settings", "max_output_tokens"), 601),
        (("sdk_version",), "2.45.0"),
    ],
)
def test_config_rejects_every_unfrozen_capability_or_setting(
    path: tuple[str, ...], value: object
) -> None:
    payload = _config_payload()
    target = payload
    for key in path[:-1]:
        nested = target[key]
        assert isinstance(nested, dict)
        target = nested
    target[path[-1]] = value
    with pytest.raises(ValidationError):
        OpenAIPilotConfig.model_validate(payload)


def test_config_rejects_embedded_api_key() -> None:
    payload = _config_payload()
    payload["api_key"] = "sk-this-secret-must-never-be-serialized"
    with pytest.raises(ValidationError, match="extra_forbidden"):
        OpenAIPilotConfig.model_validate(payload)


def test_preflight_proves_complete_matched_plan_without_network(
    p1_store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def network_forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("preflight attempted network access")

    monkeypatch.setattr("socket.create_connection", network_forbidden)
    config = load_openai_pilot_config(CONFIG_PATH)
    report = build_openai_preflight(p1_store, config)

    assert report.passed is True
    assert report.provider_identity.model == MODEL_SNAPSHOT
    assert (report.context_count, report.matched_pair_count, report.request_count) == (15, 15, 30)
    assert len(report.smoke_request_ids) == 8
    assert report.reserved_output_tokens == 30 * 600
    assert report.estimated_input_tokens > 0
    assert 0.0 < report.estimated_max_cost_usd < 1.0
    assert all(report.checks.values())


def test_preflight_report_is_secret_free_immutable_and_non_overwriting(
    p1_store: Path, tmp_path: Path
) -> None:
    report = build_openai_preflight(p1_store, load_openai_pilot_config(CONFIG_PATH))
    output = tmp_path / "preflight.json"
    write_openai_preflight(report, output)
    serialized = output.read_text("utf-8").casefold()

    assert "sk-" not in serialized
    assert "api_key" not in serialized
    assert "authorization" not in serialized
    assert "ground_truth" not in serialized
    assert "evidence_condition" not in serialized
    with pytest.raises(FileExistsError, match="refusing to replace"):
        write_openai_preflight(report, output)


class _CapturingCompletions:
    def __init__(self, *, actual_model: str = MODEL_SNAPSHOT, include_usage: bool = True) -> None:
        self.actual_model = actual_model
        self.include_usage = include_usage
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        raw = canonical_json(
            {
                "schema_version": "diagnosis-output/1",
                "root_cause_hypothesis": "The observations support a bounded hypothesis.",
                "claim_strength": "comparison",
                "supporting_evidence_ids": [],
                "counterevidence_ids": [],
                "missing_evidence": [],
                "confidence": 0.5,
                "abstain": False,
            }
        )
        usage = (
            SimpleNamespace(prompt_tokens=120, completion_tokens=40)
            if self.include_usage
            else None
        )
        return SimpleNamespace(
            id="chatcmpl-test",
            model=self.actual_model,
            choices=[SimpleNamespace(message=SimpleNamespace(content=raw, refusal=None))],
            usage=usage,
        )


class _FakeClient:
    def __init__(self, completions: _CapturingCompletions) -> None:
        self.chat = SimpleNamespace(completions=completions)


def _adapter(completions: _CapturingCompletions) -> OpenAIChatCompletionsAdapter:
    return OpenAIChatCompletionsAdapter(client=_FakeClient(completions))


def test_adapter_constructor_does_not_accept_caller_selected_model_or_schema() -> None:
    client = _FakeClient(_CapturingCompletions())
    with pytest.raises(TypeError):
        OpenAIChatCompletionsAdapter(  # type: ignore[call-arg]
            client=client,
            model_snapshot="gpt-4.1",
        )

    first = openai_output_json_schema()
    first["type"] = "tampered"
    assert openai_output_json_schema()["type"] == "json_schema"


def test_environment_factory_fails_closed_when_sdk_is_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing_sdk(distribution_name: str) -> str:
        raise importlib.metadata.PackageNotFoundError(distribution_name)

    monkeypatch.setattr("importlib.metadata.version", missing_sdk)
    with pytest.raises(AdapterError) as exc_info:
        OpenAIChatCompletionsAdapter.from_environment()
    assert exc_info.value.error_type == "missing_sdk"


def _first_request(p1_store: Path):
    store = load_bundle_store(p1_store)
    view = project_diagnosis_evidence(store.bundles[0])
    config = load_openai_pilot_config(CONFIG_PATH)
    return build_matched_requests(
        (view,), provider_identity=config.provider_identity, settings=config.settings
    )[0]


def test_adapter_sends_exact_snapshot_and_shared_schema_and_returns_raw(
    p1_store: Path,
) -> None:
    completions = _CapturingCompletions()
    adapter = _adapter(completions)
    request = _first_request(p1_store)
    expected_payload = openai_outbound_payload(request, load_openai_pilot_config(CONFIG_PATH))

    response = adapter.complete(request)

    assert isinstance(response, ProviderResponse)
    assert json.loads(response.raw_text)["schema_version"] == "diagnosis-output/1"
    assert response.provider_identity == request.provider_identity
    assert response.usage.input_tokens == 120
    assert response.usage.output_tokens == 40
    assert response.usage.estimated_cost_usd == pytest.approx(0.00056)
    assert completions.calls == [expected_payload]
    assert completions.calls[0]["response_format"] == openai_output_json_schema()
    assert "tools" not in completions.calls[0]


def test_adapter_exposes_silent_model_switch_for_runner_rejection(p1_store: Path) -> None:
    response = _adapter(_CapturingCompletions(actual_model="gpt-4.1")).complete(
        _first_request(p1_store)
    )
    assert response.provider_identity.model == "gpt-4.1"
    assert response.provider_identity.version == "unverified"
    assert response.provider_identity != _first_request(p1_store).provider_identity


def test_adapter_fails_closed_when_usage_is_missing(p1_store: Path) -> None:
    with pytest.raises(AdapterError) as exc_info:
        _adapter(_CapturingCompletions(include_usage=False)).complete(_first_request(p1_store))
    assert exc_info.value.error_type == "missing_usage"
