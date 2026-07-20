"""Offline-only canonical reporting for the frozen P1 external pilot."""

from __future__ import annotations

import hashlib
import json
import os
from collections import Counter
from pathlib import Path
from typing import Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aletheia_lab.diagnosis.openai_preflight import (
    OpenAICostEstimates,
    load_openai_preflight,
)
from aletheia_lab.diagnosis.schema import PilotVariant, ProviderIdentity
from aletheia_lab.evaluation.pilot import (
    CorrectnessLabel,
    EvaluationSummary,
    MatchedPilotEvaluationReport,
    SupportLabel,
)
from aletheia_lab.evaluation.result_lock import validate_p1_result_lock
from aletheia_lab.evidence.rubric import EvidenceCondition

CANONICAL_RESULT_SCHEMA_VERSION: Final[
    Literal["p1-canonical-result/1"]
] = "p1-canonical-result/1"
OPERATIONAL_REPORT_SCHEMA_VERSION: Final[
    Literal["p1-operational-report/1"]
] = "p1-operational-report/1"
ERROR_ANALYSIS_SCHEMA_VERSION: Final[
    Literal["p1-error-analysis-draft/1"]
] = "p1-error-analysis-draft/1"
CLOSEOUT_MANIFEST_SCHEMA_VERSION: Final[
    Literal["p1-closeout-manifest/1"]
] = "p1-closeout-manifest/1"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class VariantMachineSummary(_StrictFrozenModel):
    variant: PilotVariant
    run_count: int = Field(ge=0)
    evaluable_count: int = Field(ge=0)
    correctness_counts: dict[str, int]
    support_counts: dict[str, int]
    behavior_compliant_count: int = Field(ge=0)
    evidence_aligned_count: int = Field(ge=0)
    paired_group_count: int = Field(ge=0)
    missing_key_sensitive_count: int = Field(ge=0)
    noisy_robust_count: int = Field(ge=0)

    @model_validator(mode="after")
    def _derived_census(self) -> Self:
        if sum(self.correctness_counts.values()) != self.run_count:
            raise ValueError("variant correctness counts do not match run_count")
        if sum(self.support_counts.values()) != self.run_count:
            raise ValueError("variant support counts do not match run_count")
        if not 0 <= self.evaluable_count <= self.run_count:
            raise ValueError("variant evaluable_count exceeds run_count")
        if not 0 <= self.behavior_compliant_count <= self.run_count:
            raise ValueError("variant behavior count exceeds run_count")
        if not 0 <= self.evidence_aligned_count <= self.run_count:
            raise ValueError("variant alignment count exceeds run_count")
        if not 0 <= self.missing_key_sensitive_count <= self.paired_group_count:
            raise ValueError("variant missing-key count exceeds paired groups")
        if not 0 <= self.noisy_robust_count <= self.paired_group_count:
            raise ValueError("variant noisy count exceeds paired groups")
        return self


class P1CanonicalResult(_StrictFrozenModel):
    schema_version: Literal["p1-canonical-result/1"]
    result_status: Literal["machine_scored_pending_human_review"]
    result_lock_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_evidence_store_sha256: str = Field(pattern=_SHA256_PATTERN)
    evaluation_report_sha256: str = Field(pattern=_SHA256_PATTERN)
    provider_identity: ProviderIdentity
    independent_family_count: int = Field(gt=0)
    diagnosis_context_count: int = Field(gt=0)
    variant_count: int = Field(gt=0)
    run_count: int = Field(gt=0)
    evaluation_summary: EvaluationSummary
    variant_summaries: tuple[VariantMachineSummary, ...]
    claim_boundary: str

    @model_validator(mode="after")
    def _complete_census(self) -> Self:
        if (
            self.independent_family_count,
            self.diagnosis_context_count,
            self.variant_count,
            self.run_count,
        ) != (5, 15, 2, 30):
            raise ValueError("canonical P1 closeout must preserve the 5/15/2/30 census")
        if self.run_count != self.evaluation_summary.run_count:
            raise ValueError("canonical run_count differs from evaluation summary")
        if sum(item.run_count for item in self.variant_summaries) != self.run_count:
            raise ValueError("variant run counts do not cover the canonical result")
        variants = tuple(item.variant for item in self.variant_summaries)
        if len(variants) != self.variant_count or len(set(variants)) != self.variant_count:
            raise ValueError("variant summaries do not preserve the variant census")
        if variants != tuple(sorted(variants, key=str)):
            raise ValueError("variant summaries must be sorted")
        return self


