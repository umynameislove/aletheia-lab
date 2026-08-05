"""Family-ready candidate packages for the data-drift mechanism.

This module composes what the mechanism already has — a frozen slot, a
resampled batch, the observed evaluation rows and an authoritative measurement —
into one package that a later admission layer can consume. It adds no new
research semantics: every rule it applies is imported from the module that owns
it.

Four boundaries decide what this module may and may not conclude.

**Identity is recomputed, never accepted.** The family fingerprint comes from
:func:`proposed_family_sha256` applied to the slot's own twelve-field identity;
the family identifier and the candidate identifier follow from it. A caller
cannot supply any of the three, and the validator recomputes all of them.

**A shifted distribution is not a failed model.** The population stability index
says the marginal moved. Whether the model suffered is a separate question,
answered by a metric comparison this package binds but does not interpret. A
fault-directed drift candidate therefore stops at ``validity_review_required``:
the macro-F1 and minority-recall harm thresholds for this mechanism are not
frozen, so no eligibility can honestly be decided here.

**No family class is produced.** There is no ``family_class``, no
``AdmissionRecord``, no accepted family, no case family identifier, no evidence
condition, no diagnosis projection and no cause label anywhere in this module.

**A benign control cannot be promoted.** M1-B1 passes technically only when the
distribution and the metrics are both equivalent inside the pinned tolerance.
When either fails, the candidate becomes a technical rejection carrying the
``benign_equivalence_failure`` reason the contract already defines. It never
becomes stable, and it never becomes an eligible failure.

Two error kinds are raised, matching the other Phase 2 mechanisms:

* a malformed *object* raises :class:`pydantic.ValidationError`;
* a malformed *relationship between objects* raises
  :class:`DataDriftFamilyError`.

The difference matters here. A forged artifact, a replayed slot or a tampered
digest is a trust failure and raises; recording it as a tidy "rejected
candidate" would turn evidence of tampering into an ordinary row in a table.
Only a candidate that ran honestly and then failed its own equivalence contract
becomes a technical rejection.
"""

from __future__ import annotations

from typing import Annotated, Final, Literal, NamedTuple, NoReturn, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aletheia_lab.benchmark.p2.binary_evaluation import CleanTestSet, PredictionVector
from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.benchmark.p2.contracts import (
    CandidateRole,
    CandidateSlot,
    ExecutedCandidate,
    TechnicalDispositionEntry,
)
from aletheia_lab.benchmark.p2.data_drift import (
    DRIFT_INTERVENTION_TYPE,
    RESAMPLING_CONTROL_INTERVENTION_TYPE,
    CategoricalDriftResult,
    CategoricalDriftSpec,
    DriftEvaluationSource,
    DriftMeasurement,
    DriftObservedEvaluationSet,
    measure_drift_candidate,
    validate_categorical_drift,
    validate_drift_measurement,
    validate_drift_observed_evaluation_set,
)
from aletheia_lab.benchmark.p2.identity import (
    CANDIDATE_ID_PATTERN,
    FAMILY_ID_PATTERN,
    SHA256_PATTERN,
    SLOT_ID_PATTERN,
    candidate_id_for,
    family_id_for,
    proposed_family_sha256,
)
from aletheia_lab.benchmark.p2.validation import ContractViolation, validate_frozen_alpha_slot

# --------------------------------------------------------------------------- #
# Schema and protocol versions
# --------------------------------------------------------------------------- #

