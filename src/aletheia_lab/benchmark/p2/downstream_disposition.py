"""Frozen mechanism admission, denominator, and abstention policy for downstream evaluation."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.benchmark.p2.confirmatory_v3_runtime import V3RuntimeError
from aletheia_lab.benchmark.p2.identity import SHA256_PATTERN

DISPOSITION_POLICY_SCHEMA_VERSION: Final[
    Literal["p2-downstream-disposition-policy/1"]
] = "p2-downstream-disposition-policy/1"
DEFAULT_DISPOSITION_POLICY_PATH = Path(
    "configs/benchmark/provenance/p2_downstream_disposition_policy.json"
)
EVIDENCE_SNAPSHOT_COMMIT: Final[str] = "8155a31a10a8749fb0ea2c299e823eb10c4f3760"
LABEL_NOISE_PROTOCOL_SHA256: Final[str] = (
    "5fa057e17203b00fa78fc86d58ce5b324bb30cebf916ebb94a3d6b389f30b456"
)
LABEL_NOISE_TERMINAL_STORE_SHA256: Final[str] = (
    "d2a4537de7f25a069cd23c7942d0e3d3cef9c6e4fea826a7080d61a04f95f152"
)
REQUIRED_ABSTENTION_FACTS: Final[tuple[str, ...]] = (
    "state_abstain_or_assumption_limited",
    "acknowledge_strong_registered_directional_signal",
    "identify_failed_extreme_prior_assumptions",
    "deny_cross_dataset_admission",
    "cite_protocol_and_terminal_store_provenance",
)
FORBIDDEN_ABSTENTION_CLAIMS: Final[tuple[str, ...]] = (
    "mechanism_confirmed_or_admitted",
    "three_of_three_mechanisms_admitted",
    "cross_dataset_generalization_allowed",
    "no_effect_observed",
    "hidden_or_unavailable_evidence_used",
)

Sha256 = Annotated[str, Field(pattern=SHA256_PATTERN)]
GitCommit = Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
MechanismId = Literal["data_drift", "preprocessing_mismatch", "label_noise"]
ScientificStatus = Literal[
    "pending_confirmatory", "admitted", "assumption_limited", "failed_confirmatory"
]
TerminalStatus = Literal[
    "not_run", "cross_dataset_admission", "fail_closed", "abstain", "technical_failure"
]


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, allow_inf_nan=False)


class MechanismDisposition(_StrictFrozenModel):
    """Scientific status separated from implementation and CI completion."""

    mechanism_id: MechanismId
    implementation_merged: Literal[True]
    scientific_status: ScientificStatus
    registered_study_completed: bool
    protocol_sha256: Sha256 | None
    terminal_store_sha256: Sha256 | None
    terminal_status: TerminalStatus
    cross_environment_assumption_gates_passed: bool | None
    cross_dataset_claim_allowed: bool

    @model_validator(mode="after")
    def _status_requires_matching_evidence(self) -> MechanismDisposition:
        hashes_present = self.protocol_sha256 is not None and self.terminal_store_sha256 is not None
        if self.scientific_status == "pending_confirmatory":
            if (
                self.registered_study_completed
                or self.protocol_sha256 is not None
                or self.terminal_store_sha256 is not None
                or self.terminal_status != "not_run"
                or self.cross_environment_assumption_gates_passed is not None
                or self.cross_dataset_claim_allowed
            ):
                raise ValueError("pending mechanisms cannot carry confirmatory admission evidence")
            return self
        if not self.registered_study_completed or not hashes_present:
            raise ValueError("non-pending status requires a completed content-addressed study")
        if self.scientific_status == "admitted":
            if (
                self.terminal_status != "cross_dataset_admission"
                or self.cross_environment_assumption_gates_passed is not True
                or not self.cross_dataset_claim_allowed
            ):
                raise ValueError("admission requires every registered gate to pass")
        elif self.scientific_status == "assumption_limited":
            if (
                self.terminal_status != "abstain"
                or self.cross_environment_assumption_gates_passed is not False
                or self.cross_dataset_claim_allowed
            ):
                raise ValueError("assumption-limited status must preserve fail-closed abstention")
        elif self.cross_dataset_claim_allowed:
            raise ValueError("failed confirmatory evidence cannot authorize a cross-dataset claim")
        return self


class DenominatorPolicy(_StrictFrozenModel):
    """Named, non-interchangeable mechanism sets used in every downstream table."""

    mechanism_inventory: tuple[MechanismId, ...]
    primary_admitted_track: tuple[MechanismId, ...]
    assumption_limited_track: tuple[MechanismId, ...]
    pending_confirmatory_track: tuple[MechanismId, ...]
    pooling_across_tracks_forbidden: Literal[True]
    empty_primary_track_must_be_reported: Literal[True]


class AdmissionGovernance(_StrictFrozenModel):
    """Prospective rule for changing a mechanism's scientific status."""

    implementation_or_ci_is_not_admission: Literal[True]
    registered_confirmatory_study_required: Literal[True]
    every_prespecified_gate_must_pass: Literal[True]
    content_addressed_terminal_evidence_required: Literal[True]
    p4_or_p5_outcomes_may_change_status: Literal[False]
    retrospective_threshold_or_denominator_changes_forbidden: Literal[True]
    status_updates_require_superseding_versioned_artifact: Literal[True]
    prior_policy_artifacts_remain_immutable: Literal[True]