class P1OperationalReport(_StrictFrozenModel):
    schema_version: Literal["p1-operational-report/1"]
    result_lock_sha256: str = Field(pattern=_SHA256_PATTERN)
    provider_identity: ProviderIdentity
    pricing_contract: str
    cost_estimates: OpenAICostEstimates
    actual_run_count: int = Field(ge=0)
    actual_attempt_count: int = Field(ge=0)
    actual_retry_count: int = Field(ge=0)
    actual_unresolved_count: int = Field(ge=0)
    actual_input_tokens: int = Field(ge=0)
    actual_output_tokens: int = Field(ge=0)
    actual_estimated_cost_usd: float = Field(ge=0.0, allow_inf_nan=False)
    aggregate_latency_ms: float = Field(ge=0.0, allow_inf_nan=False)
    interpretation_boundary: str

    @model_validator(mode="after")
    def _actual_counts(self) -> Self:
        if self.actual_attempt_count != self.actual_run_count + self.actual_retry_count:
            raise ValueError("actual attempts are not runs plus retries")
        if self.actual_unresolved_count > self.actual_run_count:
            raise ValueError("unresolved count exceeds run count")
        return self


class EntryErrorFinding(_StrictFrozenModel):
    finding_id: str = Field(pattern=r"^entry-finding-[0-9]{2}$")
    request_id: str = Field(pattern=r"^diagreq-[0-9a-f]{64}$")
    diagnosis_context_id: str = Field(pattern=r"^p1-context-[0-9a-f]{64}$")
    case_family_id: str = Field(pattern=r"^p1-family-[0-9a-f]{64}$")
    evidence_condition: EvidenceCondition
    variant: PilotVariant
    correctness_label: CorrectnessLabel
    support_label: SupportLabel
    evidence_aligned: bool
    behavior_issues: tuple[str, ...]
    finding_codes: tuple[str, ...]
    machine_rationale: str
    human_review_status: Literal["pending"]


class PairedErrorFinding(_StrictFrozenModel):
    finding_id: str = Field(pattern=r"^paired-finding-[0-9]{2}$")
    case_family_id: str = Field(pattern=r"^p1-family-[0-9a-f]{64}$")
    variant: PilotVariant
    missing_key_sensitivity: bool | None
    noisy_robustness: bool | None
    issues: tuple[str, ...]
    finding_codes: tuple[str, ...]
    human_review_status: Literal["pending"]


class P1ErrorAnalysisDraft(_StrictFrozenModel):
    schema_version: Literal["p1-error-analysis-draft/1"]
    result_status: Literal["machine_scored_pending_human_review"]
    result_lock_sha256: str = Field(pattern=_SHA256_PATTERN)
    entry_finding_count: int = Field(ge=0)
    paired_finding_count: int = Field(ge=0)
    entry_findings: tuple[EntryErrorFinding, ...]
    paired_findings: tuple[PairedErrorFinding, ...]
    analysis_boundary: str

    @model_validator(mode="after")
    def _derived_counts(self) -> Self:
        if self.entry_finding_count != len(self.entry_findings):
            raise ValueError("entry finding count is not derived")
        if self.paired_finding_count != len(self.paired_findings):
            raise ValueError("paired finding count is not derived")
        return self


class CloseoutArtifact(_StrictFrozenModel):
    relative_path: str
    sha256: str = Field(pattern=_SHA256_PATTERN)


class P1CloseoutManifest(_StrictFrozenModel):
    schema_version: Literal["p1-closeout-manifest/1"]
    result_lock_sha256: str = Field(pattern=_SHA256_PATTERN)
    artifact_count: int = Field(ge=1)
    artifacts: tuple[CloseoutArtifact, ...]

    @model_validator(mode="after")
    def _artifact_contract(self) -> Self:
        paths = tuple(item.relative_path for item in self.artifacts)
        if paths != tuple(sorted(paths)) or len(paths) != len(set(paths)):
            raise ValueError("closeout artifact paths must be unique and sorted")
        if self.artifact_count != len(self.artifacts):
            raise ValueError("closeout artifact count is not derived")
        return self


class P1CloseoutPackage(_StrictFrozenModel):
    canonical_result: P1CanonicalResult
    operational_report: P1OperationalReport
    error_analysis_draft: P1ErrorAnalysisDraft
    manifest: P1CloseoutManifest


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _canonical_bytes(payload: object) -> bytes:
    return (
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n"
    ).encode("utf-8")


