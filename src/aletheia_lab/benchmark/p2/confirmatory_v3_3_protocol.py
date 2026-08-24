"""Outcome-blind v3.3 protocol for the disclosed closeout-contract repair."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from pathlib import Path
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.benchmark.p2.confirmatory_v3_2_failure import (
    V32TechnicalFailureAudit,
    load_v3_2_failure_audit,
)
from aletheia_lab.benchmark.p2.confirmatory_v3_2_protocol import (
    V32ArtifactBindings,
    V32ConfirmatoryProtocol,
    load_v3_2_confirmatory_protocol,
    verify_v3_2_protocol_artifacts,
)
from aletheia_lab.benchmark.p2.confirmatory_v3_protocol import (
    DatasetSplitReceipt,
    V3ProtocolError,
)
from aletheia_lab.benchmark.p2.identity import SHA256_PATTERN

V3_3_PROTOCOL_SCHEMA_VERSION: Final[Literal["p2-label-noise-shift-protocol/4"]] = (
    "p2-label-noise-shift-protocol/4"
)
DEFAULT_V3_3_PROTOCOL_PATH: Final[Path] = Path(
    "configs/benchmark/p2_label_noise_shift_v3_3_protocol.json"
)
V3_2_PROTOCOL_SHA256: Final[str] = (
    "7cba25f08f4e27007bf17fc837b9f11137123f2f83452378c8ac3db5de3ffe27"
)
V3_2_PROTOCOL_FILE_SHA256: Final[str] = (
    "fe141be9ea83a1f03d03810fc01d49a112bfbd89390d2a5ac90036b694665122"
)
V3_2_FAILURE_AUDIT_SHA256: Final[str] = (
    "2f18d52c682a86ba6ab638a94b6163cfb0a5459453083d31319f088235246da4"
)
V3_2_FAILURE_AUDIT_FILE_SHA256: Final[str] = (
    "dcb30fcd7e7e41b62407f0c15ce7ff96034e12f04cf82d199f081673ee3aade2"
)
V3_2_TERMINAL_STORE_SHA256: Final[str] = (
    "1ce2b827d027cdb0685ad22c520d1ff11b6fcb45e2af5894ad2f4f964c97d029"
)
V3_3_PROTOCOL_SHA256: Final[str] = (
    "5fa057e17203b00fa78fc86d58ce5b324bb30cebf916ebb94a3d6b389f30b456"
)
RECOVERY_IMPLEMENTATION_COMMIT: Final[str] = (
    "b79568c3c60ffcf0a972b00c20fd9807754d8c9d"
)


class V33ArtifactBindings(V32ArtifactBindings):
    predecessor_v3_2_protocol_uri: Literal[
        "configs/benchmark/p2_label_noise_shift_v3_2_protocol.json"
    ]
    predecessor_v3_2_protocol_sha256: str = Field(pattern=SHA256_PATTERN)
    predecessor_v3_2_protocol_file_sha256: str = Field(pattern=SHA256_PATTERN)
    predecessor_v3_2_failure_audit_uri: Literal[
        "configs/benchmark/provenance/p2_v3_2_technical_failure_audit.json"
    ]
    predecessor_v3_2_failure_audit_sha256: str = Field(pattern=SHA256_PATTERN)
    predecessor_v3_2_failure_audit_file_sha256: str = Field(pattern=SHA256_PATTERN)
    predecessor_v3_2_terminal_store_sha256: str = Field(pattern=SHA256_PATTERN)


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, allow_inf_nan=False)


class CloseoutRecoveryContract(_StrictFrozenModel):
    predecessor_tag: Literal["p2-label-noise-shift-factorial-v3.2"]
    predecessor_protocol_sha256: str = Field(pattern=SHA256_PATTERN)
    predecessor_protocol_file_sha256: str = Field(pattern=SHA256_PATTERN)
    predecessor_execution_commit: Literal["1f98a24f4f748d6e2a21631d35b8a3cd97cf93fc"]
    recovery_implementation_commit: Literal[
        "b79568c3c60ffcf0a972b00c20fd9807754d8c9d"
    ]
    failure_audit_sha256: str = Field(pattern=SHA256_PATTERN)
    failure_audit_file_sha256: str = Field(pattern=SHA256_PATTERN)
    predecessor_terminal_store_sha256: str = Field(pattern=SHA256_PATTERN)
    predecessor_terminal_status: Literal["technical_failure"]
    predecessor_failure_stage: Literal["build_closeout"]
    predecessor_exception_class: Literal["ValidationError"]
    root_cause_classification: Literal["implementation_contract_defect"]
    diagnosis_scope: Literal["failure_receipt_code_path_and_synthetic_reproduction_only"]
    predecessor_partial_outcome_published: Literal[False]
    predecessor_scientific_disposition_generated: Literal[False]
    predecessor_outcome_artifacts_available: Literal[False]
    predecessor_sealed_partition_opened: Literal[True]
    predecessor_rerun_forbidden: Literal[True]
    same_pinned_datasets_and_splits_reused: Literal[True]
    same_sealed_partitions_reused_after_disclosed_technical_failure: Literal[True]
    recovery_is_not_independent_new_dataset_replication: Literal[True]
    numerical_outcomes_unavailable_to_recovery_design: Literal[True]
    allowed_changes: tuple[
        Literal[
            "distinguish_calibration_from_scientific_abstention",
            "preserve_complete_inference_for_scientific_abstention",
            "permanent_v3_2_execution_retirement",
            "protocol_schema_tag_and_registration_identity",
        ],
        ...,
    ]
    model_or_calibration_changed: Literal[False]
    datasets_or_splits_changed: Literal[False]
    intervention_grid_or_seeds_changed: Literal[False]
    estimand_metric_or_inference_changed: Literal[False]
    thresholds_or_decision_rule_changed: Literal[False]
    outcome_information_used_for_tuning: Literal[False]
    maximum_registered_execution_attempts: Literal[1]
    further_recovery_requires_new_disclosed_protocol: Literal[True]

    @model_validator(mode="after")
    def _technical_delta_is_exact(self) -> CloseoutRecoveryContract:
        expected = (
            "distinguish_calibration_from_scientific_abstention",
            "preserve_complete_inference_for_scientific_abstention",
            "permanent_v3_2_execution_retirement",
            "protocol_schema_tag_and_registration_identity",
        )
        if self.allowed_changes != expected:
            raise ValueError("v3.3 may contain only the four disclosed technical changes")
        bindings = (
            (self.predecessor_protocol_sha256, V3_2_PROTOCOL_SHA256),
            (self.predecessor_protocol_file_sha256, V3_2_PROTOCOL_FILE_SHA256),
            (self.failure_audit_sha256, V3_2_FAILURE_AUDIT_SHA256),
            (self.failure_audit_file_sha256, V3_2_FAILURE_AUDIT_FILE_SHA256),
            (self.predecessor_terminal_store_sha256, V3_2_TERMINAL_STORE_SHA256),
            (self.recovery_implementation_commit, RECOVERY_IMPLEMENTATION_COMMIT),
        )
        if any(observed != expected_value for observed, expected_value in bindings):
            raise ValueError("v3.3 recovery identity differs from disclosed evidence")
        return self


class V33ProtocolGovernance(_StrictFrozenModel):
    required_git_tag: Literal["p2-label-noise-shift-factorial-v3.3"]
    protocol_only_commit_required: Literal[True]
    immutable_release_required_before_execution: Literal[True]
    recovery_implementation_must_predate_registration: Literal[True]
    outcome_blind_failure_audit_required_before_registration: Literal[True]
    changes_after_registration_require_new_protocol_version: Literal[True]
    primary_and_replication_outcomes_released_together: Literal[True]
    sealed_test_single_open_for_v3_3: Literal[True]
    predecessor_failure_audit_must_remain_published: Literal[True]
    registration_authorized_by_this_file: Literal[False]
    execution_authorized_by_this_file: Literal[False]


class V33ConfirmatoryProtocol(V32ConfirmatoryProtocol):
    schema_version: Literal["p2-label-noise-shift-protocol/4"]
    status: Literal["closeout_recovery_protocol_candidate_not_registered"]
    artifacts: V33ArtifactBindings
    technical_recovery: CloseoutRecoveryContract
    governance: V33ProtocolGovernance

    @model_validator(mode="after")
    def _v3_3_dataset_census_is_unchanged(self) -> V33ConfirmatoryProtocol:
        census = tuple((item.dataset_id, item.role, item.seed) for item in self.dataset_splits)
        if census != (
            ("uci_default_of_credit_card_clients", "primary", 2718),
            ("uci_online_shoppers_purchasing_intention", "external_replication", 3141),
        ):
            raise ValueError("v3.3 must preserve the ordered predecessor dataset census")
        return self


def load_v3_3_confirmatory_protocol(
    path: str | Path = DEFAULT_V3_3_PROTOCOL_PATH,
) -> V33ConfirmatoryProtocol:
    try:
        return V33ConfirmatoryProtocol.model_validate_json(
            Path(path).read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as exc:
        raise V3ProtocolError("v3.3 confirmatory protocol is unavailable or invalid") from exc


def _file_sha256(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise V3ProtocolError(f"v3.3 predecessor is not a regular file: {path}")
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise V3ProtocolError(f"cannot hash v3.3 predecessor: {path}") from exc
    return digest.hexdigest()


def verify_v3_3_technical_delta(
    protocol: V33ConfirmatoryProtocol,
    predecessor: V32ConfirmatoryProtocol,
) -> None:
    """Prove that v3.3 changes closeout representation, never study semantics."""

    checked = V33ConfirmatoryProtocol.model_validate(protocol.model_dump())
    previous = V32ConfirmatoryProtocol.model_validate(predecessor.model_dump())
    current_payload = checked.model_dump(mode="json")
    previous_payload = previous.model_dump(mode="json")
    for section in (
        "split_algorithm",
        "dataset_splits",
        "preprocessing",
        "models",
        "intervention",
        "prior_shift",
        "shift_estimators",
        "inference",
        "decision",
    ):
        if current_payload[section] != previous_payload[section]:
            raise V3ProtocolError(f"v3.3 changes forbidden scientific section: {section}")
    previous_artifacts = previous.artifacts.model_dump(mode="json")
    current_artifacts = checked.artifacts.model_dump(mode="json")
    for field, value in previous_artifacts.items():
        if current_artifacts.get(field) != value:
            raise V3ProtocolError(f"v3.3 changes predecessor artifact binding: {field}")


def verify_v3_3_protocol_artifacts(
    protocol: V33ConfirmatoryProtocol,
    *,
    root: str | Path = ".",
) -> tuple[V32ConfirmatoryProtocol, V32TechnicalFailureAudit]:
    """Verify the full v3.2 chain plus the tracked outcome-blind failure audit."""

    repository = Path(root).resolve()
    checked = V33ConfirmatoryProtocol.model_validate(protocol.model_dump())
    if checked.canonical_sha256() != V3_3_PROTOCOL_SHA256:
        raise V3ProtocolError("v3.3 protocol identity differs from the frozen candidate")
    predecessor_path = repository / checked.artifacts.predecessor_v3_2_protocol_uri
    audit_path = repository / checked.artifacts.predecessor_v3_2_failure_audit_uri
    predecessor = load_v3_2_confirmatory_protocol(predecessor_path)
    audit = load_v3_2_failure_audit(audit_path)
    verify_v3_2_protocol_artifacts(predecessor, root=repository)
    bindings = (
        (
            predecessor.canonical_sha256(),
            checked.artifacts.predecessor_v3_2_protocol_sha256,
            "v3.2 protocol",
        ),
        (
            _file_sha256(predecessor_path),
            checked.artifacts.predecessor_v3_2_protocol_file_sha256,
            "v3.2 protocol file",
        ),
        (
            audit.canonical_sha256(),
            checked.artifacts.predecessor_v3_2_failure_audit_sha256,
            "v3.2 failure audit",
        ),
        (
            _file_sha256(audit_path),
            checked.artifacts.predecessor_v3_2_failure_audit_file_sha256,
            "v3.2 failure audit file",
        ),
        (
            audit.terminal_store_sha256,
            checked.artifacts.predecessor_v3_2_terminal_store_sha256,
            "v3.2 terminal store",
        ),
    )
    for observed, expected, label in bindings:
        if observed != expected:
            raise V3ProtocolError(f"v3.3 is bound to another {label}")
    if (
        predecessor.canonical_sha256() != V3_2_PROTOCOL_SHA256
        or _file_sha256(predecessor_path) != V3_2_PROTOCOL_FILE_SHA256
        or audit.canonical_sha256() != V3_2_FAILURE_AUDIT_SHA256
        or _file_sha256(audit_path) != V3_2_FAILURE_AUDIT_FILE_SHA256
        or audit.terminal_store_sha256 != V3_2_TERMINAL_STORE_SHA256
    ):
        raise V3ProtocolError("v3.3 predecessor identities are not the disclosed evidence")
    if (
        not audit.rerun_forbidden
        or audit.scientific_disposition_generated
        or audit.partial_outcome_published
        or audit.outcome_artifacts_available
    ):
        raise V3ProtocolError("v3.2 audit does not authorize a technical-only successor")
    if checked.technical_recovery.recovery_implementation_commit != (
        RECOVERY_IMPLEMENTATION_COMMIT
    ):
        raise V3ProtocolError("v3.3 is bound to another recovery implementation")
    verify_v3_3_technical_delta(checked, predecessor)
    return predecessor, audit


def verify_v3_3_compiled_split_receipts(
    protocol: V33ConfirmatoryProtocol,
    observed: Sequence[DatasetSplitReceipt],
) -> None:
    if canonical_sha256([item.model_dump(mode="json") for item in observed]) != canonical_sha256(
        [item.model_dump(mode="json") for item in protocol.dataset_splits]
    ):
        raise V3ProtocolError("recompiled v3.3 split receipts differ from the frozen protocol")
