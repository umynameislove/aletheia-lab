"""Source-bound windows over canonical mapped numeric observations."""

from __future__ import annotations

import json
from decimal import Decimal, localcontext
from fractions import Fraction

from aletheia_lab.product.boundary import ProductError
from aletheia_lab.product.projection import build_product_graph
from aletheia_lab.product.questions import checked_product_question
from aletheia_lab.product.view import (
    AnyProductView,
    ProductClaim,
    ProductEvidence,
    ProductReproductionRef,
    parse_product_view,
)
from aletheia_lab.project.identity import canonical_project_sha256
from aletheia_lab.project.mapping import MetricObservationV2

from .upload_analysis import (
    WINDOW_AGGREGATION_POLICY,
    WINDOW_OBSERVATION_MODEL,
    _initial_view,
    analysis_identity,
)
from .upload_snapshot import AnyUploadSnapshot, parse_upload_snapshot
from .worker_limits import MAX_OUTPUT_BYTES

WINDOW_ROWS = 10
MAX_WINDOW_OBSERVATIONS = 100000
MAX_WINDOW_CLAIMS = 10000
WINDOW_POLICY = WINDOW_AGGREGATION_POLICY


def _decimal_text(value: Decimal) -> str:
    if value == 0:
        return "0"
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def analyze_metric_windows(snapshot: AnyUploadSnapshot, question: str) -> AnyProductView:
    snapshot = parse_upload_snapshot(snapshot.model_dump(mode="python"))
    return _analyze_validated_metric_windows(snapshot, question)


def _analyze_validated_metric_windows(snapshot: AnyUploadSnapshot, question: str) -> AnyProductView:
    """Project a snapshot already strictly validated by the current operation."""
    question = checked_product_question(question)
    observations = snapshot.mapping_result.metric_observations
    if not 0 < len(observations) <= MAX_WINDOW_OBSERVATIONS:
        raise ProductError("analysis_too_large", "The window observation limit was exceeded.")
    groups = {}
    for row in observations:
        dimension = (
            (row.class_label, row.unit, row.unit_origin)
            if isinstance(row, MetricObservationV2)
            else (None, None, "unknown")
        )
        scope = (row.source_mapping_id, row.project_item_id, row.run_id, row.metric_name, *dimension)
        groups.setdefault(scope, []).append(row)
    count = sum((len(rows) + WINDOW_ROWS - 1) // WINDOW_ROWS for rows in groups.values())
    if count > MAX_WINDOW_CLAIMS:
        raise ProductError("analysis_too_large", "The window claim limit was exceeded.")

    identity = analysis_identity(snapshot.workspace_id, snapshot.snapshot_id, question, WINDOW_OBSERVATION_MODEL)
    result_id = "p6-result-" + canonical_project_sha256(identity)
    turn_id = "p6-turn-" + canonical_project_sha256(identity)
    base = _initial_view(snapshot, question).model_dump(mode="python")
    runtime = {"provider": "local_evidence", "model": WINDOW_OBSERVATION_MODEL, "external_call": False}
    missing = base["claims"][0]["missing_evidence"]
    claims, evidence = [], []
    for scope in sorted(groups, key=lambda key: json.dumps(key, ensure_ascii=True)):
        rows = sorted(groups[scope], key=lambda row: (row.source_record_index, row.observation_id))
        for offset in range(0, len(rows), WINDOW_ROWS):
            window = rows[offset:offset + WINDOW_ROWS]
            canonical = [row.model_dump(mode="json") for row in window]
            operands_sha256 = canonical_project_sha256(canonical)
            binding = {
                "policy": WINDOW_POLICY, "snapshot_record": snapshot.record_sha256,
                "scope": scope, "start": offset, "count": len(window), "operands": operands_sha256,
            }
            evidence_id = "p6-evidence-" + canonical_project_sha256(binding)
            # Decimal is taken from the snapshot's canonical finite numeric
            # representation; original lexical CSV precision is not inferred.
            values = [Decimal(str(row.metric_value)) for row in window]
            with localcontext() as context:
                context.prec = 1100
                total = sum(values, Decimal(0))
                denominator = (Fraction(total) / len(values)).denominator
                for factor in (2, 5):
                    while denominator % factor == 0:
                        denominator //= factor
                exact_mean = denominator == 1
                mean_text = (
                    f"Exact decimal mean {_decimal_text(total / len(values))}."
                    if exact_mean else f"Exact mean is {_decimal_text(total)} / {len(values)}."
                )
            indexes = ",".join(str(row.source_record_index) for row in window)
            source_mapping, item, run, metric, label, unit, origin = scope
            conclusion = (
                f"Run {run}, metric {metric}, class {label!r}, unit {unit!r} ({origin}). "
                f"{len(values)} validated observations, minimum {_decimal_text(min(values))}, "
                f"maximum {_decimal_text(max(values))}. {mean_text}"
            )
            text = (
                conclusion + f" Source item {item}, mapping {source_mapping}, records [{indexes}]. "
                f"Canonical operand SHA-256 {operands_sha256}. Policy {WINDOW_POLICY}."
            )
            evidence.append(ProductEvidence(
                id=evidence_id, kind="metric_window_observation",
                title=f"Run {run}, metric {metric}, window {offset // WINDOW_ROWS + 1}",
                text=text, reproduction_ref=ProductReproductionRef(record_id=snapshot.snapshot_id, record_kind="snapshot"),
                source_sha256=snapshot.source_sha256, visibility="diagnosis", redacted=False,
            ))
            claims.append(ProductClaim(
                id="p6-claim-" + canonical_project_sha256({"turn": turn_id, "window": binding}),
                turn_id=turn_id, text=conclusion, claim_type="evidence_statement", epistemic_state="observed",
                support="fully_supported", citation_ids=(evidence_id,), counterevidence_ids=(),
                missing_evidence=missing, max_strength="observation",
            ))
    ids = tuple(item.id for item in evidence)
    summary = f"Observed {len(observations)} validated metric values in {len(claims)} disjoint source-bound windows."
    base.update(
        runtime=runtime,
        result={**base["result"], "id": result_id, "summary": summary, "allowed_wording": summary,
                "denominators": {**base["result"]["denominators"], "claims": len(claims)},
                "caveat": "Window statistics describe canonical mapped observations. Source spelling and a verified cause are not inferred."},
        conversation={"id": "p6-conversation-" + canonical_project_sha256(identity), "turns": ({
            **base["conversation"]["turns"][0], "id": turn_id, "result_id": result_id,
            "runtime": runtime, "answer": summary, "visible_evidence_ids": ids,
        },)},
        claims=tuple(claims), evidence=tuple(evidence),
        graph=build_product_graph(
            snapshot_id=snapshot.snapshot_id, evidence_ids=ids,
            claim_citations={claim.id: claim.citation_ids for claim in claims},
            disposition_id=result_id, result_scope_id=turn_id, observed_evidence_ids=ids,
        ),
    )
    view = parse_product_view(base)
    if len(view.model_dump_json().encode("utf-8")) > MAX_OUTPUT_BYTES:
        raise ProductError("analysis_too_large", "The complete window result exceeds its byte budget.")
    return view