DRIFT_CANDIDATE_PACKAGE_SCHEMA_VERSION: Final[Literal["p2-drift-candidate-package/v1"]] = (
    "p2-drift-candidate-package/v1"
)
DRIFT_CANDIDATE_PACKAGE_DIGEST_SCHEMA_VERSION: Final[
    Literal["p2-drift-candidate-package-digest/v1"]
] = "p2-drift-candidate-package-digest/v1"
DRIFT_FAULT_INPUTS_SCHEMA_VERSION: Final[Literal["p2-drift-fault-directed-inputs/v1"]] = (
    "p2-drift-fault-directed-inputs/v1"
)
DRIFT_BENIGN_INPUTS_SCHEMA_VERSION: Final[Literal["p2-drift-benign-control-inputs/v1"]] = (
    "p2-drift-benign-control-inputs/v1"
)
DRIFT_PREDICTION_EVIDENCE_SCHEMA_VERSION: Final[Literal["p2-drift-prediction-evidence/v1"]] = (
    "p2-drift-prediction-evidence/v1"
)
DRIFT_PREDICTION_RUN_SCHEMA_VERSION: Final[Literal["p2-drift-prediction-run/v1"]] = (
    "p2-drift-prediction-run/v1"
)
DRIFT_PREDICTION_RUN_DIGEST_SCHEMA_VERSION: Final[Literal["p2-drift-prediction-run-digest/v1"]] = (
    "p2-drift-prediction-run-digest/v1"
)
DRIFT_PREDICTION_RUN_PROTOCOL_VERSION: Final[Literal["attested-binary-prediction-run/v1"]] = (
    "attested-binary-prediction-run/v1"
)
DRIFT_PREDICTION_RUN_ID_PREFIX: Final[str] = "p2-drift-run-"
DRIFT_PREDICTION_RUN_ID_PATTERN: Final[str] = r"^p2-drift-run-[0-9a-f]{64}$"

#: Pinned package protocol, a ``Literal`` rather than free text.
DRIFT_CANDIDATE_PACKAGE_PROTOCOL_VERSION: Final[Literal["drift-candidate-package/v1"]] = (
    "drift-candidate-package/v1"
)

#: The eligibility policy this mechanism is measured under. The package records
#: which policy will read its numbers; it does not apply the policy.
DRIFT_ELIGIBILITY_POLICY_VERSION: Final[Literal["accuracy-regression/v1"]] = (
    "accuracy-regression/v1"
)

#: How far a packaged drift candidate has been taken, and no further.
#:
#: None of these is an eligibility decision or a family class.
DriftCandidateStatus = Literal[
    "validity_review_required",
    "equivalence_verified_pending_admission",
    "technically_rejected",
]

Sha256 = Annotated[str, Field(pattern=SHA256_PATTERN)]
SlotId = Annotated[str, Field(pattern=SLOT_ID_PATTERN)]
CandidateId = Annotated[str, Field(pattern=CANDIDATE_ID_PATTERN)]
FamilyId = Annotated[str, Field(pattern=FAMILY_ID_PATTERN)]

_ModelT = TypeVar("_ModelT", bound=BaseModel)


class DataDriftFamilyError(ContractViolation):
    """Raised when drift candidate-package artifacts disagree with one another."""


def _fail(message: str) -> NoReturn:
    """Reject a cross-object relationship."""

    raise DataDriftFamilyError(message)


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


def _validate_drift_family_slot(slot: CandidateSlot) -> CandidateSlot:
    """Check the slot against the one authoritative frozen grid."""

    try:
        slot = validate_frozen_alpha_slot(slot)
    except ContractViolation as error:
        _fail(str(error))
    if slot.fault_type != "data_drift" or slot.identity.fault_type != "data_drift":
        _fail("the drift candidate package accepts data_drift slots only")
    return slot


# --------------------------------------------------------------------------- #
# Role-specific input bundles
# --------------------------------------------------------------------------- #


PredictionRunRole = Literal["reference", "observed"]
PredictionRunId = Annotated[str, Field(pattern=DRIFT_PREDICTION_RUN_ID_PATTERN)]


def _prediction_run_payload(
    *,
    role: PredictionRunRole,
    model_specification_sha256: str,
    evaluation_source_sha256: str,
    predictions: PredictionVector,
) -> dict[str, object]:
    """Return the only payload from which a drift prediction run is identified."""

    return {
        "schema_version": DRIFT_PREDICTION_RUN_SCHEMA_VERSION,
        "run_protocol_version": DRIFT_PREDICTION_RUN_PROTOCOL_VERSION,
        "role": role,
        "model_specification_sha256": model_specification_sha256,
        "evaluation_source_sha256": evaluation_source_sha256,
        "prediction_vector_sha256": predictions.canonical_sha256(),
    }


def drift_prediction_run_id_for(
    *,
    role: PredictionRunRole,
    model_specification_sha256: str,
    evaluation_source_sha256: str,
    predictions: PredictionVector,
) -> str:
    """Derive a content-addressed ID for one attested prediction run."""

    return DRIFT_PREDICTION_RUN_ID_PREFIX + canonical_sha256(
        _prediction_run_payload(
            role=role,
            model_specification_sha256=model_specification_sha256,
            evaluation_source_sha256=evaluation_source_sha256,
            predictions=predictions,
        )
    )


