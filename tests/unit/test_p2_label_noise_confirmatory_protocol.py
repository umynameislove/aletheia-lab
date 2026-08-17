"""Contract tests for the outcome-free label-noise confirmatory protocol."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from aletheia_lab.benchmark.p2.confirmatory_protocol import (
    CONFIRMATORY_PROTOCOL_SCHEMA_VERSION,
    ConfirmatoryProtocol,
    ConfirmatoryProtocolError,
    load_confirmatory_protocol,
    verify_confirmatory_predecessor,
)

_PROTOCOL_PATH = Path("configs/benchmark/p2_label_noise_confirmatory_protocol.json")
_EXPECTED_PROTOCOL_SHA256 = "09dd4124eaf54a11c7f4b30d23c4e1369ebca77e3335ffbefc4bd3034b3d53a1"


def _payload() -> dict[str, object]:
    value = json.loads(_PROTOCOL_PATH.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _validate(payload: dict[str, object]) -> ConfirmatoryProtocol:
    return ConfirmatoryProtocol.model_validate_json(json.dumps(payload))


def test_frozen_protocol_loads_and_has_stable_canonical_hash() -> None:
    protocol = load_confirmatory_protocol()

    assert protocol.schema_version == CONFIRMATORY_PROTOCOL_SCHEMA_VERSION
    assert protocol.status == "frozen_not_executed"
    assert protocol.canonical_sha256() == _EXPECTED_PROTOCOL_SHA256
    assert protocol.predecessor.alpha_is_immutable
    assert protocol.predecessor.alpha_gate_status == "fail"


def test_protocol_binds_the_existing_alpha_store_and_recovery_report() -> None:
    protocol = load_confirmatory_protocol()
    alpha_manifest = json.loads(
        Path("experiments/p2/runs/alpha-primary-seed42/store-manifest.json").read_text(
            encoding="utf-8"
        )
    )
    recovery_report = Path("docs/p2-label-noise-recovery.md").read_bytes()
    baseline_provenance = json.loads(
        Path("experiments/baseline/runs/logistic_regression_seed42/provenance.json").read_text(
            encoding="utf-8"
        )
    )

    assert protocol.predecessor.alpha_store_sha256 == alpha_manifest["store_sha256"]
    assert protocol.predecessor.recovery_report_sha256 == hashlib.sha256(
        recovery_report
    ).hexdigest()
    assert protocol.datasets[0].snapshot_sha256 == baseline_provenance["dataset_sha256"]
    verify_confirmatory_predecessor(protocol)


def test_complete_grid_has_two_directions_three_doses_and_fixed_replicates() -> None:
    protocol = load_confirmatory_protocol()

    assert tuple(
        (cell.flip_direction, cell.conditional_flip_rate)
        for cell in protocol.intervention_cells
    ) == tuple(
        (direction, rate)
        for direction in ("yes_to_no", "no_to_yes")
        for rate in (0.1, 0.2, 0.3)
    )
    assert {
        cell.flip_direction
        for cell in protocol.intervention_cells
        if cell.hypothesis_role == "co_primary"
    } == {"yes_to_no", "no_to_yes"}
    assert all(
        len(cell.primary_replicate_seeds)
        == len(cell.replication_replicate_seeds)
        == protocol.inference.replicate_count_per_cell
        for cell in protocol.intervention_cells
    )


def test_primary_endpoint_is_proper_score_and_accuracy_is_only_legacy_comparator() -> None:
    protocol = load_confirmatory_protocol()

    assert protocol.endpoints.primary_metric == "clean_test_log_loss"
    assert protocol.endpoints.minimum_practical_effect == 0.05
    assert protocol.endpoints.legacy_comparator == "accuracy_delta_threshold_0.01"
    assert protocol.endpoints.secondary_metrics_cannot_rescue_primary


def test_external_replication_cannot_rescue_primary_or_change_claim_direction() -> None:
    protocol = load_confirmatory_protocol()

    assert protocol.datasets[1].role == "external_replication"
    assert protocol.decision.external_replication_cannot_rescue_primary
    assert protocol.decision.cross_dataset_claim_requires_same_direction_to_pass_both
    assert protocol.decision.failed_primary_action == "retain_fail_closed_and_narrow_claim"


def test_execution_requires_an_immutable_protocol_registration() -> None:
    protocol = load_confirmatory_protocol()

    assert protocol.governance.protocol_only_commit_required
    assert protocol.governance.required_git_tag == "p2-label-noise-confirmatory-v1"
    assert protocol.governance.immutable_release_or_external_timestamp_required
    assert protocol.governance.execution_before_registration_forbidden
    assert protocol.governance.changes_require_new_protocol_version
    assert protocol.governance.primary_and_replication_outputs_released_together


@pytest.mark.parametrize(
    ("section", "field", "replacement", "message"),
    [
        ("predecessor", "alpha_is_immutable", False, "literal_error"),
        ("endpoints", "primary_metric", "accuracy", "literal_error"),
        ("endpoints", "secondary_metrics_cannot_rescue_primary", False, "literal_error"),
        ("inference", "no_early_stopping", False, "literal_error"),
        ("inference", "replicate_count_per_cell", 5, "literal_error"),
        ("decision", "external_replication_cannot_rescue_primary", False, "literal_error"),
        ("decision", "additional_grid_after_results_forbidden", False, "literal_error"),
        ("governance", "execution_before_registration_forbidden", False, "literal_error"),
    ],
)
def test_frozen_safeguards_cannot_be_relaxed(
    section: str, field: str, replacement: object, message: str
) -> None:
    payload = _payload()
    nested = payload[section]
    assert isinstance(nested, dict)
    nested[field] = replacement

    with pytest.raises(ValidationError) as error:
        _validate(payload)

    assert message in str(error.value)


def test_outcome_fields_are_rejected_instead_of_becoming_part_of_preregistration() -> None:
    payload = _payload()
    payload["observed_primary_effect"] = 0.12

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        _validate(payload)


def test_partial_grid_is_rejected() -> None:
    payload = _payload()
    cells = payload["intervention_cells"]
    assert isinstance(cells, list)
    cells.pop()

    with pytest.raises(ValidationError, match="complete canonical direction-by-dose grid"):
        _validate(payload)


def test_post_outcome_rate_expansion_is_rejected() -> None:
    payload = _payload()
    cells = payload["intervention_cells"]
    assert isinstance(cells, list)
    cell = cells[0]
    assert isinstance(cell, dict)
    cell["conditional_flip_rate"] = 0.4

    with pytest.raises(ValidationError, match="rates are frozen"):
        _validate(payload)


def test_best_seed_selection_is_rejected() -> None:
    payload = _payload()
    cells = payload["intervention_cells"]
    assert isinstance(cells, list)
    cell = cells[0]
    assert isinstance(cell, dict)
    seeds = cell["primary_replicate_seeds"]
    assert isinstance(seeds, list)
    seeds.pop(0)

    with pytest.raises(ValidationError, match="complete frozen set"):
        _validate(payload)


def test_lower_dose_cannot_be_promoted_to_co_primary() -> None:
    payload = _payload()
    cells = payload["intervention_cells"]
    assert isinstance(cells, list)
    cell = cells[0]
    assert isinstance(cell, dict)
    cell["hypothesis_role"] = "co_primary"

    with pytest.raises(ValidationError, match="30% cells are co-primary"):
        _validate(payload)


def test_transfer_dataset_is_fixed_and_must_exclude_post_call_feature() -> None:
    payload = _payload()
    datasets = payload["datasets"]
    assert isinstance(datasets, list)
    transfer = datasets[1]
    assert isinstance(transfer, dict)
    transfer["excluded_features"] = []

    with pytest.raises(ValidationError, match="duration feature must be excluded"):
        _validate(payload)


def test_temporal_transfer_split_cannot_be_randomized() -> None:
    payload = _payload()
    datasets = payload["datasets"]
    assert isinstance(datasets, list)
    transfer = datasets[1]
    assert isinstance(transfer, dict)
    split = transfer["split"]
    assert isinstance(split, dict)
    split["seed"] = 42

    with pytest.raises(ValidationError, match="must not use a random seed"):
        _validate(payload)


def test_control_removal_is_rejected() -> None:
    payload = _payload()
    controls = payload["controls"]
    assert isinstance(controls, dict)
    control_ids = controls["control_ids"]
    assert isinstance(control_ids, list)
    control_ids.pop()

    with pytest.raises(ValidationError, match="complete ordered control set"):
        _validate(payload)


def test_single_factor_bootstrap_is_rejected_as_pseudoreplication() -> None:
    payload = _payload()
    inference = payload["inference"]
    assert isinstance(inference, dict)
    inference["bootstrap_factors"] = ["corruption_seed"]

    with pytest.raises(ValidationError, match="records and corruption seeds"):
        _validate(payload)


def test_loader_wraps_missing_and_invalid_protocols(tmp_path: Path) -> None:
    with pytest.raises(ConfirmatoryProtocolError, match="cannot read"):
        load_confirmatory_protocol(tmp_path / "missing.json")

    invalid = tmp_path / "invalid.json"
    invalid.write_text("{}", encoding="utf-8")
    with pytest.raises(ConfirmatoryProtocolError, match="frozen contract"):
        load_confirmatory_protocol(invalid)

    with pytest.raises(ConfirmatoryProtocolError, match="predecessor artifacts"):
        verify_confirmatory_predecessor(load_confirmatory_protocol(), root=tmp_path)
