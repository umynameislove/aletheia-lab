"""Initial upload analysis must preserve P6 scope and causal abstention."""
from datetime import UTC, datetime

from aletheia_lab.hosted.upload_analysis import analyze_initial_upload
from aletheia_lab.hosted.upload_snapshot import build_upload_snapshot
from aletheia_lab.product.view import ProductView
from aletheia_lab.project.mapping import validate_project_mapping


def test_initial_upload_produces_honest_p6_view(tmp_path):
    from test_project_mapping import _case, _configuration

    case = _case(tmp_path)
    mapping = _configuration(case)
    result = validate_project_mapping(case.bundle, case.artifacts, case.collection, mapping)
    snapshot = build_upload_snapshot(workspace_id="ws1", source_id="src1", source_sha256="a" * 64,
        mapping=mapping, mapping_result=result, captured_at=datetime.now(UTC))
    view = analyze_initial_upload(snapshot, "What is observed?")
    assert ProductView.model_validate_json(view.model_dump_json()) == view
    assert not view.demo_only and view.runtime.provider == "local_evidence"
    assert view.snapshot.id == snapshot.snapshot_id and not view.snapshot.metric_changes
    assert view.result.disposition == "abstain" and view.result.causal_status == "unverified"
    assert view.evidence[0].source_sha256 == snapshot.source_sha256
