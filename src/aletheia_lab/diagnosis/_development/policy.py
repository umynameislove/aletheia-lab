"""Pure policy derivations shared by construction and independent validation."""

from aletheia_lab.diagnosis._development.contracts import (
    DEVELOPMENT_LEDGER_SCHEMA_VERSION,
    DevelopmentCase,
    DevelopmentTool,
    DevelopmentToolEvent,
    DevelopmentToolLedger,
)
from aletheia_lab.diagnosis.variant_registry import ResolvedDiagnosisVariant
from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256


def response_schema_ref(variant: ResolvedDiagnosisVariant) -> str:
    """Return the registered response contract for a resolved variant."""

    if variant.strategy == "deterministic_rules":
        return "deterministic-diagnosis/1"
    if variant.strategy == "native_external":
        return "logdx-native-output/1"
    return "diagnosis-output/2"


def build_tool_ledger(
    case: DevelopmentCase,
    variant: ResolvedDiagnosisVariant,
) -> DevelopmentToolLedger | None:
    """Derive the registered synthetic tool trace from visible case evidence."""

    if not variant.capabilities.tool_ledger_required:
        return None
    evidence_ids = tuple(item.evidence_id for item in case.evidence)
    if variant.strategy == "native_external":
        tools: tuple[DevelopmentTool, ...] = ("native_external_fixture",)
    elif variant.strategy == "codegraph_retrieval":
        tools = ("code_graph",)
    elif variant.strategy == "full_system":
        tools = ("retrieval", "code_graph")
    else:
        tools = ("retrieval",)
    events: list[DevelopmentToolEvent] = []
    for index, tool in enumerate(tools, start=1):
        selected = evidence_ids[index - 1 :: len(tools)] or evidence_ids[:1]
        omitted = tuple(item for item in evidence_ids if item not in selected)
        event_payload = {
            "turn": index,
            "tool": tool,
            "query": f"development query {index} for {case.case_id}",
            "selected_evidence_ids": selected,
            "omitted_evidence_ids": omitted,
        }
        events.append(
            DevelopmentToolEvent.model_validate(
                {
                    **event_payload,
                    "event_sha256": canonical_execution_sha256(event_payload),
                }
            )
        )
    ledger_identity_payload = {
        "schema_version": DEVELOPMENT_LEDGER_SCHEMA_VERSION,
        "variant_id": variant.variant_id,
        "case_id": case.case_id,
        "events": tuple(item.model_dump(mode="json") for item in events),
        "web_used": False,
        "shell_used": False,
        "project_execution_used": False,
        "fallback_used": False,
    }
    return DevelopmentToolLedger(
        variant_id=variant.variant_id,
        case_id=case.case_id,
        events=tuple(events),
        ledger_sha256=canonical_execution_sha256(ledger_identity_payload),
    )


def context_payload(case: DevelopmentCase) -> dict[str, object]:
    """Project one case into the exact evaluator-visible context."""

    return {
        "schema_version": "diagnosis-development-context/v1",
        "case_id": case.case_id,
        "source": case.source,
        "evidence": [item.model_dump(mode="json") for item in case.evidence],
        "protected_outcome_visible": False,
        "evaluator_metadata_visible": False,
    }


def evidence_sha256(case: DevelopmentCase) -> str:
    """Hash the ordered visible evidence projection for request binding."""

    return canonical_execution_sha256([item.model_dump(mode="json") for item in case.evidence])
