"""Deterministic, evaluator-side scoring for the matched diagnosis pilot.

The scorer intentionally keeps semantic correctness separate from evidential
support. Its locked lexical P1 cause check is an auditable baseline, not a
substitute for final human semantic review.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections import Counter, defaultdict
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from aletheia_lab.benchmark.case_writer import LoadedCase, load_case_dir_schema_only
from aletheia_lab.diagnosis.external_pilot import validate_openai_smoke
from aletheia_lab.diagnosis.openai_preflight import load_openai_pilot_config
from aletheia_lab.diagnosis.pilot import (
    validate_matched_requests,
    validate_source_binding,
)
from aletheia_lab.diagnosis.schema import (
    DiagnosisOutput,
    DiagnosisRunRecord,
    PilotManifest,
    PilotVariant,
    parse_diagnosis_output,
)
from aletheia_lab.evidence.rubric import EvidenceCondition, EvidenceRole
from aletheia_lab.evidence.schema import EvidenceBundle, project_diagnosis_evidence
from aletheia_lab.evidence.store import load_bundle_store

EVALUATION_SCHEMA_VERSION: Final[Literal["matched-pilot-evaluation/1"]] = (
    "matched-pilot-evaluation/1"
)
SCORER_VERSION: Final[Literal["p1-structural-lexical/1"]] = "p1-structural-lexical/1"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_SHIFT_PATTERN = re.compile(
    r"\b(?:data[ -]?drift|distribution(?:al)?[ -]?(?:shift|change|drift)|"
    r"profile[ -]?shift|shift(?:s|ed)?\s+in\s+(?:the\s+)?distribution(?:\s+of)?)\b",
    re.IGNORECASE,
)
_STRONG_CAUSAL_PATTERN = re.compile(
    r"\b(?:definitely|certainly|conclusively|proves?|caused|causes|"
    r"is the root cause|root cause is)\b",
    re.IGNORECASE,
)
_NEGATED_SHIFT_PATTERN = re.compile(
    r"\b(?:no|not|cannot|can't|does not|doesn't|insufficient evidence (?:of|for|to support))"
    r"\b.{0,48}\b(?:data[ -]?drift|distribution(?:al)?[ -]?(?:shift|change|drift)|"
    r"shift(?:s|ed)?\s+in\s+(?:the\s+)?distribution(?:\s+of)?)\b",
    re.IGNORECASE,
)
_NEGATION_PREFIX_PATTERN = re.compile(
    r"\b(?:not|cannot|can't|does not|doesn't|do not|no evidence)\b(?:\W+\w+){0,3}\W*$",
    re.IGNORECASE,
)
_FAILURE_PATTERN = re.compile(r"\b(?:failure|regression|degradation|accuracy drop)\b", re.I)
_COMPARISON_ROLES: frozenset[EvidenceRole] = frozenset(
    {"candidate_psi", "metric_comparison", "secondary_distribution_comparison"}
)

CorrectnessLabel = Literal["correct", "partial", "incorrect", "not_asserted", "not_evaluable"]
SupportLabel = Literal["fully_supported", "partially_supported", "unsupported", "not_evaluable"]
DivergenceLabel = Literal[
    "correct_and_evidence_aligned",
    "correct_but_not_evidence_aligned",
    "incorrect_but_evidence_aligned",
    "incorrect_and_not_evidence_aligned",
    "not_evaluable",
]


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class CorrectnessEvaluation(_StrictFrozenModel):
    label: CorrectnessLabel
    cause_concept_detected: bool
    cause_concept_negated: bool
    affected_feature_detected: bool
    hidden_failure_eligible: bool
    rationale: str
    requires_human_semantic_review: Literal[True] = True


class CitationEvaluation(_StrictFrozenModel):
    valid: bool
    visible_citation_count: int = Field(ge=0)
    supporting_citation_count: int = Field(ge=0)
    unknown_evidence_ids: tuple[str, ...] = ()
    rationale: str


class SupportEvaluation(_StrictFrozenModel):
    label: SupportLabel
    required_roles: tuple[EvidenceRole, ...]
    supported_roles: tuple[EvidenceRole, ...]
    missing_roles: tuple[EvidenceRole, ...]
    rationale: str


class BehaviorEvaluation(_StrictFrozenModel):
    rubric_compliant: bool
    strong_causal_language: bool
    missing_evidence_requested: bool
    secondary_comparison_used_as_support: bool
    issues: tuple[str, ...]


class DiagnosisEvaluation(_StrictFrozenModel):
    request_id: str
    diagnosis_context_id: str
    case_family_id: str
    evidence_condition: EvidenceCondition
    variant: PilotVariant
    run_status: Literal["success", "unresolved"]
    correctness: CorrectnessEvaluation
    citations: CitationEvaluation
    support: SupportEvaluation
    behavior: BehaviorEvaluation
    evidence_aligned: bool
    divergence: DivergenceLabel


class PairedSensitivityEvaluation(_StrictFrozenModel):
    case_family_id: str
    variant: PilotVariant
    complete_three_condition_family: bool
    missing_key_sensitivity: bool | None
    noisy_robustness: bool | None
    issues: tuple[str, ...]


class EvaluationSummary(_StrictFrozenModel):
    run_count: int = Field(ge=0)
    evaluable_count: int = Field(ge=0)
    correctness_counts: dict[str, int]
    support_counts: dict[str, int]
    behavior_compliant_count: int = Field(ge=0)
    evidence_aligned_count: int = Field(ge=0)
    complete_paired_family_count: int = Field(ge=0)
    missing_key_sensitive_count: int = Field(ge=0)
    noisy_robust_count: int = Field(ge=0)


class MatchedPilotEvaluationReport(_StrictFrozenModel):
    schema_version: Literal["matched-pilot-evaluation/1"]
    scorer_version: Literal["p1-structural-lexical/1"]
    source_evidence_store_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_pilot_manifest_sha256: str = Field(pattern=_SHA256_PATTERN)
    diagnosis_evaluations: tuple[DiagnosisEvaluation, ...]
    paired_sensitivity: tuple[PairedSensitivityEvaluation, ...]
    summary: EvaluationSummary

    @model_validator(mode="after")
    def _derived_summary_census(self) -> Self:
        if self.summary.run_count != len(self.diagnosis_evaluations):
            raise ValueError("evaluation summary run_count does not match evaluations")
        expected_evaluable = sum(item.run_status == "success" for item in self.diagnosis_evaluations)
        if self.summary.evaluable_count != expected_evaluable:
            raise ValueError("evaluation summary evaluable_count is not derived")
        correctness_counts = dict(
            sorted(Counter(item.correctness.label for item in self.diagnosis_evaluations).items())
        )
        support_counts = dict(
            sorted(Counter(item.support.label for item in self.diagnosis_evaluations).items())
        )
        expected = EvaluationSummary(
            run_count=len(self.diagnosis_evaluations),
            evaluable_count=expected_evaluable,
            correctness_counts=correctness_counts,
            support_counts=support_counts,
            behavior_compliant_count=sum(
                item.behavior.rubric_compliant for item in self.diagnosis_evaluations
            ),
            evidence_aligned_count=sum(
                item.evidence_aligned for item in self.diagnosis_evaluations
            ),
            complete_paired_family_count=sum(
                item.complete_three_condition_family for item in self.paired_sensitivity
            ),
            missing_key_sensitive_count=sum(
                item.missing_key_sensitivity is True for item in self.paired_sensitivity
            ),
            noisy_robust_count=sum(
                item.noisy_robustness is True for item in self.paired_sensitivity
            ),
        )
        if self.summary != expected:
            raise ValueError("evaluation summary is not derived from evaluation rows")
        return self


def _safe_relative_path(value: str) -> str:
    if not value or value != value.strip() or "\\" in value or ":" in value:
        raise ValueError("pilot paths must be canonical relative POSIX paths")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or PureWindowsPath(value).is_absolute()
        or value != path.as_posix()
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ValueError("pilot path is absolute, non-canonical or traverses parents")
    return value


def _confined_file(root: Path, relative_path: str) -> Path:
    path = root / _safe_relative_path(relative_path)
    if path.is_symlink():
        raise ValueError(f"pilot artifact must not be a symlink: {relative_path}")
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"pilot artifact escapes output root: {relative_path}") from exc
    if not path.is_file():
        raise FileNotFoundError(f"pilot artifact missing: {relative_path}")
    return path


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _load_validated_pilot_records(
    pilot_dir: str | Path,
    evidence_store_dir: str | Path,
    *,
    openai_config_path: str | Path | None,
    preflight_path: str | Path | None,
) -> tuple[PilotManifest, tuple[DiagnosisRunRecord, ...], str]:
    root = Path(pilot_dir)
    if root.is_symlink() or not root.is_dir():
        raise FileNotFoundError(f"pilot store is not a real directory: {root}")
    manifest_path = _confined_file(root, "pilot-manifest.json")
    manifest_payload = manifest_path.read_bytes()
    manifest = PilotManifest.model_validate_json(manifest_payload)
    store = load_bundle_store(evidence_store_dir)
    if manifest.source_evidence_store_sha256 != store.manifest.store_sha256:
        raise ValueError("pilot is not bound to the supplied evidence store")

    expected_paths = {"pilot-manifest.json"}
    # External execution adds a separately validated authorization artifact.
    authorization = root / "execution-authorization.json"
    if authorization.exists():
        if authorization.is_symlink() or not authorization.is_file():
            raise ValueError("execution authorization must be a real file")
        if openai_config_path is None or preflight_path is None:
            raise ValueError(
                "external pilot evaluation requires its OpenAI config and preflight artifact"
            )
        validate_openai_smoke(
            root,
            evidence_store_dir,
            load_openai_pilot_config(openai_config_path),
            preflight_path,
        )
        expected_paths.add("execution-authorization.json")
    records: list[DiagnosisRunRecord] = []
    for entry in manifest.entries:
        path = _confined_file(root, entry.relative_path)
        expected_paths.add(entry.relative_path)
        payload = path.read_bytes()
        if _sha256_bytes(payload) != entry.file_sha256:
            raise ValueError(f"run file hash mismatch: {entry.relative_path}")
        record = DiagnosisRunRecord.model_validate_json(payload)
        if (
            record.request.request_id != entry.request_id
            or record.request.diagnosis_view.diagnosis_context_id != entry.diagnosis_context_id
            or record.request.variant != entry.variant
            or record.final_status != entry.final_status
        ):
            raise ValueError(f"run identity differs from manifest: {entry.relative_path}")
        if record.request.provider_identity != manifest.provider_identity:
            raise ValueError("run changes provider identity")
        if record.request.settings != manifest.settings:
            raise ValueError("run changes generation settings")
        visible = {item.evidence_id for item in record.request.diagnosis_view.items}
        for attempt in record.attempts:
            if attempt.status in {"success", "parse_failure"} and (
                attempt.provider_identity != record.request.provider_identity
            ):
                raise ValueError("accepted attempt changes provider/model identity")
            if attempt.status == "identity_mismatch" and (
                attempt.provider_identity == record.request.provider_identity
            ):
                raise ValueError("identity-mismatch attempt does not contain a mismatch")
            raw_text: str | None = None
            if attempt.raw_relative_path is not None:
                raw_path = _confined_file(root, attempt.raw_relative_path)
                expected_paths.add(attempt.raw_relative_path)
                raw = raw_path.read_bytes()
                if _sha256_bytes(raw) != attempt.raw_sha256:
                    raise ValueError(f"raw response hash mismatch: {attempt.raw_relative_path}")
                raw_text = raw.decode("utf-8")
            if attempt.status == "parse_failure" and raw_text is not None:
                try:
                    parse_diagnosis_output(raw_text, visible)
                except (ValueError, ValidationError):
                    pass
                else:
                    raise ValueError("attempt is labeled parse_failure but raw output is valid")
            if attempt.parsed_relative_path is not None:
                parsed_path = _confined_file(root, attempt.parsed_relative_path)
                expected_paths.add(attempt.parsed_relative_path)
                parsed_payload = parsed_path.read_bytes()
                if _sha256_bytes(parsed_payload) != attempt.parsed_sha256:
                    raise ValueError(f"parsed output hash mismatch: {attempt.parsed_relative_path}")
                parsed = parse_diagnosis_output(parsed_payload.decode("utf-8"), visible)
                if attempt.raw_relative_path is None:
                    raise ValueError("parsed output has no raw response")
                raw_text = _confined_file(root, attempt.raw_relative_path).read_text("utf-8")
                if parsed != parse_diagnosis_output(raw_text, visible):
                    raise ValueError("parsed output differs from raw response")
        records.append(record)

    requests = tuple(record.request for record in records)
    validate_matched_requests(requests)
    context_ids = {request.diagnosis_view.diagnosis_context_id for request in requests}
    source_views = tuple(
        project_diagnosis_evidence(bundle)
        for bundle in store.bundles
        if bundle.diagnosis_context_id in context_ids
    )
    validate_source_binding(requests, source_views)
    actual_paths: set[str] = set()
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"pilot store contains a symlink: {path.relative_to(root)}")
        if path.is_file():
            actual_paths.add(path.relative_to(root).as_posix())
    if actual_paths != expected_paths:
        raise ValueError(
            "pilot store file set differs from manifest: "
            f"missing={sorted(expected_paths - actual_paths)}, "
            f"unexpected={sorted(actual_paths - expected_paths)}"
        )
    return manifest, tuple(records), _sha256_bytes(manifest_payload)


def _load_cases(cases_dir: str | Path) -> dict[str, LoadedCase]:
    root = Path(cases_dir)
    if not root.is_dir():
        raise FileNotFoundError(f"case directory does not exist: {root}")
    cases = {
        case.manifest.case_id: case
        for case in (
            load_case_dir_schema_only(path)
            for path in sorted(root.iterdir())
            if path.is_dir() and (path / "manifest.json").is_file()
        )
    }
    if not cases:
        raise ValueError("no validated cases found")
    return cases


def _successful_output(record: DiagnosisRunRecord, pilot_root: Path) -> DiagnosisOutput | None:
    if record.final_status != "success":
        return None
    attempt = record.attempts[-1]
    if attempt.parsed_relative_path is None:
        raise ValueError("successful run lacks parsed output")
    visible = {item.evidence_id for item in record.request.diagnosis_view.items}
    return parse_diagnosis_output(
        _confined_file(pilot_root, attempt.parsed_relative_path).read_text("utf-8"), visible
    )


def evaluate_correctness(output: DiagnosisOutput, case: LoadedCase) -> CorrectnessEvaluation:
    """Apply the locked P1 lexical cause baseline against evaluator-only truth."""

    text = output.root_cause_hypothesis
    shift_mentioned = _SHIFT_PATTERN.search(text) is not None
    negated_shift = _NEGATED_SHIFT_PATTERN.search(text) is not None
    shift = shift_mentioned and not negated_shift
    feature = case.injection.feature.casefold() in text.casefold()
    eligible = case.ground_truth.failure_eligibility.classification == "eligible_failure"
    if eligible:
        if negated_shift or (
            not shift
            and (
                output.abstain
                or output.claim_strength in {"observation", "comparison"}
            )
        ):
            label: CorrectnessLabel = "not_asserted"
            rationale = "The output does not assert the hidden failure-cause concept."
        elif shift and feature:
            label = "correct"
            rationale = "The output identifies the P1 shift concept and affected feature."
        elif shift or feature:
            label = "partial"
            rationale = "Only the cause concept or affected feature is identified."
        else:
            label = "incorrect"
            rationale = "The output does not identify the eligible P1 failure cause."
    elif negated_shift or output.abstain or output.claim_strength in {"observation", "comparison"}:
        label = "correct"
        rationale = "The control outcome is not promoted to a bounded failure cause."
    elif _FAILURE_PATTERN.search(text) or shift:
        label = "incorrect"
        rationale = "A control outcome is promoted to a bounded failure-cause hypothesis."
    else:
        label = "partial"
        rationale = "The bounded statement is ambiguous for a non-failure control."
    return CorrectnessEvaluation(
        label=label,
        cause_concept_detected=shift,
        cause_concept_negated=negated_shift,
        affected_feature_detected=feature,
        hidden_failure_eligible=eligible,
        rationale=rationale,
    )


def evaluate_citations(output: DiagnosisOutput, bundle: EvidenceBundle) -> CitationEvaluation:
    visible = {item.evidence_id for item in bundle.diagnosis_visible_items}
    supporting = set(output.supporting_evidence_ids)
    cited = supporting | set(output.counterevidence_ids)
    unknown = tuple(sorted(cited - visible))
    has_required_citation = output.abstain or bool(supporting)
    valid = not unknown and has_required_citation
    rationale = (
        "All citations resolve to diagnosis-visible evidence."
        if valid
        else "A non-abstaining claim lacks support citations or cites non-visible evidence."
    )
    return CitationEvaluation(
        valid=valid,
        visible_citation_count=len(cited & visible),
        supporting_citation_count=len(supporting & visible),
        unknown_evidence_ids=unknown,
        rationale=rationale,
    )


def evaluate_support(output: DiagnosisOutput, bundle: EvidenceBundle) -> SupportEvaluation:
    by_id = {item.evidence_id: item for item in bundle.diagnosis_visible_items}
    roles = {
        role
        for evidence_id in output.supporting_evidence_ids
        if evidence_id in by_id
        for role in by_id[evidence_id].evidence_roles
    }
    if output.claim_strength == "observation":
        required: set[EvidenceRole] = set()
        label: SupportLabel = "fully_supported" if roles else "unsupported"
    elif output.claim_strength == "comparison":
        required = set(_COMPARISON_ROLES)
        comparison_supported = bool(roles & required)
        label = (
            "fully_supported"
            if comparison_supported
            else ("partially_supported" if roles else "unsupported")
        )
    else:
        required = set(bundle.required_evidence_roles) - set(
            bundle.intentionally_withheld_evidence_roles
        )
        covered = required & roles
        if required <= roles and (
            not bundle.intentionally_withheld_evidence_roles or output.missing_evidence
        ):
            label = "fully_supported"
        elif covered:
            label = "partially_supported"
        else:
            label = "unsupported"
    missing = set() if output.claim_strength == "comparison" and label == "fully_supported" else required - roles
    rationale = {
        "fully_supported": "Cited evidence covers the roles required for this claim level.",
        "partially_supported": "Cited evidence covers only part of the required role set.",
        "unsupported": "No cited evidence establishes the roles required for this claim level.",
        "not_evaluable": "No parseable diagnosis output is available.",
    }[label]
    return SupportEvaluation(
        label=label,
        required_roles=tuple(sorted(required)),
        supported_roles=tuple(sorted(roles)),
        missing_roles=tuple(sorted(missing)),
        rationale=rationale,
    )


def evaluate_behavior(
    output: DiagnosisOutput, bundle: EvidenceBundle, case: LoadedCase
) -> BehaviorEvaluation:
    by_id = {item.evidence_id: item for item in bundle.diagnosis_visible_items}
    secondary = any(
        "secondary_distribution_comparison" in by_id[evidence_id].evidence_roles
        for evidence_id in output.supporting_evidence_ids
        if evidence_id in by_id
    )
    strong = any(
        _NEGATION_PREFIX_PATTERN.search(output.root_cause_hypothesis[: match.start()]) is None
        for match in _STRONG_CAUSAL_PATTERN.finditer(output.root_cause_hypothesis)
    )
    issues: list[str] = []
    if strong:
        issues.append("strong_causal_language")
    if bundle.evidence_condition == "missing_key" and not (
        output.abstain or output.missing_evidence
    ):
        issues.append("missing_key_without_uncertainty_or_evidence_request")
    if bundle.evidence_condition in {"full", "noisy"} and output.abstain:
        issues.append("blanket_abstention_with_bounded_evidence")
    if bundle.evidence_condition == "noisy" and secondary:
        issues.append("secondary_comparison_selected_as_support")
    if (
        case.ground_truth.failure_eligibility.classification != "eligible_failure"
        and output.claim_strength == "bounded_causal_hypothesis"
        and not output.abstain
    ):
        issues.append("control_promoted_to_failure_cause")
    return BehaviorEvaluation(
        rubric_compliant=not issues,
        strong_causal_language=strong,
        missing_evidence_requested=bool(output.missing_evidence),
        secondary_comparison_used_as_support=secondary,
        issues=tuple(issues),
    )


def _not_evaluable_components(
    eligible: bool,
) -> tuple[CorrectnessEvaluation, CitationEvaluation, SupportEvaluation, BehaviorEvaluation]:
    return (
        CorrectnessEvaluation(
            label="not_evaluable",
            cause_concept_detected=False,
            cause_concept_negated=False,
            affected_feature_detected=False,
            hidden_failure_eligible=eligible,
            rationale="The run produced no parseable final output.",
        ),
        CitationEvaluation(
            valid=False,
            visible_citation_count=0,
            supporting_citation_count=0,
            rationale="The run produced no parseable final output.",
        ),
        SupportEvaluation(
            label="not_evaluable",
            required_roles=(),
            supported_roles=(),
            missing_roles=(),
            rationale="No parseable diagnosis output is available.",
        ),
        BehaviorEvaluation(
            rubric_compliant=False,
            strong_causal_language=False,
            missing_evidence_requested=False,
            secondary_comparison_used_as_support=False,
            issues=("unresolved_run",),
        ),
    )


def _divergence(correctness: CorrectnessLabel, aligned: bool) -> DivergenceLabel:
    if correctness in {"not_asserted", "not_evaluable"}:
        return "not_evaluable"
    correct = correctness == "correct"
    if correct and aligned:
        return "correct_and_evidence_aligned"
    if correct:
        return "correct_but_not_evidence_aligned"
    if aligned:
        return "incorrect_but_evidence_aligned"
    return "incorrect_and_not_evidence_aligned"


def _claim_rank(output: DiagnosisOutput) -> int:
    return {"observation": 0, "comparison": 1, "bounded_causal_hypothesis": 2}[
        output.claim_strength
    ]


def evaluate_missing_key_sensitivity(
    full_output: DiagnosisOutput,
    missing_output: DiagnosisOutput,
) -> bool:
    """Return whether withheld evidence causes a strict or explicit qualification.

    A strictly weaker claim is sufficient on its own. If claim strength is
    unchanged, the output must instead abstain, lower confidence, or request
    more missing evidence. A stronger missing-key claim always fails.
    """

    full_rank = _claim_rank(full_output)
    missing_rank = _claim_rank(missing_output)
    if missing_rank < full_rank:
        return True
    if missing_rank > full_rank:
        return False
    return (
        missing_output.abstain
        or missing_output.confidence < full_output.confidence
        or len(missing_output.missing_evidence) > len(full_output.missing_evidence)
    )


def _paired_sensitivity(
    evaluations: tuple[DiagnosisEvaluation, ...],
    outputs: dict[str, DiagnosisOutput],
) -> tuple[PairedSensitivityEvaluation, ...]:
    grouped: dict[tuple[str, PilotVariant], list[DiagnosisEvaluation]] = defaultdict(list)
    for evaluation in evaluations:
        grouped[(evaluation.case_family_id, evaluation.variant)].append(evaluation)
    results: list[PairedSensitivityEvaluation] = []
    for (family_id, variant), siblings in sorted(grouped.items(), key=lambda item: item[0]):
        by_condition = {item.evidence_condition: item for item in siblings}
        complete = set(by_condition) == {"full", "missing_key", "noisy"}
        issues: list[str] = []
        missing_sensitive: bool | None = None
        noisy_robust: bool | None = None
        if {"full", "missing_key"} <= set(by_condition):
            full = by_condition["full"]
            missing = by_condition["missing_key"]
            if full.request_id in outputs and missing.request_id in outputs:
                full_output = outputs[full.request_id]
                missing_output = outputs[missing.request_id]
                missing_sensitive = evaluate_missing_key_sensitivity(
                    full_output, missing_output
                )
                if not missing_sensitive:
                    issues.append("missing_key_did_not_reduce_or_qualify_claim")
        if {"full", "noisy"} <= set(by_condition):
            full = by_condition["full"]
            noisy = by_condition["noisy"]
            if full.request_id in outputs and noisy.request_id in outputs:
                full_output = outputs[full.request_id]
                noisy_output = outputs[noisy.request_id]
                noisy_robust = (
                    _claim_rank(noisy_output) == _claim_rank(full_output)
                    and noisy.correctness.label == full.correctness.label
                    and not noisy.behavior.secondary_comparison_used_as_support
                )
                if not noisy_robust:
                    issues.append("noisy_condition_changed_claim_or_selected_secondary_evidence")
        if not complete:
            issues.append("incomplete_three_condition_family")
        results.append(
            PairedSensitivityEvaluation(
                case_family_id=family_id,
                variant=variant,
                complete_three_condition_family=complete,
                missing_key_sensitivity=missing_sensitive,
                noisy_robustness=noisy_robust,
                issues=tuple(issues),
            )
        )
    return tuple(results)


def evaluate_matched_pilot(
    pilot_dir: str | Path,
    evidence_store_dir: str | Path,
    cases_dir: str | Path,
    *,
    openai_config_path: str | Path | None = None,
    preflight_path: str | Path | None = None,
) -> MatchedPilotEvaluationReport:
    """Validate all inputs, score each run, then compute paired-family sensitivity."""

    manifest, records, manifest_sha = _load_validated_pilot_records(
        pilot_dir,
        evidence_store_dir,
        openai_config_path=openai_config_path,
        preflight_path=preflight_path,
    )
    store = load_bundle_store(evidence_store_dir)
    bundles = {bundle.diagnosis_context_id: bundle for bundle in store.bundles}
    cases = _load_cases(cases_dir)
    evaluations: list[DiagnosisEvaluation] = []
    outputs: dict[str, DiagnosisOutput] = {}
    for record in records:
        context_id = record.request.diagnosis_view.diagnosis_context_id
        if context_id not in bundles:
            raise ValueError(f"pilot context has no evidence bundle: {context_id}")
        bundle = bundles[context_id]
        if bundle.case_id not in cases:
            raise ValueError(f"evidence bundle has no source case: {bundle.case_id}")
        case = cases[bundle.case_id]
        output = _successful_output(record, Path(pilot_dir))
        if output is None:
            correctness, citations, support, behavior = _not_evaluable_components(
                case.ground_truth.failure_eligibility.classification == "eligible_failure"
            )
        else:
            outputs[record.request.request_id] = output
            correctness = evaluate_correctness(output, case)
            citations = evaluate_citations(output, bundle)
            support = evaluate_support(output, bundle)
            behavior = evaluate_behavior(output, bundle, case)
        aligned = (
            citations.valid
            and support.label == "fully_supported"
            and behavior.rubric_compliant
        )
        evaluations.append(
            DiagnosisEvaluation(
                request_id=record.request.request_id,
                diagnosis_context_id=context_id,
                case_family_id=bundle.case_family_id,
                evidence_condition=bundle.evidence_condition,
                variant=record.request.variant,
                run_status=record.final_status,
                correctness=correctness,
                citations=citations,
                support=support,
                behavior=behavior,
                evidence_aligned=aligned,
                divergence=_divergence(correctness.label, aligned),
            )
        )
    result = tuple(sorted(evaluations, key=lambda item: item.request_id))
    paired = _paired_sensitivity(result, outputs)
    correctness_counts = Counter(item.correctness.label for item in result)
    support_counts = Counter(item.support.label for item in result)
    summary = EvaluationSummary(
        run_count=len(result),
        evaluable_count=sum(item.run_status == "success" for item in result),
        correctness_counts=dict(sorted(correctness_counts.items())),
        support_counts=dict(sorted(support_counts.items())),
        behavior_compliant_count=sum(item.behavior.rubric_compliant for item in result),
        evidence_aligned_count=sum(item.evidence_aligned for item in result),
        complete_paired_family_count=sum(item.complete_three_condition_family for item in paired),
        missing_key_sensitive_count=sum(item.missing_key_sensitivity is True for item in paired),
        noisy_robust_count=sum(item.noisy_robustness is True for item in paired),
    )
    return MatchedPilotEvaluationReport(
        schema_version=EVALUATION_SCHEMA_VERSION,
        scorer_version=SCORER_VERSION,
        source_evidence_store_sha256=manifest.source_evidence_store_sha256,
        source_pilot_manifest_sha256=manifest_sha,
        diagnosis_evaluations=result,
        paired_sensitivity=paired,
        summary=summary,
    )


def write_evaluation_report(
    report: MatchedPilotEvaluationReport, output_path: str | Path
) -> None:
    """Persist one immutable canonical evaluation artifact."""

    output = Path(output_path)
    if output.exists():
        raise FileExistsError(f"refusing to replace an existing evaluation: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        json.dumps(
            report.model_dump(mode="json"),
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n"
    )
    with output.open("x", encoding="utf-8") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
