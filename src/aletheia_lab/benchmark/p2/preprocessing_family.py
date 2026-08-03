"""Candidate lifecycle packages for the preprocessing mechanism.

This module composes the pieces the mechanism already has — a frozen slot, an
intervention or control artifact, and an authoritative metric measurement — into
one package that a later admission layer can consume. It adds no new research
semantics: every rule it applies is imported from the module that owns it.

Four boundaries decide what this module may and may not conclude.

**Identity is recomputed, never accepted.** The family fingerprint comes from
:func:`proposed_family_sha256` applied to the slot's own twelve-field identity,
and the candidate identifier from :func:`candidate_id_for` applied to that
fingerprint. A caller cannot supply either; the validator recomputes both and
rejects a mismatch.

**Outcome is read off a measurement, never off a slot name.** A slot called
``M3-F1`` is not a regression because of its name. The measured primary outcome
is whatever :class:`MetricComparison` derived from the prediction vectors, and
the package copies that value rather than forming its own opinion.

**No family class is produced.** There is no ``family_class``, no
``AdmissionRecord``, no accepted family, no case family identifier, no diagnosis
context and no hidden cause anywhere in this module. The macro-F1 and
minority-recall harm thresholds for this mechanism are still alpha-provisional,
so a measured fault-directed or improvement-control candidate stops at
``validity_review_required`` and says so.

**A control cannot be promoted.** A benign control that fails equivalence
becomes a technical rejection carrying the ``benign_equivalence_failure`` reason
the contract already defines. It never becomes stable, and it never becomes an
eligible failure. An improvement control that regressed keeps the regression in
its comparison and is reported as a control-direction violation.

Two error kinds are raised, matching the convention of the other Phase 2
modules:

* a malformed *object* raises :class:`pydantic.ValidationError`;
* a malformed *relationship between objects* raises
  :class:`PreprocessingFamilyError`.

The difference between the two matters here more than elsewhere. A forged
artifact, a replayed slot or a tampered digest is a trust failure and raises;
it must never be recorded as a tidy "rejected candidate", because that would
turn evidence of tampering into an ordinary row in a table. Only a candidate
that ran honestly and then failed its own construction contract becomes a
technical rejection.
"""

from __future__ import annotations

from typing import Annotated, Final, Literal, NamedTuple, NoReturn, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aletheia_lab.benchmark.p2.binary_evaluation import (
    CleanTestSet,
    MetricComparison,
    PredictionVector,
    PrimaryOutcome,
    compare_binary_metrics,
    validate_metric_comparison,
)
from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.benchmark.p2.contracts import (
    CandidateRole,
    CandidateSlot,
    ExecutedCandidate,
    TechnicalDispositionEntry,
)
from aletheia_lab.benchmark.p2.identity import (
    CANDIDATE_ID_PATTERN,
    SHA256_PATTERN,
    SLOT_ID_PATTERN,
    candidate_id_for,
    proposed_family_sha256,
)
from aletheia_lab.benchmark.p2.preprocessing_controls import (
    BenignEquivalenceEvidence,
    BenignEquivalenceStatus,
    ColumnPermutationResult,
    ColumnPermutationSpec,
    EncoderMappingRepairResult,
    EncoderMappingRepairSpec,
    NamedFeatureTable,
    RepairControlMeasurement,
    benign_equivalence_status,
    measure_repair_control,
    validate_column_permutation,
    validate_encoder_mapping_repair,
    validate_inference_evaluation_binding,
    validate_repair_control_measurement,
)
from aletheia_lab.benchmark.p2.preprocessing_intervention import (
    EncoderMappingMismatchResult,
    EncoderMappingMismatchSpec,
    InferenceTransformSource,
    validate_preprocessing_intervention,
)
from aletheia_lab.benchmark.p2.validation import ContractViolation, validate_frozen_alpha_slot

# --------------------------------------------------------------------------- #
# Schema and protocol versions
# --------------------------------------------------------------------------- #

