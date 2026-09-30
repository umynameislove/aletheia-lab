"""Synthetic sources only: multi-estimator controls, replay and sealed boundaries."""

from __future__ import annotations

import json
import math
import socket
import subprocess
import sys
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Any
from zipfile import ZipFile

import numpy as np
import pytest

from aletheia_lab.benchmark.p2 import score_mapping_new_source_cells as cells
from aletheia_lab.benchmark.p2 import score_mapping_new_source_plan as plans
from aletheia_lab.benchmark.p2 import score_mapping_new_source_protocol as inventory
from aletheia_lab.benchmark.p2 import score_mapping_new_source_study as study
from aletheia_lab.benchmark.p2 import score_mapping_new_source_verify as replay
from aletheia_lab.benchmark.p2 import score_mapping_new_source_wire as wire
from aletheia_lab.benchmark.p2 import target_binding_prospective as fitting
from aletheia_lab.benchmark.p2.confirmatory_v3_shift import reference_prior_standardized_log_loss
from aletheia_lab.benchmark.p2.score_mapping_evidence import DevelopmentObservation
from aletheia_lab.benchmark.p2.target_binding_prospective_sources import (
    ProspectiveBindingError,
    json_bytes,
    publish_json,
)
from aletheia_lab.content_hashing import file_sha256
from aletheia_lab.project.identity import content_sha256

REPO = Path(__file__).resolve().parents[2]


def _archive(directory: Path, spec: dict[str, Any], seed: int) -> dict[str, Any]:
    rng = np.random.default_rng(seed)
    features = rng.normal(size=(800, 2))
    probabilities = 1 / (1 + np.exp(-features[:, 0]))
    targets = (rng.random(800) < probabilities).astype(int)
    rice = spec["dataset_id"] == "rice_cammeo_osmancik"
    tokens = ["Osmancik", "Cammeo"] if rice else ["0", "1"]
    rows = [
        f"{left:.8f},{right:.8f},{tokens[label]}"
        for (left, right), label in zip(features, targets, strict=True)
    ]
    # Conflicting labels on identical features must still stay in one partition.
    first = rows[0].rsplit(",", 1)[0]
    rows.append(f"{first},{tokens[1 - targets[0]]}")
    header = (
        "@RELATION synthetic\n@ATTRIBUTE x NUMERIC\n@ATTRIBUTE y NUMERIC\n@ATTRIBUTE Class {Cammeo, Osmancik}\n@DATA\n"
        if rice
        else ""
    )
    payload = (header + "\n".join(rows) + "\n").encode()
    path = directory / spec["archive_filename"]
    with ZipFile(path, "w") as archive:
        archive.writestr(spec["member"], payload)
    return {
        **spec,
        "archive_bytes": path.stat().st_size,
        "archive_sha256": file_sha256(path),
        "member_bytes": len(payload),
        "member_sha256": content_sha256(payload),
        "row_count": len(rows),
        "feature_count": 2,
    }


@pytest.fixture
def prepared(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, str]:
    root, directory = tmp_path / "checkout", tmp_path / "private"
    (directory / "sources").mkdir(parents=True)
    for folder in ("configs/benchmark", "configs/evaluation", "src/aletheia_lab", "scripts"):
        (root / folder).mkdir(parents=True)
    (root / "pyproject.toml").write_text("# synthetic fixture\n")
    (root / plans.SCRIPT_PATH).write_text("# synthetic fixture\n")
    (root / "src/aletheia_lab/__init__.py").write_text("# synthetic fixture\n")
    for path in plans.INPUT_CONTRACTS:
        (root / path).write_bytes((REPO / path).read_bytes())
    protocol = json.loads((REPO / inventory.PROTOCOL_PATH).read_bytes())
    protocol["sources"] = [
        _archive(directory / "sources", spec, seed)
        for spec, seed in zip(protocol["sources"], (813, 127), strict=True)
    ]
    (root / inventory.PROTOCOL_PATH).write_bytes(json_bytes(protocol))
    monkeypatch.setattr(
        inventory, "FROZEN_PROTOCOL_SHA256", file_sha256(root / inventory.PROTOCOL_PATH)
    )
    monkeypatch.setattr(plans, "validate_loaded_code_root", lambda root: None)
    monkeypatch.setattr(
        plans, "_git_binding", lambda root: {"source_commit": "a" * 40, "working_tree_clean": True}
    )
    publish_json(
        directory / "inventory.json",
        inventory.audit_new_source_protocol(root=root, sources=directory / "sources"),
    )
    sealed = plans.prepare_study(root, directory)
    return root, directory, sealed["plan_sha256"]


