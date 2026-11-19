"""Seed a mixed-relation result and exercise stored reads, review and export."""
import asyncio
import hashlib
import json
import os
import time
from pathlib import Path

import test_k04_native_catalog_http_postgres as fixture
from pydantic import SecretStr
from test_k06_effect_races_postgres import _application
from test_k04_runtime_principal_postgres import _run
from test_k05_native_results_postgres import _case, _initial
from test_k13_retention_delivery_postgres import _protected_b, _ready

from aletheia_lab.hosted import native_upload_records
from aletheia_lab.hosted.project_access import PostgresProjectAccess
from aletheia_lab.hosted.project_mutations import PostgresProjectMutations
from aletheia_lab.hosted.retention import PostgresRetention
from aletheia_lab.hosted.review_repository import PostgresReviews
from aletheia_lab.hosted.upload_analysis import analyze_initial_upload
from aletheia_lab.hosted.workspace_management import WorkspaceManagement
from aletheia_lab.product.projection import build_product_graph
from aletheia_lab.product.reports import render_product_report
from aletheia_lab.product.view import ProductView

COUNTER = "j05-evidence-counter"
UNRELATED = "j05-evidence-unrelated"
VI_CLAIM = "Đối chiếu kết quả thử nghiệm và bằng chứng gốc."
VI_COUNTER = "Bằng chứng đối chiếu cho nhận định."
VI_UNRELATED = "Dữ liệu riêng của lượt trước."


def fixture_view(snapshot, question):
    base = analyze_initial_upload(snapshot, question).model_dump(mode="json")
    base["project"]["display_name"] = "Dữ liệu tiếng Việt J05"
    base["claims"][0]["text"] = VI_CLAIM
    original = base["evidence"][0]
    for ident, title in ((COUNTER, VI_COUNTER), (UNRELATED, VI_UNRELATED)):
        base["evidence"].append({**original, "id": ident, "title": title, "text": title})
    claim = base["claims"][0]
    claim["counterevidence_ids"] = [COUNTER]
    turn = base["conversation"]["turns"][0]
    turn["visible_evidence_ids"].extend((COUNTER, UNRELATED))
    base["graph"] = build_product_graph(snapshot_id=snapshot.snapshot_id,
        evidence_ids=tuple(item["id"] for item in base["evidence"]),
        claim_citations={claim["id"]: tuple(claim["citation_ids"])},
        disposition_id=base["result"]["id"], result_scope_id=turn["id"]).model_dump(mode="json")
    return ProductView.model_validate_json(json.dumps(base))