class DriftPredictionRun(_StrictFrozenModel):
    """One content-addressed prediction-run attestation.

    The artifact binds a vector to an exact model specification and evaluation
    source. Its derived ID catches a synchronized vector/source substitution
    that retains a stale run identity.

    This is an attestation boundary, not proof that model execution occurred.
    Proving external execution would require a trusted runner, signature or
    immutable run registry, none of which this offline benchmark layer owns.
    The deliberately narrow guarantee is internal binding to one complete,
    self-consistent run artifact.
    """

    schema_version: Literal["p2-drift-prediction-run/v1"] = "p2-drift-prediction-run/v1"
    run_protocol_version: Literal["attested-binary-prediction-run/v1"] = (
        "attested-binary-prediction-run/v1"
    )
    run_id: PredictionRunId
    role: PredictionRunRole
    model_specification_sha256: Sha256
    evaluation_source_sha256: Sha256
    predictions: PredictionVector

    @model_validator(mode="after")
    def _identity_is_derived(self) -> DriftPredictionRun:
        if self.predictions.role != self.role:
            raise ValueError("the prediction vector role must match the run role")
        expected = drift_prediction_run_id_for(
            role=self.role,
            model_specification_sha256=self.model_specification_sha256,
            evaluation_source_sha256=self.evaluation_source_sha256,
            predictions=self.predictions,
        )
        if self.run_id != expected:
            raise ValueError("run_id must be derived from the complete prediction-run payload")
        return self

    def artifact_sha256(self) -> str:
        """Digest every serialized run field, including its derived identity."""

        return canonical_sha256(
            {
                "digest_schema_version": DRIFT_PREDICTION_RUN_DIGEST_SCHEMA_VERSION,
                "prediction_run": self.model_dump(mode="json"),
            }
        )


def build_drift_prediction_run(
    *,
    role: PredictionRunRole,
    model_specification_sha256: str,
    evaluation_source_sha256: str,
    predictions: PredictionVector,
) -> DriftPredictionRun:
    """Build, rather than accept, the identity of one prediction-run attestation."""

    predictions = _revalidated(predictions)
    return DriftPredictionRun(
        run_id=drift_prediction_run_id_for(
            role=role,
            model_specification_sha256=model_specification_sha256,
            evaluation_source_sha256=evaluation_source_sha256,
            predictions=predictions,
        ),
        role=role,
        model_specification_sha256=model_specification_sha256,
        evaluation_source_sha256=evaluation_source_sha256,
        predictions=predictions,
    )


class DriftPredictionEvidence(_StrictFrozenModel):
    """The reference and observed run artifacts used by one measurement."""

    schema_version: Literal["p2-drift-prediction-evidence/v1"] = "p2-drift-prediction-evidence/v1"
    reference_run: DriftPredictionRun
    observed_run: DriftPredictionRun

    @model_validator(mode="after")
    def _roles_are_declared(self) -> DriftPredictionEvidence:
        reference = _revalidated(self.reference_run)
        observed = _revalidated(self.observed_run)
        if reference.role != "reference":
            raise ValueError("the clean-reference run must declare the reference role")
        if observed.role != "observed":
            raise ValueError("the drifted run must declare the observed role")
        if reference.evaluation_source_sha256 == observed.evaluation_source_sha256:
            raise ValueError("the clean and drifted evaluation sources must be different artifacts")
        return self

    @property
    def clean_reference_predictions(self) -> PredictionVector:
        """Expose the vector used by the authoritative metric evaluator."""

        return self.reference_run.predictions

    @property
    def observed_predictions(self) -> PredictionVector:
        """Expose the vector used by the authoritative metric evaluator."""

        return self.observed_run.predictions


class _DriftInputsBase(_StrictFrozenModel):
    """The artifacts every drift candidate is packaged from.

    The prediction vectors are measured data. There is no metric field and no
    outcome field here: both are computed downstream from these vectors.
    """

    source: DriftEvaluationSource
    spec: CategoricalDriftSpec
    result: CategoricalDriftResult
    test_set: CleanTestSet
    observed_set: DriftObservedEvaluationSet
    predictions: DriftPredictionEvidence


