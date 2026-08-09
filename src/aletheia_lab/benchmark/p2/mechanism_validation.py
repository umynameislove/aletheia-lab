"""One fail-closed trust boundary for every Phase 2 mechanism.

Mechanism modules remain responsible for reconstructing their own artifacts.
This module composes those validators with the shared lifecycle contract: it
recomputes identity from the frozen slot and refuses execution or disposition
records that describe a different candidate.  A valid artifact therefore
cannot be replayed under another slot, mechanism or family identity.
"""

from __future__ import annotations

from typing import Annotated, Literal, NoReturn, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.benchmark.p2.contracts import (
    CandidateSlot,
    ExecutedCandidate,
    TechnicalDispositionEntry,
)
from aletheia_lab.benchmark.p2.data_drift_family import (
    DriftBenignControlInputs,
    DriftCandidatePackage,
    DriftFaultDirectedInputs,
    validate_drift_candidate_package,
)
from aletheia_lab.benchmark.p2.identity import (
    CANDIDATE_ID_PATTERN,
    SHA256_PATTERN,
    SLOT_ID_PATTERN,
    LabelNoiseParameters,
    candidate_id_for,
    proposed_family_sha256,
)
from aletheia_lab.benchmark.p2.label_controls import (
    LabelRepairResult,
    PredictionEquivalenceEvidence,
    PredictionEvaluationSource,
    SemanticTargetSource,
    SerializationControlSpec,
    SerializationRoundTripResult,
    validate_label_repair,
    verify_prediction_equivalence,
)
from aletheia_lab.benchmark.p2.label_noise import (
    LabelCorruptionResult,
    LabelCorruptionSpec,
    LabelNoiseSource,
    validate_label_corruption,
)
from aletheia_lab.benchmark.p2.preprocessing_family import (
    BenignControlInputs,
    FaultDirectedInputs,
    PreprocessingCandidatePackage,
    RepairControlInputs,
    validate_preprocessing_candidate_package,
)
from aletheia_lab.benchmark.p2.validation import ContractViolation, validate_frozen_alpha_slot

MECHANISM_BINDING_SCHEMA_VERSION = "p2-mechanism-candidate-binding/v1"
MECHANISM_SLOT_DIGEST_SCHEMA_VERSION = "p2-mechanism-slot-binding/v1"

Sha256 = Annotated[str, Field(pattern=SHA256_PATTERN)]
SlotId = Annotated[str, Field(pattern=SLOT_ID_PATTERN)]
CandidateId = Annotated[str, Field(pattern=CANDIDATE_ID_PATTERN)]

_ModelT = TypeVar("_ModelT", bound=BaseModel)


class MechanismValidationError(ContractViolation):
    """Raised when a mechanism artifact and lifecycle record disagree."""


def _fail(message: str) -> NoReturn:
    raise MechanismValidationError(message)


def _revalidated(model: _ModelT) -> _ModelT:
    """Re-run validators even when an object used an unsafe Pydantic builder."""

    return type(model).model_validate(model.model_dump())


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class LabelFaultDirectedInputs(_StrictFrozenModel):
    """Authoritative inputs for one fault-directed label corruption."""

    kind: Literal["label_fault_directed"] = "label_fault_directed"
    source: LabelNoiseSource
    spec: LabelCorruptionSpec


class LabelRepairControlInputs(_StrictFrozenModel):
    """Authoritative inputs for the frozen label-repair control."""

    kind: Literal["label_repair_control"] = "label_repair_control"
    source: LabelNoiseSource
    corrupted: LabelCorruptionResult
    spec: LabelCorruptionSpec


class LabelBenignControlInputs(_StrictFrozenModel):
    """Authoritative construction and prediction evidence for M2-B1."""

    kind: Literal["label_benign_control"] = "label_benign_control"
    source: SemanticTargetSource
    spec: SerializationControlSpec
    evaluation_source: PredictionEvaluationSource
    prediction_evidence: PredictionEquivalenceEvidence


MechanismInputs = (
    DriftFaultDirectedInputs
    | DriftBenignControlInputs
    | LabelFaultDirectedInputs
    | LabelRepairControlInputs
    | LabelBenignControlInputs
    | FaultDirectedInputs
    | RepairControlInputs
    | BenignControlInputs
)

MechanismArtifact = (
    DriftCandidatePackage
    | LabelCorruptionResult
    | LabelRepairResult
    | SerializationRoundTripResult
    | PreprocessingCandidatePackage
)