CANDIDATE_PACKAGE_SCHEMA_VERSION: Final[Literal["p2-preprocessing-candidate-package/v1"]] = (
    "p2-preprocessing-candidate-package/v1"
)
CANDIDATE_PACKAGE_DIGEST_SCHEMA_VERSION: Final[
    Literal["p2-preprocessing-candidate-package-digest/v1"]
] = "p2-preprocessing-candidate-package-digest/v1"
CANDIDATE_SLOT_BINDING_SCHEMA_VERSION: Final[
    Literal["p2-preprocessing-candidate-slot-binding/v1"]
] = "p2-preprocessing-candidate-slot-binding/v1"
FAULT_DIRECTED_INPUTS_SCHEMA_VERSION: Final[Literal["p2-fault-directed-inputs/v1"]] = (
    "p2-fault-directed-inputs/v1"
)
REPAIR_CONTROL_INPUTS_SCHEMA_VERSION: Final[Literal["p2-repair-control-inputs/v1"]] = (
    "p2-repair-control-inputs/v1"
)
BENIGN_CONTROL_INPUTS_SCHEMA_VERSION: Final[Literal["p2-benign-control-inputs/v1"]] = (
    "p2-benign-control-inputs/v1"
)

#: Pinned package protocol, a ``Literal`` rather than free text.
CANDIDATE_PACKAGE_PROTOCOL_VERSION: Final[Literal["preprocessing-candidate-package/v1"]] = (
    "preprocessing-candidate-package/v1"
)

#: The eligibility policy this mechanism is measured under. The package records
#: which policy will read its numbers; it does not apply the policy, because the
#: guardrail harm thresholds inside it are still alpha-provisional.
ELIGIBILITY_POLICY_VERSION: Final[Literal["preprocessing-bug-impact/alpha-v1"]] = (
    "preprocessing-bug-impact/alpha-v1"
)

#: How far a packaged candidate has been taken, and no further.
#:
#: None of these is an eligibility decision or a family class.
#: ``validity_review_required`` means the measurement is complete and the
#: guardrail policy is not frozen yet. ``pending_post_execution_equivalence``
#: means a benign control was constructed but not yet measured.
#: ``equivalence_verified_pending_admission`` is the strongest benign state
#: reachable without an admission layer. ``technically_rejected`` means the
#: candidate ran honestly and failed its own construction contract.
CandidateStatus = Literal[
    "validity_review_required",
    "pending_post_execution_equivalence",
    "equivalence_verified_pending_admission",
    "technically_rejected",
]

Sha256 = Annotated[str, Field(pattern=SHA256_PATTERN)]
SlotId = Annotated[str, Field(pattern=SLOT_ID_PATTERN)]
CandidateId = Annotated[str, Field(pattern=CANDIDATE_ID_PATTERN)]

_ModelT = TypeVar("_ModelT", bound=BaseModel)


class PreprocessingFamilyError(ContractViolation):
    """Raised when candidate-package artifacts disagree with one another."""


def _fail(message: str) -> NoReturn:
    """Reject a cross-object relationship."""

    raise PreprocessingFamilyError(message)


def _revalidated(model: _ModelT) -> _ModelT:
    """Re-run validators even when a caller built the object unsafely.

    ``model_copy(update=...)`` and ``model_construct()`` skip validation in
    Pydantic v2, so any object arriving at a trust boundary is rebuilt from its
    own dump before it is trusted.
    """

    return type(model).model_validate(model.model_dump())


