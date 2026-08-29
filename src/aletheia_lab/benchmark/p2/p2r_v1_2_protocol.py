"""Outcome-blind P2R v1.2 methodological-amendment contracts.

The retired v1.1 attempt exposed a protocol-feasibility defect: the registered
``VisitorType`` intervention could not deliver the declared dose on the
replication partition.  This module does not repair or replay that attempt.  It
defines a prospective wrapper that binds the disclosed failure, the complete
covariate-only feasibility census, and the unchanged scientific sections of
the original lightweight protocols.

Only the target feature selected by the frozen outcome-blind maximin-support
rule may change.  Models, seeds, endpoints, thresholds, exclusions,
dispositions, dataset identities, and split memberships remain inherited by
content hash from v1.
"""

from __future__ import annotations

import hashlib
import math
from pathlib import Path
from typing import Final, Literal, NoReturn

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.benchmark.p2.identity import SHA256_PATTERN
from aletheia_lab.benchmark.p2.lightweight_protocol import (
    LightweightConfirmatoryProtocol,
)
from aletheia_lab.benchmark.p2.p2r_recovery_protocol import (
    P2RRecoveryProtocol,
    load_p2r_recovery_protocol,
    verify_p2r_recovery_protocol,
)
from aletheia_lab.benchmark.p2.p2r_replication_failure import (
    CAPACITY_RESERVE,
    DECLARED_MAGNITUDE,
    P2RInterventionFeasibilityReceipt,
    P2RV11ReplicationFailureAudit,
    load_intervention_feasibility_receipt,
    load_p2r_v1_1_replication_failure_audit,
)

P2R_V1_2_PROTOCOL_SCHEMA_VERSION: Final[Literal["p2r-lightweight-confirmatory-amendment/1"]] = (
    "p2r-lightweight-confirmatory-amendment/1"
)
P2R_V1_2_DATASET_SCHEMA_VERSION: Final[Literal["p2r-dataset-methodological-amendment/1"]] = (
    "p2r-dataset-methodological-amendment/1"
)

DEFAULT_DATA_DRIFT_V1_2_PROTOCOL_PATH = Path(
    "configs/benchmark/p2r_data_drift_confirmatory_v1_2_protocol.json"
)
DEFAULT_PREPROCESSING_V1_2_PROTOCOL_PATH = Path(
    "configs/benchmark/p2r_preprocessing_confirmatory_v1_2_protocol.json"
)

P2R_V1_2_AMENDMENT_IMPLEMENTATION_COMMIT: Final[str] = "0b275d1f120ff9b982fc938055fba12fa186a315"
P2R_V1_2_AMENDMENT_IMPLEMENTATION_FILE_SHA256: Final[str] = (
    "0cdca79bbafdfa8d4de8d0c3aeb681b446a626524ffe293b5b34caaf780d3c54"
)
P2R_V1_1_FEASIBILITY_SHA256: Final[str] = (
    "2234d0c7bf9b1a35e971792a34134c3917ce35b8ff6410afec8f64625f673c13"
)
P2R_V1_1_FEASIBILITY_FILE_SHA256: Final[str] = (
    "b293e620b46578e8685febcf7322bf34716b3b13ef43543929b1aaadadf98574"
)
P2R_V1_1_FAILURE_AUDIT_SHA256: Final[str] = (
    "43ac081372120a62b949b3dd732c38a50bf21e2ee0e9ba8086bd7bd4438f5c13"
)
P2R_V1_1_FAILURE_AUDIT_FILE_SHA256: Final[str] = (
    "703d85910fd500a5b80a221eff71d4724517b61465ae8ff46e30f7e7618a3ad8"
)
P2R_V1_1_TERMINAL_STORE_SHA256: Final[str] = (
    "aafbecaaab43dddad538cf23a66190ca2b71c1a573ed04c7232db14105e12a53"
)

_SHARED_SECTION_SHA256S: Final[dict[str, str]] = {
    "artifacts": "d3745938d0ee15035673f57c129bb5df5ee9298324018cca78f972423852b477",
    "model": "06925b6f37f464d610516bce6f780985bac022806b208767d7143db0e8e89363",
    "endpoint": "5fa1b5fc421b23e4aa8f2fb5273426a6d50a74f7f2f3d502de66db8c9f074110",
    "execution": "90f06aaab818d36736a8a2c241ee2e06ac73834303dac18e82d10c92f6690b15",
    "exclusions": "185520a82fad5a82c2eb6b841719cf4fd6f153d54126bd743eef33b6723a088e",
    "dispositions": "54927228072e7ed6322e29843848624dfeab892566f09eb4f6b23eb7db516eba",
    "governance": "cc65d42588ee7166a3399e0f098d5da6484baa8d88f33ee9075ac7edf67515ef",
}

