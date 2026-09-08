"""Independent integrity and sampling-feasibility closeout for a claim pool."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aletheia_lab.evaluation.claim_corpus_audit import (
    load_audited_claim_corpus_run,
)
from aletheia_lab.evaluation.claim_corpus_construction import (
    ClaimPoolPreparationLike,
)
from aletheia_lab.evaluation.claim_corpus_construction_contracts import (
    ClaimPoolPreparation,
    ClaimPoolPublicationCloseout,
    ClaimRelationResultBundle,
    RecoveryClaimPoolPreparation,
)
from aletheia_lab.evaluation.claim_corpus_contracts import (
    ClaimCorpusContractError,
    ClaimCorpusRequestCensus,
    ClaimSupportCorpusEntry,
    SupportLabel,
)
from aletheia_lab.evaluation.claim_corpus_protocol import ClaimSupportCorpusProtocol
from aletheia_lab.evaluation.claim_corpus_recovery_closeout import (
    RecoveryExecutionCloseout,
)
from aletheia_lab.evaluation.claim_evidence_census import ObservedEvidenceCensus
from aletheia_lab.evaluation.claim_evidence_semantics import (
    visible_relations_from_assignment,
)
from aletheia_lab.evaluation.claim_relation_recovery_contracts import (
    ReconciledClaimRelationResultBundle,
)
from aletheia_lab.evaluation.claim_sample_selection import (
    select_balanced_validation_sample,
)
from aletheia_lab.evaluation.claim_support_instrument import classify_visible_support
from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256
from aletheia_lab.evaluation.instrument_validation import (
    LABEL_ORDER,
    ClaimSupportValidationProtocol,
)
from aletheia_lab.project.identity import SHA256_PATTERN

Sha256 = Annotated[str, Field(pattern=SHA256_PATTERN)]
STATUS = Literal[
    "claim_pool_sampling_feasible",
    "claim_pool_insufficient_label_stratum",
]
RelationBundle = ClaimRelationResultBundle | ReconciledClaimRelationResultBundle
REQUEST_CENSUS = Path("configs/evaluation/claim_support_request_census.json")
EVIDENCE_CENSUS = Path("configs/evaluation/claim_support_observed_evidence_census.json")
VALIDATION_PROTOCOL = Path("configs/evaluation/claim_support_validation_protocol.json")
CORPUS_PROTOCOL = Path("configs/evaluation/claim_support_corpus_protocol.json")
SCHEMA_VERSION: Final = "claim-pool-feasibility-closeout/v1"


class ClaimPoolCloseoutError(ClaimCorpusContractError):
    """Raised when the persisted pool cannot be independently closed."""


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        revalidate_instances="always",
        allow_inf_nan=False,
    )


class LabelStratumCensus(_StrictFrozenModel):
    automatic_label: SupportLabel
    claim_count: int = Field(ge=0, le=1800)
    unique_claim_text_count: int = Field(ge=0, le=1800)
    family_count: int = Field(ge=0, le=15)
    output_count: int = Field(ge=0, le=360)
    maximum_claims_in_one_family: int = Field(ge=0, le=1800)
    maximum_claims_from_one_output: int = Field(ge=0, le=1800)
    claim_quota_satisfied: bool
    family_minimum_satisfied: bool
    output_minimum_satisfied: bool


class ClaimPoolFeasibilityCloseout(_StrictFrozenModel):
    schema_version: Literal["claim-pool-feasibility-closeout/v1"] = SCHEMA_VERSION
    status: STATUS
    diagnosis_source_commit_ref: str = Field(pattern=r"^[0-9a-f]{40}$")
    closeout_source_commit_ref: str = Field(pattern=r"^[0-9a-f]{40}$")
    diagnosis_preparation_schema_version: Literal[
        "claim-pool-preparation/v1",
        "claim-pool-recovery-preparation/v1",
    ]
    recovery_execution_closeout_sha256: Sha256 | None
    missingness_exchangeability_status: Literal[
        "not_available_from_original_preparation",
        "not_established_execution_order_confounded",
    ]
    availability_may_not_be_interpreted_as_mechanism_performance: Literal[True]
    publication_closeout_sha256: Sha256
    corpus_run_id: str = Field(pattern=r"^ccrun-[0-9a-f]{64}$")
    corpus_manifest_sha256: Sha256
    corpus_receipt_sha256: Sha256
    independent_audit_sha256: Sha256
    request_census_sha256: Sha256
    corpus_protocol_sha256: Sha256
    evidence_census_sha256: Sha256
    validation_protocol_sha256: Sha256
    preparation_sha256: Sha256
    relation_result_bundle_sha256: Sha256
    terminal_diagnosis_request_count: Literal[360]
    parsed_diagnosis_output_count: int = Field(ge=0, le=360)
    technical_diagnosis_failure_count: int = Field(ge=0, le=360)
    relation_request_count: int = Field(ge=0, le=1800)
    relation_provider_attempt_count: int = Field(ge=0, le=3600)
    corpus_entry_count: int = Field(ge=0, le=1800)
    canonical_claim_text_count: int = Field(ge=0, le=1800)
    repeated_claim_text_instance_count: int = Field(ge=0, le=1800)
    repeated_text_evidence_group_count: int = Field(ge=0, le=1800)
    label_strata: tuple[LabelStratumCensus, ...] = Field(min_length=4, max_length=4)
    sample_target: Literal[200]
    automatic_label_quota: Literal[50]
    minimum_families_per_label: Literal[10]
    minimum_outputs_per_label: Literal[25]
    maximum_claims_per_family_per_label: Literal[5]
    maximum_claims_per_output_per_label: Literal[2]
    exact_frozen_selection_feasible: bool
    blocking_strata: tuple[SupportLabel, ...]
    integrity_audit_passed: Literal[True]
    failures_preserved_in_denominator: Literal[True]
    labels_immutable_before_human_access: Literal[True]
    repeated_canonical_text_excluded_from_sample: Literal[True]
    family_and_output_caps_enforced_by_selector: Literal[True]
    sample_materialized: Literal[False] = False
    blind_packets_generated: Literal[False] = False
    human_annotations_collected: Literal[False] = False
    main_or_sealed_outcomes_opened: Literal[False] = False
    next_authorized_action: Literal[
        "freeze_real_onboarding_and_validation_samples",
        "review_separately_versioned_prospective_design",
    ]
    closeout_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"closeout_sha256"})

    @model_validator(mode="after")
    def _status_and_identity_reconcile(self) -> Self:
        expected_blocking = tuple(
            item.automatic_label
            for item in self.label_strata
            if not (
                item.claim_quota_satisfied
                and item.family_minimum_satisfied
                and item.output_minimum_satisfied
            )
        )
        if self.blocking_strata != expected_blocking:
            raise ValueError("claim-pool blocking strata differ from their census")
        if any(
            item.claim_quota_satisfied
            != (item.unique_claim_text_count >= self.automatic_label_quota)
            or item.family_minimum_satisfied
            != (item.family_count >= self.minimum_families_per_label)
            or item.output_minimum_satisfied
            != (item.output_count >= self.minimum_outputs_per_label)
            for item in self.label_strata
        ):
            raise ValueError("claim-pool stratum decisions differ from frozen thresholds")
        feasible = not self.blocking_strata and self.exact_frozen_selection_feasible
        if feasible != (self.status == "claim_pool_sampling_feasible"):
            raise ValueError("claim-pool feasibility status differs from its strata")
        expected_action = (
            "freeze_real_onboarding_and_validation_samples"
            if feasible
            else "review_separately_versioned_prospective_design"
        )
        if self.next_authorized_action != expected_action:
            raise ValueError("claim-pool next action differs from feasibility status")
        if (
            self.parsed_diagnosis_output_count + self.technical_diagnosis_failure_count
            != self.terminal_diagnosis_request_count
            or self.relation_request_count != self.corpus_entry_count
            or self.corpus_entry_count
            != sum(item.claim_count for item in self.label_strata)
            or not (
                self.relation_request_count
                <= self.relation_provider_attempt_count
                <= 2 * self.relation_request_count
            )
            or self.repeated_claim_text_instance_count
            != self.corpus_entry_count - self.canonical_claim_text_count
            or self.repeated_text_evidence_group_count
            > self.repeated_claim_text_instance_count
        ):
            raise ValueError("claim-pool closeout counts do not reconcile")
        if tuple(item.automatic_label for item in self.label_strata) != LABEL_ORDER:
            raise ValueError("claim-pool label strata are not canonically ordered")
        recovery = self.diagnosis_preparation_schema_version.endswith("recovery-preparation/v1")
        if recovery != (self.recovery_execution_closeout_sha256 is not None) or recovery != (
            self.missingness_exchangeability_status == "not_established_execution_order_confounded"
        ):
            raise ValueError("claim-pool missingness provenance differs from preparation")
        if self.closeout_sha256 != canonical_execution_sha256(self.identity_payload()):
            raise ValueError("claim-pool feasibility closeout hash differs")
        return self


@dataclass(frozen=True)
class _SamplingEntry:
    source: ClaimSupportCorpusEntry

    @property
    def automatic_label(self) -> str:
        return self.source.automatic_label

    @property
    def claim_type(self) -> str:
        return self.source.claim_type

    @property
    def evidence_condition(self) -> str:
        return self.source.evidence_condition

    @property
    def variant(self) -> str:
        return self.source.variant

    @property
    def entry_sha256(self) -> str:
        return self.source.entry_sha256

    @property
    def case_family_id(self) -> str:
        return self.source.family_id

    @property
    def output_id(self) -> str:
        return self.source.output_sha256

    @property
    def claim_text(self) -> str:
        return self.source.claim_text

    @property
    def claim_id(self) -> str:
        return self.source.entry_sha256

    @property
    def source_partition(self) -> str:
        return self.source.source_partition


def build_claim_pool_feasibility_closeout(
    root: Path,
    *,
    preparation: ClaimPoolPreparationLike,
    relation_results: RelationBundle,
    publication_closeout: ClaimPoolPublicationCloseout,
    store_root: Path,
    closeout_source_commit_ref: str,
    recovery_execution_closeout: RecoveryExecutionCloseout | None = None,
) -> ClaimPoolFeasibilityCloseout:
    """Audit immutable inputs and close sampling feasibility without sampling."""

    if isinstance(preparation, RecoveryClaimPoolPreparation):
        checked_preparation: ClaimPoolPreparationLike = RecoveryClaimPoolPreparation.model_validate(
            preparation.model_dump(mode="python")
        )
    else:
        checked_preparation = ClaimPoolPreparation.model_validate(
            preparation.model_dump(mode="python")
        )
    checked_publication = ClaimPoolPublicationCloseout.model_validate(
        publication_closeout.model_dump(mode="python")
    )
    if isinstance(relation_results, ReconciledClaimRelationResultBundle):
        checked_results: RelationBundle = ReconciledClaimRelationResultBundle.model_validate(
            relation_results.model_dump(mode="python")
        )
    else:
        checked_results = ClaimRelationResultBundle.model_validate(
            relation_results.model_dump(mode="python")
        )
    checked_recovery_closeout = _checked_recovery_closeout(
        checked_preparation,
        recovery_execution_closeout,
    )
    receipt = checked_publication.corpus_store_receipt
    try:
        audit, manifest, persisted_receipt, entries = load_audited_claim_corpus_run(
            store_root,
            receipt.run_id,
        )
    except ClaimCorpusContractError as exc:
        raise ClaimPoolCloseoutError("published pool failed independent integrity audit") from exc
    if persisted_receipt != receipt:
        raise ClaimPoolCloseoutError("publication closeout differs from persisted receipt")
    request_census = ClaimCorpusRequestCensus.model_validate_json(
        (root.resolve() / REQUEST_CENSUS).read_bytes()
    )
    evidence_census = ObservedEvidenceCensus.model_validate_json(
        (root.resolve() / EVIDENCE_CENSUS).read_bytes()
    )
    protocol = ClaimSupportValidationProtocol.model_validate_json(
        (root.resolve() / VALIDATION_PROTOCOL).read_bytes()
    )
    corpus_protocol = ClaimSupportCorpusProtocol.model_validate_json(
        (root.resolve() / CORPUS_PROTOCOL).read_bytes()
    )
    _verify_top_level_bindings(
        checked_preparation,
        checked_results,
        checked_publication,
        request_census,
        evidence_census,
        corpus_protocol,
        protocol,
        manifest.protocol_sha256,
        manifest.census_sha256,
        entries,
    )
    _verify_entry_provenance(
        checked_preparation,
        checked_results,
        request_census,
        evidence_census,
        entries,
    )

    label_strata = _label_strata(entries, protocol, corpus_protocol)
    exact_feasible = _exact_selection_is_feasible(entries, protocol)
    blocking = tuple(
        item.automatic_label
        for item in label_strata
        if not (
            item.claim_quota_satisfied
            and item.family_minimum_satisfied
            and item.output_minimum_satisfied
        )
    )
    feasible = exact_feasible and not blocking
    claim_text_count = len({item.claim_text for item in entries})
    text_evidence = Counter(
        (
            item.claim_text,
            tuple((ev.evidence_id, ev.text) for ev in item.visible_evidence),
        )
        for item in entries
    )
    provider_attempt_count = (
        checked_results.total_provider_attempt_count
        if isinstance(checked_results, ReconciledClaimRelationResultBundle)
        else checked_results.registered_attempt_count
    )
    payload: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "status": (
            "claim_pool_sampling_feasible" if feasible else "claim_pool_insufficient_label_stratum"
        ),
        "diagnosis_source_commit_ref": checked_preparation.source_commit_ref,
        "closeout_source_commit_ref": closeout_source_commit_ref,
        "diagnosis_preparation_schema_version": checked_preparation.schema_version,
        "recovery_execution_closeout_sha256": (
            checked_recovery_closeout.closeout_sha256
            if checked_recovery_closeout is not None
            else None
        ),
        "missingness_exchangeability_status": (
            checked_recovery_closeout.missingness_exchangeability_status
            if checked_recovery_closeout is not None
            else "not_available_from_original_preparation"
        ),
        "availability_may_not_be_interpreted_as_mechanism_performance": True,
        "publication_closeout_sha256": checked_publication.closeout_sha256,
        "corpus_run_id": receipt.run_id,
        "corpus_manifest_sha256": receipt.manifest_sha256,
        "corpus_receipt_sha256": receipt.receipt_sha256,
        "independent_audit_sha256": audit.audit_sha256,
        "request_census_sha256": request_census.census_sha256,
        "corpus_protocol_sha256": corpus_protocol.protocol_sha256,
        "evidence_census_sha256": evidence_census.census_sha256,
        "validation_protocol_sha256": protocol.protocol_sha256,
        "preparation_sha256": checked_preparation.preparation_sha256,
        "relation_result_bundle_sha256": checked_results.bundle_sha256,
        "terminal_diagnosis_request_count": checked_preparation.terminal_request_count,
        "parsed_diagnosis_output_count": checked_preparation.parsed_terminal_count,
        "technical_diagnosis_failure_count": (checked_preparation.technical_failure_terminal_count),
        "relation_request_count": checked_preparation.relation_request_count,
        "relation_provider_attempt_count": provider_attempt_count,
        "corpus_entry_count": len(entries),
        "canonical_claim_text_count": claim_text_count,
        "repeated_claim_text_instance_count": len(entries) - claim_text_count,
        "repeated_text_evidence_group_count": sum(count > 1 for count in text_evidence.values()),
        "label_strata": tuple(item.model_dump(mode="json") for item in label_strata),
        "sample_target": protocol.sample_target,
        "automatic_label_quota": protocol.automatic_label_quota,
        "minimum_families_per_label": (
            corpus_protocol.family_census.minimum_distinct_families_per_automatic_label
        ),
        "minimum_outputs_per_label": (
            corpus_protocol.family_census.minimum_eligible_outputs_per_automatic_label
        ),
        "maximum_claims_per_family_per_label": (
            protocol.maximum_claims_per_family_per_label
        ),
        "maximum_claims_per_output_per_label": (
            protocol.maximum_claims_per_output_per_label
        ),
        "exact_frozen_selection_feasible": exact_feasible,
        "blocking_strata": blocking,
        "integrity_audit_passed": True,
        "failures_preserved_in_denominator": True,
        "labels_immutable_before_human_access": True,
        "repeated_canonical_text_excluded_from_sample": True,
        "family_and_output_caps_enforced_by_selector": True,
        "sample_materialized": False,
        "blind_packets_generated": False,
        "human_annotations_collected": False,
        "main_or_sealed_outcomes_opened": False,
        "next_authorized_action": (
            "freeze_real_onboarding_and_validation_samples"
            if feasible
            else "review_separately_versioned_prospective_design"
        ),
    }
    return ClaimPoolFeasibilityCloseout.model_validate(
        {**payload, "closeout_sha256": canonical_execution_sha256(payload)}
    )


def _verify_top_level_bindings(
    preparation: ClaimPoolPreparationLike,
    relation_results: RelationBundle,
    publication: ClaimPoolPublicationCloseout,
    request_census: ClaimCorpusRequestCensus,
    evidence_census: ObservedEvidenceCensus,
    corpus_protocol: ClaimSupportCorpusProtocol,
    validation_protocol: ClaimSupportValidationProtocol,
    manifest_protocol_sha256: str,
    manifest_census_sha256: str,
    entries: tuple[ClaimSupportCorpusEntry, ...],
) -> None:
    receipt = publication.corpus_store_receipt
    if (
        publication.preparation_sha256 != preparation.preparation_sha256
        or publication.relation_result_bundle_sha256 != relation_results.bundle_sha256
        or relation_results.preparation_sha256 != preparation.preparation_sha256
        or publication.policy_sha256 != preparation.evidence_semantics_policy_sha256
        or relation_results.policy_sha256 != preparation.evidence_semantics_policy_sha256
        or evidence_census.request_census_sha256 != request_census.census_sha256
        or preparation.evidence_census_sha256 != evidence_census.census_sha256
        or corpus_protocol.parent_validation_protocol_sha256 != validation_protocol.protocol_sha256
        or manifest_protocol_sha256 != corpus_protocol.protocol_sha256
        or manifest_census_sha256 != request_census.census_sha256
    ):
        raise ClaimPoolCloseoutError("claim-pool closeout inputs differ")
    family_policy = corpus_protocol.family_census
    if (
        validation_protocol.source_partition != corpus_protocol.source_boundary.source_partition
        or validation_protocol.automatic_label_quota
        != family_policy.target_claims_per_automatic_label
        or validation_protocol.maximum_claims_per_family_per_label
        != family_policy.maximum_claims_per_family_per_label
        or validation_protocol.maximum_claims_per_output_per_label
        != family_policy.maximum_claims_per_output_per_label
    ):
        raise ClaimPoolCloseoutError("frozen corpus and validation policies differ")
    if (
        receipt.entry_count != len(entries)
        or publication.corpus_entry_count != len(entries)
        or publication.candidate_claim_count != preparation.relation_request_count
        or publication.automatically_labeled_claim_count != len(entries)
    ):
        raise ClaimPoolCloseoutError("claim-pool publication counts differ")


def _verify_entry_provenance(
    preparation: ClaimPoolPreparationLike,
    relation_results: RelationBundle,
    request_census: ClaimCorpusRequestCensus,
    evidence_census: ObservedEvidenceCensus,
    entries: tuple[ClaimSupportCorpusEntry, ...],
) -> None:
    requests = {item.request_sha256: item for item in request_census.primary_requests}
    records = {item.request_sha256: item for item in preparation.records}
    relations = {item.assignment_request_sha256: item for item in preparation.relation_requests}
    results = {item.assignment_request_sha256: item for item in relation_results.results}
    bindings = {
        (item.family_id, item.evidence_condition): item for item in evidence_census.bindings
    }
    seen: set[tuple[str, str, str]] = set()
    for entry in entries:
        request = requests.get(entry.request_sha256)
        record = records.get(entry.request_sha256)
        if request is None or record is None or record.normalized_output is None:
            raise ClaimPoolCloseoutError("pool entry has no normalized request provenance")
        output = record.normalized_output
        claims = {item.claim_local_id: item for item in output.atomic_claims}
        claim = claims.get(entry.claim_local_id)
        if (
            claim is None
            or output.output_sha256 != entry.output_sha256
            or output.source_record_sha256 != entry.source_record_sha256
            or request.family_id != entry.family_id
            or request.mechanism != entry.mechanism
            or request.evidence_condition != entry.evidence_condition
            or request.variant != entry.variant
            or claim.claim_text != entry.claim_text
            or claim.claim_type != entry.claim_type
            or claim.material_parts != entry.material_parts
        ):
            raise ClaimPoolCloseoutError("pool entry differs from frozen request or output")
        key = (entry.request_sha256, entry.output_sha256, entry.claim_local_id)
        if key in seen:
            raise ClaimPoolCloseoutError("pool contains duplicate request-local claims")
        seen.add(key)
        matching = tuple(
            relations[digest]
            for digest in record.relation_request_sha256s
            if digest in relations
            and relations[digest].source_output_sha256 == entry.output_sha256
            and relations[digest].claim_local_id == entry.claim_local_id
        )
        if len(matching) != 1:
            raise ClaimPoolCloseoutError("pool entry relation request is ambiguous")
        assignment = matching[0]
        result = results.get(assignment.assignment_request_sha256)
        if result is None or result.response is None or result.terminal_status != "parsed":
            raise ClaimPoolCloseoutError("pool entry has no parsed relation provenance")
        expected_binding = bindings.get((entry.family_id, entry.evidence_condition))
        if expected_binding is None:
            raise ClaimPoolCloseoutError("pool entry has no frozen evidence binding")
        evidence_by_id = {
            item.evidence_id: item for item in expected_binding.visible_context.items
        }
        try:
            expected_assignment_evidence = tuple(
                evidence_by_id[evidence_id] for evidence_id in claim.visible_evidence_ids
            )
        except KeyError as exc:
            raise ClaimPoolCloseoutError(
                "pool claim cites evidence outside its frozen context"
            ) from exc
        if (
            assignment.claim_text != claim.claim_text
            or assignment.claim_type != claim.claim_type
            or assignment.visible_evidence != expected_assignment_evidence
            or assignment.visible_context_sha256
            != expected_binding.visible_context.context_sha256
        ):
            raise ClaimPoolCloseoutError(
                "pool relation request differs from its frozen claim or evidence"
            )
        expected_visible = visible_relations_from_assignment(assignment, result.response)
        if (
            entry.visible_evidence != expected_visible
            or any(
                evidence_by_id.get(item.evidence_id) is None
                or evidence_by_id[item.evidence_id].content != item.text
                for item in entry.visible_evidence
            )
            or classify_visible_support(
                claim_text=entry.claim_text,
                claim_type=entry.claim_type,
                visible_evidence=entry.visible_evidence,
            )
            != entry.automatic_label
        ):
            raise ClaimPoolCloseoutError("pool entry evidence or automatic label differs")
    expected = {
        (record.request_sha256, record.normalized_output.output_sha256, claim.claim_local_id)
        for record in preparation.records
        if record.normalized_output is not None
        and record.normalized_output.output_status == "completed"
        for claim in record.normalized_output.atomic_claims
    }
    if seen != expected:
        raise ClaimPoolCloseoutError("pool entry provenance census is incomplete")


def _label_strata(
    entries: tuple[ClaimSupportCorpusEntry, ...],
    protocol: ClaimSupportValidationProtocol,
    corpus_protocol: ClaimSupportCorpusProtocol,
) -> tuple[LabelStratumCensus, ...]:
    result: list[LabelStratumCensus] = []
    family_minimum = corpus_protocol.family_census.minimum_distinct_families_per_automatic_label
    output_minimum = corpus_protocol.family_census.minimum_eligible_outputs_per_automatic_label
    for label in LABEL_ORDER:
        selected = tuple(item for item in entries if item.automatic_label == label)
        family_counts = Counter(item.family_id for item in selected)
        output_counts = Counter(item.output_sha256 for item in selected)
        result.append(
            LabelStratumCensus(
                automatic_label=label,
                claim_count=len(selected),
                unique_claim_text_count=len({item.claim_text for item in selected}),
                family_count=len({item.family_id for item in selected}),
                output_count=len({item.output_sha256 for item in selected}),
                maximum_claims_in_one_family=max(family_counts.values(), default=0),
                maximum_claims_from_one_output=max(output_counts.values(), default=0),
                claim_quota_satisfied=(
                    len({item.claim_text for item in selected}) >= protocol.automatic_label_quota
                ),
                family_minimum_satisfied=(
                    len({item.family_id for item in selected}) >= family_minimum
                ),
                output_minimum_satisfied=(
                    len({item.output_sha256 for item in selected}) >= output_minimum
                ),
            )
        )
    return tuple(result)


def _checked_recovery_closeout(
    preparation: ClaimPoolPreparationLike,
    closeout: RecoveryExecutionCloseout | None,
) -> RecoveryExecutionCloseout | None:
    if isinstance(preparation, RecoveryClaimPoolPreparation):
        if closeout is None:
            raise ClaimPoolCloseoutError("recovery preparation requires its execution closeout")
        checked = RecoveryExecutionCloseout.model_validate(closeout.model_dump(mode="python"))
        if (
            checked.closeout_sha256 != preparation.recovery_closeout_sha256
            or checked.recovery_receipt_sha256 != preparation.recovery_receipt_sha256
            or checked.recovery_terminal_store_sha256 != preparation.recovery_terminal_store_sha256
            or checked.source_commit_ref != preparation.source_commit_ref
            or checked.terminal_request_count != preparation.terminal_request_count
            or checked.parsed_terminal_count != preparation.parsed_terminal_count
            or checked.technical_failure_terminal_count
            != preparation.technical_failure_terminal_count
            or checked.normalized_output_count != preparation.normalized_output_count
            or checked.claim_candidate_count != preparation.claim_candidate_count
        ):
            raise ClaimPoolCloseoutError(
                "recovery execution closeout differs from pool preparation"
            )
        return checked
    if closeout is not None:
        raise ClaimPoolCloseoutError(
            "original preparation cannot bind a recovery execution closeout"
        )
    return None


def _rank(protocol_sha256: str, label: str, value: object) -> str:
    return canonical_execution_sha256(
        {"protocol_sha256": protocol_sha256, "label": label, "value": value}
    )


def _exact_selection_is_feasible(
    entries: tuple[ClaimSupportCorpusEntry, ...],
    protocol: ClaimSupportValidationProtocol,
) -> bool:
    def fail(_message: str) -> None:
        raise ClaimPoolCloseoutError("frozen balanced selection is infeasible")

    try:
        selected = select_balanced_validation_sample(
            tuple(_SamplingEntry(item) for item in entries),
            protocol,
            labels=LABEL_ORDER,
            rank=_rank,
            fail=fail,
        )
    except ClaimPoolCloseoutError:
        return False
    return len(selected) == protocol.sample_target


__all__ = [
    "ClaimPoolCloseoutError",
    "ClaimPoolFeasibilityCloseout",
    "LabelStratumCensus",
    "build_claim_pool_feasibility_closeout",
]
