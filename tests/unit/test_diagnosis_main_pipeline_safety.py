from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from aletheia_lab.diagnosis._main_pipeline_budget import (
    BudgetedProviderAdapter,
    SharedProviderBudget,
)
from aletheia_lab.diagnosis.main_pipeline import (
    DiagnosisMainPipelineError,
    _exclusive_execution,
    checked_pipeline_run_directory,
    load_private_main_packet,
)
from aletheia_lab.model_gateway import (
    AdapterInvocationError,
    ProviderAdapter,
    ProviderCall,
    ProviderEnvelope,
)


def _call(context_json: str = '{"payload":"synthetic"}') -> ProviderCall:
    return cast(
        ProviderCall,
        SimpleNamespace(
            context_json=context_json,
            prompt_text="synthetic prompt",
            response_schema_json='{"type":"object"}',
            attempt_identity_sha256="a" * 64,
        ),
    )


class _CountingDelegate:
    def __init__(self) -> None:
        self.calls = 0

    def invoke(self, _call: ProviderCall) -> ProviderEnvelope:
        self.calls += 1
        return cast(
            ProviderEnvelope,
            SimpleNamespace(usage=SimpleNamespace(input_tokens=100, output_tokens=20)),
        )


def test_shared_budget_refuses_before_delegate_dispatch() -> None:
    budget = SharedProviderBudget(0.000001)
    delegate = _CountingDelegate()
    adapter = BudgetedProviderAdapter(cast(ProviderAdapter, delegate), budget)

    with pytest.raises(AdapterInvocationError):
        adapter.invoke(_call())

    assert delegate.calls == 0
    assert budget.exhausted is True


def test_shared_budget_settles_usage_and_releases_unused_reservation() -> None:
    budget = SharedProviderBudget(0.008)
    delegate = _CountingDelegate()
    adapter = BudgetedProviderAdapter(cast(ProviderAdapter, delegate), budget)

    adapter.invoke(_call())
    assert delegate.calls == 1
    assert budget.committed_usd == pytest.approx(0.00036)
    assert budget.exhausted is False

    adapter.invoke(_call())
    assert delegate.calls == 2
    assert budget.committed_usd == pytest.approx(0.00072)


def test_budget_refusal_remains_latched_for_smaller_subsequent_requests() -> None:
    budget = SharedProviderBudget(0.01)
    large_call = _call(context_json="x" * 100_000)
    with pytest.raises(AdapterInvocationError):
        budget.reserve(large_call)
    with pytest.raises(AdapterInvocationError):
        budget.reserve(_call())
    assert budget.committed_usd == 0


def test_execution_lock_excludes_another_writer_and_releases_on_exception(tmp_path: Path) -> None:
    with (
        pytest.raises(RuntimeError, match="synthetic interruption"),
        _exclusive_execution(tmp_path),
    ):
        with (
            pytest.raises(DiagnosisMainPipelineError, match="concurrent dispatch"),
            _exclusive_execution(tmp_path),
        ):
            pytest.fail("second writer acquired the execution lock")
        raise RuntimeError("synthetic interruption")
    assert not (tmp_path / "active-execution").exists()
    with _exclusive_execution(tmp_path):
        assert (tmp_path / "active-execution").is_dir()


def test_private_pipeline_paths_reject_symlink_aliases(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    private_directory = tmp_path / "private"
    private_directory.mkdir()
    alias = tmp_path / "private-alias"
    try:
        alias.symlink_to(private_directory, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is not available")

    with pytest.raises(DiagnosisMainPipelineError, match="must remain private"):
        checked_pipeline_run_directory(repository, alias)
    with pytest.raises(DiagnosisMainPipelineError, match="must remain private"):
        checked_pipeline_run_directory(repository, alias / "new-run")

    packet = private_directory / "packet.json"
    packet.write_text("{}", encoding="utf-8")
    with pytest.raises(DiagnosisMainPipelineError, match="must remain outside git"):
        load_private_main_packet(repository, alias / "packet.json")
