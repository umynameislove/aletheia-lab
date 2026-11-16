"""Real mapped S/M/L sources through the versioned product analyzer."""
from __future__ import annotations

import hashlib
import json
import time

import pytest

from aletheia_lab.hosted.capacity_seed_domain import confirm_mapping, source_payload
from aletheia_lab.hosted.capacity_seed_models import SeedPlan
from aletheia_lab.hosted.native_upload_records import (
    build_initial_upload_result,
    parse_upload_result_record,
)
from aletheia_lab.hosted.result_views import prepare_projection
from aletheia_lab.hosted.upload_analysis import WINDOW_OBSERVATION_MODEL, analyze_upload
from aletheia_lab.hosted.upload_project import UploadProject
from aletheia_lab.product.reports import render_product_report


@pytest.mark.parametrize("project,claims", [(0, 100), (1, 1000), (2, 10000)])
def test_real_mapped_window_geometry_canonical_records_projections_and_reports(tmp_path, project, claims):
    started = time.monotonic()
    plan = SeedPlan(namespace="k17_windowproof", analysis_model=WINDOW_OBSERVATION_MODEL,
                    source_rows={"S": 1000, "M": 10000, "L": 100000})
    importer = UploadProject(tmp_path, maximum_upload_bytes=16 * 1024**2,
                             maximum_expanded_bytes=64 * 1024**2, maximum_entries=16)
    source = source_payload(plan, project, 0)
    preview = importer.preview(source, captured_at="2026-11-15T00:00:00Z")
    snapshot = importer.confirm(source, workspace_id="ws1", source_id="src1",
        mapping=confirm_mapping(preview, 1, windowed=True), captured_at="2026-11-15T00:00:00Z")
    view = analyze_upload(snapshot, "What is observed?", WINDOW_OBSERVATION_MODEL)
    assert len(snapshot.mapping_result.metric_observations) == claims * 10
    assert len(view.claims) == len(view.evidence) == view.result.denominators.claims == claims
    assert len(view.graph.nodes) == 2 * claims + 2 and len(view.graph.edges) == 3 * claims
    assert view.result.causal_status == "unverified" and view.result.disposition == "abstain"
    assert "Exact decimal mean 0.50045." in view.claims[0].text
    assert all(e.source_sha256 == snapshot.source_sha256 and e.reproduction_ref.record_id == snapshot.snapshot_id
               for e in view.evidence)
    assert len({e.id for e in view.evidence}) == claims
    record = build_initial_upload_result(snapshot, "What is observed?", view)
    raw = record.model_dump_json().encode()
    assert parse_upload_result_record(raw) == record
    projection = prepare_projection(view, hashlib.sha256(raw).hexdigest())
    assert projection.manifest.claim_count == projection.manifest.evidence_count == claims
    formats = {fmt: render_product_report(view, fmt) for fmt in ("json", "markdown", "pdf")}
    assert json.loads(formats["json"])["result"]["id"] == view.result.id
    assert formats["pdf"].startswith(b"%PDF-")
    assert all(len(payload) <= 32 * 1024**2 for payload in formats.values())
    print(json.dumps({"size": plan.size(project), "observations": claims * 10, "claims": claims,
        "nodes": len(view.graph.nodes), "edges": len(view.graph.edges), "source_bytes": len(source),
        "snapshot_bytes": len(snapshot.model_dump_json().encode()), "record_bytes": len(raw),
        "reports": {fmt: {"bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}
                    for fmt, payload in formats.items()}, "seconds": time.monotonic() - started}))