class _StrictFrozenModel(BaseModel):
    """Reject unknown fields, implicit coercion and post-construction mutation."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _slot_sha256(slot: CandidateSlot) -> str:
    """Bind the complete frozen slot, including its twelve-field family identity.

    The whole slot is digested rather than the seed and parameters alone: two
    slots can agree on every parameter and still belong to different families,
    and a package that bound only the parameters could be replayed across them.
    """

    slot = _revalidated(slot)
    return canonical_sha256(
        {
            "schema_version": CANDIDATE_SLOT_BINDING_SCHEMA_VERSION,
            "slot": slot.model_dump(mode="json"),
        }
    )


def _inference_source_sha256(source: InferenceTransformSource) -> str:
    """Bind every declared field of an inference source.

    The source carries attestations rather than raw matrices, targets and model
    bytes, so this digest binds those attestations. It does not claim to
    recompute artifacts this layer never receives.
    """

    source = _revalidated(source)
    return canonical_sha256(
        {
            "schema_version": source.schema_version,
            "inference_source": source.model_dump(mode="json"),
        }
    )


def _validate_preprocessing_slot(slot: CandidateSlot) -> CandidateSlot:
    """Check the slot against the one authoritative frozen grid."""

    try:
        slot = validate_frozen_alpha_slot(slot)
    except ContractViolation as error:
        _fail(str(error))
    if slot.fault_type != "preprocessing_bug" or slot.identity.fault_type != "preprocessing_bug":
        _fail("the preprocessing candidate package accepts preprocessing_bug slots only")
    return slot


# --------------------------------------------------------------------------- #
# Role-specific input bundles
# --------------------------------------------------------------------------- #


class FaultDirectedInputs(_StrictFrozenModel):
    """Everything one fault-directed preprocessing candidate was run from.

    The prediction vectors are measured data. There is no metric field and no
    outcome field here: both are computed downstream from these vectors.
    """

    kind: Literal["fault_directed"] = "fault_directed"
    schema_version: Literal["p2-fault-directed-inputs/v1"] = "p2-fault-directed-inputs/v1"
    source: InferenceTransformSource
    spec: EncoderMappingMismatchSpec
    result: EncoderMappingMismatchResult
    test_set: CleanTestSet
    clean_reference_predictions: PredictionVector
    mismatched_predictions: PredictionVector

    @model_validator(mode="after")
    def _prediction_roles_are_declared(self) -> FaultDirectedInputs:
        if self.clean_reference_predictions.role != "reference":
            raise ValueError("the clean-reference vector must declare the reference role")
        if self.mismatched_predictions.role != "observed":
            raise ValueError("the mismatched vector must declare the observed role")
        return self


class RepairControlInputs(_StrictFrozenModel):
    """Everything the M3-I1 repair control was run from.

    The reference is the predeclared mismatched transform, not the clean one:
    the control asks whether restoring the fitted mapping recovers what the
    mismatch cost.
    """

    kind: Literal["improvement_control"] = "improvement_control"
    schema_version: Literal["p2-repair-control-inputs/v1"] = "p2-repair-control-inputs/v1"
    source: InferenceTransformSource
    spec: EncoderMappingRepairSpec
    result: EncoderMappingRepairResult
    test_set: CleanTestSet
    mismatched_reference_predictions: PredictionVector
    repaired_predictions: PredictionVector

    @model_validator(mode="after")
    def _prediction_roles_are_declared(self) -> RepairControlInputs:
        if self.mismatched_reference_predictions.role != "reference":
            raise ValueError("the mismatched-reference vector must declare the reference role")
        if self.repaired_predictions.role != "observed":
            raise ValueError("the repaired vector must declare the observed role")
        return self


class BenignControlInputs(_StrictFrozenModel):
    """Everything the M3-B1 layout control was run from.

    Post-execution evidence is optional because a constructed control that has
    not been measured yet is a real, honest state. It is not a pass and it is
    not a failure.
    """

    kind: Literal["benign_control"] = "benign_control"
    schema_version: Literal["p2-benign-control-inputs/v1"] = "p2-benign-control-inputs/v1"
    table: NamedFeatureTable
    spec: ColumnPermutationSpec
    result: ColumnPermutationResult
    test_set: CleanTestSet | None = None
    evidence: BenignEquivalenceEvidence | None = None

    @model_validator(mode="after")
    def _evidence_comes_with_its_test_set(self) -> BenignControlInputs:
        if (self.test_set is None) != (self.evidence is None):
            raise ValueError(
                "post-execution evidence and its clean test set must be supplied together"
            )
        return self


CandidateInputs = Annotated[
    FaultDirectedInputs | RepairControlInputs | BenignControlInputs,
    Field(discriminator="kind"),
]

#: Which slot role each input bundle is allowed to package.
_KIND_TO_ROLE: Final[dict[str, CandidateRole]] = {
    "fault_directed": "fault_directed",
    "improvement_control": "designed_improvement_control",
    "benign_control": "designed_benign_control",
}


# --------------------------------------------------------------------------- #
# The package
# --------------------------------------------------------------------------- #


class PreprocessingCandidatePackage(_StrictFrozenModel):
    """One preprocessing candidate on its way toward a later admission decision.

    Every identity field is recomputed by
    :func:`validate_preprocessing_candidate_package` rather than trusted, and
    the role-to-content matrix below is enforced by a validator rather than by
    convention.

    The model deliberately owns no ``family_class``, ``case_family_id``,
    ``admission``, ``evidence_condition`` or ``cause`` field. A caller cannot
    smuggle an admission decision through a package, because there is nowhere
    to put one.
    """

    schema_version: Literal["p2-preprocessing-candidate-package/v1"]
    package_protocol_version: Literal["preprocessing-candidate-package/v1"]
    eligibility_policy_version: Literal["preprocessing-bug-impact/alpha-v1"]

    # Identity, all recomputable from the slot.
    candidate_id: CandidateId
    slot_id: SlotId
    proposed_family_sha256: Sha256
    slot_sha256: Sha256
    role: CandidateRole
    fault_type: Literal["preprocessing_bug"]
    intervention_type: str = Field(min_length=1, max_length=128)
    dataset_sha256: Sha256
    model_data_split_manifest_sha256: Sha256
    model_specification_sha256: Sha256
    preprocessing_specification_sha256: Sha256

    # Execution and the artifact it produced.
    execution: ExecutedCandidate
    artifact_sha256: Sha256
    source_binding_sha256: Sha256

    # Measurement. Present exactly when the role and status say it should be.
    evaluation_source_sha256: Sha256 | None = None
    metric_comparison: MetricComparison | None = None
    measured_primary_outcome: PrimaryOutcome | None = None
    repair_measurement: RepairControlMeasurement | None = None
    equivalence_status: BenignEquivalenceStatus | None = None

    # Disposition.
    # A missing disposition is legal only while a benign control is waiting for
    # post-execution equivalence evidence.  Missing evidence is not technical
    # validity and must not enter the valid-candidate ledger.
    disposition: TechnicalDispositionEntry | None
    status: CandidateStatus

    @model_validator(mode="after")
    def _package_is_internally_consistent(self) -> PreprocessingCandidatePackage:
        # Nested models are not revalidated by Pydantic when they arrive
        # already constructed, so rebuild the ones that carry derived numbers.
        if self.metric_comparison is not None:
            MetricComparison.model_validate(self.metric_comparison.model_dump())
        if self.repair_measurement is not None:
            RepairControlMeasurement.model_validate(self.repair_measurement.model_dump())

        expected_candidate = candidate_id_for(
            slot_id=self.slot_id, family_fingerprint=self.proposed_family_sha256
        )
        if self.candidate_id != expected_candidate:
            raise ValueError("candidate_id must be derived from the slot and the fingerprint")

        execution = self.execution
        if execution.candidate_id != self.candidate_id or execution.slot_id != self.slot_id:
            raise ValueError("the execution record must describe this candidate")
        if execution.role != self.role or execution.fault_type != self.fault_type:
            raise ValueError("the execution record must carry this role and fault type")
        if execution.proposed_family_sha256 != self.proposed_family_sha256:
            raise ValueError("the execution record must carry this family fingerprint")
        if execution.dataset_sha256 != self.dataset_sha256:
            raise ValueError("the execution record must carry this dataset digest")
        if execution.model_data_split_manifest_sha256 != self.model_data_split_manifest_sha256:
            raise ValueError("the execution record must carry this split manifest digest")
        if self.disposition is not None and self.disposition.candidate_id != self.candidate_id:
            raise ValueError("the disposition must describe this candidate")

        if self.measured_primary_outcome is not None and self.metric_comparison is None:
            raise ValueError("a measured outcome requires the comparison it was read from")
        if (
            self.metric_comparison is not None
            and self.measured_primary_outcome is not None
            and self.measured_primary_outcome != self.metric_comparison.measured_primary_outcome
        ):
            raise ValueError("measured_primary_outcome must be the outcome the comparison derived")
        if self.metric_comparison is not None and self.evaluation_source_sha256 is None:
            raise ValueError("a comparison must be bound to the clean test set it scored")
        if (
            self.metric_comparison is not None
            and self.metric_comparison.evaluation_source_sha256 != self.evaluation_source_sha256
        ):
            raise ValueError("the comparison is bound to a different clean test set")

        self._check_role_content()
        self._check_status_content()
        return self

    def _check_role_content(self) -> None:
        """Reject content that does not belong to the declared role."""

        if self.role == "designed_benign_control":
            if self.equivalence_status is None:
                raise ValueError("a benign control must record how far equivalence was verified")
            if self.repair_measurement is not None:
                raise ValueError("a benign control does not produce a repair measurement")
            if self.measured_primary_outcome is not None:
                raise ValueError(
                    "a benign control is decided by equivalence, not by a primary-metric "
                    "outcome; recording one would let it be relabelled stable"
                )
            return

        if self.equivalence_status is not None:
            raise ValueError("equivalence status is reserved for the benign control")
        if self.role == "designed_improvement_control":
            if self.repair_measurement is None and self.status != "technically_rejected":
                raise ValueError("an improvement control must record its repair measurement")
        elif self.repair_measurement is not None:
            raise ValueError("a repair measurement is reserved for the improvement control")

    def _check_status_content(self) -> None:
        """Reject a status the rest of the package does not support."""

        if self.status == "technically_rejected":
            if self.disposition is None:
                raise ValueError("a rejected package must carry a technical rejection")
            if self.disposition.disposition != "technical_rejected":
                raise ValueError("a rejected package must carry a technical rejection")
            if self.disposition.rejection_reason is None:
                raise ValueError("a technical rejection must name a machine-readable reason")
            if self.measured_primary_outcome is not None:
                raise ValueError("a rejected candidate carries no measured outcome")
            return

        if self.status == "pending_post_execution_equivalence":
            if self.role != "designed_benign_control":
                raise ValueError("only a benign control can be pending equivalence")
            if self.equivalence_status != "pending_post_execution_equivalence":
                raise ValueError("the package status must match the equivalence status")
            if self.disposition is not None:
                raise ValueError(
                    "a benign control pending equivalence must not carry a technical disposition"
                )
            if self.metric_comparison is not None or self.evaluation_source_sha256 is not None:
                raise ValueError("a benign control pending equivalence must not carry evaluation")
            return

        if self.disposition is None or self.disposition.disposition != "technically_valid":
            raise ValueError("only a rejected package may carry a technical rejection")

        if self.status == "validity_review_required":
            if self.role == "designed_benign_control":
                raise ValueError("a benign control is not decided by a primary-metric review")
            if self.metric_comparison is None or self.measured_primary_outcome is None:
                raise ValueError("a validity review needs a completed metric measurement")
        elif self.status == "equivalence_verified_pending_admission":
            if self.role != "designed_benign_control":
                raise ValueError("only a benign control can reach verified equivalence")
            if self.equivalence_status != "equivalence_verified_pending_admission":
                raise ValueError("the package status must match the equivalence status")

    def artifact_package_sha256(self) -> str:
        """Digest every serialized field of the package.

        Order-sensitive by design: the package binds ordered intervention and
        prediction artifacts through their digests, so permuting records or
        predictions produces a different package even though raw vectors are
        not duplicated here.

        This is an integrity digest, not an identity. Family identity is
        :attr:`proposed_family_sha256`, which is computed from twelve frozen
        fields and therefore does not move when an outcome does.
        """

        return canonical_sha256(
            {
                "digest_schema_version": CANDIDATE_PACKAGE_DIGEST_SCHEMA_VERSION,
                "package": self.model_dump(mode="json"),
            }
        )


# --------------------------------------------------------------------------- #
# Construction
# --------------------------------------------------------------------------- #


class _IdentityDigests(NamedTuple):
    """The four system digests the package copies straight from the slot."""

    dataset_sha256: str
    model_data_split_manifest_sha256: str
    model_specification_sha256: str
    preprocessing_specification_sha256: str


def _identity_digests(slot: CandidateSlot) -> _IdentityDigests:
    """Copy the identity digests the package binds, straight from the slot."""

    identity = slot.identity
    return _IdentityDigests(
        dataset_sha256=identity.dataset_sha256,
        model_data_split_manifest_sha256=identity.model_data_split_manifest_sha256,
        model_specification_sha256=identity.model_specification_sha256,
        preprocessing_specification_sha256=identity.preprocessing_specification_sha256,
    )


def _execution_for(
    *, slot: CandidateSlot, candidate_id: str, fingerprint: str
) -> ExecutedCandidate:
    """Build the execution record for one slot run."""

    return ExecutedCandidate(
        candidate_id=candidate_id,
        slot_id=slot.slot_id,
        fault_type=slot.fault_type,
        role=slot.role,
        slot_kind=slot.slot_kind,
        proposed_family_sha256=fingerprint,
        dataset_sha256=slot.identity.dataset_sha256,
        model_data_split_manifest_sha256=slot.identity.model_data_split_manifest_sha256,
    )


def _fault_directed_package(
    *, slot: CandidateSlot, inputs: FaultDirectedInputs, candidate_id: str, fingerprint: str
) -> PreprocessingCandidatePackage:
    """Package a fault-directed mismatch candidate."""

    digests = _identity_digests(slot)
    validated = validate_preprocessing_intervention(
        inputs.result, source=inputs.source, spec=inputs.spec, slot=slot
    )
    validate_inference_evaluation_binding(source=inputs.source, test_set=inputs.test_set)
    comparison = compare_binary_metrics(
        test_set=inputs.test_set,
        reference_predictions=inputs.clean_reference_predictions,
        observed_predictions=inputs.mismatched_predictions,
    )
    if len(inputs.mismatched_predictions.predictions) != len(validated.record_ids):
        _fail("the prediction vectors do not cover the records the intervention touched")

    return PreprocessingCandidatePackage(
        schema_version=CANDIDATE_PACKAGE_SCHEMA_VERSION,
        package_protocol_version=CANDIDATE_PACKAGE_PROTOCOL_VERSION,
        eligibility_policy_version=ELIGIBILITY_POLICY_VERSION,
        candidate_id=candidate_id,
        slot_id=slot.slot_id,
        proposed_family_sha256=fingerprint,
        slot_sha256=_slot_sha256(slot),
        role=slot.role,
        fault_type="preprocessing_bug",
        intervention_type=slot.identity.intervention_type,
        execution=_execution_for(slot=slot, candidate_id=candidate_id, fingerprint=fingerprint),
        artifact_sha256=validated.artifact_sha256(),
        source_binding_sha256=_inference_source_sha256(inputs.source),
        evaluation_source_sha256=inputs.test_set.artifact_sha256(),
        metric_comparison=comparison,
        measured_primary_outcome=comparison.measured_primary_outcome,
        disposition=TechnicalDispositionEntry(
            candidate_id=candidate_id, disposition="technically_valid"
        ),
        status="validity_review_required",
        dataset_sha256=digests.dataset_sha256,
        model_data_split_manifest_sha256=digests.model_data_split_manifest_sha256,
        model_specification_sha256=digests.model_specification_sha256,
        preprocessing_specification_sha256=digests.preprocessing_specification_sha256,
    )


def _repair_control_package(
    *, slot: CandidateSlot, inputs: RepairControlInputs, candidate_id: str, fingerprint: str
) -> PreprocessingCandidatePackage:
    """Package an improvement-control repair candidate."""

    digests = _identity_digests(slot)
    validated = validate_encoder_mapping_repair(
        inputs.result, source=inputs.source, spec=inputs.spec, slot=slot
    )
    measurement = measure_repair_control(
        result=validated,
        source=inputs.source,
        spec=inputs.spec,
        slot=slot,
        test_set=inputs.test_set,
        mismatched_reference_predictions=inputs.mismatched_reference_predictions,
        repaired_predictions=inputs.repaired_predictions,
    )
    return PreprocessingCandidatePackage(
        schema_version=CANDIDATE_PACKAGE_SCHEMA_VERSION,
        package_protocol_version=CANDIDATE_PACKAGE_PROTOCOL_VERSION,
        eligibility_policy_version=ELIGIBILITY_POLICY_VERSION,
        candidate_id=candidate_id,
        slot_id=slot.slot_id,
        proposed_family_sha256=fingerprint,
        slot_sha256=_slot_sha256(slot),
        role=slot.role,
        fault_type="preprocessing_bug",
        intervention_type=slot.identity.intervention_type,
        execution=_execution_for(slot=slot, candidate_id=candidate_id, fingerprint=fingerprint),
        artifact_sha256=validated.artifact_sha256(),
        source_binding_sha256=_inference_source_sha256(inputs.source),
        evaluation_source_sha256=inputs.test_set.artifact_sha256(),
        metric_comparison=measurement.comparison,
        measured_primary_outcome=measurement.comparison.measured_primary_outcome,
        repair_measurement=measurement,
        disposition=TechnicalDispositionEntry(
            candidate_id=candidate_id, disposition="technically_valid"
        ),
        status="validity_review_required",
        dataset_sha256=digests.dataset_sha256,
        model_data_split_manifest_sha256=digests.model_data_split_manifest_sha256,
        model_specification_sha256=digests.model_specification_sha256,
        preprocessing_specification_sha256=digests.preprocessing_specification_sha256,
    )


def _benign_control_package(
    *, slot: CandidateSlot, inputs: BenignControlInputs, candidate_id: str, fingerprint: str
) -> PreprocessingCandidatePackage:
    """Package a benign layout-control candidate.

    Equivalence failure becomes a technical rejection with the reason the
    contract already defines. It is never relabelled stable, and it never
    becomes an eligible failure.
    """

    digests = _identity_digests(slot)
    validated = validate_column_permutation(
        inputs.result, table=inputs.table, spec=inputs.spec, slot=slot
    )
    status = benign_equivalence_status(
        result=validated,
        table=inputs.table,
        spec=inputs.spec,
        slot=slot,
        test_set=inputs.test_set,
        evidence=inputs.evidence,
    )
    comparison: MetricComparison | None = None
    evaluation_source: str | None = None
    if inputs.test_set is not None and inputs.evidence is not None:
        evaluation_source = inputs.test_set.artifact_sha256()
        comparison = compare_binary_metrics(
            test_set=inputs.test_set,
            reference_predictions=inputs.evidence.reference_predictions,
            observed_predictions=inputs.evidence.observed_predictions,
        )

    rejected = status == "benign_equivalence_failure"
    disposition: TechnicalDispositionEntry | None = None
    if rejected:
        disposition = TechnicalDispositionEntry(
            candidate_id=candidate_id,
            disposition="technical_rejected",
            rejection_reason="benign_equivalence_failure",
            detail=(
                "the permuted layout did not reproduce the transformed matrix, the predictions "
                "or the metric snapshot"
            ),
        )
    elif status == "equivalence_verified_pending_admission":
        disposition = TechnicalDispositionEntry(
            candidate_id=candidate_id,
            disposition="technically_valid",
        )
    package_status: CandidateStatus
    if rejected:
        package_status = "technically_rejected"
    elif status == "equivalence_verified_pending_admission":
        package_status = "equivalence_verified_pending_admission"
    else:
        package_status = "pending_post_execution_equivalence"
    return PreprocessingCandidatePackage(
        schema_version=CANDIDATE_PACKAGE_SCHEMA_VERSION,
        package_protocol_version=CANDIDATE_PACKAGE_PROTOCOL_VERSION,
        eligibility_policy_version=ELIGIBILITY_POLICY_VERSION,
        candidate_id=candidate_id,
        slot_id=slot.slot_id,
        proposed_family_sha256=fingerprint,
        slot_sha256=_slot_sha256(slot),
        role=slot.role,
        fault_type="preprocessing_bug",
        intervention_type=slot.identity.intervention_type,
        execution=_execution_for(slot=slot, candidate_id=candidate_id, fingerprint=fingerprint),
        artifact_sha256=validated.artifact_sha256(),
        # The table's own artifact digest is reused rather than a second digest
        # defined here: one more hash over the same data would be one more claim
        # nobody checks against the first.
        source_binding_sha256=inputs.table.artifact_sha256(),
        evaluation_source_sha256=None if rejected else evaluation_source,
        metric_comparison=None if rejected else comparison,
        equivalence_status=status,
        disposition=disposition,
        status=package_status,
        dataset_sha256=digests.dataset_sha256,
        model_data_split_manifest_sha256=digests.model_data_split_manifest_sha256,
        model_specification_sha256=digests.model_specification_sha256,
        preprocessing_specification_sha256=digests.preprocessing_specification_sha256,
    )


def build_preprocessing_candidate_package(
    *, slot: CandidateSlot, inputs: CandidateInputs
) -> PreprocessingCandidatePackage:
    """Compose one frozen slot and its artifacts into a candidate package.

    The single public entry point for every M3 role, including the reserve fault
    slots: the role comes from the frozen grid, not from a second API that could
    drift away from the first.

    Nothing here decides eligibility, produces a family class, admits a
    candidate or materialises a diagnosis context.
    """

    slot = _validate_preprocessing_slot(slot)
    inputs = _revalidated(inputs)

    expected_role = _KIND_TO_ROLE[inputs.kind]
    if slot.role != expected_role:
        _fail(f"a {inputs.kind!r} bundle cannot package a {slot.role!r} slot")

    fingerprint = proposed_family_sha256(slot.identity)
    candidate_id = candidate_id_for(slot_id=slot.slot_id, family_fingerprint=fingerprint)

    if isinstance(inputs, FaultDirectedInputs):
        return _fault_directed_package(
            slot=slot, inputs=inputs, candidate_id=candidate_id, fingerprint=fingerprint
        )
    if isinstance(inputs, RepairControlInputs):
        return _repair_control_package(
            slot=slot, inputs=inputs, candidate_id=candidate_id, fingerprint=fingerprint
        )
    return _benign_control_package(
        slot=slot, inputs=inputs, candidate_id=candidate_id, fingerprint=fingerprint
    )


# --------------------------------------------------------------------------- #
# Authoritative validation
# --------------------------------------------------------------------------- #


def validate_preprocessing_candidate_package(
    package: PreprocessingCandidatePackage,
    *,
    slot: CandidateSlot,
    inputs: CandidateInputs,
) -> PreprocessingCandidatePackage:
    """Recompute the whole package and reject any mismatch.

    This is the one authoritative entry point. The package is rebuilt from its
    own dump first, so anything assembled with ``model_copy`` or
    ``model_construct`` is re-validated rather than trusted, and the entire
    construction is then run again and compared by digest — so a forged field
    anywhere in the package is caught by one check rather than by a list of
    checks somebody has to keep complete.
    """

    package = _revalidated(package)
    expected = build_preprocessing_candidate_package(slot=slot, inputs=inputs)

    if package.candidate_id != expected.candidate_id:
        _fail("the package candidate identifier does not match the recomputed identity")
    if package.proposed_family_sha256 != expected.proposed_family_sha256:
        _fail("the package family fingerprint does not match the recomputed identity")
    if package.slot_sha256 != expected.slot_sha256:
        _fail("the package is not bound to this frozen slot")
    if package.artifact_sha256 != expected.artifact_sha256:
        _fail("the package is not bound to this artifact")
    if package.source_binding_sha256 != expected.source_binding_sha256:
        _fail("the package is not bound to this source")
    if package.evaluation_source_sha256 != expected.evaluation_source_sha256:
        _fail("the package is not bound to this clean test set")
    if package.measured_primary_outcome != expected.measured_primary_outcome:
        _fail("the measured outcome does not match the recomputed measurement")
    if package.equivalence_status != expected.equivalence_status:
        _fail("the equivalence status does not match the recomputed evidence")
    if package.status != expected.status:
        _fail("the package status does not match the recomputed construction")

    if package.metric_comparison is not None:
        if isinstance(inputs, FaultDirectedInputs):
            validate_metric_comparison(
                package.metric_comparison,
                test_set=inputs.test_set,
                reference_predictions=inputs.clean_reference_predictions,
                observed_predictions=inputs.mismatched_predictions,
            )
        elif isinstance(inputs, RepairControlInputs):
            validate_metric_comparison(
                package.metric_comparison,
                test_set=inputs.test_set,
                reference_predictions=inputs.mismatched_reference_predictions,
                observed_predictions=inputs.repaired_predictions,
            )
        elif inputs.test_set is not None and inputs.evidence is not None:
            validate_metric_comparison(
                package.metric_comparison,
                test_set=inputs.test_set,
                reference_predictions=inputs.evidence.reference_predictions,
                observed_predictions=inputs.evidence.observed_predictions,
            )

    if package.repair_measurement is not None and isinstance(inputs, RepairControlInputs):
        validate_repair_control_measurement(
            package.repair_measurement,
            result=inputs.result,
            source=inputs.source,
            spec=inputs.spec,
            slot=slot,
            test_set=inputs.test_set,
            mismatched_reference_predictions=inputs.mismatched_reference_predictions,
            repaired_predictions=inputs.repaired_predictions,
        )

    if package.artifact_package_sha256() != expected.artifact_package_sha256():
        _fail("the package does not match the recomputed construction")
    return package
