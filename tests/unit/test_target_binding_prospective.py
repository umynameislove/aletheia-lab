"""Synthetic-only prospective runner tests; no protected source outcomes or network."""

from __future__ import annotations

import json
import socket
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Any
from zipfile import ZipFile

import numpy as np
import pytest

from aletheia_lab.benchmark.p2 import target_binding_prospective as runtime
from aletheia_lab.benchmark.p2 import target_binding_prospective_sources as sources
from aletheia_lab.benchmark.p2.score_mapping_verification import _feature_matrix_sha256
from aletheia_lab.benchmark.p2.target_binding_intervention import TargetBindingSource
from aletheia_lab.benchmark.p2.target_binding_prospective_verify import _verify_pair, verify_cell
from aletheia_lab.content_hashing import file_sha256
from aletheia_lab.project.identity import content_sha256

REPO = Path(__file__).resolve().parents[2]


def _archive(directory: Path, name: str, rows: list[str]) -> dict[str, Any]:
    payload = ("\n".join(rows) + "\n").encode("ascii")
    path = directory / f"{name}.zip"
    with ZipFile(path, "w") as archive:
        archive.writestr(f"{name}.data", payload)
    return {
        "dataset_id": name,
        "archive_filename": path.name,
        "url": "https://archive.ics.uci.edu/static/public/synthetic-test",
        "doi": "synthetic-test-only",
        "license": "CC-BY-4.0",
        "archive_bytes": path.stat().st_size,
        "archive_sha256": file_sha256(path),
        "member": f"{name}.data",
        "member_bytes": len(payload),
        "member_sha256": content_sha256(payload),
        "row_count": len(rows),
        "feature_count": 2,
        "target_encoding": {"0": 0, "1": 1},
    }


@pytest.fixture
def study(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, dict[str, Any]]:
    root, directory = tmp_path / "checkout", tmp_path / "private"
    (directory / "sources").mkdir(parents=True)
    (root / "configs/benchmark").mkdir(parents=True)
    (root / "src/aletheia_lab").mkdir(parents=True)
    (root / "scripts").mkdir()
    (root / "pyproject.toml").write_text("# synthetic fixture\n")
    (root / sources.SCRIPT_PATH).write_text("# synthetic fixture\n")
    (root / "src/aletheia_lab/__init__.py").write_text("# synthetic fixture\n")
    protocol = json.loads((REPO / sources.PROTOCOL_PATH).read_bytes())
    specs = []
    for name, seed in (("synthetic_one", 813), ("synthetic_two", 127)):
        rng = np.random.default_rng(seed)
        matrix = rng.normal(size=(800, 2))
        # No separability tuning: stochastic labels from a fixed moderate signal.
        probabilities = 1 / (1 + np.exp(-matrix[:, 0]))
        labels = (rng.random(800) < probabilities).astype(int)
        rows = [
            f"{left:.8f},{right:.8f},{label}"
            for (left, right), label in zip(matrix, labels, strict=True)
        ]
        rows += [rows[0], rows[1]]  # Duplicate groups must never cross partitions.
        specs.append(_archive(directory / "sources", name, rows))
    protocol["sources"] = specs
    (root / sources.PROTOCOL_PATH).write_bytes(sources.json_bytes(protocol))
    # The fake root exists only to exercise seal drift. Production origin validation
    # is tested separately and cannot be bypassed through any CLI option.
    monkeypatch.setattr(sources, "validate_loaded_code_root", lambda root: None)
    return root, directory, protocol


def _prepare(study: tuple[Path, Path, dict[str, Any]]) -> str:
    root, directory, _ = study
    prepared = sources.prepare_study(root, directory)
    assert prepared["status"] == "outcome_blind_preflight_pass"
    return prepared["plan_sha256"]


def _unexpected(*args: Any, **kwargs: Any) -> Any:
    raise AssertionError("this operation must not occur")