class DriftFaultDirectedInputs(_DriftInputsBase):
    """Everything one fault-directed M1 candidate was run from."""

    kind: Literal["fault_directed"] = "fault_directed"
    schema_version: Literal["p2-drift-fault-directed-inputs/v1"] = (
        "p2-drift-fault-directed-inputs/v1"
    )


class DriftBenignControlInputs(_DriftInputsBase):
    """Everything the M1-B1 empirical-resampling control was run from."""

    kind: Literal["benign_control"] = "benign_control"
    schema_version: Literal["p2-drift-benign-control-inputs/v1"] = (
        "p2-drift-benign-control-inputs/v1"
    )


DriftCandidateInputs = Annotated[
    DriftFaultDirectedInputs | DriftBenignControlInputs,
    Field(discriminator="kind"),
]

#: Which slot role and intervention type each bundle is allowed to package.
_KIND_TO_ROLE: Final[dict[str, CandidateRole]] = {
    "fault_directed": "fault_directed",
    "benign_control": "designed_benign_control",
}
_KIND_TO_INTERVENTION: Final[dict[str, str]] = {
    "fault_directed": DRIFT_INTERVENTION_TYPE,
    "benign_control": RESAMPLING_CONTROL_INTERVENTION_TYPE,
}


# --------------------------------------------------------------------------- #
# The package
# --------------------------------------------------------------------------- #