def _load_evaluation(path: Path) -> MatchedPilotEvaluationReport:
    if path.is_symlink() or not path.is_file():
        raise FileNotFoundError(f"canonical evaluation is not a real file: {path}")
    return MatchedPilotEvaluationReport.model_validate_json(path.read_bytes())


def _variant_summary(
    report: MatchedPilotEvaluationReport, variant: PilotVariant
) -> VariantMachineSummary:
    rows = tuple(item for item in report.diagnosis_evaluations if item.variant == variant)
    pairs = tuple(item for item in report.paired_sensitivity if item.variant == variant)
    return VariantMachineSummary(
        variant=variant,
        run_count=len(rows),
        evaluable_count=sum(item.run_status == "success" for item in rows),
        correctness_counts=dict(
            sorted(Counter(item.correctness.label for item in rows).items())
        ),
        support_counts=dict(sorted(Counter(item.support.label for item in rows).items())),
        behavior_compliant_count=sum(item.behavior.rubric_compliant for item in rows),
        evidence_aligned_count=sum(item.evidence_aligned for item in rows),
        paired_group_count=len(pairs),
        missing_key_sensitive_count=sum(
            item.missing_key_sensitivity is True for item in pairs
        ),
        noisy_robust_count=sum(item.noisy_robustness is True for item in pairs),
    )


def _entry_findings(
    report: MatchedPilotEvaluationReport,
) -> tuple[EntryErrorFinding, ...]:
    findings: list[EntryErrorFinding] = []
    for row in report.diagnosis_evaluations:
        codes: list[str] = []
        if row.correctness.label in {"partial", "incorrect", "not_evaluable"}:
            codes.append(f"correctness:{row.correctness.label}")
        if row.correctness.label == "not_asserted" and row.evidence_condition != "missing_key":
            codes.append("correctness:unexpected_not_asserted")
        if not row.citations.valid:
            codes.append("citation_invalid")
        if row.support.label != "fully_supported":
            codes.append(f"support:{row.support.label}")
        if not row.behavior.rubric_compliant:
            codes.extend(f"behavior:{issue}" for issue in row.behavior.issues)
        if not row.evidence_aligned:
            codes.append("not_evidence_aligned")
        if not codes:
            continue
        rationale_parts = [
            row.correctness.rationale,
            row.citations.rationale,
            row.support.rationale,
        ]
        if row.behavior.issues:
            rationale_parts.append("Behavior issues: " + ", ".join(row.behavior.issues) + ".")
        findings.append(
            EntryErrorFinding(
                finding_id=f"entry-finding-{len(findings) + 1:02d}",
                request_id=row.request_id,
                diagnosis_context_id=row.diagnosis_context_id,
                case_family_id=row.case_family_id,
                evidence_condition=row.evidence_condition,
                variant=row.variant,
                correctness_label=row.correctness.label,
                support_label=row.support.label,
                evidence_aligned=row.evidence_aligned,
                behavior_issues=row.behavior.issues,
                finding_codes=tuple(codes),
                machine_rationale=" ".join(rationale_parts),
                human_review_status="pending",
            )
        )
    return tuple(findings)


def _paired_findings(
    report: MatchedPilotEvaluationReport,
) -> tuple[PairedErrorFinding, ...]:
    findings: list[PairedErrorFinding] = []
    for row in report.paired_sensitivity:
        codes: list[str] = []
        if row.missing_key_sensitivity is not True:
            codes.append("missing_key_sensitivity_failure")
        if row.noisy_robustness is not True:
            codes.append("noisy_robustness_failure")
        if not row.complete_three_condition_family:
            codes.append("incomplete_three_condition_family")
        if not codes:
            continue
        findings.append(
            PairedErrorFinding(
                finding_id=f"paired-finding-{len(findings) + 1:02d}",
                case_family_id=row.case_family_id,
                variant=row.variant,
                missing_key_sensitivity=row.missing_key_sensitivity,
                noisy_robustness=row.noisy_robustness,
                issues=row.issues,
                finding_codes=tuple(codes),
                human_review_status="pending",
            )
        )
    return tuple(findings)