MechanismName = Literal["data_drift", "preprocessing_bug"]
DatasetRole = Literal["primary", "external_replication"]
Sha256 = str


class P2RV12ProtocolError(ValueError):
    """Raised when the v1.2 amendment exceeds its outcome-blind scope."""


def _fail(message: str) -> NoReturn:
    raise P2RV12ProtocolError(message)


def _file_sha256(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        _fail(f"bound v1.2 artifact is not a regular file: {path}")
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise P2RV12ProtocolError(f"cannot hash bound v1.2 artifact: {path}") from exc
    return digest.hexdigest()


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, allow_inf_nan=False)


class P2RV12ArtifactBindings(_StrictFrozenModel):
    scientific_protocol_uri: str
    scientific_protocol_file_sha256: Sha256 = Field(pattern=SHA256_PATTERN)
    scientific_protocol_sha256: Sha256 = Field(pattern=SHA256_PATTERN)
    recovery_protocol_uri: str
    recovery_protocol_file_sha256: Sha256 = Field(pattern=SHA256_PATTERN)
    recovery_protocol_sha256: Sha256 = Field(pattern=SHA256_PATTERN)
    failure_audit_uri: Literal[
        "configs/benchmark/provenance/p2r_v1_1_replication_failure_audit.json"
    ]
    failure_audit_file_sha256: Sha256 = Field(pattern=SHA256_PATTERN)
    failure_audit_sha256: Sha256 = Field(pattern=SHA256_PATTERN)
    feasibility_receipt_uri: Literal[
        "configs/benchmark/provenance/p2r_v1_1_intervention_feasibility.json"
    ]
    feasibility_receipt_file_sha256: Sha256 = Field(pattern=SHA256_PATTERN)
    feasibility_receipt_sha256: Sha256 = Field(pattern=SHA256_PATTERN)
    amendment_implementation_uri: Literal[
        "src/aletheia_lab/benchmark/p2/p2r_replication_failure.py"
    ]
    amendment_implementation_file_sha256: Sha256 = Field(pattern=SHA256_PATTERN)
    amendment_implementation_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    predecessor_terminal_store_sha256: Sha256 = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def _shared_evidence_is_exact(self) -> P2RV12ArtifactBindings:
        observed = (
            self.failure_audit_file_sha256,
            self.failure_audit_sha256,
            self.feasibility_receipt_file_sha256,
            self.feasibility_receipt_sha256,
            self.amendment_implementation_file_sha256,
            self.amendment_implementation_commit,
            self.predecessor_terminal_store_sha256,
        )
        expected = (
            P2R_V1_1_FAILURE_AUDIT_FILE_SHA256,
            P2R_V1_1_FAILURE_AUDIT_SHA256,
            P2R_V1_1_FEASIBILITY_FILE_SHA256,
            P2R_V1_1_FEASIBILITY_SHA256,
            P2R_V1_2_AMENDMENT_IMPLEMENTATION_FILE_SHA256,
            P2R_V1_2_AMENDMENT_IMPLEMENTATION_COMMIT,
            P2R_V1_1_TERMINAL_STORE_SHA256,
        )
        if observed != expected:
            raise ValueError("v1.2 binds evidence outside the disclosed amendment chain")
        return self


