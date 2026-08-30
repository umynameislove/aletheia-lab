"""Post-P2R mechanism reconciliation and fail-closed P4/P5 filter policy."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.benchmark.p2.confirmatory_v3_runtime import V3RuntimeError
from aletheia_lab.benchmark.p2.downstream_disposition import (
    load_downstream_disposition_policy,
    verify_frozen_downstream_policy,
)
from aletheia_lab.benchmark.p2.identity import SHA256_PATTERN
from aletheia_lab.benchmark.p2.p2r_v1_2_results import (
    P2RV12PublicationSummary,
    load_p2r_v1_2_publication_summary,
)

DISPOSITION_POLICY_V2_SCHEMA_VERSION: Final[Literal["p2-downstream-disposition-policy/2"]] = (
    "p2-downstream-disposition-policy/2"
)
FILTER_MANIFEST_SCHEMA_VERSION: Final[Literal["p4-p5-mechanism-filter-manifest/1"]] = (
    "p4-p5-mechanism-filter-manifest/1"
)
DEFAULT_DISPOSITION_POLICY_V2_PATH = Path(
    "configs/benchmark/provenance/p2_downstream_disposition_policy_v2.json"
)
DEFAULT_P4_P5_FILTER_MANIFEST_PATH = Path(
    "configs/benchmark/provenance/p4_p5_mechanism_filter_manifest.json"
)
PREDECESSOR_POLICY_SHA256: Final[str] = (
    "ddd5fdabff327ba42c4a4e175954c1e82df2ca2d6ec0029bcfd6403eac8ca32b"
)
P2R_PUBLICATION_SUMMARY_SHA256: Final[str] = (
    "e8595547c26f73f81d20fd53d00e5e876bb3a3c4f4044027bf8c9c6205fedd2e"
)
LABEL_NOISE_PROTOCOL_SHA256: Final[str] = (
    "5fa057e17203b00fa78fc86d58ce5b324bb30cebf916ebb94a3d6b389f30b456"
)
LABEL_NOISE_TERMINAL_STORE_SHA256: Final[str] = (
    "d2a4537de7f25a069cd23c7942d0e3d3cef9c6e4fea826a7080d61a04f95f152"
)
LABEL_NOISE_TERMINAL_ARTIFACT_SHA256: Final[str] = (
    "9b8d87cbd3e52dc5c6da50066c6816d5620457a3d8ac8094fafc8136560339c4"
)

Sha256 = Annotated[str, Field(pattern=SHA256_PATTERN)]
GitCommit = Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
MechanismId = Literal["data_drift", "preprocessing_mismatch", "label_noise"]
ScientificStatus = Literal["admitted", "assumption_limited", "rejected"]


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, allow_inf_nan=False)


class ConfirmatoryEvidenceBinding(_StrictFrozenModel):
    """Minimum content-addressed evidence required for a terminal mechanism state."""

    study_id: str = Field(min_length=1)
    protocol_sha256: Sha256
    execution_protocol_sha256: Sha256 | None
    terminal_store_sha256: Sha256
    terminal_artifact_sha256: Sha256
    mechanism_closeout_sha256: Sha256 | None
    registered_study_completed: Literal[True]


class MechanismDispositionV2(_StrictFrozenModel):
    mechanism_id: MechanismId
    implementation_merged: Literal[True]
    scientific_status: ScientificStatus
    terminal_disposition: Literal["cross_dataset_admission", "assumption_limited", "rejected"]
    all_prespecified_admission_gates_passed: bool
    cross_dataset_claim_allowed: bool
    diagnostic_ground_truth_eligible: bool
    evidence: ConfirmatoryEvidenceBinding

    @model_validator(mode="after")
    def _status_matches_terminal_evidence(self) -> MechanismDispositionV2:
        admitted = self.scientific_status == "admitted"
        if admitted != self.all_prespecified_admission_gates_passed:
            raise ValueError("admission must equal the all-gates-pass verdict")
        if (
            admitted != self.cross_dataset_claim_allowed
            or admitted != self.diagnostic_ground_truth_eligible
        ):
            raise ValueError("only an admitted mechanism may enter diagnosis-positive denominators")
        if admitted and self.terminal_disposition != "cross_dataset_admission":
            raise ValueError("admitted status requires cross-dataset terminal admission")
        if self.scientific_status == "assumption_limited":
            if self.terminal_disposition != "assumption_limited":
                raise ValueError("assumption-limited status must preserve its terminal disposition")
            if self.evidence.mechanism_closeout_sha256 is not None:
                raise ValueError("label-noise evidence must not be rebound to a P2R closeout")
        if self.scientific_status == "rejected" and (
            self.terminal_disposition != "rejected"
            or self.evidence.execution_protocol_sha256 is None
            or self.evidence.mechanism_closeout_sha256 is None
        ):
            raise ValueError("rejected P2R status requires its execution and closeout evidence")
        return self


class ReconciledDenominatorPolicy(_StrictFrozenModel):
    mechanism_inventory: tuple[MechanismId, ...]
    primary_admitted_track: tuple[MechanismId, ...]
    assumption_limited_track: tuple[MechanismId, ...]
    rejected_track: tuple[MechanismId, ...]
    pending_confirmatory_track: tuple[MechanismId, ...]
    diagnostic_ground_truth_track: tuple[MechanismId, ...]
    validity_behavior_track: tuple[MechanismId, ...]
    pooling_across_tracks_forbidden: Literal[True]
    empty_primary_track_must_be_reported: Literal[True]


class ReconciledAdmissionGovernance(_StrictFrozenModel):
    implementation_or_ci_is_not_admission: Literal[True]
    every_prespecified_gate_must_pass: Literal[True]
    p4_or_p5_outcomes_may_change_status: Literal[False]
    retrospective_threshold_or_denominator_changes_forbidden: Literal[True]
    predecessor_policy_remains_immutable: Literal[True]
    future_studies_require_new_independent_protocol_and_evidence: Literal[True]
    rerunning_p2r_v1_2_forbidden: Literal[True]


class DownstreamDispositionPolicyV2(_StrictFrozenModel):
    """Superseding status artifact after the completed P2R v1.2 study."""

    schema_version: Literal["p2-downstream-disposition-policy/2"] = (
        DISPOSITION_POLICY_V2_SCHEMA_VERSION
    )
    predecessor_policy_sha256: Sha256
    p2r_publication_summary_sha256: Sha256
    evidence_snapshot_commit: GitCommit
    reconciled_before_p4_p5_outcomes: Literal[True]
    mechanisms: tuple[MechanismDispositionV2, ...]
    denominators: ReconciledDenominatorPolicy
    admission_governance: ReconciledAdmissionGovernance

    @model_validator(mode="after")
    def _mechanisms_and_denominators_reconcile(self) -> DownstreamDispositionPolicyV2:
        inventory = ("data_drift", "preprocessing_mismatch", "label_noise")
        if (
            tuple(item.mechanism_id for item in self.mechanisms) != inventory
            or self.denominators.mechanism_inventory != inventory
        ):
            raise ValueError("reconciled mechanism inventory must contain all mechanisms once")
        by_status = {
            status: tuple(
                item.mechanism_id for item in self.mechanisms if item.scientific_status == status
            )
            for status in ("admitted", "assumption_limited", "rejected")
        }
        if self.denominators.primary_admitted_track != by_status["admitted"]:
            raise ValueError("primary admitted denominator differs from mechanism states")
        if self.denominators.assumption_limited_track != by_status["assumption_limited"]:
            raise ValueError("assumption-limited denominator differs from mechanism states")
        if self.denominators.rejected_track != by_status["rejected"]:
            raise ValueError("rejected denominator differs from mechanism states")
        if self.denominators.pending_confirmatory_track:
            raise ValueError("all three registered mechanism studies are terminal")
        diagnostic = tuple(
            item.mechanism_id for item in self.mechanisms if item.diagnostic_ground_truth_eligible
        )
        if self.denominators.diagnostic_ground_truth_track != diagnostic:
            raise ValueError("diagnostic ground-truth denominator differs from eligibility")
        if self.denominators.validity_behavior_track != inventory:
            raise ValueError("validity-behavior evaluation must retain the complete inventory")
        return self

    @property
    def n_inventory(self) -> int:
        return len(self.denominators.mechanism_inventory)

    @property
    def n_admitted(self) -> int:
        return len(self.denominators.primary_admitted_track)

    def canonical_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))


class P4P5MechanismFilterManifest(_StrictFrozenModel):
    """Machine-enforced denominator routing for the post-P2R evaluation phases."""

    schema_version: Literal["p4-p5-mechanism-filter-manifest/1"] = FILTER_MANIFEST_SCHEMA_VERSION
    disposition_policy_sha256: Sha256
    mechanism_inventory: tuple[MechanismId, ...]
    primary_causal_diagnosis_track: tuple[MechanismId, ...]
    assumption_limited_abstention_track: tuple[MechanismId, ...]
    instrument_rejection_track: tuple[MechanismId, ...]
    evidence_accountability_track: tuple[MechanismId, ...]
    permitted_assumption_limited_endpoints: tuple[
        Literal[
            "abstention_correctness",
            "evidence_sufficiency",
            "faithfulness",
            "provenance_sufficiency",
            "leakage",
            "reproducibility",
        ],
        ...,
    ]
    permitted_rejection_endpoints: tuple[
        Literal[
            "false_admission_rate",
            "rejection_correctness",
            "evidence_sufficiency",
            "faithfulness",
            "provenance_sufficiency",
            "leakage",
            "reproducibility",
        ],
        ...,
    ]
    causal_diagnosis_scoring_for_non_admitted_forbidden: Literal[True]
    family_clustered_primary_statistics_require_nonempty_admitted_track: Literal[True]
    empty_primary_result_must_be_published: Literal[True]
    pooling_or_denominator_substitution_forbidden: Literal[True]
    p4_p5_outcomes_may_change_filter: Literal[False]

    @model_validator(mode="after")
    def _routing_is_exact(self) -> P4P5MechanismFilterManifest:
        inventory = ("data_drift", "preprocessing_mismatch", "label_noise")
        assumption_endpoints = (
            "abstention_correctness",
            "evidence_sufficiency",
            "faithfulness",
            "provenance_sufficiency",
            "leakage",
            "reproducibility",
        )
        rejection_endpoints = (
            "false_admission_rate",
            "rejection_correctness",
            "evidence_sufficiency",
            "faithfulness",
            "provenance_sufficiency",
            "leakage",
            "reproducibility",
        )
        if (
            self.mechanism_inventory != inventory
            or self.primary_causal_diagnosis_track
            or self.assumption_limited_abstention_track != ("label_noise",)
            or self.instrument_rejection_track
            != (
                "data_drift",
                "preprocessing_mismatch",
            )
            or self.evidence_accountability_track != inventory
            or self.permitted_assumption_limited_endpoints != assumption_endpoints
            or self.permitted_rejection_endpoints != rejection_endpoints
        ):
            raise ValueError("P4/P5 filter routing differs from the terminal mechanism states")
        return self

    def canonical_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))


def load_downstream_disposition_policy_v2(
    path: str | Path = DEFAULT_DISPOSITION_POLICY_V2_PATH,
) -> DownstreamDispositionPolicyV2:
    try:
        return DownstreamDispositionPolicyV2.model_validate_json(
            Path(path).read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as exc:
        raise V3RuntimeError("reconciled downstream policy is unavailable or invalid") from exc


def load_p4_p5_filter_manifest(
    path: str | Path = DEFAULT_P4_P5_FILTER_MANIFEST_PATH,
) -> P4P5MechanismFilterManifest:
    try:
        return P4P5MechanismFilterManifest.model_validate_json(
            Path(path).read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as exc:
        raise V3RuntimeError("P4/P5 mechanism filter manifest is unavailable or invalid") from exc


def verify_reconciled_downstream_policy(
    policy: DownstreamDispositionPolicyV2,
    *,
    p2r_summary: P2RV12PublicationSummary | None = None,
) -> DownstreamDispositionPolicyV2:
    """Reject status inflation, evidence rebinding, or erasure of the predecessor policy."""

    checked = DownstreamDispositionPolicyV2.model_validate(policy.model_dump())
    predecessor = verify_frozen_downstream_policy(load_downstream_disposition_policy())
    summary = p2r_summary or load_p2r_v1_2_publication_summary()
    if (
        predecessor.canonical_sha256() != PREDECESSOR_POLICY_SHA256
        or checked.predecessor_policy_sha256 != PREDECESSOR_POLICY_SHA256
        or summary.canonical_sha256() != P2R_PUBLICATION_SUMMARY_SHA256
        or checked.p2r_publication_summary_sha256 != P2R_PUBLICATION_SUMMARY_SHA256
        or checked.n_inventory != 3
        or checked.n_admitted != 0
    ):
        raise V3RuntimeError("reconciled downstream policy is bound to different evidence")
    drift, preprocessing, label_noise = checked.mechanisms
    p2r_by_mechanism = {item.mechanism: item for item in summary.mechanisms}
    expected = (
        (
            drift.scientific_status,
            drift.evidence.protocol_sha256,
            drift.evidence.execution_protocol_sha256,
            drift.evidence.terminal_store_sha256,
            drift.evidence.mechanism_closeout_sha256,
        ),
        (
            preprocessing.scientific_status,
            preprocessing.evidence.protocol_sha256,
            preprocessing.evidence.execution_protocol_sha256,
            preprocessing.evidence.terminal_store_sha256,
            preprocessing.evidence.mechanism_closeout_sha256,
        ),
        (
            label_noise.scientific_status,
            label_noise.evidence.protocol_sha256,
            label_noise.evidence.terminal_store_sha256,
            label_noise.evidence.terminal_artifact_sha256,
        ),
    )
    observed = (
        (
            "rejected",
            p2r_by_mechanism["data_drift"].amendment_protocol_sha256,
            p2r_by_mechanism["data_drift"].execution_protocol_sha256,
            summary.terminal_store_sha256,
            p2r_by_mechanism["data_drift"].mechanism_closeout_sha256,
        ),
        (
            "rejected",
            p2r_by_mechanism["preprocessing_bug"].amendment_protocol_sha256,
            p2r_by_mechanism["preprocessing_bug"].execution_protocol_sha256,
            summary.terminal_store_sha256,
            p2r_by_mechanism["preprocessing_bug"].mechanism_closeout_sha256,
        ),
        (
            "assumption_limited",
            LABEL_NOISE_PROTOCOL_SHA256,
            LABEL_NOISE_TERMINAL_STORE_SHA256,
            LABEL_NOISE_TERMINAL_ARTIFACT_SHA256,
        ),
    )
    if expected != observed:
        raise V3RuntimeError("mechanism states do not reproduce from terminal evidence")
    return checked


def verify_p4_p5_filter_manifest(
    manifest: P4P5MechanismFilterManifest,
    *,
    policy: DownstreamDispositionPolicyV2 | None = None,
) -> P4P5MechanismFilterManifest:
    checked = P4P5MechanismFilterManifest.model_validate(manifest.model_dump())
    disposition = verify_reconciled_downstream_policy(
        policy or load_downstream_disposition_policy_v2()
    )
    if checked.disposition_policy_sha256 != disposition.canonical_sha256():
        raise V3RuntimeError("P4/P5 filter is bound to another disposition policy")
    if (
        checked.primary_causal_diagnosis_track
        != disposition.denominators.diagnostic_ground_truth_track
        or checked.assumption_limited_abstention_track
        != disposition.denominators.assumption_limited_track
        or checked.instrument_rejection_track != disposition.denominators.rejected_track
    ):
        raise V3RuntimeError("P4/P5 filter denominators differ from the reconciled policy")
    return checked