def derive_p1_closeout(
    lock_path: str | Path,
    pilot_dir: str | Path,
    evidence_store_dir: str | Path,
    cases_dir: str | Path,
    config_path: str | Path,
    preflight_path: str | Path,
    evaluation_path: str | Path,
) -> tuple[P1CanonicalResult, P1OperationalReport, P1ErrorAnalysisDraft]:
    """Validate the complete source chain and deterministically derive reports."""

    lock_file = Path(lock_path)
    evaluation_file = Path(evaluation_path)
    lock = validate_p1_result_lock(
        lock_file,
        pilot_dir,
        evidence_store_dir,
        cases_dir,
        config_path,
        preflight_path,
        evaluation_file,
    )
    lock_sha = _sha256_bytes(lock_file.read_bytes())
    report = _load_evaluation(evaluation_file)
    preflight = load_openai_preflight(preflight_path)
    if preflight.cost_estimates is None:
        raise ValueError("P1 closeout requires the explicit four-budget preflight")
    variants = tuple(sorted({item.variant for item in report.diagnosis_evaluations}, key=str))
    canonical = P1CanonicalResult(
        schema_version=CANONICAL_RESULT_SCHEMA_VERSION,
        result_status="machine_scored_pending_human_review",
        result_lock_sha256=lock_sha,
        source_evidence_store_sha256=lock.source_evidence_store_sha256,
        evaluation_report_sha256=lock.evaluation_report_sha256,
        provider_identity=lock.provider_identity,
        independent_family_count=len(
            {item.case_family_id for item in report.diagnosis_evaluations}
        ),
        diagnosis_context_count=len(
            {item.diagnosis_context_id for item in report.diagnosis_evaluations}
        ),
        variant_count=len(variants),
        run_count=report.summary.run_count,
        evaluation_summary=report.summary,
        variant_summaries=tuple(_variant_summary(report, variant) for variant in variants),
        claim_boundary=(
            "Feasibility and directional pilot evidence only; human semantic review is "
            "pending and five independent families do not support a statistical "
            "superiority or generalization claim."
        ),
    )
    totals = lock.operational_totals
    operational = P1OperationalReport(
        schema_version=OPERATIONAL_REPORT_SCHEMA_VERSION,
        result_lock_sha256=lock_sha,
        provider_identity=lock.provider_identity,
        pricing_contract="USD per million tokens frozen by openai-pilot-config/1",
        cost_estimates=preflight.cost_estimates,
        actual_run_count=totals.run_count,
        actual_attempt_count=totals.attempt_count,
        actual_retry_count=totals.retry_count,
        actual_unresolved_count=totals.unresolved_count,
        actual_input_tokens=totals.input_tokens,
        actual_output_tokens=totals.output_tokens,
        actual_estimated_cost_usd=totals.estimated_cost_usd,
        aggregate_latency_ms=totals.latency_ms,
        interpretation_boundary=(
            "Preflight values are authorization ceilings. Actual cost is an estimate "
            "from immutable provider usage metadata, not an independently audited invoice; "
            "aggregate latency is not production throughput."
        ),
    )
    entries = _entry_findings(report)
    pairs = _paired_findings(report)
    errors = P1ErrorAnalysisDraft(
        schema_version=ERROR_ANALYSIS_SCHEMA_VERSION,
        result_status="machine_scored_pending_human_review",
        result_lock_sha256=lock_sha,
        entry_finding_count=len(entries),
        paired_finding_count=len(pairs),
        entry_findings=entries,
        paired_findings=pairs,
        analysis_boundary=(
            "This is a deterministic machine-scored draft. Preserve all negative results "
            "and reconcile them with the staged independent human review without altering "
            "the canonical provider outputs."
        ),
    )
    return canonical, operational, errors


def _format_counts(counts: dict[str, int]) -> str:
    return "; ".join(f"{key}={value}" for key, value in sorted(counts.items()))