class P2RV12DatasetAmendment(_StrictFrozenModel):
    schema_version: Literal["p2r-dataset-methodological-amendment/1"] = (
        P2R_V1_2_DATASET_SCHEMA_VERSION
    )
    dataset_id: Literal[
        "uci_default_of_credit_card_clients",
        "uci_online_shoppers_purchasing_intention",
    ]
    role: DatasetRole
    split_membership_sha256: Sha256 = Field(pattern=SHA256_PATTERN)
    sealed_membership_sha256: Sha256 = Field(pattern=SHA256_PATTERN)
    feasibility_census_sha256: Sha256 = Field(pattern=SHA256_PATTERN)
    predecessor_target_feature: Literal["EDUCATION", "VisitorType"]
    selected_target_feature: Literal["EDUCATION", "OperatingSystems"]
    target_changed: bool
    selection_reason: Literal[
        "frozen_target_retained",
        "frozen_target_infeasible_capacity_selected_without_outcomes",
    ]
    intervention_rule: str = Field(min_length=1)
    nuisance_comparator: str = Field(min_length=1)
    sealed_record_count: int = Field(gt=0)
    target_row_count: int = Field(gt=0)
    reserve_row_count: int = Field(ge=0)
    data_drift_capacity_count: int = Field(ge=0)
    preprocessing_capacity_count: int = Field(ge=0)
    minimum_capacity_count: int = Field(ge=0)
    jointly_feasible_with_reserve: Literal[True]
    selected_feature_capacity_sha256: Sha256 = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def _dose_and_capacity_are_derived(self) -> P2RV12DatasetAmendment:
        target = math.floor(DECLARED_MAGNITUDE * self.sealed_record_count)
        reserve = math.ceil(CAPACITY_RESERVE * self.sealed_record_count)
        minimum = min(
            self.data_drift_capacity_count,
            self.preprocessing_capacity_count,
        )
        if (
            self.target_row_count,
            self.reserve_row_count,
            self.minimum_capacity_count,
        ) != (target, reserve, minimum):
            raise ValueError("v1.2 dose or capacity is not derived from the sealed census")
        if minimum < target + reserve:
            raise ValueError("v1.2 selected target lacks prespecified bidirectional reserve")
        if self.target_changed != (self.predecessor_target_feature != self.selected_target_feature):
            raise ValueError("v1.2 target-change flag is not derived")
        expected_identity = {
            "uci_default_of_credit_card_clients": (
                "primary",
                "EDUCATION",
                "EDUCATION",
                False,
                "frozen_target_retained",
            ),
            "uci_online_shoppers_purchasing_intention": (
                "external_replication",
                "VisitorType",
                "OperatingSystems",
                True,
                "frozen_target_infeasible_capacity_selected_without_outcomes",
            ),
        }
        observed = (
            self.role,
            self.predecessor_target_feature,
            self.selected_target_feature,
            self.target_changed,
            self.selection_reason,
        )
        if observed != expected_identity[self.dataset_id]:
            raise ValueError("v1.2 dataset amendment differs from the outcome-blind selection")
        return self


class P2RV12ScientificInvariants(_StrictFrozenModel):
    inherited_artifacts_sha256: Sha256 = Field(pattern=SHA256_PATTERN)
    inherited_model_sha256: Sha256 = Field(pattern=SHA256_PATTERN)
    inherited_endpoint_sha256: Sha256 = Field(pattern=SHA256_PATTERN)
    inherited_execution_sha256: Sha256 = Field(pattern=SHA256_PATTERN)
    inherited_exclusions_sha256: Sha256 = Field(pattern=SHA256_PATTERN)
    inherited_dispositions_sha256: Sha256 = Field(pattern=SHA256_PATTERN)
    inherited_governance_sha256: Sha256 = Field(pattern=SHA256_PATTERN)
    declared_manipulation_magnitude: float
    minimum_capacity_reserve: float
    target_selection_policy: Literal[
        "retain_if_jointly_feasible_with_reserve_else_maximize_minimum_capacity_then_manifest_order_v1"
    ]
    allowed_changes: tuple[
        Literal[
            "outcome_blind_target_feature_selected_by_frozen_feasibility_rule",
            "structured_bidirectional_dose_and_reserve_binding",
            "permanent_v1_1_execution_retirement",
            "protocol_schema_tag_release_registration_and_store_identity",
        ],
        ...,
    ]
    datasets_or_roles_changed: Literal[False]
    split_or_sealed_membership_changed: Literal[False]
    model_or_preprocessing_changed: Literal[False]
    seeds_or_candidate_count_changed: Literal[False]
    intervention_dose_changed: Literal[False]
    nuisance_comparator_semantics_changed: Literal[False]
    endpoint_estimand_metric_or_threshold_changed: Literal[False]
    exclusion_or_disposition_rule_changed: Literal[False]
    target_feature_changed_under_frozen_rule: Literal[True]
    scientific_semantics_changed_and_disclosed: Literal[True]
    predictive_outcomes_used_for_selection_or_tuning: Literal[False]

    @model_validator(mode="after")
    def _delta_is_exact(self) -> P2RV12ScientificInvariants:
        section_hashes = (
            self.inherited_artifacts_sha256,
            self.inherited_model_sha256,
            self.inherited_endpoint_sha256,
            self.inherited_execution_sha256,
            self.inherited_exclusions_sha256,
            self.inherited_dispositions_sha256,
            self.inherited_governance_sha256,
        )
        if section_hashes != tuple(_SHARED_SECTION_SHA256S.values()):
            raise ValueError("v1.2 changes an inherited scientific section")
        if not math.isclose(
            self.declared_manipulation_magnitude,
            DECLARED_MAGNITUDE,
            rel_tol=0.0,
            abs_tol=1e-15,
        ) or not math.isclose(
            self.minimum_capacity_reserve,
            CAPACITY_RESERVE,
            rel_tol=0.0,
            abs_tol=1e-15,
        ):
            raise ValueError("v1.2 changes the declared dose or prospective reserve")
        expected_changes = (
            "outcome_blind_target_feature_selected_by_frozen_feasibility_rule",
            "structured_bidirectional_dose_and_reserve_binding",
            "permanent_v1_1_execution_retirement",
            "protocol_schema_tag_release_registration_and_store_identity",
        )
        if self.allowed_changes != expected_changes:
            raise ValueError("v1.2 contains an undeclared methodological change")
        return self


