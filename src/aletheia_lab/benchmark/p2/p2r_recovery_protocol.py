"""Outcome-blind paired protocol for the disclosed P2R v1 technical recovery."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Final, Literal, NoReturn

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.benchmark.p2.identity import SHA256_PATTERN
from aletheia_lab.benchmark.p2.lightweight_protocol import (
    LightweightConfirmatoryProtocol,
    load_lightweight_confirmatory_protocol,
    verify_lightweight_confirmatory_protocol,
)
from aletheia_lab.benchmark.p2.p2r_recovery import (
    ARCHIVE_ITEM_SCHEMA_VERSION,
    ARCHIVE_READINESS_SCHEMA_VERSION,
    P2R_V1_TERMINAL_STORE_SHA256,
    P2RV1TechnicalFailureAudit,
    load_p2r_v1_failure_audit,
)

P2R_RECOVERY_PROTOCOL_SCHEMA_VERSION: Final[Literal["p2r-lightweight-confirmatory-recovery/1"]] = (
    "p2r-lightweight-confirmatory-recovery/1"
)
P2R_RECOVERY_IMPLEMENTATION_COMMIT: Final[str] = "c2fd14155c857fe6595fa8bea78db607860fa29d"
P2R_V1_FAILURE_AUDIT_SHA256: Final[str] = (
    "b5d8701cbc50f2eab32cfe0a1d880126907510778cb58edea2f1273397caec24"
)
P2R_ARCHIVE_READINESS_SHA256: Final[str] = (
    "528e5d1d25f905c450faeafe6c35c87b7cc09f25f4f9fe77666f85da5c36403c"
)
DEFAULT_DATA_DRIFT_RECOVERY_PROTOCOL_PATH = Path(
    "configs/benchmark/p2r_data_drift_confirmatory_v1_1_protocol.json"
)
DEFAULT_PREPROCESSING_RECOVERY_PROTOCOL_PATH = Path(
    "configs/benchmark/p2r_preprocessing_confirmatory_v1_1_protocol.json"
)

MechanismName = Literal["data_drift", "preprocessing_bug"]
Sha256 = str


class P2RRecoveryProtocolError(ValueError):
    """Raised when the prospective recovery is not a technical-only successor."""


def _fail(message: str) -> NoReturn:
    raise P2RRecoveryProtocolError(message)


def _file_sha256(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        _fail(f"bound recovery artifact is not a regular file: {path}")
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise P2RRecoveryProtocolError(f"cannot hash bound recovery artifact: {path}") from exc
    return digest.hexdigest()


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, allow_inf_nan=False)


class P2RRecoveryArtifactBindings(_StrictFrozenModel):
    predecessor_protocol_uri: str
    predecessor_protocol_file_sha256: Sha256 = Field(pattern=SHA256_PATTERN)
    predecessor_protocol_sha256: Sha256 = Field(pattern=SHA256_PATTERN)
    failure_audit_uri: Literal["configs/benchmark/provenance/p2r_v1_technical_failure_audit.json"]
    failure_audit_file_sha256: Sha256 = Field(pattern=SHA256_PATTERN)
    failure_audit_sha256: Sha256 = Field(pattern=SHA256_PATTERN)
    readiness_implementation_uri: Literal["src/aletheia_lab/benchmark/p2/p2r_recovery.py"]
    readiness_implementation_file_sha256: Sha256 = Field(pattern=SHA256_PATTERN)
    predecessor_terminal_store_sha256: Sha256 = Field(pattern=SHA256_PATTERN)


class P2RArchiveReadinessContract(_StrictFrozenModel):
    receipt_schema_version: Literal["p2r-archive-readiness/1"]
    item_schema_version: Literal["p2r-archive-readiness-item/1"]
    expected_receipt_sha256: Sha256 = Field(pattern=SHA256_PATTERN)
    required_archive_file_names: tuple[
        Literal["uci-default-of-credit-card-clients.zip"],
        Literal["uci-online-shoppers-purchasing-intention.zip"],
    ]
    required_archive_sha256s: tuple[Sha256, Sha256]
    inspection_scope: Literal["encoding_and_class_eligibility_only"]
    reproduce_pinned_archive_member_schema_and_eligibility: Literal[True]
    required_before_registration_receipt: Literal[True]
    revalidated_immediately_before_sealed_marker: Literal[True]
    failure_consumes_execution_attempt: Literal[False]
    split_membership_compilation_forbidden: Literal[True]
    sealed_partition_open_forbidden: Literal[True]
    model_fit_forbidden: Literal[True]
    predictive_metrics_forbidden: Literal[True]

    @model_validator(mode="after")
    def _readiness_identity_is_exact(self) -> P2RArchiveReadinessContract:
        if self.receipt_schema_version != ARCHIVE_READINESS_SCHEMA_VERSION:
            raise ValueError("recovery binds another readiness receipt schema")
        if self.item_schema_version != ARCHIVE_ITEM_SCHEMA_VERSION:
            raise ValueError("recovery binds another readiness item schema")
        if self.expected_receipt_sha256 != P2R_ARCHIVE_READINESS_SHA256:
            raise ValueError("recovery binds another archive readiness receipt")
        expected_archives = (
            "56c885f84457f6680f8438f02bfcdac9579323d8a94465ee5f26e32baa727602",
            "2972e6184d3ad7beaaa831d9fc2b059dc3ee29df69d1ec593c466a5cd8485d14",
        )
        if self.required_archive_sha256s != expected_archives:
            raise ValueError("recovery changes the frozen archive identities")
        return self


class P2RTechnicalRecoveryContract(_StrictFrozenModel):
    predecessor_execution_commit: Literal["451388b8a5bd9c0e5d421b9130daf00e51268103"]
    recovery_implementation_commit: Literal["c2fd14155c857fe6595fa8bea78db607860fa29d"]
    predecessor_failure_stage: Literal["load_primary"]
    root_cause_classification: Literal["preflight_archive_readiness_defect"]
    predecessor_model_fitted: Literal[False]
    predecessor_partial_outcome_published: Literal[False]
    predecessor_scientific_disposition_generated: Literal[False]
    predecessor_rerun_forbidden: Literal[True]
    predecessor_attempt_permanently_retired: Literal[True]
    allowed_changes: tuple[
        Literal[
            "pre_registration_archive_readiness_gate",
            "pre_marker_archive_readiness_revalidation",
            "permanent_v1_execution_retirement",
            "protocol_tag_release_registration_and_store_identity",
        ],
        ...,
    ]
    datasets_or_roles_changed: Literal[False]
    split_membership_changed: Literal[False]
    model_or_preprocessing_changed: Literal[False]
    seeds_or_candidate_plan_changed: Literal[False]
    intervention_or_nuisance_comparator_changed: Literal[False]
    estimand_metric_or_threshold_changed: Literal[False]
    exclusion_or_disposition_rule_changed: Literal[False]
    outcome_information_used_for_recovery: Literal[False]
    maximum_registered_execution_attempts: Literal[1]
    further_recovery_requires_new_disclosed_protocol: Literal[True]

    @model_validator(mode="after")
    def _technical_delta_is_exact(self) -> P2RTechnicalRecoveryContract:
        expected = (
            "pre_registration_archive_readiness_gate",
            "pre_marker_archive_readiness_revalidation",
            "permanent_v1_execution_retirement",
            "protocol_tag_release_registration_and_store_identity",
        )
        if self.allowed_changes != expected:
            raise ValueError("recovery contains an undeclared change")
        if self.recovery_implementation_commit != P2R_RECOVERY_IMPLEMENTATION_COMMIT:
            raise ValueError("recovery binds another implementation commit")
        return self


class P2RRecoveryGovernance(_StrictFrozenModel):
    required_git_tag: str = Field(min_length=1)
    immutable_release_required_before_execution: Literal[True]
    tagged_protocol_must_equal_execution_protocol: Literal[True]
    recovery_implementation_must_predate_registration: Literal[True]
    changes_after_registration_require_new_protocol_version: Literal[True]
    paired_mechanism_outcomes_released_together: Literal[True]
    one_sealed_open_marker_for_both_mechanisms: Literal[True]
    predecessor_failure_evidence_must_remain_preserved: Literal[True]
    recovery_is_not_independent_new_dataset_replication: Literal[True]
    registration_authorized_by_this_file: Literal[False]
    execution_authorized_by_this_file: Literal[False]


class P2RRecoveryProtocol(_StrictFrozenModel):
    """Mechanism-specific identity around an unchanged predecessor protocol."""

    schema_version: Literal["p2r-lightweight-confirmatory-recovery/1"] = (
        P2R_RECOVERY_PROTOCOL_SCHEMA_VERSION
    )
    protocol_version: str = Field(min_length=1)
    status: Literal["technical_recovery_protocol_candidate_not_registered"]
    mechanism: MechanismName
    artifacts: P2RRecoveryArtifactBindings
    readiness: P2RArchiveReadinessContract
    technical_recovery: P2RTechnicalRecoveryContract
    governance: P2RRecoveryGovernance
    outcome_fields_forbidden: Literal[True]
    model_fitted: Literal[False]
    predictive_metrics_generated: Literal[False]
    sealed_outcomes_generated: Literal[False]

    @model_validator(mode="after")
    def _identity_is_mechanism_specific(self) -> P2RRecoveryProtocol:
        expected = {
            "data_drift": (
                "p2r-data_drift-confirmatory/1.1",
                "p2r-data-drift-confirmatory-v1.1",
                "configs/benchmark/p2r_data_drift_confirmatory_protocol.json",
                "bad097a4298f7925b314f049a762da2f0e4485a24f40860d667ae936b422c289",
                "00411d49762bd29d1fe1b1304757c08249c8050510b67e0ae35ae8362bec7103",
            ),
            "preprocessing_bug": (
                "p2r-preprocessing_bug-confirmatory/1.1",
                "p2r-preprocessing-mismatch-confirmatory-v1.1",
                "configs/benchmark/p2r_preprocessing_confirmatory_protocol.json",
                "4fcca028153fce45098e8547608d16231c33f9a78cdc243ff9931d119eca4904",
                "9b81d608df02e402760217fd1d8f1b891dd0dd24b9a93d9a5d9396ba65fe2802",
            ),
        }
        version, tag, uri, protocol_hash, file_hash = expected[self.mechanism]
        observed = (
            self.protocol_version,
            self.governance.required_git_tag,
            self.artifacts.predecessor_protocol_uri,
            self.artifacts.predecessor_protocol_sha256,
            self.artifacts.predecessor_protocol_file_sha256,
        )
        if observed != (version, tag, uri, protocol_hash, file_hash):
            raise ValueError("recovery protocol identity differs from its predecessor chain")
        return self

    def canonical_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))


def load_p2r_recovery_protocol(path: str | Path) -> P2RRecoveryProtocol:
    target = Path(path)
    if target.is_symlink() or not target.is_file():
        raise P2RRecoveryProtocolError("P2R recovery protocol is unavailable or invalid")
    try:
        return P2RRecoveryProtocol.model_validate_json(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise P2RRecoveryProtocolError("P2R recovery protocol is unavailable or invalid") from exc


def verify_p2r_recovery_protocol(
    protocol: P2RRecoveryProtocol,
    *,
    root: str | Path = ".",
) -> tuple[P2RRecoveryProtocol, LightweightConfirmatoryProtocol, P2RV1TechnicalFailureAudit]:
    """Verify the outcome-free predecessor chain and technical-only delta."""

    checked = P2RRecoveryProtocol.model_validate(protocol.model_dump())
    repository = Path(root).resolve()
    bindings = checked.artifacts
    predecessor_path = repository / bindings.predecessor_protocol_uri
    failure_audit_path = repository / bindings.failure_audit_uri
    readiness_path = repository / bindings.readiness_implementation_uri
    predecessor = verify_lightweight_confirmatory_protocol(
        load_lightweight_confirmatory_protocol(predecessor_path)
    )
    failure_audit = load_p2r_v1_failure_audit(failure_audit_path)
    observed = (
        (_file_sha256(predecessor_path), bindings.predecessor_protocol_file_sha256),
        (predecessor.canonical_sha256(), bindings.predecessor_protocol_sha256),
        (_file_sha256(failure_audit_path), bindings.failure_audit_file_sha256),
        (failure_audit.canonical_sha256(), bindings.failure_audit_sha256),
        (_file_sha256(readiness_path), bindings.readiness_implementation_file_sha256),
        (failure_audit.terminal_store_sha256, bindings.predecessor_terminal_store_sha256),
    )
    if any(actual != expected for actual, expected in observed):
        _fail("P2R recovery artifact binding does not reproduce")
    if failure_audit.canonical_sha256() != P2R_V1_FAILURE_AUDIT_SHA256:
        _fail("P2R recovery binds another failure audit")
    if failure_audit.terminal_store_sha256 != P2R_V1_TERMINAL_STORE_SHA256:
        _fail("P2R recovery binds another terminal failure store")
    if predecessor.mechanism != checked.mechanism:
        _fail("P2R recovery mechanism differs from its predecessor")
    if not failure_audit.rerun_forbidden or failure_audit.scientific_disposition_generated:
        _fail("P2R v1 failure does not permit this technical-only successor")
    return checked, predecessor, failure_audit


def verify_p2r_recovery_protocol_pair(
    data_drift: P2RRecoveryProtocol,
    preprocessing: P2RRecoveryProtocol,
    *,
    root: str | Path = ".",
) -> tuple[P2RRecoveryProtocol, P2RRecoveryProtocol]:
    drift, drift_predecessor, drift_failure = verify_p2r_recovery_protocol(data_drift, root=root)
    prep, prep_predecessor, prep_failure = verify_p2r_recovery_protocol(preprocessing, root=root)
    if (drift.mechanism, prep.mechanism) != ("data_drift", "preprocessing_bug"):
        _fail("recovery pair must cover both mechanisms exactly once")
    if drift_failure != prep_failure:
        _fail("recovery pair must bind one shared terminal failure audit")
    if drift.readiness != prep.readiness:
        _fail("recovery pair must bind one shared archive readiness contract")
    if drift.technical_recovery != prep.technical_recovery:
        _fail("recovery pair must bind one shared technical delta")
    drift_census = tuple(
        (
            item.dataset_id,
            item.role,
            item.split_membership_sha256,
            item.sealed_membership_sha256,
        )
        for item in drift_predecessor.datasets
    )
    prep_census = tuple(
        (
            item.dataset_id,
            item.role,
            item.split_membership_sha256,
            item.sealed_membership_sha256,
        )
        for item in prep_predecessor.datasets
    )
    if drift_census != prep_census:
        _fail("recovery pair changes the shared dataset census")
    return drift, prep
