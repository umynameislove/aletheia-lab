"""Hosted local analysis cannot activate the separate, opt-in provider gateway."""
from __future__ import annotations

import asyncio
import json
import socket

import pytest
from pydantic import SecretStr, ValidationError
from test_k07_dimensions_v2 import snapshot_v2

from aletheia_lab.hosted.job_http import Submission
from aletheia_lab.hosted.request_boundary import RequestRejected
from aletheia_lab.hosted.upload_operations import UploadOperations
from aletheia_lab.hosted.upload_worker import WorkerInput, _execute
from aletheia_lab.hosted_contract.project_analysis import AnalysisRequest
from aletheia_lab.model_gateway import OpenAIChatCompletionsGatewayAdapter


@pytest.fixture(autouse=True)
def no_external_provider(monkeypatch):
    calls = []

    def blocked(*args, **kwargs):
        calls.append(True)
        raise AssertionError("Hosted analysis must not activate an external provider")

    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-private-key-not-for-use")
    monkeypatch.setattr(socket, "getaddrinfo", blocked)
    monkeypatch.setattr(OpenAIChatCompletionsGatewayAdapter, "from_environment", blocked)
    monkeypatch.setattr(OpenAIChatCompletionsGatewayAdapter, "invoke", blocked)
    yield
    assert not calls


class Sources:
    def __init__(self, snapshot):
        self.value, self.calls = snapshot, 0

    async def snapshot(self, **kwargs):
        assert (kwargs["workspace_id"], kwargs["snapshot_id"]) == (self.value.workspace_id, self.value.snapshot_id)
        self.calls += 1
        return self.value


class Worker:
    def __init__(self, root, mutation=None):
        self.root, self.mutation, self.calls = root, mutation, 0

    async def run_upload(self, payload):
        self.calls += 1
        value = _execute(WorkerInput.model_validate_json(payload), self.root)
        if self.mutation:
            key, replacement = self.mutation
            value["runtime"][key] = replacement
            value["conversation"]["turns"][0]["runtime"][key] = replacement
        return json.dumps(value, ensure_ascii=False).encode()


@pytest.mark.parametrize("model", ["upload-snapshot-observation/v1", "upload-snapshot-metric-observation/v2"])
def test_ambient_provider_credentials_never_activate_paid_analysis(tmp_path, model):
    _, snapshot, _, _ = snapshot_v2(tmp_path)
    sources, worker = Sources(snapshot), Worker(tmp_path)
    view = asyncio.run(UploadOperations(sources, worker).analyze(credential_digest=SecretStr("synthetic"),
        workspace_id=snapshot.workspace_id, snapshot_id=snapshot.snapshot_id,
        question="What do the validated observations establish?", analysis_model=model))
    assert view.runtime.provider == "local_evidence" and view.runtime.model == model
    assert view.runtime.external_call is False and view.result.causal_status == "unverified"
    assert sources.calls == 2 and worker.calls == 1


@pytest.mark.parametrize("field,value", [("backend", "openai"), ("provider", "openai"),
    ("api_key", "synthetic-not-authorized"), ("endpoint", "https://provider.example.invalid"),
    ("analysis_model", "unknown-provider-model")])
def test_public_requests_cannot_choose_or_configure_a_provider(field, value):
    payload = {"schema_version": "product-api/v1-draft", "snapshot_id": "upload-snapshot-" + "a" * 64,
               "question": "What is observed?", field: value}
    with pytest.raises(ValidationError):
        AnalysisRequest.model_validate(payload)
    del payload["schema_version"]
    with pytest.raises(ValidationError):
        Submission.model_validate({"operation": "analyze", **payload})


@pytest.mark.parametrize("mutation", [("provider", "openai"), ("external_call", True), ("model", "unknown-provider-model")])
def test_forged_worker_provider_metadata_never_becomes_an_accepted_result(tmp_path, mutation):
    _, snapshot, _, _ = snapshot_v2(tmp_path)
    worker = Worker(tmp_path, mutation)
    with pytest.raises(ValueError) as error:
        asyncio.run(UploadOperations(Sources(snapshot), worker).analyze(credential_digest=SecretStr("synthetic"),
            workspace_id=snapshot.workspace_id, snapshot_id=snapshot.snapshot_id,
            question="What is observed?", analysis_model="upload-snapshot-metric-observation/v2"))
    if mutation[0] == "model":
        assert str(error.value) == "Worker analysis mismatch"
    else:
        assert isinstance(error.value, RequestRejected)
        assert (error.value.status, error.value.code) == (422, "schema_invalid")
    assert worker.calls == 1


def test_unknown_internal_analysis_version_rejects_before_worker(tmp_path):
    _, snapshot, _, _ = snapshot_v2(tmp_path)
    worker = Worker(tmp_path)
    with pytest.raises(ValueError, match="Unsupported analysis version"):
        asyncio.run(UploadOperations(Sources(snapshot), worker).analyze(credential_digest=SecretStr("synthetic"),
            workspace_id=snapshot.workspace_id, snapshot_id=snapshot.snapshot_id,
            question="What is observed?", analysis_model="paid-provider/unknown"))
    assert worker.calls == 0