def _canonical_markdown(result: P1CanonicalResult) -> str:
    summary = result.evaluation_summary
    lines = [
        "# P1 Canonical Machine-Scored Result",
        "",
        "> Status: machine-scored; independent human semantic review pending.",
        "",
        "## Experimental census",
        "",
        "| Independent families | Contexts | Variants | Outputs | Model |",
        "|---:|---:|---:|---:|---|",
        f"| {result.independent_family_count} | {result.diagnosis_context_count} | "
        f"{result.variant_count} | {result.run_count} | `{result.provider_identity.model}` |",
        "",
        "## Aggregate evaluation",
        "",
        "| Metric | Result |",
        "|---|---:|",
        f"| Evaluable | {summary.evaluable_count}/{summary.run_count} |",
        f"| Correctness | {_format_counts(summary.correctness_counts)} |",
        f"| Support | {_format_counts(summary.support_counts)} |",
        f"| Behavior compliant | {summary.behavior_compliant_count}/{summary.run_count} |",
        f"| Evidence aligned | {summary.evidence_aligned_count}/{summary.run_count} |",
        f"| Complete paired groups | {summary.complete_paired_family_count}/10 |",
        f"| Missing-key sensitive | {summary.missing_key_sensitive_count}/10 |",
        f"| Noisy robust | {summary.noisy_robust_count}/10 |",
        "",
        "## Variant breakdown",
        "",
        "| Variant | Correctness | Support | Aligned | Missing sensitivity | Noisy robustness |",
        "|---|---|---|---:|---:|---:|",
    ]
    for item in result.variant_summaries:
        lines.append(
            f"| `{item.variant}` | {_format_counts(item.correctness_counts)} | "
            f"{_format_counts(item.support_counts)} | {item.evidence_aligned_count}/{item.run_count} | "
            f"{item.missing_key_sensitive_count}/{item.paired_group_count} | "
            f"{item.noisy_robust_count}/{item.paired_group_count} |"
        )
    lines.extend(
        [
            "",
            "## Claim boundary",
            "",
            result.claim_boundary,
            "",
            f"Result-lock SHA-256: `{result.result_lock_sha256}`",
        ]
    )
    return "\n".join(lines) + "\n"


def _operational_markdown(report: P1OperationalReport) -> str:
    costs = report.cost_estimates
    rows = (
        ("Smoke, one attempt", costs.smoke_one_attempt),
        ("Smoke, retry ceiling", costs.smoke_retry_ceiling),
        ("Full, one attempt", costs.full_one_attempt),
        ("Full, retry ceiling", costs.full_retry_ceiling),
    )
    lines = [
        "# P1 Operational and Cost Report",
        "",
        "## Preflight authorization budgets",
        "",
        "| Scenario | Request-attempts | Estimated input tokens | Reserved output tokens | Estimated ceiling (USD) |",
        "|---|---:|---:|---:|---:|",
    ]
    for label, value in rows:
        lines.append(
            f"| {label} | {value.request_attempt_count} | {value.estimated_input_tokens} | "
            f"{value.reserved_output_tokens} | {value.estimated_cost_usd:.6f} |"
        )
    lines.extend(
        [
            "",
            "## Actual canonical full run",
            "",
            "| Runs | Attempts | Retries | Unresolved | Input tokens | Output tokens | Estimated cost (USD) | Aggregate latency (s) |",
            "|---:|---:|---:|---:|---:|---:|---:|---:|",
            f"| {report.actual_run_count} | {report.actual_attempt_count} | "
            f"{report.actual_retry_count} | {report.actual_unresolved_count} | "
            f"{report.actual_input_tokens} | {report.actual_output_tokens} | "
            f"{report.actual_estimated_cost_usd:.6f} | "
            f"{report.aggregate_latency_ms / 1000:.6f} |",
            "",
            "## Interpretation boundary",
            "",
            report.interpretation_boundary,
            "",
            f"Result-lock SHA-256: `{report.result_lock_sha256}`",
        ]
    )
    return "\n".join(lines) + "\n"


def _error_markdown(report: P1ErrorAnalysisDraft) -> str:
    lines = [
        "# P1 Machine-Scored Error Analysis Draft",
        "",
        "> Human semantic review and adjudication are pending. This draft must not be "
        "treated as the final qualitative analysis.",
        "",
        "## Entry-level findings",
        "",
        "| Finding | Variant | Condition | Correctness | Support | Aligned | Codes |",
        "|---|---|---|---|---|---:|---|",
    ]
    for item in report.entry_findings:
        lines.append(
            f"| `{item.finding_id}` | `{item.variant}` | `{item.evidence_condition}` | "
            f"{item.correctness_label} | {item.support_label} | "
            f"{'yes' if item.evidence_aligned else 'no'} | {', '.join(item.finding_codes)} |"
        )
    if not report.entry_findings:
        lines.append("| none | - | - | - | - | - | - |")
    lines.extend(
        [
            "",
            "## Paired-sensitivity findings",
            "",
            "| Finding | Variant | Missing-key sensitivity | Noisy robustness | Codes |",
            "|---|---|---:|---:|---|",
        ]
    )
    for item in report.paired_findings:
        lines.append(
            f"| `{item.finding_id}` | `{item.variant}` | "
            f"{item.missing_key_sensitivity} | {item.noisy_robustness} | "
            f"{', '.join(item.finding_codes)} |"
        )
    if not report.paired_findings:
        lines.append("| none | - | - | - | - |")
    lines.extend(
        [
            "",
            "## Analysis boundary",
            "",
            report.analysis_boundary,
            "",
            f"Result-lock SHA-256: `{report.result_lock_sha256}`",
        ]
    )
    return "\n".join(lines) + "\n"