class ValidatedMechanismCandidate(_StrictFrozenModel):
    """Canonical binding produced only after all trust checks succeed."""

    schema_version: Literal["p2-mechanism-candidate-binding/v1"] = (
        "p2-mechanism-candidate-binding/v1"
    )
    candidate_id: CandidateId
    slot_id: SlotId
    fault_type: Literal["data_drift", "label_noise", "preprocessing_bug"]
    proposed_family_sha256: Sha256
    slot_sha256: Sha256
    artifact_sha256: Sha256
    supporting_evidence_sha256: Sha256 | None = None
    execution: ExecutedCandidate
    disposition: TechnicalDispositionEntry

    @model_validator(mode="after")
    def _records_describe_the_binding(self) -> ValidatedMechanismCandidate:
        if self.execution.candidate_id != self.candidate_id:
            raise ValueError("execution must describe the validated candidate")
        if self.disposition.candidate_id != self.candidate_id:
            raise ValueError("disposition must describe the validated candidate")
        if self.execution.slot_id != self.slot_id:
            raise ValueError("execution must describe the validated slot")
        if self.execution.fault_type != self.fault_type:
            raise ValueError("execution must describe the validated mechanism")
        if self.execution.proposed_family_sha256 != self.proposed_family_sha256:
            raise ValueError("execution must carry the validated family fingerprint")
        return self

    def binding_sha256(self) -> str:
        """Digest the complete validated binding for immutable storage."""

        return canonical_sha256(self.model_dump(mode="json"))


def _slot_sha256(slot: CandidateSlot) -> str:
    return canonical_sha256(
        {
            "schema_version": MECHANISM_SLOT_DIGEST_SCHEMA_VERSION,
            "slot": slot.model_dump(mode="json"),
        }
    )


def _execution_for(slot: CandidateSlot) -> ExecutedCandidate:
    fingerprint = proposed_family_sha256(slot.identity)
    return ExecutedCandidate(
        candidate_id=candidate_id_for(
            slot_id=slot.slot_id,
            family_fingerprint=fingerprint,
        ),
        slot_id=slot.slot_id,
        fault_type=slot.fault_type,
        role=slot.role,
        slot_kind=slot.slot_kind,
        proposed_family_sha256=fingerprint,
        dataset_sha256=slot.identity.dataset_sha256,
        model_data_split_manifest_sha256=slot.identity.model_data_split_manifest_sha256,
    )


def _same_model(actual: BaseModel, expected: BaseModel, label: str) -> None:
    if actual.model_dump(mode="json") != expected.model_dump(mode="json"):
        _fail(f"{label} does not match the record recomputed from the mechanism artifact")


def _validate_label_fault(
    artifact: LabelCorruptionResult,
    *,
    slot: CandidateSlot,
    inputs: LabelFaultDirectedInputs,
) -> tuple[str, TechnicalDispositionEntry]:
    if slot.role != "fault_directed":
        _fail("a label corruption cannot be reused as a control")
    identity = slot.identity
    parameters = identity.canonical_intervention_parameters
    if (
        identity.intervention_type != "training_target_label_corruption"
        or not isinstance(parameters, LabelNoiseParameters)
        or inputs.spec.seed != identity.seed
        or inputs.spec.parameters != parameters
    ):
        _fail("label corruption inputs differ from the frozen slot identity")
    if (
        inputs.source.attested_model_specification_sha256 != identity.model_specification_sha256
        or inputs.source.attested_preprocessing_specification_sha256
        != identity.preprocessing_specification_sha256
    ):
        _fail("label corruption source attestations differ from the frozen family identity")
    validated = validate_label_corruption(
        artifact,
        source=inputs.source,
        spec=inputs.spec,
    )
    candidate_id = _execution_for(slot).candidate_id
    return (
        validated.artifact_sha256(),
        TechnicalDispositionEntry(
            candidate_id=candidate_id,
            disposition="technically_valid",
        ),
    )


def _validate_label_repair(
    artifact: LabelRepairResult,
    *,
    slot: CandidateSlot,
    inputs: LabelRepairControlInputs,
) -> tuple[str, TechnicalDispositionEntry]:
    if slot.role != "designed_improvement_control":
        _fail("a label repair cannot be reused under another candidate role")
    validated = validate_label_repair(
        artifact,
        source=inputs.source,
        corrupted=inputs.corrupted,
        spec=inputs.spec,
        slot=slot,
    )
    return (
        validated.artifact_sha256(),
        TechnicalDispositionEntry(
            candidate_id=_execution_for(slot).candidate_id,
            disposition="technically_valid",
        ),
    )