def _unexpected(*args: Any, **kwargs: Any) -> Any:
    raise AssertionError("operation is forbidden in this test")


def _execute(prepared: tuple[Path, Path, str]) -> dict[str, Any]:
    root, directory, digest = prepared
    return study.execute_study(
        root, directory, confirm_plan_sha256=digest, authorize_final_execution=True
    )


def test_preparation_is_prediction_blind_idempotent_and_group_safe(
    prepared: tuple[Path, Path, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, directory, digest = prepared
    for name in ("_fit_reference_model", "fit_logit_calibration", "capture_evaluator_score_source"):
        monkeypatch.setattr(fitting, name, _unexpected)
    monkeypatch.setattr(cells, "match_target_swaps", _unexpected)
    monkeypatch.setattr(socket, "create_connection", _unexpected)
    assert plans.prepare_study(root, directory)["plan_sha256"] == digest
    assert plans.preflight_study(root, directory)["prospective_plan_sha256"] == digest
    plan, parsed = plans.bound_plan(root, directory)
    assert len(plan["cell_census"]) == 4 and plan["source_cluster_count"] == 2
    for data in parsed:
        assert data.group_ids[0] == data.group_ids[-1]
        assert data.targets[0] != data.targets[-1]
        assert next(name for name, indices in data.partitions.items() if 0 in indices) == next(
            name for name, indices in data.partitions.items() if len(data.targets) - 1 in indices
        )
        assert sum(len(indices) for indices in data.partitions.values()) == 801
    assert not (directory / plans.LEASE_FILE).exists()
    assert not (directory / "cells").exists()


@pytest.mark.parametrize(
    "drift",
    [
        "code",
        "protocol",
        "archive",
        "inventory",
        "runtime",
        "commit",
        "prompt",
        "dirty",
        "loaded_root",
    ],
)
def test_prepared_bindings_reject_drift_before_lease(
    prepared: tuple[Path, Path, str],
    monkeypatch: pytest.MonkeyPatch,
    drift: str,
) -> None:
    root, directory, _ = prepared
    if drift in ("code", "protocol", "prompt"):
        path = {
            "code": "src/aletheia_lab/__init__.py",
            "protocol": inventory.PROTOCOL_PATH,
            "prompt": plans.INPUT_CONTRACTS[0],
        }[drift]
        (root / path).write_bytes((root / path).read_bytes() + b" ")
    elif drift == "archive":
        (directory / "sources/htru2.zip").write_bytes(b"different")
    elif drift == "inventory":
        (directory / "inventory.json").write_text("{}")
    elif drift == "runtime":
        monkeypatch.setattr(plans, "runtime_binding", lambda: {"python": "different"})
    elif drift == "loaded_root":
        monkeypatch.setattr(plans, "validate_loaded_code_root", _unexpected)
    else:
        monkeypatch.setattr(
            plans,
            "_git_binding",
            lambda root: {
                "source_commit": "b" * 40 if drift == "commit" else "a" * 40,
                "working_tree_clean": drift != "dirty",
            },
        )
    with pytest.raises((ValueError, AssertionError)):
        _execute(prepared)
    assert not (directory / plans.LEASE_FILE).exists()


def test_missing_authority_and_wrong_hash_never_fit_or_lease(
    prepared: tuple[Path, Path, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, directory, digest = prepared
    monkeypatch.setattr(study, "run_cell", _unexpected)
    with pytest.raises(ProspectiveBindingError, match="U3"):
        study.execute_study(root, directory, confirm_plan_sha256=digest)
    with pytest.raises(ProspectiveBindingError, match="confirmation"):
        study.execute_study(
            root, directory, confirm_plan_sha256="0" * 64, authorize_final_execution=True
        )
    assert not (directory / plans.LEASE_FILE).exists()


def test_end_to_end_two_sources_two_estimators_and_replay_without_injector_or_search(
    prepared: tuple[Path, Path, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, directory, _ = prepared
    monkeypatch.setattr(socket, "create_connection", _unexpected)
    receipt = _execute(prepared)
    assert receipt["cell_status_counts"] == {"verified": 4}
    assert receipt["scientific_summary"]["g1_denominator"] == 4
    assert receipt["scientific_summary"]["g2_denominator"] == 4
    assert receipt["scientific_summary"]["estimator_family_count"] == 2
    assert receipt["mechanism_admitted"] is False and receipt["provider_calls"] == 0
    for name in (
        "apply_evaluator_mapping_fault",
        "match_target_swaps",
        "apply_paired_target_binding_fault",
    ):
        monkeypatch.setattr(cells, name, _unexpected)
    checked = study.verify_study(root, directory)
    assert checked["status"] == "new_source_verification_pass"
    assert checked["scientific_summary"] == receipt["scientific_summary"]
    before = deepcopy(receipt)
    with pytest.raises(ProspectiveBindingError, match="already leased"):
        _execute(prepared)
    assert json.loads((directory / plans.RECEIPT_FILE).read_bytes()) == before


@pytest.mark.parametrize(
    "artifact", ["result", "witness", "model", "calibration", "receipt", "lease", "symlink"]
)
def test_verification_rejects_tampered_artifacts(
    prepared: tuple[Path, Path, str],
    artifact: str,
) -> None:
    root, directory, _ = prepared
    _execute(prepared)
    cell_dir = directory / "cells/htru2-logistic_regression"
    path = {
        "result": cell_dir / "result.json",
        "witness": cell_dir / "source-witness.json",
        "model": cell_dir / "fitted_model.joblib",
        "calibration": cell_dir / "calibration.json",
        "receipt": directory / plans.RECEIPT_FILE,
        "lease": directory / plans.LEASE_FILE,
        "symlink": cell_dir / "result.json",
    }[artifact]
    if artifact == "symlink":
        path.unlink()
        path.symlink_to(directory / plans.PLAN_FILE)
    else:
        path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ValueError):
        study.verify_study(root, directory)


def test_rehashed_forged_score_source_is_rejected_by_model_replay(
    prepared: tuple[Path, Path, str],
) -> None:
    root, directory, _ = prepared
    _execute(prepared)
    path = directory / "cells/htru2-logistic_regression/source-witness.json"
    witness = json.loads(path.read_bytes())
    witness["raw_score_rows"][0] = [0.6, 0.4]
    path.write_bytes(json_bytes(witness))
    receipt = json.loads((directory / plans.RECEIPT_FILE).read_bytes())
    receipt["files_sha256"][path.relative_to(directory).as_posix()] = file_sha256(path)
    (directory / plans.RECEIPT_FILE).write_bytes(json_bytes(receipt))
    with pytest.raises(ValueError):
        study.verify_study(root, directory)


@pytest.mark.parametrize("dose", [2, 4, True, -1])
def test_runner_rejects_nonfrozen_doses(dose: int) -> None:
    with pytest.raises(ProspectiveBindingError, match="frozen dose"):
        cells._mapping_control({}, dose)


def test_failures_are_retained_in_fixed_census_and_do_not_admit_mechanism(
    prepared: tuple[Path, Path, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, directory, _ = prepared
    monkeypatch.setattr(study, "run_cell", _unexpected)
    receipt = _execute(prepared)
    assert receipt["cell_status_counts"] == {"runtime_failure": 4}
    assert receipt["scientific_summary"]["g1_denominator"] == 4
    assert receipt["scientific_summary"]["disposition"] == "incomplete"
    assert study.verify_study(root, directory)["status"] == "new_source_verification_pass"


def test_interruption_preserves_unexecuted_cells_and_consumed_lease(
    prepared: tuple[Path, Path, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, directory, _ = prepared

    def interrupted(*args: Any, **kwargs: Any) -> Any:
        raise KeyboardInterrupt

    monkeypatch.setattr(study, "_execute_cells", interrupted)
    receipt = _execute(prepared)
    assert receipt["cell_status_counts"] == {"not_executed": 4}
    assert receipt["status"] == "new_source_failed_closed"
    assert study.verify_study(root, directory)["status"] == "failure_receipt_verified"
    with pytest.raises(ValueError, match="already leased"):
        _execute(prepared)


def _observation() -> DevelopmentObservation:
    return DevelopmentObservation(
        40,
        0.5,
        0.6000001,
        (0, 1),
        (1, 0),
        (0.1, 0.9),
        0.1,
        0.9,
        True,
        0.5,
        2,
        80,
        0.1,
        "a" * 64,
        "b" * 64,
    )


def test_complete_sdk_input_boundary_and_rounding_crossing() -> None:
    mapping = _observation()
    rival = replace(
        mapping,
        observed_log_loss=0.6000002,
        evaluator_classes=(0, 1),
        example_observed_positive=0.9,
        target_binding_matches_source=False,
        corrected_log_loss=0.6000002,
        source_identity_sha256="c" * 64,
    )
    audit = wire.audit_reader_pair(REPO, mapping, rival)
    assert audit["wire_equal"] == {
        "full": False,
        "missing_key": True,
        "noisy": False,
        "misleading": False,
    }
    assert audit["twelve_decimal_missing_key_equal"] is False
    assert audit["raw_metric_equal"] is False
    payload = audit["wire_payloads"]["missing_key"][0]
    assert set(payload) == wire._WIRE_FIELDS - {"extra_headers"}
    assert len(payload["messages"]) == 2 and payload["temperature"] == 0
    assert payload["response_format"]["type"] == "json_schema"
    assert payload["response_format"]["json_schema"]["strict"] is True
    left = replace(mapping, observed_log_loss=0.60000049)
    right = replace(rival, observed_log_loss=0.60000051)
    assert abs(left.observed_log_loss - right.observed_log_loss) < 5e-7
    assert wire.audit_reader_pair(REPO, left, right)["wire_equal"]["missing_key"] is False


def test_weighted_loss_does_not_inherit_subset_bce_identity() -> None:
    labels, probabilities = (0, 0, 1), (0.1, 0.2, 0.8)
    faulty, swapped = (0.9, 0.2, 0.2), (1, 0, 0)

    def mean_bce(y: Any, p: Any) -> float:
        return sum(
            -math.log(value if label else 1 - value) for label, value in zip(y, p, strict=True)
        ) / len(y)

    assert mean_bce(labels, faulty) == pytest.approx(mean_bce(swapped, probabilities))
    mapping_loss = reference_prior_standardized_log_loss(true_labels=labels, probabilities=faulty)
    target_loss = reference_prior_standardized_log_loss(
        true_labels=swapped, probabilities=probabilities
    )
    assert mapping_loss == pytest.approx(1.4361511172941142)
    assert target_loss == pytest.approx(1.6094379124341003)
    assert mapping_loss != pytest.approx(target_loss)
    assert reference_prior_standardized_log_loss(
        true_labels=labels, probabilities=tuple(1 - p for p in probabilities)
    ) == pytest.approx(
        reference_prior_standardized_log_loss(
            true_labels=tuple(1 - y for y in labels), probabilities=probabilities
        )
    )


def test_cli_missing_authority_is_safe_and_network_incapable(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(REPO / plans.SCRIPT_PATH),
            "execute",
            "--root",
            str(REPO),
            "--study-dir",
            str(tmp_path),
            "--confirm-plan-sha256",
            "0" * 64,
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert json.loads(result.stdout)["status"] == "new_source_failed_closed"
    assert "explicit U3" in result.stdout
    assert not (tmp_path / plans.LEASE_FILE).exists()


@pytest.mark.parametrize("case", ["missing_inventory", "symlink_code", "dirty", "already_leased"])
def test_preparation_boundary_failures(
    prepared: tuple[Path, Path, str], monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    root, directory, _ = prepared
    if case == "missing_inventory":
        (directory / "inventory.json").unlink()
    elif case == "symlink_code":
        (root / "src/aletheia_lab/helper.py").symlink_to(root / "pyproject.toml")
    elif case == "dirty":
        monkeypatch.setattr(plans, "_git_binding", lambda root: {"working_tree_clean": False})
    else:
        (directory / plans.LEASE_FILE).write_text("{}")
    with pytest.raises(ValueError):
        plans.prepare_study(root, directory)


def test_actual_git_binding_and_package_origin(tmp_path: Path) -> None:
    observed = plans._git_binding(REPO)
    assert len(observed["source_commit"]) == 40
    assert type(observed["working_tree_clean"]) is bool
    plans.validate_loaded_code_root(REPO)
    with pytest.raises(ProspectiveBindingError, match="loaded package"):
        plans.validate_loaded_code_root(tmp_path)


@pytest.mark.parametrize(
    "healthy,faulty,footprint,status",
    [
        (0.5, 0.5, 2, "nonpositive_mapping_effect"),
        (0.5, 0.4, 2, "nonpositive_mapping_effect"),
        (0.5, 0.6, 1, "insufficient_mapping_footprint"),
    ],
)
def test_nonpositive_or_insufficient_effect_is_retained(
    healthy: float, faulty: float, footprint: int, status: str
) -> None:
    from types import SimpleNamespace

    checked = SimpleNamespace(
        healthy_log_loss=healthy,
        faulty_log_loss=faulty,
        affected_record_ids=tuple(str(index) for index in range(footprint)),
        changed_score_record_ids=("0",),
    )
    result = cells._rival(REPO, {}, None, checked)
    assert result == {"match_status": status, "g2_pass": False}
    replay._verify_rival(REPO, None, None, result, checked.__dict__, None)
    summary = study.scientific_summary(
        [
            {
                "cell_id": str(index),
                "status": "verified",
                "g1_pass": False,
                "g2_pass": False,
                "effect_direction": "flat",
                **result,
            }
            for index in range(4)
        ]
    )
    assert summary["g1_pass_count"] == summary["g2_pass_count"] == 0
    assert summary["disposition"] == "assumption_limited"
    assert summary["g1_denominator"] == summary["g2_denominator"] == 4


def test_unmatched_empty_rivals_keep_all_four_cells(
    prepared: tuple[Path, Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from aletheia_lab.benchmark.p2.score_mapping_symptom_matching import TargetSwapMatch

    def no_pairs(**kwargs: Any) -> TargetSwapMatch:
        loss = reference_prior_standardized_log_loss(
            true_labels=kwargs["targets"], probabilities=kwargs["probabilities"]
        )
        return TargetSwapMatch(
            tuple(zip(kwargs["record_ids"], kwargs["targets"], strict=True)),
            (),
            loss,
            abs(loss - kwargs["mapping_log_loss"]),
            False,
        )

    monkeypatch.setattr(cells, "match_target_swaps", no_pairs)
    root, directory, _ = prepared
    receipt = _execute(prepared)
    assert receipt["cell_status_counts"] == {"verified": 4}
    assert receipt["scientific_summary"]["g2_pass_count"] == 0
    assert receipt["match_status_counts"] == {"unmatched": 4}
    assert study.verify_study(root, directory)["status"] == "new_source_verification_pass"


@pytest.mark.parametrize("mutation", ["extra_field", "header"])
def test_transport_rejects_unclassified_channel(
    monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    original = wire._Capture.create

    def changed(self: Any, **kwargs: Any) -> object:
        response = original(self, **kwargs)
        self.calls[-1]["unknown"] = "extra" if mutation == "extra_field" else ""
        if mutation == "header":
            del self.calls[-1]["unknown"]
            self.calls[-1]["extra_headers"] = {"cause": "mapping"}
        return response

    monkeypatch.setattr(wire._Capture, "create", changed)
    with pytest.raises(wire.NewSourceWireError):
        wire.capture_reader_wire(REPO, _observation(), "full")


def test_receipt_disposition_cannot_be_rewritten_as_success(
    prepared: tuple[Path, Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, directory, _ = prepared
    monkeypatch.setattr(study, "run_cell", _unexpected)
    _execute(prepared)
    receipt = json.loads((directory / plans.RECEIPT_FILE).read_bytes())
    receipt["scientific_summary"]["disposition"] = "finite_controls_pass"
    (directory / plans.RECEIPT_FILE).write_bytes(json_bytes(receipt))
    with pytest.raises(ProspectiveBindingError, match="receipt claims"):
        study.verify_study(root, directory)


def test_invalid_and_unknown_cell_dispositions_are_not_passes(
    prepared: tuple[Path, Path, str],
) -> None:
    root, directory, _ = prepared
    _, data = plans.bound_plan(root, directory)
    base = {
        "cell_id": "htru2/logistic_regression",
        "dataset_id": "htru2",
        "model_kind": "logistic_regression",
        "g1_pass": False,
        "g2_pass": False,
    }
    cell_dir = directory / "unused"
    for status in ("invented", "runtime_failure", "ineligible_class_partition"):
        with pytest.raises(ValueError):
            replay.verify_cell(
                root, data[0], "logistic_regression", cell_dir, {**base, "status": status}
            )
    ineligible = replace(data[0], targets=tuple(0 for _ in data[0].targets))
    result = cells.run_cell(root, ineligible, "logistic_regression", cell_dir)
    assert result == {**base, "status": "ineligible_class_partition"}
    replay.verify_cell(root, ineligible, "logistic_regression", cell_dir, result)