def _report_payloads(
    canonical: P1CanonicalResult,
    operational: P1OperationalReport,
    errors: P1ErrorAnalysisDraft,
) -> dict[str, bytes]:
    return {
        "canonical-result.json": _canonical_bytes(canonical.model_dump(mode="json")),
        "canonical-result.md": _canonical_markdown(canonical).encode("utf-8"),
        "error-analysis-draft.json": _canonical_bytes(errors.model_dump(mode="json")),
        "error-analysis-draft.md": _error_markdown(errors).encode("utf-8"),
        "operational-report.json": _canonical_bytes(operational.model_dump(mode="json")),
        "operational-report.md": _operational_markdown(operational).encode("utf-8"),
    }


def _manifest(result_lock_sha256: str, payloads: dict[str, bytes]) -> P1CloseoutManifest:
    artifacts = tuple(
        CloseoutArtifact(relative_path=path, sha256=_sha256_bytes(payload))
        for path, payload in sorted(payloads.items())
    )
    return P1CloseoutManifest(
        schema_version=CLOSEOUT_MANIFEST_SCHEMA_VERSION,
        result_lock_sha256=result_lock_sha256,
        artifact_count=len(artifacts),
        artifacts=artifacts,
    )


def _write_immutable(path: Path, payload: bytes) -> None:
    with path.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def generate_p1_closeout(
    lock_path: str | Path,
    pilot_dir: str | Path,
    evidence_store_dir: str | Path,
    cases_dir: str | Path,
    config_path: str | Path,
    preflight_path: str | Path,
    evaluation_path: str | Path,
    output_dir: str | Path,
) -> P1CloseoutPackage:
    """Validate sources and immutably write all deterministic closeout reports."""

    canonical, operational, errors = derive_p1_closeout(
        lock_path,
        pilot_dir,
        evidence_store_dir,
        cases_dir,
        config_path,
        preflight_path,
        evaluation_path,
    )
    payloads = _report_payloads(canonical, operational, errors)
    manifest = _manifest(canonical.result_lock_sha256, payloads)
    root = Path(output_dir)
    root.parent.mkdir(parents=True, exist_ok=True)
    root.mkdir()
    for relative_path, payload in sorted(payloads.items()):
        _write_immutable(root / relative_path, payload)
    _write_immutable(
        root / "closeout-manifest.json",
        _canonical_bytes(manifest.model_dump(mode="json")),
    )
    return P1CloseoutPackage(
        canonical_result=canonical,
        operational_report=operational,
        error_analysis_draft=errors,
        manifest=manifest,
    )


def validate_p1_closeout(
    lock_path: str | Path,
    pilot_dir: str | Path,
    evidence_store_dir: str | Path,
    cases_dir: str | Path,
    config_path: str | Path,
    preflight_path: str | Path,
    evaluation_path: str | Path,
    output_dir: str | Path,
) -> P1CloseoutPackage:
    """Offline-recompute all reports and reject stale, missing or extra artifacts."""

    root = Path(output_dir)
    if root.is_symlink() or not root.is_dir():
        raise FileNotFoundError(f"closeout output is not a real directory: {root}")
    if any(path.is_symlink() for path in root.rglob("*")):
        raise ValueError("closeout output contains a symlink")
    canonical, operational, errors = derive_p1_closeout(
        lock_path,
        pilot_dir,
        evidence_store_dir,
        cases_dir,
        config_path,
        preflight_path,
        evaluation_path,
    )
    payloads = _report_payloads(canonical, operational, errors)
    manifest = _manifest(canonical.result_lock_sha256, payloads)
    expected = {
        **payloads,
        "closeout-manifest.json": _canonical_bytes(manifest.model_dump(mode="json")),
    }
    actual_paths = {
        path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()
    }
    if actual_paths != set(expected):
        raise ValueError(
            "closeout artifact set differs from the canonical package: "
            f"missing={sorted(set(expected) - actual_paths)}, "
            f"unexpected={sorted(actual_paths - set(expected))}"
        )
    for relative_path, payload in expected.items():
        if (root / relative_path).read_bytes() != payload:
            raise ValueError(f"closeout artifact differs from recomputation: {relative_path}")
    return P1CloseoutPackage(
        canonical_result=canonical,
        operational_report=operational,
        error_analysis_draft=errors,
        manifest=manifest,
    )