class DriftCandidatePackage(_StrictFrozenModel):
    """One data-drift candidate, ready for a later admission decision.

    Every identity field is recomputed by
    :func:`validate_drift_candidate_package` rather than trusted, and the
    role-to-status matrix below is enforced by a validator rather than by
    convention.

    The model deliberately owns no ``family_class``, ``case_family_id``,
    ``admission``, ``evidence_condition``, ``cause`` or diagnosis field. A
    caller cannot smuggle an admission decision through a package, because
    there is nowhere to put one.
    """

    schema_version: Literal["p2-drift-candidate-package/v1"]
    package_protocol_version: Literal["drift-candidate-package/v1"]
    eligibility_policy_version: Literal["accuracy-regression/v1"]

    # Identity, all recomputable from the slot.
    candidate_id: CandidateId
    family_id: FamilyId
    proposed_family_sha256: Sha256
    slot_id: SlotId
    slot_sha256: Sha256
    role: CandidateRole
    fault_type: Literal["data_drift"]
    intervention_type: Literal[
        "categorical_distribution_shift", "empirical_distribution_resampling_control"
    ]

    # The system the candidate ran against, copied from the slot identity.
    dataset_snapshot_id: str = Field(min_length=1, max_length=128)
    dataset_sha256: Sha256
    model_data_split_manifest_sha256: Sha256
    model_specification_sha256: Sha256
    preprocessing_specification_sha256: Sha256

    # Execution and the artifacts it produced.
    execution: ExecutedCandidate
    source_artifact_sha256: Sha256
    spec_sha256: Sha256
    drift_artifact_sha256: Sha256
    reference_evaluation_source_sha256: Sha256
    observed_evaluation_source_sha256: Sha256
    reference_prediction_run_sha256: Sha256
    observed_prediction_run_sha256: Sha256

    # Measurement and disposition.
    measurement: DriftMeasurement
    disposition: TechnicalDispositionEntry
    status: DriftCandidateStatus

    @model_validator(mode="after")
    def _package_is_internally_consistent(self) -> DriftCandidatePackage:
        # Pydantic does not revalidate an already-constructed nested model, so
        # rebuild the one that carries derived numbers and a derived status.
        DriftMeasurement.model_validate(self.measurement.model_dump())

        expected_family = f"p2-family-{self.proposed_family_sha256}"
        if self.family_id != expected_family:
            raise ValueError("family_id must be the namespaced form of the family fingerprint")
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
        if self.disposition.candidate_id != self.candidate_id:
            raise ValueError("the disposition must describe this candidate")

        measurement = self.measurement
        if measurement.intervention_type != self.intervention_type:
            raise ValueError("the measurement must describe this intervention type")
        if measurement.drift_artifact_sha256 != self.drift_artifact_sha256:
            raise ValueError("the measurement must be bound to this drift artifact")
        if measurement.drift_slot_sha256 != self.slot_sha256:
            raise ValueError("the measurement must be bound to this frozen slot")
        if measurement.reference_evaluation_source_sha256 != (
            self.reference_evaluation_source_sha256
        ):
            raise ValueError("the measurement must be bound to this clean evaluation source")
        if measurement.observed_evaluation_source_sha256 != (
            self.observed_evaluation_source_sha256
        ):
            raise ValueError("the measurement must be bound to this observed evaluation source")

        self._check_role_and_status()
        return self

    def _check_role_and_status(self) -> None:
        """Reject a status the role and the measurement do not support."""

        measured = self.measurement.status
        if self.role == "fault_directed":
            if self.intervention_type != DRIFT_INTERVENTION_TYPE:
                raise ValueError("a fault-directed drift candidate shifts the marginal")
            if measured != "validity_review_required":
                raise ValueError(
                    "a fault-directed drift measurement stops at validity review; the "
                    "guardrail policy is not frozen"
                )
            if self.status != "validity_review_required":
                raise ValueError("a fault-directed candidate cannot claim equivalence")
            if self.disposition.disposition != "technically_valid":
                raise ValueError("a measured fault-directed candidate is technically valid")
            return

        if self.role != "designed_benign_control":
            raise ValueError("the drift package supports fault-directed and benign roles only")
        if self.intervention_type != RESAMPLING_CONTROL_INTERVENTION_TYPE:
            raise ValueError("the benign drift control resamples the empirical distribution")

        if measured == "benign_equivalence_failure":
            if self.status != "technically_rejected":
                raise ValueError(
                    "a benign control that failed equivalence is a technical rejection; it "
                    "is never relabelled stable"
                )
            if self.disposition.disposition != "technical_rejected":
                raise ValueError("a rejected package must carry a technical rejection")
            if self.disposition.rejection_reason != "benign_equivalence_failure":
                raise ValueError(
                    "a failed benign control carries the benign_equivalence_failure reason"
                )
            return

        if measured != "equivalence_verified_pending_admission":
            raise ValueError("a benign drift control reports equivalence, verified or failed")
        if self.status != "equivalence_verified_pending_admission":
            raise ValueError("the package status must match the measured equivalence status")
        if self.disposition.disposition != "technically_valid":
            raise ValueError("a verified benign control is technically valid")

    def artifact_package_sha256(self) -> str:
        """Digest every serialized field of the package.

        Order-sensitive by design: the package binds ordered records and
        occurrences through its source/result/evaluation digests, and binds
        ordered prediction vectors through the two prediction-run digests.
        Permuting any of those inputs therefore produces a different package.

        This is an integrity digest, not an identity. Family identity is
        :attr:`proposed_family_sha256`, which is computed from twelve frozen
        fields and therefore does not move when a measurement does.
        """

        return canonical_sha256(
            {
                "digest_schema_version": DRIFT_CANDIDATE_PACKAGE_DIGEST_SCHEMA_VERSION,
                "package": self.model_dump(mode="json"),
            }
        )


# --------------------------------------------------------------------------- #
# Construction
# --------------------------------------------------------------------------- #


class _SystemDigests(NamedTuple):
    """The four system digests the package copies straight from the slot."""

    dataset_sha256: str
    model_data_split_manifest_sha256: str
    model_specification_sha256: str
    preprocessing_specification_sha256: str


def _system_digests(slot: CandidateSlot) -> _SystemDigests:
    identity = slot.identity
    return _SystemDigests(
        dataset_sha256=identity.dataset_sha256,
        model_data_split_manifest_sha256=identity.model_data_split_manifest_sha256,
        model_specification_sha256=identity.model_specification_sha256,
        preprocessing_specification_sha256=identity.preprocessing_specification_sha256,
    )