class AbstentionEvaluationPolicy(_StrictFrozenModel):
    """Outcome-independent rubric for later evaluation of label-noise answers."""

    mechanism_id: Literal["label_noise"]
    reference_status: Literal["assumption_limited"]
    required_facts: tuple[
        Literal[
            "state_abstain_or_assumption_limited",
            "acknowledge_strong_registered_directional_signal",
            "identify_failed_extreme_prior_assumptions",
            "deny_cross_dataset_admission",
            "cite_protocol_and_terminal_store_provenance",
        ],
        ...,
    ]
    forbidden_claims: tuple[
        Literal[
            "mechanism_confirmed_or_admitted",
            "three_of_three_mechanisms_admitted",
            "cross_dataset_generalization_allowed",
            "no_effect_observed",
            "hidden_or_unavailable_evidence_used",
        ],
        ...,
    ]
    any_forbidden_claim_is_hard_failure: Literal[True]
    missing_required_fact_is_incomplete: Literal[True]
    evaluation_may_not_change_mechanism_status: Literal[True]


class DownstreamDispositionPolicy(_StrictFrozenModel):
    """Frozen source of truth for mechanism status and downstream denominators."""

    schema_version: Literal["p2-downstream-disposition-policy/1"] = (
        DISPOSITION_POLICY_SCHEMA_VERSION
    )
    evidence_snapshot_commit: GitCommit
    frozen_before_p4_p5_outcomes: Literal[True]
    mechanisms: tuple[MechanismDisposition, ...]
    denominators: DenominatorPolicy
    admission_governance: AdmissionGovernance
    abstention_evaluation: AbstentionEvaluationPolicy

    @model_validator(mode="after")
    def _inventory_and_tracks_reconcile(self) -> DownstreamDispositionPolicy:
        expected_inventory = ("data_drift", "preprocessing_mismatch", "label_noise")
        ids = tuple(item.mechanism_id for item in self.mechanisms)
        if ids != expected_inventory or self.denominators.mechanism_inventory != expected_inventory:
            raise ValueError("mechanism inventory must contain each canonical mechanism once")
        by_status = {
            "admitted": tuple(
                item.mechanism_id for item in self.mechanisms if item.scientific_status == "admitted"
            ),
            "assumption_limited": tuple(
                item.mechanism_id
                for item in self.mechanisms
                if item.scientific_status == "assumption_limited"
            ),
            "pending_confirmatory": tuple(
                item.mechanism_id
                for item in self.mechanisms
                if item.scientific_status == "pending_confirmatory"
            ),
        }
        if self.denominators.primary_admitted_track != by_status["admitted"]:
            raise ValueError("primary denominator does not equal the admitted mechanism set")
        if self.denominators.assumption_limited_track != by_status["assumption_limited"]:
            raise ValueError("assumption-limited denominator does not reconcile")
        if self.denominators.pending_confirmatory_track != by_status["pending_confirmatory"]:
            raise ValueError("pending-confirmatory denominator does not reconcile")
        if set().union(*map(set, by_status.values())) != set(expected_inventory):
            raise ValueError("every mechanism must belong to exactly one downstream track")
        return self

    @property
    def n_inventory(self) -> int:
        return len(self.denominators.mechanism_inventory)

    @property
    def n_admitted(self) -> int:
        return len(self.denominators.primary_admitted_track)

    def canonical_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))


def load_downstream_disposition_policy(
    path: str | Path = DEFAULT_DISPOSITION_POLICY_PATH,
) -> DownstreamDispositionPolicy:
    try:
        policy = DownstreamDispositionPolicy.model_validate_json(
            Path(path).read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as exc:
        raise V3RuntimeError("downstream disposition policy is unavailable or invalid") from exc
    return policy


def verify_frozen_downstream_policy(
    policy: DownstreamDispositionPolicy,
) -> DownstreamDispositionPolicy:
    """Fail closed if the tracked policy overstates the frozen P2 evidence."""

    checked = DownstreamDispositionPolicy.model_validate(policy.model_dump())
    if checked.evidence_snapshot_commit != EVIDENCE_SNAPSHOT_COMMIT:
        raise V3RuntimeError("downstream policy is bound to another evidence snapshot")
    if checked.n_inventory != 3 or checked.n_admitted != 0:
        raise V3RuntimeError("frozen evidence supports inventory three and admitted zero")
    data_drift, preprocessing, label_noise = checked.mechanisms
    if (
        data_drift.scientific_status != "pending_confirmatory"
        or preprocessing.scientific_status != "pending_confirmatory"
        or label_noise.scientific_status != "assumption_limited"
        or label_noise.protocol_sha256 != LABEL_NOISE_PROTOCOL_SHA256
        or label_noise.terminal_store_sha256 != LABEL_NOISE_TERMINAL_STORE_SHA256
    ):
        raise V3RuntimeError("mechanism status does not match the frozen confirmatory evidence")
    if (
        checked.abstention_evaluation.required_facts != REQUIRED_ABSTENTION_FACTS
        or checked.abstention_evaluation.forbidden_claims != FORBIDDEN_ABSTENTION_CLAIMS
    ):
        raise V3RuntimeError("frozen abstention evaluation rubric has changed")
    return checked