def test_j05_bound_evidence_two_reviewers_and_vietnamese_export(tmp_path, monkeypatch):
    async def run():
        async with _application(tmp_path) as (case, client):
            out = Path(os.environ["J05_EVIDENCE"])
            out.mkdir(parents=True, exist_ok=True)
            old_view, view = case.initial.view, case.child.view
            early, late = view.conversation.turns
            first, follow = view.claims
            assert early == old_view.conversation.turns[0] and first == old_view.claims[0]
            assert (first.turn_id, follow.turn_id) == (early.id, late.id)
            assert UNRELATED in early.visible_evidence_ids and UNRELATED not in late.visible_evidence_ids
            assert first.citation_ids == follow.citation_ids and follow.counterevidence_ids == (COUNTER,)
            assert set(late.visible_evidence_ids) == {*follow.citation_ids, COUNTER}
            base = f"/v1/workspaces/{case.workspace}/results/"
            path = base + case.child.record_id
            owner, foreign = case.headers["owner_a"], case.headers["owner_b"]

            requests = []

            async def request(method, endpoint, status, headers=owner, **kwargs):
                response = await client.request(method, endpoint, headers=headers, **kwargs)
                requests.append({"method": method, "path": endpoint, "status": response.status_code})
                assert response.status_code == status, (method, endpoint, response.status_code, response.text[:260])
                return response

            bound = {}
            for claim in (first, follow):
                for evidence_id, relation in ((claim.citation_ids[0], "citation"), (COUNTER, "counterevidence")):
                    result = (await request("GET", path + f"/evidence/{evidence_id}", 200,
                                            params={"claim_id": claim.id})).json()
                    assert result["relation"] == relation and result["claim_id"] == claim.id
                    assert result["evidence"]["source_sha256"] == case.source.sha256
                    assert result["evidence"]["reproduction_ref"]["record_id"] == case.snapshot.snapshot_id
                    bound[f"{claim.id}/{relation}"] = result
                await request("GET", path + f"/evidence/{UNRELATED}", 404, params={"claim_id": claim.id})
            loose = (await request("GET", path + f"/evidence/{UNRELATED}", 200)).json()
            assert loose["relation"] is None
            await request("GET", path + f"/evidence/{COUNTER}", 404, foreign, params={"claim_id": follow.id})
            parent = (await request("GET", base + case.initial.record_id, 200)).json()
            assert parent["result_id"] == case.initial.record_id and parent["view"] == old_view.model_dump(mode="json")

            manager = WorkspaceManagement(case.api, audit=True)
            grants = PostgresProjectAccess(case.api)

            async def member_revision(user):
                async with case.owner.connection() as conn:
                    return (await (await conn.execute("SELECT revision FROM j01_memberships WHERE "
                        "workspace_id=%s AND user_id=%s", (case.workspace, user))).fetchone())[0]

            digest = case.digests["owner_a"]
            await manager.mutate_member(credential_digest=digest, workspace_id=case.workspace,
                user_id="analyst", expected_revision=await member_revision("analyst"), role="reviewer")
            invite = await manager.invite(credential_digest=digest, workspace_id=case.workspace, user_id="owner_b",
                                          role="reviewer", expires_in_seconds=600)
            await manager.accept_invite(credential_digest=case.digests["owner_b"], token=SecretStr(invite["token"]))
            await grants.set_grant(credential_digest=digest, workspace_id=case.workspace,
                project_id=case.initial.project_id, user_id="owner_b", actions=["read", "review"], expected_revision=0)
            await manager.set_source_grant(credential_digest=digest, workspace_id=case.workspace,
                source_id=case.source.source_id, user_id="owner_b", actions=["read", "review"], expected_revision=0)

            async def save(note, who, rev, text, status):
                return await request("PUT", path + "/annotations/" + note, status,
                                     {**case.headers[who], "if-match": f'"{rev}"'},
                                     json={"body": text, "status": "comment"})

            first_note = (await save("reviewer-a", "analyst", 0, "Ghi chú của người rà soát A.", 200)).json()
            await save("reviewer-a", "owner_b", 1, "Thử thay ghi chú của A.", 404)
            second_note = (await save("reviewer-b", "owner_b", 0, "Ghi chú riêng của người rà soát B.", 200)).json()
            assert first_note["author_id"] == "analyst" and second_note["author_id"] == "owner_b"

            class RacingReviews(PostgresReviews):
                barrier = asyncio.Barrier(2)
                pids = set()

                async def _current(self, conn, *args):
                    self.pids.add((await (await conn.execute("SELECT pg_backend_pid()")).fetchone())[0])
                    await asyncio.wait_for(self.barrier.wait(), 10)
                    return await super()._current(conn, *args)

            original = case.app._reviews
            racing = RacingReviews(case.api, case.app._access.catalog, audit=True)
            case.app._reviews = racing
            note_path = path + "/annotations/reviewer-a"
            try:
                responses = await asyncio.wait_for(asyncio.gather(*(client.put(note_path,
                    headers={**case.headers["analyst"], "if-match": '"1"'},
                    json={"body": draft, "status": "comment"})
                    for draft in ("Bản chỉnh sửa số một.", "Bản chỉnh sửa số hai."))), 15)
            finally:
                case.app._reviews = original
            assert sorted(r.status_code for r in responses) == [200, 412] and len(racing.pids) == 2
            winner = next(r.json() for r in responses if r.status_code == 200)
            assert winner["revision"] == 2
            history = (await request("GET", note_path + "/history", 200, case.headers["owner_b"])).json()["items"]
            assert history == [first_note, winner]
            await manager.mutate_member(credential_digest=digest, workspace_id=case.workspace, user_id="owner_b",
                                        expected_revision=await member_revision("owner_b"), active=False)
            await save("reviewer-b", "owner_b", 1, "Bản nháp sau thu hồi quyền.", 404)
            second_history = (await request("GET", path + "/annotations/reviewer-b/history", 200, owner)).json()["items"]
            assert second_history == [second_note]
            async with case.owner.connection() as conn:
                audit = (await (await conn.execute("SELECT count(*) FROM k06_action_audit "
                                                   "WHERE action='review.put'")).fetchone())[0]
                assert audit == 3

            exports = {}
            for format, suffix in (("json", "json"), ("markdown", "md"), ("pdf", "pdf")):
                desc = (await request("POST", path + "/exports", 201, headers={**owner,
                    "idempotency-key": f"j05-vietnamese-{format}"}, json={"format": format})).json()
                downloaded = (await request("GET", desc["download_url"], 200)).content
                assert desc["result_id"] == case.child.record_id and len(downloaded) == desc["byte_size"]
                assert hashlib.sha256(downloaded).hexdigest() == desc["sha256"]
                assert downloaded == render_product_report(view, format)
                filename = f"bao-cao-j05.{suffix}"
                (out / filename).write_bytes(downloaded)
                exports[format] = {key: desc[key] for key in ("result_id", "byte_size", "sha256")}
            archived = json.loads((out / "bao-cao-j05.json").read_bytes())
            assert archived == view.model_dump(mode="json")
            markdown = (out / "bao-cao-j05.md").read_text(encoding="utf-8")
            for text in (VI_CLAIM, VI_COUNTER, VI_UNRELATED, early.id, late.id):
                assert text in markdown
            assert f"Counterevidence IDs: `{COUNTER}`" in markdown
            payload = fixture._stable_zip()
            assert hashlib.sha256(payload).hexdigest() == case.source.sha256
            (out / "j05-source.zip").write_bytes(payload)
            (out / "j05-relationship-review-export.json").write_text(json.dumps({
                "fixture": "Synthetic stored ProductView with a cited item, counterevidence and unrelated evidence.",
                "result_id": case.child.record_id, "previous_result_id": case.initial.record_id,
                "snapshot_id": case.snapshot.snapshot_id, "source_sha256": case.source.sha256,
                "evidence": bound, "unlinked_without_claim": loose, "old_turn_id": early.id, "new_turn_id": late.id,
                "two_reviewers": {"author_a": first_note["author_id"], "author_b": second_note["author_id"],
                    "concurrent_http": sorted(r.status_code for r in responses), "history_a": history,
                    "history_b": second_history, "review_write_audits": audit},
                "exports": exports, "requests": requests,
            }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    monkeypatch.setattr(fixture, "analyze_initial_upload", fixture_view)
    monkeypatch.setattr(native_upload_records, "analyze_upload", lambda snapshot, question, model: fixture_view(snapshot, question))
    _run(run())


def test_j05_native_purge_preserves_physically_shared_reference(tmp_path):
    async def run():
        async with _case(tmp_path) as case:
            _, initial = await _initial(case)
            await _ready(case)
            protected_source, protected = await _protected_b(case)
            retention = PostgresRetention(case.pool, case.catalog)
            await retention.configure(project_days=0, terminal_job_days=0, object_grace_seconds=60)

            async def reference(workspace, result, payload):
                item = await case.objects.stage(workspace, payload, media_type="text/plain")
                async with case.pool.connection() as conn:
                    await case.objects.ready(conn, workspace, item)
                    await conn.execute("INSERT INTO k05_object_references VALUES (%s,%s,%s)",
                                       (workspace, result, item.descriptor.sha256))
                    await case.objects.finish(conn, workspace, item)
                return item.descriptor

            shared = b"synthetic shared attachment A and B"
            a = await reference("a", initial.record_id, shared)
            b = await reference("b", protected.record_id, shared)
            private = await reference("a", initial.record_id, b"synthetic private attachment A")
            assert a.sha256 == b.sha256 and a.sha256 != private.sha256
            await PostgresReviews(case.pool, case.catalog).put(
                credential_digest=case.a, workspace_id="a", result_id=initial.record_id,
                annotation_id="private-j05", body="Ghi chú riêng cần được purge.", status="comment",
                expected_revision=0)
            for filename in case.objects.store.object_root.glob("*/*"):
                if filename.is_file():
                    old_time = time.time() - 120
                    os.utime(filename, (old_time, old_time))
            receipt = await PostgresProjectMutations(case.pool, case.catalog).delete(
                credential_digest=case.a, workspace_id="a", project_id=initial.project_id, expected_revision=2)
            assert receipt["retention"] == "retained"
            actions = []
            for _ in range(200):
                batch = await retention.purge(workspace_id="a", project_id=initial.project_id, limit=1)
                assert batch.changed <= 1
                actions.append(batch.action)
                if batch.status == "purged":
                    break
            else:
                raise AssertionError("Bounded purge did not finish")
            status = await retention.status(credential_digest=case.a, workspace_id="a", project_id=initial.project_id)
            assert status.status == "purged" and status.shared_objects_retained >= 1
            assert not case.objects.store.path(private.sha256).exists()
            assert case.objects.store.path(b.sha256).read_bytes() == shared
            assert await case.catalog.read_record(credential_digest=case.b, workspace_id="b",
                project_id=protected.project_id, record_id=protected.record_id) == protected
            assert await case.sources.read(credential_digest=case.b, workspace_id="b",
                                           source_id=protected_source.source_id) == case.payload
            async with case.pool.connection() as conn:
                assert await (await conn.execute("SELECT count(*) FROM j01_annotations WHERE workspace_id='a'")).fetchone() == (0,)
                assert await (await conn.execute("SELECT workspace_id FROM k05_object_references "
                    "WHERE sha256=%s", (a.sha256,))).fetchall() == [("b",)]
            out = Path(os.environ["J05_EVIDENCE"])
            out.mkdir(parents=True, exist_ok=True)
            (out / "j05-shared-purge.json").write_text(json.dumps({
                "delete_acceptance": receipt["retention"], "purge_final": status.status, "actions": actions,
                "shared_object_sha256": b.sha256, "shared_objects_retained": status.shared_objects_retained,
                "unlinked_a_private_object_removed": True, "a_private_annotations": 0,
                "remaining_shared_reference": "b", "b_source_sha256": protected_source.sha256,
                "b_original_result_unchanged": True,
            }, indent=2) + "\n", encoding="utf-8")

    _run(run())
