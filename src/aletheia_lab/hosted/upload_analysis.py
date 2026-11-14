"""Initial upload observation in the existing P6 view, without causal claims."""
from __future__ import annotations

import json

from aletheia_lab.hosted.content_hashing import content_hasher
from aletheia_lab.product.boundary import ProductError
from aletheia_lab.product.projection import build_product_graph
from aletheia_lab.product.questions import checked_product_question
from aletheia_lab.product.view import (
    COUNTERFACTUAL_NOT_AVAILABLE,
    AnyProductView,
    ProductClaim,
    ProductConversation,
    ProductDenominators,
    ProductEvidence,
    ProductProject,
    ProductReproductionRef,
    ProductResult,
    ProductRuntime,
    ProductSnapshot,
    ProductSnapshotV2,
    ProductTurn,
    ProductView,
    ProductViewV2,
    parse_product_view,
)
from aletheia_lab.project.identity import canonical_project_sha256
from aletheia_lab.project.mapping import MetricObservation, MetricObservationV2

from .upload_snapshot import AnyUploadSnapshot, UploadSnapshotV2, parse_upload_snapshot

INITIAL_OBSERVATION_MODEL = 'upload-snapshot-observation/v1'
METRIC_OBSERVATION_MODEL = 'upload-snapshot-metric-observation/v2'
MAX_OBSERVATION_SERIES = 64


def analysis_identity(workspace: str, snapshot_id: str, question: str, model: str) -> dict[str, str]:
    identity = {'workspace': workspace, 'snapshot': snapshot_id, 'question': question}
    if model == METRIC_OBSERVATION_MODEL:
        identity['analysis_model'] = model
    elif model != INITIAL_OBSERVATION_MODEL:
        raise ValueError('Unsupported native observation version.')
    return identity


def analyze_initial_upload(snapshot: AnyUploadSnapshot, question: str) -> AnyProductView:
    """Project one verified snapshot; no prior snapshot or cause is inferred."""
    snapshot = parse_upload_snapshot(snapshot.model_dump(mode="python"))
    question = checked_product_question(question)
    identity = {"workspace": snapshot.workspace_id, "snapshot": snapshot.snapshot_id, "question": question}
    def scoped(prefix: str, value: object) -> str:
        return prefix + "-" + canonical_project_sha256(value)
    result_id = scoped("p6-result", identity)
    turn_id = scoped("p6-turn", identity)
    claim_id = scoped("p6-claim", identity)
    evidence_id = scoped("p6-evidence", {"snapshot": snapshot.snapshot_id, "record": snapshot.record_sha256})
    text = "The authorized upload produced an immutable mapped project snapshot."
    missing = ("No prior snapshot comparison is available for this initial upload.",
               "Independent causal verification is not available.", COUNTERFACTUAL_NOT_AVAILABLE)
    runtime = ProductRuntime(provider="local_evidence", model="upload-snapshot-observation/v1", external_call=False)
    evidence = ProductEvidence(id=evidence_id, kind="snapshot", title="Uploaded project snapshot",
        text=text, reproduction_ref=ProductReproductionRef(record_id=snapshot.snapshot_id, record_kind="snapshot"),
        source_sha256=snapshot.source_sha256, visibility="diagnosis", redacted=False)
    claim = ProductClaim(id=claim_id, turn_id=turn_id, text=text, claim_type="evidence_statement",
        epistemic_state="observed", support="fully_supported", citation_ids=(evidence_id,), counterevidence_ids=(),
        missing_evidence=missing, max_strength="observation")
    model = ProductViewV2 if isinstance(snapshot, UploadSnapshotV2) else ProductView
    snapshot_data = dict(id=snapshot.snapshot_id, baseline_id=snapshot.snapshot_id,
        sha256=snapshot.snapshot_id.removeprefix('upload-snapshot-'),
        metric_definitions=tuple(item.model_dump(mode='python') for item in snapshot.mapping.metric_definitions),
        metric_changes=())
    projected_snapshot = (ProductSnapshotV2.model_validate(snapshot_data) if isinstance(snapshot, UploadSnapshotV2)
                          else ProductSnapshot.model_validate(snapshot_data))
    return parse_product_view(dict(schema_version=model.model_fields['schema_version'].default,
        demo_only=False, mode="project_audit", visibility="diagnosis",
        project=ProductProject(id=snapshot.mapping.project_id, display_name="Uploaded project"),
        snapshot=projected_snapshot,
        runtime=runtime, result=ProductResult(id=result_id, status="complete", disposition="abstain",
            causal_status="unverified", summary=text,
            denominators=ProductDenominators(independent_families=None, contexts=1, outputs=1, claims=1),
            allowed_wording=text, forbidden_wording="The visible project artifacts establish a verified cause.",
            caveat="Initial upload observation; no snapshot comparison or verified cause."),
        conversation=ProductConversation(id=scoped("p6-conversation", identity), turns=(ProductTurn(
            id=turn_id, snapshot_id=snapshot.snapshot_id, result_id=result_id, runtime=runtime, question=question,
            answer=text + " Its cause remains unverified.", abstained=True, visible_evidence_ids=(evidence_id,),
            missing_evidence=missing),)), claims=(claim,), evidence=(evidence,),
        graph=build_product_graph(snapshot_id=snapshot.snapshot_id, evidence_ids=(evidence_id,),
            claim_citations={claim_id:(evidence_id,)}, disposition_id=result_id, result_scope_id=turn_id,
            observed_evidence_ids=(evidence_id,))))


