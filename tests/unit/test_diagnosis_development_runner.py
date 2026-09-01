"""Fail-closed contracts for the offline diagnosis development runner."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

import aletheia_lab.diagnosis._development.store as development_store
from aletheia_lab.diagnosis.development import (
    DeterministicDevelopmentExecutor,
    DevelopmentArtifactStore,
    DevelopmentCase,
    DevelopmentPilotError,
    DevelopmentVariantRequest,
    DevelopmentVariantResponse,
    load_development_plan,
    load_run_record,
    load_run_request,
    load_run_response,
    run_development_pilot,
)
from aletheia_lab.diagnosis.variant_registry import (
    ResolvedDiagnosisVariant,
    load_variant_registry,
)
from aletheia_lab.evaluation.development_audit import (
    DevelopmentPilotAuditError,
    audit_development_pilot,
    require_development_pilot_ready,
)
from aletheia_lab.evaluation.variant_fairness import (
    MATCHED_MODEL_VARIANTS,
    REQUIRED_VARIANTS,
    load_diagnosis_variant_freeze,
)
from aletheia_lab.project.identity import canonical_project_json

ROOT = Path(__file__).resolve().parents[2]
FREEZE_PATH = ROOT / "configs/evaluation/diagnosis_variant_fairness_freeze.json"
PLAN_PATH = ROOT / "configs/evaluation/diagnosis_development_pilot_plan.json"


def _authorities() -> tuple[object, object, object]:
    return (
        load_development_plan(PLAN_PATH),
        load_diagnosis_variant_freeze(FREEZE_PATH),
        load_variant_registry(FREEZE_PATH),
    )


def _completed_store(tmp_path: Path) -> tuple[object, object, object, DevelopmentArtifactStore, str]:
    plan, freeze, registry = _authorities()
    store = DevelopmentArtifactStore(tmp_path / "store")
    terminal = run_development_pilot(plan, freeze, registry, store)  # type: ignore[arg-type]
    return plan, freeze, registry, store, terminal.run_id


def test_tracked_plan_executes_exact_complete_variant_matrix(tmp_path: Path) -> None:
    plan, freeze, registry, store, run_id = _completed_store(tmp_path)

    manifest = store.load_manifest(run_id)
    audit = audit_development_pilot(
        plan, freeze, registry, store, run_id  # type: ignore[arg-type]
    )

    assert manifest.variant_ids == REQUIRED_VARIANTS
    assert len(manifest.case_ids) == 3
    assert len(manifest.records) == 27
    assert tuple(
        (item.case_id, item.variant_id) for item in manifest.records
    ) == tuple(
        (case_id, variant_id)
        for case_id in manifest.case_ids
        for variant_id in REQUIRED_VARIANTS
    )
    assert audit.status == "development_pilot_validated"
    assert audit.blocker_codes == ()
    assert all(item.status == "pass" for item in audit.findings)
    assert next(
        item for item in audit.findings if item.code == "implementation_artifacts_resolve"
    ).status == "pass"
    require_development_pilot_ready(audit)


def test_records_preserve_tool_ledgers_and_development_boundary(tmp_path: Path) -> None:
    _, _, registry, store, run_id = _completed_store(tmp_path)
    manifest = store.load_manifest(run_id)
    by_variant = {item.variant_id: item for item in registry.variants}  # type: ignore[union-attr]

    for pointer in manifest.records:
        record = load_run_record(store, run_id, pointer.record_object_sha256)
        request = load_run_request(store, run_id, record.request_object_sha256)
        response = load_run_response(store, run_id, record.response_object_sha256)
        requires_ledger = by_variant[pointer.variant_id].capabilities.tool_ledger_required
        assert (request.tool_ledger is not None) is requires_ledger
        assert request.external_network_permitted is False
        assert request.live_provider_call is False
        assert request.protected_outcome_visible is False
        assert request.registered_attempt_consumed is False
        assert record.resources.provider_calls == 0
        assert record.resources.context_tokens_upper_bound == len(
            canonical_project_json(request.context_payload).encode("utf-8")
        )
        assert record.fallback_used is False
        assert response.synthetic_fixture is True
        assert response.scientific_interpretation_permitted is False
        if request.tool_ledger is not None:
            assert request.tool_ledger.web_used is False
            assert request.tool_ledger.shell_used is False
            assert request.tool_ledger.project_execution_used is False
            assert request.tool_ledger.fallback_used is False
            assert len(request.tool_ledger.events) == (
                2 if pointer.variant_id == "FULL" else 1
            )


def test_matched_requests_share_model_information_context_and_evidence(tmp_path: Path) -> None:
    _, _, registry, store, run_id = _completed_store(tmp_path)
    manifest = store.load_manifest(run_id)
    variants = {item.variant_id: item for item in registry.variants}  # type: ignore[union-attr]
    grouped: dict[str, list[DevelopmentVariantRequest]] = {}
    for pointer in manifest.records:
        if pointer.variant_id not in MATCHED_MODEL_VARIANTS:
            continue
        record = load_run_record(store, run_id, pointer.record_object_sha256)
        request = load_run_request(store, run_id, record.request_object_sha256)
        grouped.setdefault(pointer.case_id, []).append(request)

    assert len({variants[item].model_policy_sha256 for item in MATCHED_MODEL_VARIANTS}) == 1
    assert len(
        {variants[item].information_budget_sha256 for item in MATCHED_MODEL_VARIANTS}
    ) == 1
    assert all(len(items) == len(MATCHED_MODEL_VARIANTS) for items in grouped.values())
    assert all(len({item.context_sha256 for item in items}) == 1 for items in grouped.values())
    assert all(
        len({item.evidence_content_sha256 for item in items}) == 1
        for items in grouped.values()
    )


def test_insufficient_fixture_exercises_registered_abstention_only(tmp_path: Path) -> None:
    _, _, registry, store, run_id = _completed_store(tmp_path)
    manifest = store.load_manifest(run_id)
    by_variant = {item.variant_id: item for item in registry.variants}  # type: ignore[union-attr]

    for pointer in manifest.records:
        if pointer.case_id != "devcase-insufficient-trace":
            continue
        record = load_run_record(store, run_id, pointer.record_object_sha256)
        response = load_run_response(store, run_id, record.response_object_sha256)
        assert response.abstained is by_variant[pointer.variant_id].capabilities.abstention_required
        assert bool(response.missing_evidence) is response.abstained


def test_repeated_execution_is_content_addressed_and_idempotent(tmp_path: Path) -> None:
    plan, freeze, registry = _authorities()
    store = DevelopmentArtifactStore(tmp_path / "store")

    first = run_development_pilot(plan, freeze, registry, store)  # type: ignore[arg-type]
    second = run_development_pilot(plan, freeze, registry, store)  # type: ignore[arg-type]

    assert first == second
    assert store.list_runs() == (first.run_id,)
    assert tuple(store.failures_root.iterdir()) == ()


class _ExternalExecutor(DeterministicDevelopmentExecutor):
    @property
    def external_calls(self) -> bool:
        return True


def test_external_executor_fails_without_terminal_publication(
    tmp_path: Path,
) -> None:
    plan, freeze, registry = _authorities()
    store = DevelopmentArtifactStore(tmp_path / "store")

    with pytest.raises(DevelopmentPilotError):
        run_development_pilot(
            plan, freeze, registry, store, executor=_ExternalExecutor()  # type: ignore[arg-type]
        )

    assert store.list_runs() == ()
    receipts = tuple(store.failures_root.iterdir())
    assert len(receipts) == 1
    assert '"stage":"preflight"' in receipts[0].read_text(encoding="utf-8")
    assert "Synthetic" not in receipts[0].read_text(encoding="utf-8")


def test_malformed_builtin_response_fails_without_terminal_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan, freeze, registry = _authorities()
    store = DevelopmentArtifactStore(tmp_path / "store")
    original = DeterministicDevelopmentExecutor.execute

    def drop_required_citation(
        self: DeterministicDevelopmentExecutor,
        request: DevelopmentVariantRequest,
        variant: ResolvedDiagnosisVariant,
        case: DevelopmentCase,
    ) -> DevelopmentVariantResponse:
        response = original(self, request, variant, case)
        if variant.capabilities.citation_required:
            return response.model_copy(update={"cited_evidence_ids": ()})
        return response

    monkeypatch.setattr(
        DeterministicDevelopmentExecutor,
        "execute",
        drop_required_citation,
    )

    with pytest.raises(DevelopmentPilotError):
        run_development_pilot(plan, freeze, registry, store)  # type: ignore[arg-type]

    assert store.list_runs() == ()
    receipts = tuple(store.failures_root.iterdir())
    assert len(receipts) == 1
    assert '"stage":"validate_response"' in receipts[0].read_text(encoding="utf-8")


def test_object_byte_tampering_is_detected_before_audit(tmp_path: Path) -> None:
    plan, freeze, registry, store, run_id = _completed_store(tmp_path)
    manifest = store.load_manifest(run_id)
    digest = manifest.object_sha256s[0]
    target = store.runs_root / run_id / "objects" / "sha256" / digest[:2] / digest[2:]
    target.write_bytes(b"tampered\n")

    with pytest.raises(DevelopmentPilotAuditError, match="failed closed"):
        audit_development_pilot(
            plan, freeze, registry, store, run_id  # type: ignore[arg-type]
        )


def test_missing_object_is_reported_as_structured_fail_closed_audit(
    tmp_path: Path,
) -> None:
    plan, freeze, registry, store, run_id = _completed_store(tmp_path)
    manifest = store.load_manifest(run_id)
    digest = manifest.object_sha256s[0]
    target = store.runs_root / run_id / "objects" / "sha256" / digest[:2] / digest[2:]
    target.unlink()

    with pytest.raises(DevelopmentPilotAuditError, match="failed closed"):
        audit_development_pilot(
            plan, freeze, registry, store, run_id  # type: ignore[arg-type]
        )


def test_tree_sync_never_reopens_regular_payloads_for_fsync(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "stage"
    nested = root / "objects" / "sha256"
    nested.mkdir(parents=True)
    (nested / "payload").write_bytes(b"immutable\n")
    synced_directories: list[int] = []
    original_fsync = os.fsync

    def directory_only_fsync(file_descriptor: int) -> None:
        mode = os.fstat(file_descriptor).st_mode
        assert stat.S_ISDIR(mode)
        synced_directories.append(file_descriptor)
        original_fsync(file_descriptor)

    monkeypatch.setattr(development_store.os, "fsync", directory_only_fsync)
    development_store._fsync_tree(root)

    if hasattr(os, "O_DIRECTORY"):
        assert len(synced_directories) == 3
    else:
        assert synced_directories == []


def test_untracked_file_blocks_store_integrity(tmp_path: Path) -> None:
    _, _, _, store, run_id = _completed_store(tmp_path)
    (store.runs_root / run_id / "ambient.txt").write_text("not tracked\n", encoding="utf-8")

    with pytest.raises(DevelopmentPilotError, match="untracked files"):
        store.verify_run(run_id)


def test_audit_receipt_cannot_be_promoted_after_a_blocker(tmp_path: Path) -> None:
    plan, freeze, registry, store, run_id = _completed_store(tmp_path)
    receipt = audit_development_pilot(
        plan, freeze, registry, store, run_id  # type: ignore[arg-type]
    )
    forged = receipt.model_copy(
        update={
            "status": "development_pilot_blocked",
            "blocker_codes": ("complete_variant_matrix",),
        }
    )

    with pytest.raises((DevelopmentPilotAuditError, ValueError)):
        require_development_pilot_ready(forged)
