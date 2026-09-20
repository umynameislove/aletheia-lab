"""Model-visible context projections for diagnosis main-study routes."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import cast

from aletheia_lab.diagnosis._main_runtime_contracts import (
    MainRuntimeError,
    MainSelectionOutput,
)
from aletheia_lab.evaluation.claim_evidence_semantics import (
    ModelVisibleEvidenceContext,
    ModelVisibleEvidenceItem,
    build_visible_evidence_item,
)
from aletheia_lab.evaluation.execution_contracts import (
    canonical_execution_json,
    canonical_execution_sha256,
)
from aletheia_lab.model_gateway import GatewayExecutionResult


def selection_schema(available_ids: Sequence[str]) -> dict[str, object]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "schema_version": {
                "type": "string",
                "const": "diagnosis-main-selection-output/v1",
            },
            "requested_evidence_ids": {
                "type": "array",
                "minItems": 1,
                "maxItems": min(32, len(available_ids)),
                "items": {"type": "string", "enum": list(available_ids)},
            },
        },
        "required": ["schema_version", "requested_evidence_ids"],
    }


def final_schema(response_contract: Mapping[str, object]) -> dict[str, object]:
    schema = response_contract.get("json_schema")
    if not isinstance(schema, dict):
        raise MainRuntimeError("main response contract contains no JSON schema")
    return cast(dict[str, object], schema)


def _context_from_items(
    items: Sequence[ModelVisibleEvidenceItem],
) -> ModelVisibleEvidenceContext:
    ordered = tuple(sorted(items, key=lambda item: item.evidence_id))
    if not ordered:
        raise MainRuntimeError("a provider turn cannot receive an empty context")
    payload = {
        "schema_version": "claim-visible-evidence-context/v1",
        "items": tuple(item.model_dump(mode="json") for item in ordered),
    }
    digest = canonical_execution_sha256(payload)
    return ModelVisibleEvidenceContext(
        context_id=f"ccctx-{digest}",
        items=ordered,
        context_sha256=digest,
    )


def catalog_context(original: ModelVisibleEvidenceContext) -> ModelVisibleEvidenceContext:
    return _context_from_items(
        tuple(
            build_visible_evidence_item(
                evidence_id=item.evidence_id,
                kind="artifact",
                title="Available observed record",
                content=canonical_execution_json({"kind": item.kind, "title": item.title}),
                source_content_sha256=item.content_sha256,
            )
            for item in original.items
        )
    )


def plain_context(
    original: ModelVisibleEvidenceContext,
    selected_ids: Sequence[str],
) -> ModelVisibleEvidenceContext:
    by_id = {item.evidence_id: item for item in original.items}
    lines = [
        f"Record {evidence_id}: {by_id[evidence_id].title}. {by_id[evidence_id].content}"
        for evidence_id in selected_ids
    ]
    source_sha = canonical_execution_sha256(
        tuple(by_id[evidence_id].content_sha256 for evidence_id in selected_ids)
    )
    return _context_from_items(
        (
            build_visible_evidence_item(
                evidence_id="ev-plain-rendering",
                kind="artifact",
                title="Plain rendering of observed facts",
                content=" | ".join(lines),
                source_content_sha256=source_sha,
            ),
        )
    )


def graph_context(
    original: ModelVisibleEvidenceContext,
    selected_ids: Sequence[str],
    *,
    full: bool,
    prior_turn_hashes: Sequence[str],
) -> ModelVisibleEvidenceContext:
    by_id = {item.evidence_id: item for item in original.items}
    selected = tuple(selected_ids)
    graph_items = [
        build_visible_evidence_item(
            evidence_id=evidence_id,
            kind=by_id[evidence_id].kind,
            title=by_id[evidence_id].title,
            content=canonical_execution_json(
                {
                    "node_id": evidence_id,
                    "observed_record": by_id[evidence_id].content,
                    "related_observed_record_ids": tuple(
                        candidate for candidate in selected if candidate != evidence_id
                    ),
                    "source_fingerprint": by_id[evidence_id].source_content_sha256,
                }
            ),
            source_content_sha256=by_id[evidence_id].content_sha256,
        )
        for evidence_id in selected
    ]
    if full:
        graph_items.append(
            build_visible_evidence_item(
                evidence_id="ev-runtime-provenance",
                kind="lineage",
                title="Registered retrieval and turn provenance",
                content=canonical_execution_json(
                    {
                        "selected_observed_record_ids": selected,
                        "turn_request_fingerprints": tuple(prior_turn_hashes),
                        "record_count": len(selected),
                    }
                ),
                source_content_sha256=canonical_execution_sha256(
                    {"selected": selected, "turns": tuple(prior_turn_hashes)}
                ),
            )
        )
    return _context_from_items(tuple(graph_items))


def all_original_ids(context: ModelVisibleEvidenceContext) -> tuple[str, ...]:
    return tuple(item.evidence_id for item in context.items)


def validate_selection(
    result: GatewayExecutionResult,
    *,
    available_ids: tuple[str, ...],
) -> MainSelectionOutput:
    if result.status != "parsed" or result.parsed_response is None:
        raise MainRuntimeError("selection turn did not produce a parsed response")
    selection = MainSelectionOutput.model_validate_json(json.dumps(result.parsed_response.payload))
    if not set(selection.requested_evidence_ids) <= set(available_ids):
        raise MainRuntimeError("selection turn requested unavailable evidence")
    return selection


__all__ = [
    "all_original_ids",
    "catalog_context",
    "final_schema",
    "graph_context",
    "plain_context",
    "selection_schema",
    "validate_selection",
]