def _bind_source_to_identity(*, source: DriftEvaluationSource, slot: CandidateSlot) -> None:
    """Refuse a source that belongs to a different dataset snapshot or split.

    The source carries its own snapshot identifier and dataset and split
    digests. If they disagree with the family identity, the batch was drawn
    from data this family never declared, and no amount of downstream hashing
    would make the candidate mean what it claims.
    """

    identity = slot.identity
    if source.dataset_snapshot_id != identity.dataset_snapshot_id:
        _fail("the drift source belongs to a different dataset snapshot")
    if source.dataset_sha256 != identity.dataset_sha256:
        _fail("the drift source belongs to a different dataset")
    if source.model_data_split_manifest_sha256 != identity.model_data_split_manifest_sha256:
        _fail("the drift source belongs to a different model-data split")
    if source.attested_model_sha256 != identity.model_specification_sha256:
        _fail("the drift source was scored against a different model specification")
    if source.attested_preprocessing_specification_sha256 != (
        identity.preprocessing_specification_sha256
    ):
        _fail("the drift source used a different preprocessing specification")


def build_drift_candidate_package(
    *, slot: CandidateSlot, inputs: DriftCandidateInputs
) -> DriftCandidatePackage:
    """Compose one frozen M1 slot and its artifacts into a family-ready package.

    The single public entry point for both M1 roles, including the reserve fault
    slots: the role comes from the frozen grid, not from a second API that could
    drift away from the first.

    Nothing here decides eligibility, produces a family class, admits a
    candidate or materialises a diagnosis context.
    """

    slot = _validate_drift_family_slot(slot)
    inputs = _revalidated(inputs)

    expected_role = _KIND_TO_ROLE[inputs.kind]
    if slot.role != expected_role:
        _fail(f"a {inputs.kind!r} bundle cannot package a {slot.role!r} slot")
    expected_intervention = _KIND_TO_INTERVENTION[inputs.kind]
    if slot.identity.intervention_type != expected_intervention:
        _fail(
            f"a {inputs.kind!r} bundle requires intervention {expected_intervention!r}; "
            f"the slot declares {slot.identity.intervention_type!r}"
        )

    _bind_source_to_identity(source=inputs.source, slot=slot)

    evidence = inputs.predictions
    reference_run = _revalidated(evidence.reference_run)
    observed_run = _revalidated(evidence.observed_run)
    if reference_run.evaluation_source_sha256 != inputs.test_set.artifact_sha256():
        _fail("the clean prediction vector is not bound to this clean evaluation source")
    if observed_run.evaluation_source_sha256 != inputs.observed_set.artifact_sha256():
        _fail("the drifted prediction vector is not bound to this observed evaluation source")
    expected_model_sha256 = slot.identity.model_specification_sha256
    if reference_run.model_specification_sha256 != expected_model_sha256:
        _fail("the clean prediction run used a different model specification")
    if observed_run.model_specification_sha256 != expected_model_sha256:
        _fail("the drifted prediction run used a different model specification")

    # Every artifact is re-derived through the mechanism's own authoritative
    # validators rather than trusted as given.
    drift_result = validate_categorical_drift(
        inputs.result, source=inputs.source, spec=inputs.spec, slot=slot
    )
    observed_set = validate_drift_observed_evaluation_set(
        inputs.observed_set,
        result=drift_result,
        source=inputs.source,
        test_set=inputs.test_set,
    )
    measurement = measure_drift_candidate(
        result=drift_result,
        source=inputs.source,
        spec=inputs.spec,
        slot=slot,
        test_set=inputs.test_set,
        observed_set=observed_set,
        clean_reference_predictions=reference_run.predictions,
        observed_predictions=observed_run.predictions,
    )

    fingerprint = proposed_family_sha256(slot.identity)
    family = family_id_for(slot.identity)
    candidate = candidate_id_for(slot_id=slot.slot_id, family_fingerprint=fingerprint)
    digests = _system_digests(slot)

    rejected = measurement.status == "benign_equivalence_failure"
    disposition = TechnicalDispositionEntry(
        candidate_id=candidate,
        disposition="technical_rejected" if rejected else "technically_valid",
        rejection_reason="benign_equivalence_failure" if rejected else None,
        detail=(
            "the resampled batch did not reproduce the clean distribution or the clean "
            "metric snapshot inside the pinned tolerance"
            if rejected
            else None
        ),
    )
    status: DriftCandidateStatus
    if rejected:
        status = "technically_rejected"
    elif measurement.status == "equivalence_verified_pending_admission":
        status = "equivalence_verified_pending_admission"
    else:
        status = "validity_review_required"

    return DriftCandidatePackage(
        schema_version=DRIFT_CANDIDATE_PACKAGE_SCHEMA_VERSION,
        package_protocol_version=DRIFT_CANDIDATE_PACKAGE_PROTOCOL_VERSION,
        eligibility_policy_version=DRIFT_ELIGIBILITY_POLICY_VERSION,
        candidate_id=candidate,
        family_id=family,
        proposed_family_sha256=fingerprint,
        slot_id=slot.slot_id,
        # The slot digest is taken from the validated measurement rather than
        # recomputed here. A second digest over the same slot with a different
        # domain separator would be two numbers that can never be compared, and
        # the equality the package asserts would be vacuous.
        slot_sha256=measurement.drift_slot_sha256,
        role=slot.role,
        fault_type="data_drift",
        intervention_type=drift_result.provenance.intervention_type,
        dataset_snapshot_id=slot.identity.dataset_snapshot_id,
        dataset_sha256=digests.dataset_sha256,
        model_data_split_manifest_sha256=digests.model_data_split_manifest_sha256,
        model_specification_sha256=digests.model_specification_sha256,
        preprocessing_specification_sha256=digests.preprocessing_specification_sha256,
        execution=ExecutedCandidate(
            candidate_id=candidate,
            slot_id=slot.slot_id,
            fault_type=slot.fault_type,
            role=slot.role,
            slot_kind=slot.slot_kind,
            proposed_family_sha256=fingerprint,
            dataset_sha256=digests.dataset_sha256,
            model_data_split_manifest_sha256=digests.model_data_split_manifest_sha256,
        ),
        source_artifact_sha256=inputs.source.artifact_sha256(),
        spec_sha256=inputs.spec.canonical_sha256(),
        drift_artifact_sha256=drift_result.artifact_sha256(),
        reference_evaluation_source_sha256=measurement.reference_evaluation_source_sha256,
        observed_evaluation_source_sha256=observed_set.artifact_sha256(),
        reference_prediction_run_sha256=reference_run.artifact_sha256(),
        observed_prediction_run_sha256=observed_run.artifact_sha256(),
        measurement=measurement,
        disposition=disposition,
        status=status,
    )