def test_prepare_is_metric_blind_group_safe_and_idempotent(
    study: tuple[Path, Path, dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, directory, protocol = study
    monkeypatch.setattr(runtime, "_fit_reference_model", _unexpected)
    monkeypatch.setattr(runtime, "fit_logit_calibration", _unexpected)
    monkeypatch.setattr(runtime, "match_target_swaps", _unexpected)
    prepared = sources.prepare_study(root, directory)
    before = file_sha256(directory / "plan.json")
    assert sources.prepare_study(root, directory) == prepared
    assert before == file_sha256(directory / "plan.json")
    assert prepared["cell_count"] == 4 and prepared["source_cluster_count"] == 2
    assert prepared["provider_calls"] == 0 and not prepared["final_predictions_computed"]
    assert not (directory / "lease.json").exists()
    for spec in protocol["sources"]:
        data = sources.load_source(
            directory / "sources",
            sources.SourceSpec.model_validate(spec),
            seed=protocol["split_seed"],
        )
        seen: dict[str, str] = {}
        for partition, indices in data.partitions.items():
            for index in indices:
                assert seen.setdefault(data.group_ids[index], partition) == partition
        assert data.audit()["duplicate_feature_rows"] == 2
        assert sorted(index for indices in data.partitions.values() for index in indices) == list(
            range(802)
        )


def test_loaded_origin_must_match_sealed_checkout(tmp_path: Path) -> None:
    sources.validate_loaded_code_root(REPO)
    with pytest.raises(sources.ProspectiveBindingError, match="loaded package"):
        sources.validate_loaded_code_root(tmp_path)


@pytest.mark.parametrize("mutation", ["code", "archive", "runtime", "confirmation", "plan"])
def test_drift_rejected_before_lease_or_fit(
    study: tuple[Path, Path, dict[str, Any]], monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    root, directory, _ = study
    plan_hash = _prepare(study)
    monkeypatch.setattr(runtime, "_fit_reference_model", _unexpected)
    if mutation == "code":
        (root / sources.SCRIPT_PATH).write_text("changed\n")
    elif mutation == "archive":
        (directory / "sources/synthetic_one.zip").write_bytes(b"changed")
    elif mutation == "runtime":
        monkeypatch.setattr(sources, "version", lambda name: "changed")
    elif mutation == "confirmation":
        plan_hash = "0" * 64
    else:
        (directory / "plan.json").write_text("{}")
    with pytest.raises(sources.ProspectiveBindingError):
        runtime.execute_study(root, directory, confirm_plan_sha256=plan_hash)
    assert not (directory / "lease.json").exists()


def test_synthetic_end_to_end_separate_calibration_and_no_replay(
    study: tuple[Path, Path, dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, directory, _ = study
    plan_hash = _prepare(study)
    monkeypatch.setattr(socket, "create_connection", _unexpected)
    fit_calibration = runtime.fit_logit_calibration
    calibration_counts = []

    def calibration_spy(probabilities: Any, labels: Any, **kwargs: Any) -> Any:
        calibration_counts.append(len(labels))
        return fit_calibration(probabilities, labels, **kwargs)

    monkeypatch.setattr(runtime, "fit_logit_calibration", calibration_spy)
    receipt = runtime.execute_study(root, directory, confirm_plan_sha256=plan_hash)
    assert receipt["status"] == "prospective_census_terminalized"
    assert receipt["cell_status_counts"] == {"verified": 4}
    assert sum(receipt["match_status_counts"].values()) == 4
    plan, parsed = sources.build_plan(root, directory)
    assert calibration_counts == [
        len(data.partitions["calibration"]) for data in parsed for _ in range(2)
    ]
    assert receipt["provider_calls"] == 0 and receipt["mechanism_admitted"] is False
    assert len(receipt["census_dispositions"]) == len(plan["cell_census"]) == 4
    before = deepcopy(receipt)
    monkeypatch.setattr(runtime, "_fit_reference_model", _unexpected)
    monkeypatch.setattr(runtime, "match_target_swaps", _unexpected)
    verified = runtime.verify_study(root, directory)
    assert verified["status"] == "prospective_verification_pass"
    assert json.loads((directory / "receipt.json").read_bytes()) == before
    with pytest.raises(sources.ProspectiveBindingError, match="already leased"):
        runtime.execute_study(root, directory, confirm_plan_sha256=plan_hash)
    with pytest.raises(sources.ProspectiveBindingError, match="leased"):
        sources.prepare_study(root, directory)


@pytest.mark.parametrize(
    "field,value",
    [
        ("mechanism_admitted", True),
        ("provider_calls", 1),
        ("source_cluster_count", 4),
        ("schema_version", "other"),
        ("status", "PASS"),
        ("claim_scope", "global"),
    ],
)
def test_receipt_cannot_promote_its_own_claims(
    study: tuple[Path, Path, dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: Any,
) -> None:
    root, directory, _ = study
    plan_hash = _prepare(study)
    monkeypatch.setattr(runtime, "_run_cell", _unexpected)
    receipt = runtime.execute_study(root, directory, confirm_plan_sha256=plan_hash)
    receipt[field] = value
    (directory / "receipt.json").write_bytes(sources.json_bytes(receipt))
    with pytest.raises(sources.ProspectiveBindingError, match="receipt claims"):
        runtime.verify_study(root, directory)


def test_runtime_failures_are_all_retained_not_reported_as_mechanism_pass(
    study: tuple[Path, Path, dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, directory, _ = study
    plan_hash = _prepare(study)
    monkeypatch.setattr(runtime, "_run_cell", _unexpected)
    receipt = runtime.execute_study(root, directory, confirm_plan_sha256=plan_hash)
    assert receipt["cell_status_counts"] == {"runtime_failure": 4}
    assert receipt["match_status_counts"] == {"not_evaluated": 4}
    verified = runtime.verify_study(root, directory)
    assert verified["cell_status_counts"] == {"runtime_failure": 4}
    assert verified["mechanism_admitted"] is False


def test_interrupt_terminalizes_unfinished_census_and_consumes_lease(
    study: tuple[Path, Path, dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, directory, _ = study
    plan_hash = _prepare(study)

    def interrupt(*args: Any) -> Any:
        raise KeyboardInterrupt

    monkeypatch.setattr(runtime, "_run_cell", interrupt)
    receipt = runtime.execute_study(root, directory, confirm_plan_sha256=plan_hash)
    assert receipt["status"] == "prospective_execution_failed_closed"
    assert receipt["cell_status_counts"] == {"not_terminalized": 1, "not_executed": 3}
    assert receipt["failure"] == {"stage": "execute_census", "error_type": "KeyboardInterrupt"}
    assert runtime.verify_study(root, directory)["status"] == "prospective_failure_receipt_verified"
    with pytest.raises(sources.ProspectiveBindingError, match="already leased"):
        runtime.execute_study(root, directory, confirm_plan_sha256=plan_hash)


def test_output_publication_failure_retains_census(
    study: tuple[Path, Path, dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, directory, _ = study
    plan_hash = _prepare(study)
    original = runtime.publish_json

    def failing_publish(path: Path, payload: Any) -> None:
        if path.name == "result.json":
            raise OSError("synthetic publication failure")
        original(path, payload)

    monkeypatch.setattr(runtime, "publish_json", failing_publish)
    monkeypatch.setattr(runtime, "_run_cell", _unexpected)
    receipt = runtime.execute_study(root, directory, confirm_plan_sha256=plan_hash)
    assert receipt["cell_count"] == 4
    assert receipt["failure"]["error_type"] == "OSError"
    assert runtime.verify_study(root, directory)["status"] == "prospective_failure_receipt_verified"


def test_post_execution_code_drift_is_not_a_pass(
    study: tuple[Path, Path, dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, directory, _ = study
    plan_hash = _prepare(study)

    def change_code(*args: Any) -> Any:
        (root / sources.SCRIPT_PATH).write_text("changed\n")
        raise ValueError("synthetic failure")

    monkeypatch.setattr(runtime, "_run_cell", change_code)
    receipt = runtime.execute_study(root, directory, confirm_plan_sha256=plan_hash)
    assert receipt["status"] == "prospective_execution_failed_closed"
    assert receipt["failure"]["stage"] == "post_execution_binding"
    assert receipt["cell_count"] == 4
    with pytest.raises(sources.ProspectiveBindingError):
        runtime.verify_study(root, directory)


@pytest.mark.parametrize(
    "mutation", ["donor", "metric", "score", "target", "calibration", "reader"]
)
def test_independent_replay_rejects_trace_tampering(
    study: tuple[Path, Path, dict[str, Any]], mutation: str
) -> None:
    root, directory, _ = study
    plan_hash = _prepare(study)
    runtime.execute_study(root, directory, confirm_plan_sha256=plan_hash)
    _, parsed = sources.build_plan(root, directory)
    data = parsed[0]
    cell_dir = directory / "cells" / f"{data.spec.dataset_id}-logistic_regression"
    result = json.loads((cell_dir / "result.json").read_bytes())
    if mutation == "donor":
        result["cyclic_controls"][1]["donor_record_ids"][0] = "unknown-row"
    elif mutation == "metric":
        result["cyclic_controls"][1]["verification"]["faulty_log_loss"] += 0.001
    elif mutation == "reader":
        if result["match_status"] == "resolution_matched":
            result["reader_payloads"]["missing_key"][0]["context_id"] = "changed"
        else:
            result["reader_payloads"] = {}
    else:
        path = cell_dir / (
            "calibration.json" if mutation == "calibration" else "source-witness.json"
        )
        payload = json.loads(path.read_bytes())
        if mutation == "score":
            payload["raw_score_rows"][0] = [0.5, 0.5]
        elif mutation == "target":
            payload["target_rows"][0][1] = 1 - payload["target_rows"][0][1]
        else:
            payload["development_record_count"] += 1
        path.write_bytes(sources.json_bytes(payload))
    with pytest.raises(sources.ProspectiveBindingError):
        verify_cell(data, cell_dir, result)


@pytest.mark.parametrize(
    "change",
    [
        "nonfinite",
        "width",
        "label",
        "count",
        "member_size",
        "member_digest",
        "member_missing",
        "member_duplicate",
    ],
)
def test_source_parser_has_no_silent_exclusions(
    study: tuple[Path, Path, dict[str, Any]], change: str
) -> None:
    _, directory, protocol = study
    rows = ["0.1,0.2,0", "0.3,0.4,1"]
    if change == "nonfinite":
        rows[0] = "nan,0.2,0"
    elif change == "width":
        rows[0] = "0.1,0"
    elif change == "label":
        rows[0] = "0.1,0.2,unknown"
    spec = _archive(directory / "sources", "malformed", rows)
    if change == "count":
        spec["row_count"] += 1
    elif change == "member_size":
        spec["member_bytes"] += 1
    elif change == "member_digest":
        spec["member_sha256"] = "0" * 64
    elif change == "member_missing":
        spec["member"] = "absent.data"
    elif change == "member_duplicate":
        with (
            ZipFile(directory / "sources/malformed.zip", "a") as archive,
            pytest.warns(UserWarning),
        ):
            archive.writestr(spec["member"], "0.1,0.2,0\n0.3,0.4,1\n")
        path = directory / "sources/malformed.zip"
        spec["archive_bytes"], spec["archive_sha256"] = path.stat().st_size, file_sha256(path)
    with pytest.raises(sources.ProspectiveBindingError):
        sources.load_source(
            directory / "sources",
            sources.SourceSpec.model_validate(spec),
            seed=protocol["split_seed"],
        )


def test_single_class_source_is_retained_without_final_fitting(
    study: tuple[Path, Path, dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, directory, protocol = study
    specs = [
        _archive(directory / "sources", name, [f"{i},0.5,0" for i in range(40)])
        for name in ("single_one", "single_two")
    ]
    protocol["sources"] = specs
    (root / sources.PROTOCOL_PATH).write_bytes(sources.json_bytes(protocol))
    prepared = sources.prepare_study(root, directory)
    assert prepared["status"] == "preflight_ineligible_retained"
    monkeypatch.setattr(runtime, "_fit_reference_model", _unexpected)
    receipt = runtime.execute_study(root, directory, confirm_plan_sha256=prepared["plan_sha256"])
    assert receipt["cell_status_counts"] == {"ineligible_class_partition": 4}
    assert runtime.verify_study(root, directory)["cell_status_counts"] == {
        "ineligible_class_partition": 4
    }


@pytest.mark.parametrize(
    "field,value",
    [
        ("dose", 2),
        ("visible_metric_decimals", 12),
        ("maximum_absolute_loss_gap", 0.01),
        ("retry_policy", "retry"),
        ("sources", []),
    ],
)
def test_frozen_scientific_settings_cannot_be_relaxed(
    study: tuple[Path, Path, dict[str, Any]], field: str, value: Any
) -> None:
    _, _, protocol = study
    protocol[field] = value
    with pytest.raises(ValueError):
        sources.ProspectiveProtocol.model_validate_json(sources.json_bytes(protocol))


@pytest.fixture
def oracle_inputs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    import test_score_mapping_evidence as oracle

    def selected(parity: int) -> str:
        return next(
            f"r{index}"
            for index in range(parity, 1000, 2)
            if int(
                content_sha256(f"m5-dev-v1-2026-09-25\0synthetic-dataset\0r{index}".encode()), 16
            )
            % 20
            == 0
        )

    monkeypatch.setattr(oracle, "_selected_id", selected)
    case = oracle._case(tmp_path)
    return {
        "witness": case.witness,
        "score_source": case.source,
        "target_source": TargetBindingSource("synthetic-dataset", case.targets),
        "reference_model": case.model,
        "reference_calibration": case.calibration,
        "reference_features": case.reference_features,
        "evaluation_matrix": case.evaluation_features,
        "artifacts": case.artifacts,
    }


def test_matched_oracle_checks_complete_payload_and_actual_target_correction(
    oracle_inputs: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    result = runtime._mapping_contrast(oracle_inputs)
    assert result["match_status"] == "resolution_matched"
    assert result["reader_equality"] == {
        "full": False,
        "missing_key": True,
        "noisy": False,
        "misleading": False,
    }
    target = result["paired_target_verification"]
    mapping = result["mapping_verification"]
    assert target["corrected_log_loss"] == target["healthy_log_loss"]
    assert target["faulty_log_loss"] == pytest.approx(mapping["faulty_log_loss"], abs=1e-14)
    witness = oracle_inputs["witness"]
    matrices = {
        "train": oracle_inputs["reference_features"],
        "final": oracle_inputs["evaluation_matrix"],
    }
    faulty = runtime.apply_evaluator_mapping_fault(
        oracle_inputs["score_source"], selected_shard_count=1
    ).positive_probabilities
    monkeypatch.setattr(runtime, "match_target_swaps", _unexpected)
    _verify_pair(witness, matrices, result, mapping, faulty)
    corrupted = deepcopy(result)
    corrupted["reader_payloads"]["missing_key"][0]["context_id"] = "cause-shortcut"
    with pytest.raises(sources.ProspectiveBindingError):
        _verify_pair(witness, matrices, corrupted, mapping, faulty)


@pytest.mark.parametrize("control", ["nonpositive", "footprint"])
def test_ineligible_mapping_contrast_never_calls_matcher(
    oracle_inputs: dict[str, Any], monkeypatch: pytest.MonkeyPatch, control: str
) -> None:
    original = runtime.verify_evaluator_mapping

    def controlled(**kwargs: Any) -> Any:
        measured = original(**kwargs)
        return (
            replace(measured, faulty_log_loss=measured.healthy_log_loss)
            if control == "nonpositive"
            else replace(measured, affected_record_ids=measured.affected_record_ids[:1])
        )

    monkeypatch.setattr(runtime, "verify_evaluator_mapping", controlled)
    monkeypatch.setattr(runtime, "match_target_swaps", _unexpected)
    result = runtime._mapping_contrast(oracle_inputs)
    assert result["match_status"] == (
        "nonpositive_mapping_effect"
        if control == "nonpositive"
        else "insufficient_mapping_footprint"
    )
    assert "reader_payloads" not in result


def test_resealed_scale_drift_is_rejected_by_train_only_reconstruction(
    study: tuple[Path, Path, dict[str, Any]],
) -> None:
    root, directory, _ = study
    runtime.execute_study(root, directory, confirm_plan_sha256=_prepare(study))
    _, parsed = sources.build_plan(root, directory)
    data = parsed[0]
    cell_dir = directory / "cells" / f"{data.spec.dataset_id}-logistic_regression"
    result = json.loads((cell_dir / "result.json").read_bytes())
    state_path, witness_path = cell_dir / "preprocessor.json", cell_dir / "source-witness.json"
    state = json.loads(state_path.read_bytes())
    state["scale"][0] *= 2
    state_path.write_bytes(sources.json_bytes(state))
    witness = json.loads(witness_path.read_bytes())
    witness["artifact_hashes"]["preprocessor"] = file_sha256(state_path)
    altered = (data.features[list(data.partitions["final"])] - state["mean"]) / state["scale"]
    witness["feature_matrix_sha256"] = _feature_matrix_sha256(tuple(witness["record_ids"]), altered)
    witness_path.write_bytes(sources.json_bytes(witness))
    with pytest.raises(sources.ProspectiveBindingError):
        verify_cell(data, cell_dir, result)
