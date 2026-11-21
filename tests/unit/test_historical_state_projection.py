"""Canonical state evidence preserves the scoped verdict and rejects worker drift."""

from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path

import pytest
from pydantic import ValidationError
from pypdf import PdfReader
from test_k07_dimensions_v2 import snapshot_v2

from aletheia_lab.hosted.historical_state_evidence import (
    HistoricalState,
    HistoricalStateContract,
    HistoricalStateInput,
    HistoricalStateReceipt,
)
from aletheia_lab.hosted.native_upload_records import (
    UploadResultRecord,
    UploadResultRecordV2,
    build_initial_upload_result,
    parse_upload_result_record,
)
from aletheia_lab.hosted.upload_analysis import HISTORICAL_STATE_MODEL, analyze_upload
from aletheia_lab.hosted.upload_snapshot import (
    HistoricalStateUploadSnapshot,
    build_upload_snapshot,
    parse_upload_snapshot,
)
from aletheia_lab.product.reports import render_product_report
from aletheia_lab.product.view import (
    ProductHistoricalStateEvidenceItem,
    ProductView,
    ProductViewV2,
    parse_product_view,
)

_QUESTION = "Did the operation use the state permitted by the original contract?"
_STATE_A = HistoricalState(namespace="left", generation="A", state_sha256="a" * 64)
_STATE_B = HistoricalState(namespace="left", generation="B", state_sha256="b" * 64)
_CONSUMER = "scoped-two-slice-reader"
_LEGACY_SNAPSHOT = (
    Path(__file__).resolve().parents[2]
    / "fixtures/k04/producer-20261110-native/native-snapshot-record.json"
)


def _historical_state_snapshot(snapshot, actual, permitted, *, alias="B", consumer=_CONSUMER):
    evidence = HistoricalStateInput(
        receipt=HistoricalStateReceipt(
            operation_id="historical/operation", request_id="original-request", consumer_id=consumer,
            execution_mode="replay", producer_version="source/v1", source_receipt_sha256="c" * 64,
            observation=actual, output=(4.0, 3.0), graph_sha256="d" * 64, current_alias=alias,
        ),
        contract=HistoricalStateContract(consumer_id=consumer, allowed_state=permitted) if permitted else None,
    )
    return build_upload_snapshot(
        workspace_id=snapshot.workspace_id, source_id=snapshot.source_id, source_sha256=snapshot.source_sha256,
        mapping=snapshot.mapping, mapping_result=snapshot.mapping_result, captured_at=snapshot.captured_at,
        historical_state=evidence,
    )


@pytest.mark.parametrize(("actual", "permitted", "consumer", "verdict"), [
    (_STATE_A, _STATE_B, _CONSUMER, "denied"),
    (_STATE_B, _STATE_B, _CONSUMER, "allowed"),
    (None, _STATE_B, _CONSUMER, "unknown"),
    (_STATE_B, None, _CONSUMER, "underdefined"),
    (_STATE_B, _STATE_B, "unsupported-consumer", "unsupported"),
])
def test_canonical_projection_keeps_decision_and_provenance(tmp_path, actual, permitted, consumer, verdict):
    _, base, _, _ = snapshot_v2(tmp_path)
    snapshot = _historical_state_snapshot(base, actual, permitted, consumer=consumer)
    view = analyze_upload(snapshot, _QUESTION, HISTORICAL_STATE_MODEL)
    record = build_initial_upload_result(snapshot, _QUESTION, view)
    restored = parse_upload_result_record(record.model_dump_json()).view
    evidence, = restored.evidence
    assert isinstance(evidence, ProductHistoricalStateEvidenceItem)
    assert evidence.historical_state.verdict == verdict
    assert evidence.historical_state.output == (4.0, 3.0)
    assert evidence.historical_state.operation_id == "historical/operation"
    assert evidence.historical_state.source_receipt_sha256 == "c" * 64
    assert evidence.historical_state.execution_mode == "replay"
    assert evidence.reproduction_ref.record_id == snapshot.snapshot_id
    assert restored.claims[0].citation_ids == (evidence.id,)
    assert restored.result.status == "complete"
    assert restored.conversation.turns[0].abstained == (verdict not in {"allowed", "denied"})