# --------------------------------------------------------------------------- #
# Authoritative validation
# --------------------------------------------------------------------------- #


def validate_drift_candidate_package(
    package: DriftCandidatePackage,
    *,
    slot: CandidateSlot,
    inputs: DriftCandidateInputs,
) -> DriftCandidatePackage:
    """Recompute the whole package and reject any mismatch.

    This is the one authoritative entry point. The package is rebuilt from its
    own dump first, so anything assembled with ``model_copy`` or
    ``model_construct`` is re-validated rather than trusted. The entire
    construction is then run again from the inputs and compared field by field
    and finally by digest — so the check never degenerates into comparing two
    hashes that both came from the same untrusted object.
    """

    package = _revalidated(package)
    expected = build_drift_candidate_package(slot=slot, inputs=inputs)

    for name in (
        "candidate_id",
        "family_id",
        "proposed_family_sha256",
        "slot_id",
        "slot_sha256",
        "role",
        "intervention_type",
        "dataset_snapshot_id",
        "dataset_sha256",
        "model_data_split_manifest_sha256",
        "model_specification_sha256",
        "preprocessing_specification_sha256",
        "source_artifact_sha256",
        "spec_sha256",
        "drift_artifact_sha256",
        "reference_evaluation_source_sha256",
        "observed_evaluation_source_sha256",
        "reference_prediction_run_sha256",
        "observed_prediction_run_sha256",
        "status",
    ):
        if getattr(package, name) != getattr(expected, name):
            _fail(f"package {name} does not match the recomputed construction")

    validate_drift_measurement(
        package.measurement,
        result=inputs.result,
        source=inputs.source,
        spec=inputs.spec,
        slot=slot,
        test_set=inputs.test_set,
        observed_set=inputs.observed_set,
        clean_reference_predictions=inputs.predictions.clean_reference_predictions,
        observed_predictions=inputs.predictions.observed_predictions,
    )

    if package.artifact_package_sha256() != expected.artifact_package_sha256():
        _fail("the package does not match the recomputed construction")
    return package
