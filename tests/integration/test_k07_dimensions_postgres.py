"""Class/unit v2 survives real durable confirm, analysis and follow-up publication."""
from __future__ import annotations

import io
import json
import os
import sys
import zipfile
from pathlib import Path

import pytest
from pydantic import SecretStr
from test_k05_native_results_postgres import _case, _child, _run
from test_k06_canonical_snapshot_postgres import _setup

from aletheia_lab.hosted.managed_worker import _supervisor_type
from aletheia_lab.hosted.native_upload_records import (
    UploadResultRecordV2,
    build_initial_upload_result,
)
from aletheia_lab.hosted.project_operations import CanonicalProjectOperations
from aletheia_lab.hosted.source_bundle import (
    MAXIMUM_ENTRIES,
    MAXIMUM_EXPANDED_BYTES,
    MAXIMUM_UPLOAD_BYTES,
)
from aletheia_lab.hosted.upload_analysis import analyze_initial_upload
from aletheia_lab.hosted.upload_job_worker import run_one_upload_job
from aletheia_lab.hosted.upload_project import UploadProject
from aletheia_lab.hosted.upload_snapshot import UploadSnapshotV2
from aletheia_lab.product.view import parse_product_view_json

pytestmark = [pytest.mark.integration, pytest.mark.skipif(
    not os.environ.get("K05_TEST_DATABASE_URL"), reason="Explicit K05 test database URL required")]