def test_legacy_mapping_keeps_metric_format_and_uses_typed_historical_format():
    record = json.loads(_LEGACY_SNAPSHOT.read_bytes())
    base = parse_upload_snapshot(json.dumps(record["data"]))
    snapshot = _historical_state_snapshot(base, _STATE_A, _STATE_B)
    assert isinstance(snapshot, HistoricalStateUploadSnapshot)
    assert snapshot.mapping == base.mapping and snapshot.mapping_result == base.mapping_result
    assert parse_upload_snapshot(snapshot.model_dump_json()) == snapshot

    historical = analyze_upload(snapshot, _QUESTION, HISTORICAL_STATE_MODEL)
    assert isinstance(historical, ProductViewV2)
    assert all(definition.unit is None for definition in historical.snapshot.metric_definitions)
    assert isinstance(build_initial_upload_result(snapshot, _QUESTION, historical), UploadResultRecordV2)

    metric = analyze_upload(snapshot, _QUESTION, "upload-snapshot-observation/v1")
    assert isinstance(metric, ProductView)
    assert isinstance(build_initial_upload_result(snapshot, _QUESTION, metric), UploadResultRecord)
    malformed_view = metric.model_dump(mode="python")
    malformed_view["evidence"][0]["historical_state"] = historical.evidence[0].model_dump()["historical_state"]
    with pytest.raises(ValidationError, match="extra_forbidden"):
        ProductView.model_validate(malformed_view)


def test_alias_leaves_decision_unchanged_and_worker_drift_is_rejected(tmp_path):
    _, base, _, _ = snapshot_v2(tmp_path)
    before = _historical_state_snapshot(base, _STATE_A, _STATE_B)
    after = _historical_state_snapshot(base, _STATE_A, _STATE_B, alias="current-name")
    first = analyze_upload(before, _QUESTION, HISTORICAL_STATE_MODEL)
    second = analyze_upload(after, _QUESTION, HISTORICAL_STATE_MODEL)
    first_evidence = first.evidence[0].historical_state
    second_evidence = second.evidence[0].historical_state
    assert first_evidence is not None and second_evidence is not None
    assert first_evidence.verdict == second_evidence.verdict == "denied"
    assert first_evidence.observed_state == second_evidence.observed_state
    assert first.result.id != second.result.id

    drifted = first.model_dump(mode="python")
    drifted["evidence"][0]["historical_state"]["verdict"] = "allowed"
    with pytest.raises(ValueError, match="differs from the verified snapshot projection"):
        build_initial_upload_result(before, _QUESTION, parse_product_view(drifted))

    legacy = analyze_upload(base, _QUESTION, "upload-snapshot-observation/v1")
    assert "historical_state" not in legacy.model_dump(mode="json")["evidence"][0]


@pytest.mark.parametrize(("actual", "permitted", "verdict", "missing"), [
    (_STATE_A, _STATE_B, "denied", ()),
    (None, _STATE_B, "unknown", ("Observed state: null",)),
    (_STATE_B, None, "underdefined", ("Allowed state: null", "Contract SHA-256: null")),
])
def test_historical_state_reports_preserve_typed_evidence_and_original_scope(tmp_path, actual, permitted, verdict, missing):
    _, base, _, _ = snapshot_v2(tmp_path)
    snapshot = _historical_state_snapshot(base, actual, permitted)
    view = analyze_upload(snapshot, _QUESTION, HISTORICAL_STATE_MODEL)
    saved = parse_upload_result_record(build_initial_upload_result(snapshot, _QUESTION, view).model_dump_json()).view
    evidence, = saved.evidence
    data = evidence.historical_state
    assert data is not None
    assert json.loads(render_product_report(saved, "json")) == saved.model_dump(mode="json")
    markdown = render_product_report(saved, "markdown").decode("utf-8").replace("`", "")
    pdf_bytes = render_product_report(saved, "pdf")
    reader = PdfReader(BytesIO(pdf_bytes), strict=True)
    pdf = "\n".join(page.extract_text(extraction_mode="layout") for page in reader.pages)
    expected = (
        f"Contract verdict: {verdict}", data.reason, saved.result.caveat,
        "Original operation ID: historical/operation", "Original request ID: original-request",
        "Consumer ID: scoped-two-slice-reader", "Execution mode: replay",
        "Producer version: source/v1", "Reported output: [4.0, 3.0]", "Current alias (context): B",
        "Source receipt SHA-256: " + "c" * 64, "Graph SHA-256: " + "d" * 64,
        "Receipt SHA-256: " + data.receipt_sha256,
        "Source SHA-256: " + base.source_sha256,
        saved.project.id, saved.snapshot.id, saved.result.id, evidence.id, *missing,
    )
    for rendered in (markdown, pdf):
        semantic_text = " ".join(rendered.split())
        for token in expected:
            assert token in semantic_text
        for state in (actual, permitted):
            if state is not None:
                assert f"Generation: {state.generation}" in semantic_text
                assert f"State SHA-256: {state.state_sha256}" in semantic_text
        if data.contract_sha256 is not None:
            assert f"Contract SHA-256: {data.contract_sha256}" in semantic_text
