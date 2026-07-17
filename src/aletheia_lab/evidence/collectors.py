"""Deterministic collectors for diagnosis-safe evidence.

The P1 collector consumes only integrity-validated case artifacts.  It never
uses ``ground_truth.json`` or ``injection.json`` as diagnosis-visible evidence.
For ``missing_key`` contexts, the evaluator-held counterparts are reconstructed
from the validated ``full`` sibling in the same case family.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import cast

from aletheia_lab.benchmark.case_schema import CaseManifest, DiagnosisInput
from aletheia_lab.benchmark.case_validation import EXPECTED_CASE_COUNT, validate_p1_cases
from aletheia_lab.benchmark.case_writer import LoadedCase, load_case_dir_schema_only
from aletheia_lab.evidence.rubric import (
    EVIDENCE_CONDITIONS,
    EvidenceCondition,
    EvidenceRole,
    condition_rubric_for,
)
from aletheia_lab.evidence.schema import (
    EVIDENCE_BUNDLE_SCHEMA_VERSION,
    EvidenceBundle,
    EvidenceItem,
    EvidenceKind,
    canonical_json,
)
from aletheia_lab.evidence.validation import (
    validate_bundle_for_case,
    validate_sibling_bundles,
)

P1_COLLECTOR_VERSION = "p1-context/2"


class EvidenceCollectionError(RuntimeError):
    """Raised when validated P1 cases cannot yield a canonical evidence set."""


def collect_text_log(
    path: str | Path,
    evidence_id: str,
    title: str,
    *,
    source_root: str | Path,
    evidence_roles: tuple[EvidenceRole, ...] = ("symptom",),
    collector_version: str = "text-log/1",
) -> EvidenceItem:
    """Create a diagnosis-visible log item using an allowlisted relative source path.

    This generic helper remains usable without accepting absolute paths as
    evidence provenance; the strict P1 matrix collector is below.
    """

    root = Path(source_root).resolve()
    log_path = Path(path).resolve()
    try:
        relative_path = log_path.relative_to(root).as_posix()
    except ValueError as exc:
        raise ValueError("log path must be inside source_root") from exc
    content = log_path.read_text(encoding="utf-8")
    return EvidenceItem.from_content(
        evidence_id=evidence_id,
        kind="log",
        evidence_roles=evidence_roles,
        title=title,
        content=content,
        source_path=relative_path,
        collector_version=collector_version,
        visibility="diagnosis",
    )


def _case_source_path(cases_root: Path, case_dir: Path) -> str:
    """Verify confinement and return a path relative to the source context."""

    source_link = case_dir / "diagnosis_input.json"
    if case_dir.is_symlink() or source_link.is_symlink():
        raise EvidenceCollectionError("case directories and source artifacts must not be symlinks")
    try:
        source = source_link.resolve()
        source.relative_to(cases_root.resolve())
    except ValueError as exc:
        raise EvidenceCollectionError("case directory escapes the cases root") from exc
    if not source.is_file():
        raise EvidenceCollectionError("diagnosis source artifact is missing")
    return "diagnosis_input.json"


def _item(
    *,
    evidence_id: str,
    kind: EvidenceKind,
    roles: tuple[EvidenceRole, ...],
    title: str,
    payload: object,
    source_path: str,
    source_context_id: str,
    visible: bool,
    evaluator_role: str | None = None,
) -> EvidenceItem:
    metadata: dict[str, str] = {"source_context_id": source_context_id}
    if evaluator_role is not None:
        metadata["evaluator_role"] = evaluator_role
    return EvidenceItem.from_content(
        evidence_id=evidence_id,
        kind=kind,
        evidence_roles=roles,
        title=title,
        content=canonical_json(payload),
        source_path=source_path,
        collector_version=P1_COLLECTOR_VERSION,
        visibility="diagnosis" if visible else "evaluator",
        redaction_state="none" if visible else "withheld",
        metadata=metadata,
    )


def _core_items(
    *,
    signals: object,
    source_path: str,
    source_context_id: str,
    condition: EvidenceCondition,
) -> tuple[EvidenceItem, ...]:
    """Materialize canonical core evidence from the validated full sibling."""

    # Local import only avoids weakening the public collector signature to a
    # loose dict while retaining strict Pydantic values from DiagnosisInput.
    from aletheia_lab.benchmark.case_schema import ObservableSignals

    if not isinstance(signals, ObservableSignals):
        raise EvidenceCollectionError("collector requires strict ObservableSignals")
    required = (
        signals.candidate_feature,
        signals.distribution_reference,
        signals.distribution_observed,
        signals.psi,
        signals.sample_size,
        signals.baseline_metric_reference,
    )
    if any(value is None for value in required):
        raise EvidenceCollectionError("full sibling lacks canonical decisive evidence")

    withheld = set(condition_rubric_for(condition).intentionally_withheld_evidence_roles)
    feature = cast("str", signals.candidate_feature)
    sample_size = cast("int", signals.sample_size)
    common = {"feature": feature, "sample_size": sample_size}

    items = [
        _item(
            evidence_id="candidate-observed",
            kind="dataset_profile",
            roles=("symptom", "candidate_distribution_observed"),
            title="Candidate-window categorical profile",
            payload={
                **common,
                "window": "candidate",
                "distribution": signals.distribution_observed,
            },
            source_path=source_path,
            source_context_id=source_context_id,
            visible=True,
        ),
        _item(
            evidence_id="candidate-reference",
            kind="dataset_profile",
            roles=("candidate_distribution_reference",),
            title="Reference-window categorical profile",
            payload={
                **common,
                "window": "reference",
                "distribution": signals.distribution_reference,
            },
            source_path=source_path,
            source_context_id=source_context_id,
            visible="candidate_distribution_reference" not in withheld,
        ),
        _item(
            evidence_id="candidate-psi",
            kind="metric",
            roles=("candidate_psi",),
            title="Categorical profile comparison statistic",
            payload={**common, "comparison": "reference_to_candidate", "psi": signals.psi},
            source_path=source_path,
            source_context_id=source_context_id,
            visible="candidate_psi" not in withheld,
        ),
        _item(
            evidence_id="metric-comparison",
            kind="metric",
            roles=("metric_comparison",),
            title="Evaluation metric comparison",
            payload=signals.baseline_metric_reference.model_dump(mode="json"),
            source_path=source_path,
            source_context_id=source_context_id,
            visible="metric_comparison" not in withheld,
        ),
    ]
    return tuple(items)


def _bundle_for_case(
    *,
    manifest: CaseManifest,
    diagnosis_input: DiagnosisInput,
    canonical_full_input: DiagnosisInput,
    canonical_source_path: str,
    canonical_source_context_id: str,
    own_source_path: str,
) -> EvidenceBundle:
    """Collect one bundle without crossing the ground-truth trust boundary."""

    condition = manifest.evidence_condition
    # The candidate observation is present in all three conditions.  Use the
    # context's own validated payload for it, but the full sibling for fields
    # intentionally absent from missing_key.
    core = list(
        _core_items(
            signals=canonical_full_input.observable_signals,
            source_path=canonical_source_path,
            source_context_id=canonical_source_context_id,
            condition=condition,
        )
    )
    own_observed = _core_items(
        signals=canonical_full_input.observable_signals,
        source_path=own_source_path,
        source_context_id=diagnosis_input.diagnosis_context_id,
        condition=condition,
    )[0]
    core[0] = EvidenceItem.from_content(
        evidence_id=own_observed.evidence_id,
        kind=own_observed.kind,
        evidence_roles=own_observed.evidence_roles,
        title=own_observed.title,
        content=canonical_json(
            {
                "feature": diagnosis_input.observable_signals.candidate_feature,
                "sample_size": diagnosis_input.observable_signals.sample_size,
                "window": "candidate",
                "distribution": diagnosis_input.observable_signals.distribution_observed,
            }
        ),
        source_path=own_source_path,
        collector_version=P1_COLLECTOR_VERSION,
        visibility="diagnosis",
        metadata={"source_context_id": diagnosis_input.diagnosis_context_id},
    )

    if condition == "noisy":
        comparisons = diagnosis_input.observable_signals.additional_comparisons
        if len(comparisons) != 1:
            raise EvidenceCollectionError(
                "noisy context must have exactly one additional comparison"
            )
        comparison = comparisons[0]
        core.append(
            _item(
                evidence_id="secondary-comparison",
                kind="dataset_profile",
                roles=("secondary_distribution_comparison",),
                title="Additional categorical profile comparison",
                payload=comparison.model_dump(mode="json"),
                source_path=own_source_path,
                source_context_id=diagnosis_input.diagnosis_context_id,
                visible=True,
                evaluator_role="distractor",
            )
        )

    rubric = condition_rubric_for(condition)
    bundle = EvidenceBundle(
        schema_version=EVIDENCE_BUNDLE_SCHEMA_VERSION,
        evidence_bundle_id=manifest.evidence_bundle_id,
        case_id=manifest.case_id,
        case_family_id=manifest.case_family_id,
        diagnosis_context_id=diagnosis_input.diagnosis_context_id,
        evidence_condition=condition,
        dataset_id=manifest.dataset_id,
        dataset_sha256=manifest.dataset_sha256,
        split_manifest_sha256=manifest.split_manifest_sha256,
        items=tuple(core),
        required_evidence_roles=rubric.required_evidence_roles,
        missing_required_evidence_roles=rubric.intentionally_withheld_evidence_roles,
        intentionally_withheld_evidence_roles=rubric.intentionally_withheld_evidence_roles,
    )
    validate_bundle_for_case(bundle, manifest, diagnosis_input)
    return bundle


def collect_p1_bundles(cases_dir: str | Path) -> tuple[EvidenceBundle, ...]:
    """Collect and independently validate the canonical 15-bundle P1 set."""

    cases_root = Path(cases_dir)
    if cases_root.is_symlink():
        raise EvidenceCollectionError("cases root must not be a symlink")
    report = validate_p1_cases(cases_root)
    if not report.passed:
        raise EvidenceCollectionError(f"P1 case integrity gate failed: {report.as_dict()}")

    case_dirs = sorted(
        path
        for path in cases_root.iterdir()
        if path.is_dir() and (path / "manifest.json").is_file()
    )
    if len(case_dirs) != EXPECTED_CASE_COUNT:
        raise EvidenceCollectionError(
            f"expected {EXPECTED_CASE_COUNT} validated case directories, got {len(case_dirs)}"
        )

    loaded = [(path, load_case_dir_schema_only(path)) for path in case_dirs]
    families: dict[str, list[tuple[Path, LoadedCase]]] = defaultdict(list)
    for path, case in loaded:
        families[case.manifest.case_family_id].append((path, case))

    bundles: list[EvidenceBundle] = []
    for family_id in sorted(families):
        siblings = families[family_id]
        by_condition = {case.manifest.evidence_condition: (path, case) for path, case in siblings}
        if set(by_condition) != set(EVIDENCE_CONDITIONS):
            raise EvidenceCollectionError(f"family {family_id} lacks the canonical siblings")
        full_path, full_case = by_condition["full"]
        family_bundles: list[EvidenceBundle] = []
        for condition in EVIDENCE_CONDITIONS:
            case_path, case = by_condition[condition]
            bundle = _bundle_for_case(
                manifest=case.manifest,
                diagnosis_input=case.diagnosis_input,
                canonical_full_input=full_case.diagnosis_input,
                canonical_source_path=_case_source_path(cases_root, full_path),
                canonical_source_context_id=full_case.diagnosis_input.diagnosis_context_id,
                own_source_path=_case_source_path(cases_root, case_path),
            )
            family_bundles.append(bundle)
            bundles.append(bundle)
        validate_sibling_bundles(family_bundles)

    ordered = tuple(sorted(bundles, key=lambda bundle: bundle.evidence_bundle_id))
    if len(ordered) != EXPECTED_CASE_COUNT:
        raise EvidenceCollectionError(f"collector produced {len(ordered)} bundles, expected 15")
    return ordered