def analyze_upload_metrics(snapshot: AnyUploadSnapshot, question: str) -> AnyProductView:
    """Observe every validated row through explicit series counts/ranges, never causes.

    The immutable snapshot retains every operand. Grouping is stated in the
    result rather than presenting a truncated row sample as the complete census.
    """
    snapshot = parse_upload_snapshot(snapshot.model_dump(mode='python'))
    question = checked_product_question(question)
    identity = analysis_identity(snapshot.workspace_id, snapshot.snapshot_id, question, METRIC_OBSERVATION_MODEL)

    def scoped(prefix: str, value: object) -> str:
        return prefix + '-' + canonical_project_sha256(value)

    groups: dict[tuple[str, str, str | None, str | None, str], list[MetricObservation | MetricObservationV2]] = {}
    for row in snapshot.mapping_result.metric_observations:
        key: tuple[str, str, str | None, str | None, str] = (row.run_id, row.metric_name, row.class_label, row.unit, row.unit_origin) if isinstance(
            row, MetricObservationV2) else (row.run_id, row.metric_name, None, None, 'unknown')
        groups.setdefault(key, []).append(row)
        if len(groups) > MAX_OBSERVATION_SERIES:
            raise ProductError('analysis_too_large', 'This observation exceeds the supported series limit; no rows were omitted.')
    result_id, turn_id = scoped('p6-result', identity), scoped('p6-turn', identity)
    runtime = ProductRuntime(provider='local_evidence', model=METRIC_OBSERVATION_MODEL, external_call=False)
    missing = ('No cross-generation metric comparison is asserted by this snapshot observation.',
               'Independent causal verification is not available.', COUNTERFACTUAL_NOT_AVAILABLE)
    evidence, claims = [], []
    for key in sorted(groups, key=lambda value: json.dumps(value, ensure_ascii=True, separators=(',', ':'))):
        run, metric, label, unit, origin = key
        rows = groups[key]
        operands = content_hasher()
        for row in sorted(rows, key=lambda value: value.observation_id):
            operands.update(row.observation_id.encode('ascii') + b'\n')
        eid = scoped('p6-evidence', {'snapshot_record': snapshot.record_sha256,
            'series': key, 'operands_sha256': operands.hexdigest()})
        class_text = 'not mapped' if label is None else repr(label)
        unit_text = 'unknown' if unit is None else repr(unit)
        title = f'Run {run}, metric {metric}'
        scope = f'Run {run}, metric {metric}, class {class_text}, unit {unit_text} ({origin})'
        low = min(rows, key=lambda value: value.metric_value).metric_value
        high = max(rows, key=lambda value: value.metric_value).metric_value
        text = f'{scope}: {len(rows)} validated observations; minimum {low!r}; maximum {high!r}.'
        stepped = [row for row in rows if row.step is not None]
        if stepped:
            first = min(stepped, key=lambda value: (value.step or 0, value.observation_id))
            last = max(stepped, key=lambda value: (value.step or 0, value.observation_id))
            text += f' Lowest mapped step {first.step}: {first.metric_value!r}; highest mapped step {last.step}: {last.metric_value!r}.'
        text += f' Unstepped observations: {len(rows) - len(stepped)}.'
        role = 'configured baseline' if run == snapshot.mapping.baseline_run_id else 'mapped run'
        text += f' This is the {role}; no cause or cross-generation delta is inferred.'
        evidence.append(ProductEvidence(id=eid, kind='snapshot', title=title, text=text,
            reproduction_ref=ProductReproductionRef(record_id=snapshot.snapshot_id, record_kind='snapshot'),
            source_sha256=snapshot.source_sha256, visibility='diagnosis', redacted=False))
        claims.append(ProductClaim(id=scoped('p6-claim', {'turn': turn_id, 'evidence': eid, 'text': text}),
            turn_id=turn_id, text=text, claim_type='evidence_statement', epistemic_state='observed',
            support='fully_supported', citation_ids=(eid,), counterevidence_ids=(),
            missing_evidence=missing, max_strength='observation'))
    total = len(snapshot.mapping_result.metric_observations)
    summary = f'Observed {total} validated metric values in {len(groups)} mapped series.'
    model = ProductViewV2 if isinstance(snapshot, UploadSnapshotV2) else ProductView
    snapshot_data = dict(id=snapshot.snapshot_id, baseline_id=snapshot.snapshot_id,
        sha256=snapshot.snapshot_id.removeprefix('upload-snapshot-'),
        metric_definitions=tuple(item.model_dump(mode='python') for item in snapshot.mapping.metric_definitions),
        metric_changes=())
    projected = (ProductSnapshotV2.model_validate(snapshot_data) if isinstance(snapshot, UploadSnapshotV2)
                 else ProductSnapshot.model_validate(snapshot_data))
    ids = tuple(item.id for item in evidence)
    return parse_product_view(dict(schema_version=model.model_fields['schema_version'].default,
        demo_only=False, mode='project_audit', visibility='diagnosis',
        project=ProductProject(id=snapshot.mapping.project_id, display_name='Uploaded project'), snapshot=projected,
        runtime=runtime, result=ProductResult(id=result_id, status='complete', disposition='abstain',
            causal_status='unverified', summary=summary,
            denominators=ProductDenominators(independent_families=None, contexts=1, outputs=1, claims=len(claims)),
            allowed_wording=summary, forbidden_wording='These metric observations establish a verified cause.',
            caveat='All validated rows are included in per-series counts and ranges; raw operands remain in the pinned snapshot. No causal diagnosis or missing value imputation.'),
        conversation=ProductConversation(id=scoped('p6-conversation', identity), turns=(ProductTurn(id=turn_id,
            snapshot_id=snapshot.snapshot_id, result_id=result_id, runtime=runtime, question=question,
            answer=summary + ' The cited series give exact counts, ranges and mapped step endpoints. Causal attribution remains unverified.',
            abstained=True, visible_evidence_ids=ids, missing_evidence=missing),)),
        claims=tuple(claims), evidence=tuple(evidence),
        graph=build_product_graph(snapshot_id=snapshot.snapshot_id, evidence_ids=ids,
            claim_citations={claim.id: claim.citation_ids for claim in claims}, disposition_id=result_id,
            result_scope_id=turn_id, observed_evidence_ids=ids)))