def _validate_label_benign(
    artifact: SerializationRoundTripResult,
    *,
    slot: CandidateSlot,
    inputs: LabelBenignControlInputs,
) -> tuple[str, str, TechnicalDispositionEntry]:
    if slot.role != "designed_benign_control":
        _fail("a label round trip cannot be reused under another candidate role")
    report = verify_prediction_equivalence(
        inputs.prediction_evidence,
        result=artifact,
        source=inputs.source,
        spec=inputs.spec,
        slot=slot,
        evaluation_source=inputs.evaluation_source,
    )
    candidate_id = _execution_for(slot).candidate_id
    if report.verdict == "prediction_divergence":
        disposition = TechnicalDispositionEntry(
            candidate_id=candidate_id,
            disposition="technical_rejected",
            rejection_reason="benign_equivalence_failure",
        )
    else:
        disposition = TechnicalDispositionEntry(
            candidate_id=candidate_id,
            disposition="technically_valid",
        )
    return artifact.artifact_sha256(), canonical_sha256(report.model_dump(mode="json")), disposition


def _validate_mechanism_artifact(
    artifact: MechanismArtifact,
    *,
    slot: CandidateSlot,
    inputs: MechanismInputs,
) -> tuple[str, str | None, TechnicalDispositionEntry]:
    """Dispatch only combinations declared by the frozen mechanism contract."""

    if slot.fault_type == "data_drift":
        if not isinstance(artifact, DriftCandidatePackage) or not isinstance(
            inputs, DriftFaultDirectedInputs | DriftBenignControlInputs
        ):
            _fail("a data-drift slot requires a data-drift package and inputs")
        drift_package = validate_drift_candidate_package(artifact, slot=slot, inputs=inputs)
        return (
            drift_package.artifact_package_sha256(),
            None,
            drift_package.disposition,
        )

    if slot.fault_type == "preprocessing_bug":
        if not isinstance(artifact, PreprocessingCandidatePackage) or not isinstance(
            inputs, FaultDirectedInputs | RepairControlInputs | BenignControlInputs
        ):
            _fail("a preprocessing slot requires a preprocessing package and inputs")
        preprocessing_package = validate_preprocessing_candidate_package(
            artifact, slot=slot, inputs=inputs
        )
        if preprocessing_package.disposition is None:
            _fail("the preprocessing candidate is still awaiting equivalence evidence")
        return (
            preprocessing_package.artifact_package_sha256(),
            None,
            preprocessing_package.disposition,
        )

    if isinstance(artifact, LabelCorruptionResult) and isinstance(inputs, LabelFaultDirectedInputs):
        artifact_sha256, disposition = _validate_label_fault(artifact, slot=slot, inputs=inputs)
        return artifact_sha256, None, disposition
    if isinstance(artifact, LabelRepairResult) and isinstance(inputs, LabelRepairControlInputs):
        artifact_sha256, disposition = _validate_label_repair(artifact, slot=slot, inputs=inputs)
        return artifact_sha256, None, disposition
    if isinstance(artifact, SerializationRoundTripResult) and isinstance(
        inputs, LabelBenignControlInputs
    ):
        return _validate_label_benign(artifact, slot=slot, inputs=inputs)
    _fail("a label-noise slot requires the artifact and inputs declared for its role")


def validate_mechanism_candidate(
    artifact: MechanismArtifact,
    *,
    slot: CandidateSlot,
    inputs: MechanismInputs,
    execution: ExecutedCandidate,
    disposition: TechnicalDispositionEntry,
) -> ValidatedMechanismCandidate:
    """Validate any Phase 2 candidate and bind it to shared lifecycle records.

    The function deliberately accepts lifecycle records rather than creating a
    second ledger.  Both are reconstructed independently here and must match
    byte-for-byte before the canonical binding is returned.
    """

    slot = validate_frozen_alpha_slot(slot)
    execution = _revalidated(execution)
    disposition = _revalidated(disposition)
    artifact_sha256, supporting_evidence_sha256, expected_disposition = (
        _validate_mechanism_artifact(
            artifact,
            slot=slot,
            inputs=inputs,
        )
    )
    expected_execution = _execution_for(slot)
    _same_model(execution, expected_execution, "execution")
    _same_model(disposition, expected_disposition, "technical disposition")

    return ValidatedMechanismCandidate(
        candidate_id=expected_execution.candidate_id,
        slot_id=slot.slot_id,
        fault_type=slot.fault_type,
        proposed_family_sha256=expected_execution.proposed_family_sha256,
        slot_sha256=_slot_sha256(slot),
        artifact_sha256=artifact_sha256,
        supporting_evidence_sha256=supporting_evidence_sha256,
        execution=execution,
        disposition=disposition,
    )
