"""Development-only score-mapping reader using the real gateway context type.

The evaluator-side observation and study ledger must never be passed to a
provider.  Only the selected six-decimal projection becomes model-visible
content.  This module prepares a context; it neither authorizes nor executes
a provider call or a protected experiment.
"""

from __future__ import annotations

from aletheia_lab.benchmark.p2.score_mapping_evidence import (
    DevelopmentObservation,
    EvidenceCondition,
    ScoreMappingEvidenceError,
    serialize_m5_diagnostic_view,
)
from aletheia_lab.evaluation.claim_evidence_semantics import (
    ModelVisibleEvidenceContext,
    ModelVisibleEvidenceItem,
)
from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256
from aletheia_lab.project.identity import content_sha256


def build_score_mapping_reader_context(
    observation: DevelopmentObservation, *, condition: EvidenceCondition
) -> ModelVisibleEvidenceContext:
    """Wrap exactly one projection in the gateway's model-visible envelope.

    All identifying fields are fixed or derived from *visible* content.  In
    particular, source_content_sha256 is the projection hash, not the private
    upstream source hash.  For an indistinguishable matched pair this yields
    identical complete context JSON, including its IDs and every digest.
    """

    projected = serialize_m5_diagnostic_view(observation, condition=condition)
    content = projected.decode("utf-8", errors="strict")
    projected_sha256 = content_sha256(projected)
    item = ModelVisibleEvidenceItem(
        evidence_id="development-observation",
        kind="metric",
        title="Observed evaluation measurements",
        content=content,
        content_sha256=projected_sha256,
        source_content_sha256=projected_sha256,
    )
    identity = {
        "schema_version": "claim-visible-evidence-context/v1",
        "items": (item.model_dump(mode="json"),),
    }
    context_sha256 = canonical_execution_sha256(identity)
    return ModelVisibleEvidenceContext(
        context_id=f"ccctx-{context_sha256}",
        items=(item,),
        context_sha256=context_sha256,
    )


def validate_score_mapping_reader_context(
    context: ModelVisibleEvidenceContext,
    *,
    observation: DevelopmentObservation,
    condition: EvidenceCondition,
) -> None:
    """Reject a rewrapped context with extra or substituted model-visible data.

    A valid generic gateway context may still contain a private source digest,
    a cause-bearing title, extra catalog items or unrounded metrics.  Compare
    the complete context against this reader's authorized construction rather
    than relying on the generic context schema alone.
    """

    expected = build_score_mapping_reader_context(observation, condition=condition)
    if (
        not isinstance(context, ModelVisibleEvidenceContext)
        or context.model_payload() != expected.model_payload()
    ):
        raise ScoreMappingEvidenceError("context differs from the authorized reader projection")
