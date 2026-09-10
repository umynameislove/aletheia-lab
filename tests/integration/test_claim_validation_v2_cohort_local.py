"""Process-level reproducibility test for the V2 cohort authority."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from aletheia_lab.evaluation.claim_corpus_execution import RepositoryExecutionState
from aletheia_lab.evaluation.claim_validation_v2_cohort import (
    load_verified_qualification,
)
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
from aletheia_lab.evaluation.claim_validation_v2_qualification_contracts import (
    PROVIDER_VARIANTS,
    V2QualificationOutcome,
    V2QualificationReceipt,
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
HISTORICAL_COMMIT = "a" * 40
_PROCESS_PLAN = """
import sys
from pathlib import Path

from aletheia_lab.evaluation.claim_validation_v2_cohort import build_cohort_plan
from aletheia_lab.evaluation.claim_validation_v2_qualification_contracts import (
    V2QualificationReceipt,
)
from aletheia_lab.project.identity import canonical_project_json

root = Path(sys.argv[1])
receipt = V2QualificationReceipt.model_validate_json(Path(sys.argv[2]).read_bytes())
plan = build_cohort_plan(
    root,
    source_commit_ref=sys.argv[3],
    qualification_receipt=receipt,
)
print(canonical_project_json(plan.model_dump(mode="json")))
"""


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


def _passed_receipt() -> V2QualificationReceipt:
    outcomes = []
    for index, variant in enumerate(PROVIDER_VARIANTS, start=1):
        outcome: dict[str, object] = {
            "variant": variant,
            "qualification_request_sha256": f"{index:064x}",
            "gateway_request_identity_sha256": f"{index + 10:064x}",
            "gateway_status": "parsed",
            "attempt_count": 1,
            "first_witness_accepted": True,
            "issue_sha256": None,
        }
        outcomes.append(
            V2QualificationOutcome.model_validate(
                {
                    **outcome,
                    "outcome_sha256": canonical_execution_sha256(outcome),
                }
            )
        )
    payload: dict[str, object] = {
        "schema_version": "claim-support-validation-v2-qualification-receipt/v1",
        "status": "claim_support_validation_v2_qualification_passed",
        "authorization_sha256": "1" * 64,
        "plan_sha256": "2" * 64,
        "rehearsal_sha256": "3" * 64,
        "amendment_sha256": "4" * 64,
        "expressiveness_review_sha256": "5" * 64,
        "source_commit_ref": HISTORICAL_COMMIT,
        "terminal_store_sha256": "7" * 64,
        "terminal_request_count": 7,
        "parsed_count": 7,
        "first_witness_accepted_count": 7,
        "technical_failure_count": 0,
        "semantic_validation_failure_count": 0,
        "provider_attempt_count": 7,
        "gateway_status_counts": {"parsed": 7},
        "outcomes": tuple(item.model_dump(mode="json") for item in outcomes),
        "synthetic_only": True,
        "admitted_to_corpus": False,
        "provider_calls_executed": True,
        "rerun_forbidden": True,
        "full_cohort_authorization_unlocked": True,
        "claims_materialized": False,
        "automatic_labels_generated": False,
        "blind_packets_generated": False,
        "human_annotations_collected": False,
        "main_or_sealed_outcomes_opened": False,
    }
    return V2QualificationReceipt.model_validate(
        {**payload, "receipt_sha256": canonical_execution_sha256(payload)}
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
    seed: int,
    receipt: Path,
) -> subprocess.CompletedProcess[bytes]:
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(ROOT / "src")
    environment["PYTHONHASHSEED"] = str(seed)
    environment.pop("OPENAI_API_KEY", None)
    return subprocess.run(
        [
            sys.executable,
            "-c",
            _PROCESS_PLAN,
            str(ROOT),
            str(receipt),
            HISTORICAL_COMMIT,
        ],
        check=False,
        cwd=ROOT,
        env=environment,
        capture_output=True,
    )


def test_plan_is_hash_seed_stable_and_executes_zero_provider_calls(
    tmp_path: Path,
) -> None:
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text(
        canonical_project_json(_passed_receipt().model_dump(mode="json")) + "\n",
        encoding="utf-8",
    )
    first = _run(1, receipt_path)
    second = _run(104729, receipt_path)

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


@pytest.mark.skipif(
    os.name == "nt",
    reason=(
        "the Linux matrices rebuild the filesystem-heavy qualification store; "
        "Windows exercises the same core plan plus shared publication contracts"
    ),
)
def test_completed_qualification_store_is_independently_rebuilt(
    tmp_path: Path,
) -> None:
    qualification = _qualification(tmp_path)
    verified = load_verified_qualification(ROOT, qualification)

    assert verified.full_cohort_authorization_unlocked is True
    assert verified.parsed_count == 7
    assert verified.technical_failure_count == 0