class P2RV12Governance(_StrictFrozenModel):
    required_git_tag: str = Field(min_length=1)
    protocol_only_commit_required: Literal[True]
    immutable_release_required_before_registration: Literal[True]
    immutable_release_required_before_execution: Literal[True]
    amendment_implementation_must_predate_registration: Literal[True]
    failure_audit_and_feasibility_receipt_must_remain_published: Literal[True]
    changes_after_registration_require_new_protocol_version: Literal[True]
    paired_mechanism_outcomes_released_together: Literal[True]
    one_sealed_open_marker_for_both_mechanisms: Literal[True]
    maximum_registered_execution_attempts: Literal[1]
    predecessor_v1_1_rerun_forbidden: Literal[True]
    same_previously_opened_named_partitions_reused: Literal[True]
    independent_new_dataset_replication: Literal[False]
    claim_scope: Literal["named_dataset_methodological_amendment_bounded"]
    registration_authorized_by_this_file: Literal[False]
    execution_authorized_by_this_file: Literal[False]


class P2RV12MethodologicalAmendmentProtocol(_StrictFrozenModel):
    """One mechanism-specific prospective v1.2 protocol wrapper."""

    schema_version: Literal["p2r-lightweight-confirmatory-amendment/1"] = (
        P2R_V1_2_PROTOCOL_SCHEMA_VERSION
    )
    protocol_version: str = Field(min_length=1)
    status: Literal["methodological_amendment_candidate_not_registered"]
    mechanism: MechanismName
    artifacts: P2RV12ArtifactBindings
    datasets: tuple[P2RV12DatasetAmendment, P2RV12DatasetAmendment]
    scientific_invariants: P2RV12ScientificInvariants
    governance: P2RV12Governance
    outcome_fields_forbidden: Literal[True]
    model_fitted: Literal[False]
    predictive_metrics_generated: Literal[False]
    sealed_outcomes_generated: Literal[False]

    @model_validator(mode="after")
    def _mechanism_identity_is_exact(self) -> P2RV12MethodologicalAmendmentProtocol:
        expected = {
            "data_drift": (
                "p2r-data_drift-confirmatory/1.2",
                "p2r-data-drift-confirmatory-v1.2",
                "configs/benchmark/p2r_data_drift_confirmatory_protocol.json",
                "00411d49762bd29d1fe1b1304757c08249c8050510b67e0ae35ae8362bec7103",
                "bad097a4298f7925b314f049a762da2f0e4485a24f40860d667ae936b422c289",
                "configs/benchmark/p2r_data_drift_confirmatory_v1_1_protocol.json",
                "0cead30921847865d72a3920f48e7ea11e7eb6014935775d14dffb94525a0239",
                "e9d9dd57f3e92a0825631c11dbf2d570b01a993a04757c8e08a503f1c76c0003",
            ),
            "preprocessing_bug": (
                "p2r-preprocessing_bug-confirmatory/1.2",
                "p2r-preprocessing-mismatch-confirmatory-v1.2",
                "configs/benchmark/p2r_preprocessing_confirmatory_protocol.json",
                "9b81d608df02e402760217fd1d8f1b891dd0dd24b9a93d9a5d9396ba65fe2802",
                "4fcca028153fce45098e8547608d16231c33f9a78cdc243ff9931d119eca4904",
                "configs/benchmark/p2r_preprocessing_confirmatory_v1_1_protocol.json",
                "873e93ae1ab0a76f77cbb020bfd8eaa23aa879c798f6f349a437e978f076c8b8",
                "4a166b04da1b801af6d625703a900d542dc66d001c88a486a0a8984c792230f2",
            ),
        }
        observed = (
            self.protocol_version,
            self.governance.required_git_tag,
            self.artifacts.scientific_protocol_uri,
            self.artifacts.scientific_protocol_file_sha256,
            self.artifacts.scientific_protocol_sha256,
            self.artifacts.recovery_protocol_uri,
            self.artifacts.recovery_protocol_file_sha256,
            self.artifacts.recovery_protocol_sha256,
        )
        if observed != expected[self.mechanism]:
            raise ValueError("v1.2 mechanism identity differs from its predecessor chain")
        if tuple((item.dataset_id, item.role) for item in self.datasets) != (
            ("uci_default_of_credit_card_clients", "primary"),
            ("uci_online_shoppers_purchasing_intention", "external_replication"),
        ):
            raise ValueError("v1.2 changes the ordered dataset-role census")
        return self

    def canonical_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))