def test_v2_real_queue_catalog_and_follow_up_keep_class_unit_and_legacy_schema(tmp_path):
    async def scenario():
        async with _case(tmp_path) as case:
            fixtures = Path(__file__).resolve().parents[2] / "fixtures/metric-dimensions-hosted-v2"
            data = (fixtures / "source.zip").read_bytes()
            selected = json.loads((fixtures / "confirm.json").read_text(encoding="utf-8"))["mapping"]
            receipt = await case.sources.put(credential_digest=case.a, workspace_id="a",
                request_key=SecretStr("k07-dimensions-v2-real-source"), payload=data)
            private = tmp_path / "dimension-materialization"
            private.mkdir()
            worker = UploadProject(private, maximum_upload_bytes=MAXIMUM_UPLOAD_BYTES,
                maximum_expanded_bytes=MAXIMUM_EXPANDED_BYTES, maximum_entries=MAXIMUM_ENTRIES)
            preview = worker.preview(data, captured_at="2026-11-11T00:00:00Z")
            # Use current candidate bindings; fixture source and selected dimensions are fixed.
            for field, candidates in (("target", "targets"), ("metric_sources", "metric_sources")):
                target = selected[field] if field == "target" else selected[field][0]
                current = preview["mapping_candidates"][candidates][0]
                target.update(mapping_id=current["mapping_id"], project_item_id=current["project_item_id"])
            snapshot = worker.confirm(data, workspace_id="a", source_id=receipt.source_id,
                mapping=selected, captured_at="2026-11-11T00:00:00Z",
                expected_source_state_sha256=preview["source_state_sha256"])
            assert isinstance(snapshot, UploadSnapshotV2)
            jobs = await _setup(case)
            queued = await jobs.enqueue(credential_digest=case.a, workspace_id="a", operation="confirm",
                request_key=SecretStr("k07-dimensions-v2-real-confirm"),
                request={"source_id": receipt.source_id, "mapping": selected})
            lease = SecretStr("4" * 64)
            claim = await jobs.claim(lease_digest=lease, lease_seconds=120, operations=("confirm",))
            assert claim is not None and claim.job_id == queued.job_id
            assert await jobs.complete_snapshot(claim=claim, lease_digest=lease, snapshot=snapshot)
            assert await jobs._access.snapshot(credential_digest=case.a, workspace_id="a",
                                               snapshot_id=snapshot.snapshot_id) == snapshot
            question = "Which source dimensions are explicitly known?"
            analysis = await jobs.enqueue_analysis(credential_digest=case.a, workspace_id="a",
                project_id=snapshot.mapping.project_id, snapshot_id=snapshot.snapshot_id,
                question=question, request_key=SecretStr("k07-dimensions-v2-real-analyze"))
            claim = await jobs.claim(lease_digest=lease, lease_seconds=120, operations=("analyze",))
            assert claim is not None and claim.job_id == analysis.job_id
            view = analyze_initial_upload(snapshot, question)
            initial = build_initial_upload_result(snapshot, question, view)
            assert await jobs.complete_result(claim=claim, lease_digest=lease, record=initial)
            output = await jobs.output(credential_digest=case.a, workspace_id="a", job_id=analysis.job_id)
            assert parse_product_view_json(json.dumps(output["result"])) == view
            kwargs = dict(credential_digest=case.a, workspace_id="a", project_id=snapshot.mapping.project_id)
            assert await case.catalog.read_record(**kwargs, record_id=snapshot.snapshot_id) == snapshot
            assert await case.catalog.read_record(**kwargs, record_id=initial.record_id) == initial
            child = _child(initial, "What evidence remains missing?")
            assert isinstance(child, UploadResultRecordV2)
            prepared = await case.results.prepare_result("a", parent=initial, record=child)
            async with case.pool.connection() as conn:
                from aletheia_lab.hosted.authorization import PostgresAuthorization
                authorization = await PostgresAuthorization().authorize(conn, case.a.get_secret_value(), "a", "analyze")
                published = await case.results.enlist_result(conn, authorization=authorization,
                    prepared=prepared, expected_revision=2)
                assert published.revision == 3
                assert await (await conn.execute("SELECT count(*) FROM k05_records WHERE record_type='upload_snapshot'" )).fetchone() == (1,)
                assert await (await conn.execute("SELECT count(*) FROM k05_records WHERE record_type='upload_result'" )).fetchone() == (2,)
            assert await case.catalog.read_record(**kwargs, record_id=child.record_id) == child
            assert child.view.snapshot.metric_definitions[0].unit == "%"
            assert {row.class_label for row in snapshot.mapping_result.metric_observations} == {"lớp A", "lớp B"}
            private = tmp_path / "refresh-worker"
            private.mkdir()
            isolated = _supervisor_type()(python=Path(sys.executable).absolute(),
                source_root=Path(__file__).resolve().parents[2] / "src", private_root=private,
                slots=1, timeout_seconds=30)
            operations = CanonicalProjectOperations(case.sources, isolated, jobs, private_root=private)
            try:
                # A source-unit mismatch must fail before any snapshot effect.
                for name, old, new, expected_status in (("mismatch", b",%", b",ms", "failed"),
                                                       ("valid", b",0.5,", b",0.55,", "succeeded")):
                    changed = io.BytesIO()
                    with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(changed, "w") as target:
                        for info in source.infolist():
                            content = source.read(info.filename)
                            target.writestr(info, content.replace(old, new) if info.filename == "metrics.csv" else content)
                    fresh = await case.sources.put(credential_digest=case.a, workspace_id="a",
                        request_key=SecretStr("k07-v2-refresh-source-" + name), payload=changed.getvalue())
                    refresh = await jobs.enqueue_refresh(credential_digest=case.a, workspace_id="a",
                        project_id=snapshot.mapping.project_id, expected_revision=3,
                        request_key=SecretStr("k07-v2-refresh-job-" + name), source_id=fresh.source_id)
                    assert await run_one_upload_job(jobs, operations, lease_seconds=120)
                    status = await jobs.status(credential_digest=case.a, workspace_id="a", job_id=refresh.job_id)
                    assert status.status == expected_status
                    current = await case.catalog.current(**kwargs, pointer_name="snapshot")
                    assert isinstance(current, UploadSnapshotV2)
                    if name == "mismatch":
                        assert current == snapshot
                    else:
                        assert current.snapshot_id != snapshot.snapshot_id
                        assert {row.class_label for row in current.mapping_result.metric_observations} == {"lớp A", "lớp B"}
                        assert {(row.unit, row.unit_origin) for row in current.mapping_result.metric_observations} == {("%", "source")}
                    assert await case.catalog.read_record(**kwargs, record_id=snapshot.snapshot_id) == snapshot
                    assert await case.catalog.read_record(**kwargs, record_id=initial.record_id) == initial
            finally:
                await isolated.aclose()

    _run(scenario())
