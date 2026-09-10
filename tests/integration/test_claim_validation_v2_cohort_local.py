"""Process-level reproducibility and secret-boundary tests for V2 cohort authority."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from aletheia_lab.evaluation.claim_corpus_execution import RepositoryExecutionState
from aletheia_lab.evaluation.claim_validation_v2_expressiveness import (
    build_v2_expressiveness_amendment,
)
from aletheia_lab.evaluation.claim_validation_v2_qualification import (
    _expected_provider_payload,
    build_qualification_authorization,
    build_qualification_gateway_requests,
    build_qualification_plan,
    checked_qualification_run_directory,
    publish_qualification_result,
    rehearse_qualification,
)
from aletheia_lab.evaluation.claim_validation_v2_qualification_execution import (
    execute_qualification,
)
from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256
from aletheia_lab.model_gateway import (
    ProviderBinding,
    ProviderCall,
    ProviderEnvelope,
    RawResponseArtifact,
    UsageMetadata,
)
from aletheia_lab.project.identity import canonical_project_json

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "claim_support_validation_v2_authorization.py"
HISTORICAL_COMMIT = "a" * 40


class _Clock:
    def __init__(self) -> None:
        self.value = 1_000

    def now_ns(self) -> int:
        self.value += 1_000
        return self.value


class _FakeAdapter:
    def __init__(
        self,
        binding: ProviderBinding,
        payloads: dict[str, dict[str, object]],
    ) -> None:
        self._binding = binding
        self._payloads = payloads

    @property
    def binding(self) -> ProviderBinding:
        return self._binding

    def invoke(self, call: ProviderCall) -> ProviderEnvelope:
        raw = canonical_project_json(
            self._payloads[call.request_identity_sha256]
        ).encode("utf-8")
        return ProviderEnvelope(
            request_identity_sha256=call.request_identity_sha256,
            binding=self.binding,
            provider_attempt_ref=(
                f"ev-{canonical_execution_sha256({'call': call.attempt_id})}"
            ),
            response_mode="structured",
            raw_response=RawResponseArtifact.from_bytes(raw),
            usage=UsageMetadata(
                input_tokens=10,
                output_tokens=10,
                total_tokens=20,
                cost_amount=None,
                cost_currency_ref=None,
            ),
        )


def _qualification(tmp_path: Path) -> Path:
    state = RepositoryExecutionState(
        branch="main",
        head_commit=HISTORICAL_COMMIT,
        origin_main_commit=HISTORICAL_COMMIT,
        clean=True,
    )
    plan = build_qualification_plan(ROOT, source_commit_ref=HISTORICAL_COMMIT)
    rehearsal = rehearse_qualification(ROOT, plan)
    run_dir = checked_qualification_run_directory(ROOT, tmp_path / "qualification")
    authorization = build_qualification_authorization(
        plan,
        rehearsal,
        repository_state=state,
        run_dir=run_dir,
        authorized_at="2026-09-10T00:00:00Z",
        operator_cost_ceiling_usd=plan.estimated_upper_cost_usd,
    )
    run_dir.mkdir()
    publish_qualification_result(run_dir / "authorization.json", authorization)
    prepared = build_qualification_gateway_requests(ROOT, plan, authorization)
    probes = {
        item.qualification_request_sha256: item
        for item in build_v2_expressiveness_amendment(ROOT).qualification_probes
    }
    payloads = {
        item.request.initial_attempt.request_identity_sha256: (
            _expected_provider_payload(probes[item.request_sha256])
        )
        for item in prepared
    }
    binding = ProviderBinding.from_model_policy(
        prepared[0].request.initial_attempt.model_policy
    )
    receipt = execute_qualification(
        ROOT,
        repository_state=state,
        run_dir=run_dir,
        confirm_authorization_sha256=authorization.authorization_sha256,
        adapter=_FakeAdapter(binding, payloads),
        clock=_Clock(),
    )
    assert receipt.full_cohort_authorization_unlocked is True
    return run_dir


def _run(
    command: str,
    seed: int,
    qualification: Path,
    cohort: Path,
    *,
    credential: str | None = None,
) -> subprocess.CompletedProcess[bytes]:
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(ROOT / "src")
    environment["PYTHONHASHSEED"] = str(seed)
    if credential is None:
        environment.pop("OPENAI_API_KEY", None)
    else:
        environment["OPENAI_API_KEY"] = credential
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            command,
            "--root",
            str(ROOT),
            "--qualification-run-dir",
            str(qualification),
            "--run-dir",
            str(cohort),
        ],
        check=False,
        cwd=ROOT,
        env=environment,
        capture_output=True,
    )


def test_plan_is_hash_seed_stable_and_executes_zero_provider_calls(
    tmp_path: Path,
) -> None:
    qualification = _qualification(tmp_path)
    first = _run("plan", 1, qualification, tmp_path / "cohort")
    second = _run("plan", 104729, qualification, tmp_path / "cohort")

    assert first.returncode == second.returncode == 0
    assert first.stdout == second.stdout
    plan = json.loads(first.stdout)
    assert plan["diagnosis_request_count"] == 360
    assert plan["model_request_count"] == 315
    assert plan["deterministic_request_count"] == 45
    assert plan["qualification_receipt_sha256"]
    assert plan["provider_calls_executed"] is False
    assert plan["claims_materialized"] is False
    assert plan["blind_packets_generated"] is False


def test_preflight_fails_closed_and_never_prints_credential(
    tmp_path: Path,
) -> None:
    qualification = _qualification(tmp_path)
    secret = "sk-this-value-must-never-be-rendered"
    completed = _run(
        "require-live-ready",
        1,
        qualification,
        tmp_path / "cohort",
        credential=secret,
    )

    assert completed.returncode == 2
    assert secret.encode() not in completed.stdout
    assert secret.encode() not in completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["credential_present"] is True
    assert "authorization_pending" in payload["live_blockers"]
    assert payload["provider_calls_executed"] is False