def load_p2r_v1_2_protocol(
    path: str | Path,
) -> P2RV12MethodologicalAmendmentProtocol:
    target = Path(path)
    if target.is_symlink() or not target.is_file():
        _fail("P2R v1.2 protocol is unavailable or invalid")
    try:
        return P2RV12MethodologicalAmendmentProtocol.model_validate_json(
            target.read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as exc:
        raise P2RV12ProtocolError("P2R v1.2 protocol is unavailable or invalid") from exc


def _verify_inherited_sections(protocol: LightweightConfirmatoryProtocol) -> None:
    observed = {
        section: canonical_sha256(getattr(protocol, section).model_dump(mode="json"))
        for section in _SHARED_SECTION_SHA256S
    }
    if observed != _SHARED_SECTION_SHA256S:
        _fail("v1.2 predecessor scientific sections differ from the frozen inheritance")


def _verify_dataset_amendments(
    protocol: P2RV12MethodologicalAmendmentProtocol,
    predecessor: LightweightConfirmatoryProtocol,
    feasibility: P2RInterventionFeasibilityReceipt,
) -> None:
    for amendment, prior, census in zip(
        protocol.datasets,
        predecessor.datasets,
        feasibility.datasets,
        strict=True,
    ):
        selected = next(
            item for item in census.capacities if item.feature == census.selected_target_feature
        )
        expected = (
            census.dataset_id,
            census.dataset_role,
            prior.split_membership_sha256,
            prior.sealed_membership_sha256,
            census.census_sha256,
            prior.target_feature,
            census.selected_target_feature,
            prior.target_feature != census.selected_target_feature,
            census.selection_reason,
            prior.intervention_rule,
            prior.nuisance_comparator,
            selected.sealed_record_count,
            selected.target_row_count,
            selected.reserve_row_count,
            selected.data_drift_capacity_count,
            selected.preprocessing_capacity_count,
            selected.minimum_capacity_count,
            selected.jointly_feasible_with_reserve,
            selected.capacity_sha256,
        )
        observed = (
            amendment.dataset_id,
            amendment.role,
            amendment.split_membership_sha256,
            amendment.sealed_membership_sha256,
            amendment.feasibility_census_sha256,
            amendment.predecessor_target_feature,
            amendment.selected_target_feature,
            amendment.target_changed,
            amendment.selection_reason,
            amendment.intervention_rule,
            amendment.nuisance_comparator,
            amendment.sealed_record_count,
            amendment.target_row_count,
            amendment.reserve_row_count,
            amendment.data_drift_capacity_count,
            amendment.preprocessing_capacity_count,
            amendment.minimum_capacity_count,
            amendment.jointly_feasible_with_reserve,
            amendment.selected_feature_capacity_sha256,
        )
        if observed != expected:
            _fail("v1.2 dataset amendment does not reproduce the frozen feasibility receipt")


def verify_p2r_v1_2_protocol(
    protocol: P2RV12MethodologicalAmendmentProtocol,
    *,
    root: str | Path = ".",
) -> tuple[
    P2RV12MethodologicalAmendmentProtocol,
    LightweightConfirmatoryProtocol,
    P2RRecoveryProtocol,
    P2RV11ReplicationFailureAudit,
    P2RInterventionFeasibilityReceipt,
]:
    """Verify the full predecessor chain without opening predictive outcomes."""

    checked = P2RV12MethodologicalAmendmentProtocol.model_validate(protocol.model_dump())
    repository = Path(root).resolve()
    bindings = checked.artifacts
    scientific_path = repository / bindings.scientific_protocol_uri
    recovery_path = repository / bindings.recovery_protocol_uri
    audit_path = repository / bindings.failure_audit_uri
    feasibility_path = repository / bindings.feasibility_receipt_uri
    implementation_path = repository / bindings.amendment_implementation_uri

    recovery, predecessor, _ = verify_p2r_recovery_protocol(
        load_p2r_recovery_protocol(recovery_path),
        root=repository,
    )
    audit = load_p2r_v1_1_replication_failure_audit(audit_path)
    feasibility = load_intervention_feasibility_receipt(feasibility_path)
    observed_bindings = (
        (_file_sha256(scientific_path), bindings.scientific_protocol_file_sha256),
        (predecessor.canonical_sha256(), bindings.scientific_protocol_sha256),
        (_file_sha256(recovery_path), bindings.recovery_protocol_file_sha256),
        (recovery.canonical_sha256(), bindings.recovery_protocol_sha256),
        (_file_sha256(audit_path), bindings.failure_audit_file_sha256),
        (audit.canonical_sha256(), bindings.failure_audit_sha256),
        (_file_sha256(feasibility_path), bindings.feasibility_receipt_file_sha256),
        (feasibility.canonical_sha256(), bindings.feasibility_receipt_sha256),
        (_file_sha256(implementation_path), bindings.amendment_implementation_file_sha256),
        (audit.terminal_store_sha256, bindings.predecessor_terminal_store_sha256),
    )
    if any(actual != expected for actual, expected in observed_bindings):
        _fail("v1.2 artifact binding does not reproduce")
    if predecessor.mechanism != checked.mechanism or recovery.mechanism != checked.mechanism:
        _fail("v1.2 mechanism differs from its predecessor chain")
    if (
        audit.canonical_sha256() != P2R_V1_1_FAILURE_AUDIT_SHA256
        or feasibility.canonical_sha256() != P2R_V1_1_FEASIBILITY_SHA256
        or audit.terminal_store_sha256 != P2R_V1_1_TERMINAL_STORE_SHA256
    ):
        _fail("v1.2 does not bind the disclosed v1.1 evidence")
    if (
        not audit.rerun_forbidden
        or not audit.v1_1_attempt_retired
        or not audit.protocol_feasibility_defect
        or audit.scientific_negative_result
        or audit.predictive_outcomes_inspected_for_repair
        or audit.scientific_disposition_generated
    ):
        _fail("v1.1 audit does not authorize an outcome-blind methodological successor")
    if (
        feasibility.target_values_used_for_capacity_or_selection
        or feasibility.model_fitted
        or feasibility.predictive_metrics_generated
        or not feasibility.generated_after_retired_v1_1_failure
    ):
        _fail("v1.2 feasibility evidence crossed the outcome-blind boundary")
    _verify_inherited_sections(predecessor)
    _verify_dataset_amendments(checked, predecessor, feasibility)
    return checked, predecessor, recovery, audit, feasibility


def verify_p2r_v1_2_protocol_pair(
    data_drift: P2RV12MethodologicalAmendmentProtocol,
    preprocessing: P2RV12MethodologicalAmendmentProtocol,
    *,
    root: str | Path = ".",
) -> tuple[P2RV12MethodologicalAmendmentProtocol, P2RV12MethodologicalAmendmentProtocol]:
    drift, _, _, drift_audit, drift_feasibility = verify_p2r_v1_2_protocol(data_drift, root=root)
    prep, _, _, prep_audit, prep_feasibility = verify_p2r_v1_2_protocol(preprocessing, root=root)
    if (drift.mechanism, prep.mechanism) != ("data_drift", "preprocessing_bug"):
        _fail("v1.2 pair must cover both mechanisms exactly once")
    if drift_audit != prep_audit or drift_feasibility != prep_feasibility:
        _fail("v1.2 pair must bind one shared failure audit and feasibility receipt")
    if drift.scientific_invariants != prep.scientific_invariants:
        _fail("v1.2 pair must preserve one shared scientific delta")
    drift_targets = tuple(
        (item.dataset_id, item.selected_target_feature) for item in drift.datasets
    )
    prep_targets = tuple((item.dataset_id, item.selected_target_feature) for item in prep.datasets)
    if drift_targets != prep_targets:
        _fail("v1.2 mechanisms must use the same outcome-blind target selection")
    if drift.canonical_sha256() == prep.canonical_sha256():
        _fail("v1.2 mechanism protocols must have distinct identities")
    return drift, prep
