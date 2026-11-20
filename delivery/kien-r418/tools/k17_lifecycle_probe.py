"""Dedicated real-HTTPS lifecycle recipes on a disposable K17 staging fleet."""

from __future__ import annotations

import asyncio
import hashlib
import json
import ssl
import sys
import time
from collections import Counter
from pathlib import Path
from uuid import uuid4

import httpx2
from stage_load import CONFIG, credentials, stats
from verify_remaining import fresh_actor

from aletheia_lab.hosted.capacity_models import Actor
from aletheia_lab.hosted.capacity_seed_domain import confirm_mapping, source_payload
from aletheia_lab.hosted.capacity_seed_models import SeedPlan
from aletheia_lab.hosted_contract.job_page import Job
from aletheia_lab.hosted_contract.uploads import UploadManifest, UploadStatus

VERSION = "product-api/v1-draft"


class Lifecycle:
    def __init__(self, output, tls):
        self.output, self.tls = output, tls
        self.requests, self.cases = [], []
        self.run = uuid4().hex

    async def request(self, client, actor, operation, method, path, expected, **kwargs):
        began = time.monotonic()
        response = await client.request(
            method, path, headers={**credentials(actor), **kwargs.pop("headers", {})}, **kwargs
        )
        self.requests.append(
            {
                "operation": operation,
                "method": method,
                "status": response.status_code,
                "expected": expected,
                "seconds": time.monotonic() - began,
                "response_bytes": len(response.content),
            }
        )
        assert response.status_code == expected, (
            operation,
            method,
            response.status_code,
            response.json().get("code")
            if response.headers.get("content-type", "").startswith("application/problem")
            else None,
        )
        return response

    def headers(self, *, revision=None):
        result = {"idempotency-key": "k17-" + uuid4().hex}
        if revision is not None:
            result["if-match"] = f'"{revision}"'
        return result

    async def job_output(self, client, actor, receipt):
        path = receipt["status_url"]
        assert receipt["workspace_id"] == actor.values["workspace_id"]
        assert path == f"/v1/workspaces/{receipt['workspace_id']}/jobs/{receipt['job_id']}"
        begin = time.monotonic()
        async with asyncio.timeout(120):
            while True:
                response = await self.request(client, actor, "job_poll", "GET", path, 200)
                job = Job.model_validate_json(response.content)
                assert (
                    job.job_id == receipt["job_id"] and job.workspace_id == receipt["workspace_id"]
                )
                assert job.status not in ("failed", "cancelled", "expired"), job.status
                if job.status == "succeeded":
                    assert job.attempt == 1
                    break
                await asyncio.sleep(2)
        response = await self.request(client, actor, "job_output", "GET", path + "/output", 200)
        envelope = response.json()
        assert envelope["job_id"] == job.job_id and envelope["workspace_id"] == job.workspace_id
        return envelope["result"], time.monotonic() - begin

    async def upload(self, client, actor, payload, *, cancel=False):
        size = 128 * 1024
        parts = [payload[i : i + size] for i in range(0, len(payload), size)]
        manifest = UploadManifest.model_validate(
            {
                "schema_version": VERSION,
                "source_type": "zip_bundle",
                "byte_size": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "parent_source_id": None,
                "parts": [
                    {
                        "part_number": i + 1,
                        "byte_size": len(part),
                        "sha256": hashlib.sha256(part).hexdigest(),
                    }
                    for i, part in enumerate(parts)
                ],
            }
        )
        base = f"/v1/workspaces/{actor.values['workspace_id']}/uploads"
        headers = self.headers()
        response = await self.request(
            client,
            actor,
            "P06",
            "POST",
            base,
            201,
            headers=headers,
            json=manifest.model_dump(mode="json"),
        )
        uploaded = UploadStatus.model_validate_json(response.content)
        replay = await self.request(
            client,
            actor,
            "P06_replay",
            "POST",
            base,
            201,
            headers=headers,
            json=manifest.model_dump(mode="json"),
        )
        assert UploadStatus.model_validate_json(replay.content).upload_id == uploaded.upload_id
        path = base + "/" + uploaded.upload_id
        first = await self.request(
            client,
            actor,
            "P29",
            "PUT",
            path + "/parts/1",
            200,
            content=parts[0],
            headers={"content-type": "application/octet-stream"},
        )
        assert UploadStatus.model_validate_json(first.content).received_bytes == len(parts[0])
        if cancel:
            response = await self.request(client, actor, "P31", "DELETE", path, 200)
            assert UploadStatus.model_validate_json(response.content).state == "cancelled"
            readback = await self.request(client, actor, "P31_readback", "GET", path, 200)
            assert UploadStatus.model_validate_json(readback.content).state == "cancelled"
            self.cases.append(
                {"operation": "P31", "cancelled_upload_readback": True, "source_published": False}
            )
            return None
        async with httpx2.AsyncClient(
            base_url=CONFIG["origin"], verify=self.tls, trust_env=False, timeout=15
        ) as resumed:
            response = await self.request(resumed, actor, "P29_resume", "GET", path, 200)
            status = UploadStatus.model_validate_json(response.content)
            assert status.received_parts == [1] and status.received_bytes == len(parts[0])
            assert status.missing_parts == list(range(2, len(parts) + 1))
            for index in reversed(range(len(parts))):
                await self.request(
                    resumed,
                    actor,
                    "P29",
                    "PUT",
                    path + f"/parts/{index + 1}",
                    200,
                    content=parts[index],
                    headers={"content-type": "application/octet-stream"},
                )
        response = await self.request(client, actor, "P29_readback", "GET", path, 200)
        before = UploadStatus.model_validate_json(response.content)
        assert before.received_bytes == len(payload) and not before.missing_parts
        done = await self.request(client, actor, "P30", "POST", path + "/complete", 200, json={})
        ready = UploadStatus.model_validate_json(done.content)
        assert ready.state == "ready" and ready.sha256 == manifest.sha256
        replay = await self.request(
            client, actor, "P30_replay", "POST", path + "/complete", 200, json={}
        )
        assert UploadStatus.model_validate_json(replay.content).source_id == ready.source_id
        self.cases.append(
            {
                "operation": "P06/P29/P30",
                "bytes": len(payload),
                "parts": len(parts),
                "sha256": manifest.sha256,
                "ready": True,
                "resumed_in_new_client": True,
                "duplicate_part_and_completion_preserved": True,
            }
        )
        return ready.source_id

    async def import_refresh_delete(self, client, actor, index, *, verify_delete=False):
        size = actor.size
        plan = SeedPlan(
            namespace="k17_" + self.run[:12],
            seed=int(self.run[:8], 16),
            generations=2,
            results_per_generation=1,
            source_rows={"S": 1000, "M": 10000, "L": 100000},
        )
        payload = source_payload(plan, index, 0)
        changed = source_payload(plan, index, 1)
        assert payload != changed
        await self.upload(client, actor, payload, cancel=True)
        source = await self.upload(client, actor, payload)
        base = f"/v1/workspaces/{actor.values['workspace_id']}"
        response = await self.request(
            client,
            actor,
            "P07",
            "POST",
            base + "/imports/preview",
            202,
            headers=self.headers(),
            json={"source_id": source},
        )
        preview, _ = await self.job_output(client, actor, response.json())
        assert preview["source_sha256"] == hashlib.sha256(payload).hexdigest()
        mapping = confirm_mapping(preview, plan.metric_series[size])
        preview_path = base + "/imports/" + preview["preview_id"]
        await self.request(client, actor, "P08", "GET", preview_path, 200)
        response = await self.request(
            client, actor, "P09_before_projects", "GET", base + "/projects?limit=100", 200
        )
        page = response.json()
        assert not page["next_cursor"]
        before_projects = {item["project_id"] for item in page["items"]}
        key = self.headers()
        response = await self.request(
            client,
            actor,
            "P09",
            "POST",
            preview_path + "/confirm",
            202,
            headers=key,
            json={"mapping": mapping},
        )
        receipt = response.json()
        confirmed, confirm_seconds = await self.job_output(client, actor, receipt)
        assert "snapshot_id" in confirmed, "mapping validation failed"
        replay = await self.request(
            client,
            actor,
            "P09_replay",
            "POST",
            preview_path + "/confirm",
            202,
            headers=key,
            json={"mapping": mapping},
        )
        assert replay.json()["job_id"] == receipt["job_id"]
        response = await self.request(
            client, actor, "P09_after_projects", "GET", base + "/projects?limit=100", 200
        )
        page = response.json()
        assert not page["next_cursor"]
        added = {item["project_id"] for item in page["items"]} - before_projects
        assert len(added) == 1
        project = added.pop()
        path = base + "/projects/" + project
        response = await self.request(client, actor, "P35_read", "GET", path, 200)
        details = response.json()
        assert details["current_snapshot_id"] == confirmed["snapshot_id"]
        name = "K17 synthetic " + size + " " + self.run[:8]
        renamed = await self.request(
            client,
            actor,
            "P35",
            "PATCH",
            path,
            200,
            headers=self.headers(revision=details["revision"]),
            json={"schema_version": VERSION, "display_name": name},
        )
        update = renamed.json()
        assert (
            update["display_name"] == name
            and update["current_snapshot_id"] == confirmed["snapshot_id"]
        )
        unchanged = await self.request(
            client,
            actor,
            "P10_noop",
            "POST",
            path + "/refresh",
            202,
            headers=self.headers(revision=update["revision"]),
            json={"schema_version": VERSION, "source_id": source},
        )
        noop, _ = await self.job_output(client, actor, unchanged.json())
        assert noop["status"] == "unchanged" and noop["snapshot_id"] == confirmed["snapshot_id"]
        fresh_source = await self.upload(client, actor, changed)
        response = await self.request(
            client,
            actor,
            "P10",
            "POST",
            path + "/refresh",
            202,
            headers=self.headers(revision=noop["revision"]),
            json={"schema_version": VERSION, "source_id": fresh_source},
        )
        refreshed, refresh_seconds = await self.job_output(client, actor, response.json())
        assert refreshed["status"] == "new_snapshot"
        assert refreshed["previous_snapshot_id"] == confirmed["snapshot_id"]
        assert refreshed["snapshot_id"] != confirmed["snapshot_id"]
        old_result = old_download = None
        if verify_delete:
            accepted = await self.request(
                client,
                actor,
                "P25_analysis",
                "POST",
                base + "/jobs",
                202,
                headers=self.headers(),
                json={
                    "operation": "analyze",
                    "snapshot_id": refreshed["snapshot_id"],
                    "question": "Which metric observations are present?",
                },
            )
            view, _ = await self.job_output(client, actor, accepted.json())
            assert view["snapshot"]["id"] == refreshed["snapshot_id"]
            assert view["evidence"] and all(
                item["source_sha256"] == hashlib.sha256(changed).hexdigest()
                for item in view["evidence"]
            )
            old_result = base + "/results/" + view["result"]["id"] + "/header"
            await self.request(client, actor, "P25_old_header", "GET", old_result, 200)
            created = await self.request(
                client,
                actor,
                "P25_export",
                "POST",
                base + "/results/" + view["result"]["id"] + "/exports",
                201,
                headers=self.headers(),
                json={"format": "json"},
            )
            descriptor = created.json()
            old_download = descriptor["download_url"]
            raw = await self.request(client, actor, "P25_export_hash", "GET", old_download, 200)
            assert len(raw.content) == descriptor["byte_size"]
            assert hashlib.sha256(raw.content).hexdigest() == descriptor["sha256"]
        before = await self.request(client, actor, "P25_read", "GET", path, 200)
        deleted = await self.request(
            client,
            actor,
            "P25",
            "DELETE",
            path,
            200,
            headers=self.headers(revision=before.json()["revision"]),
        )
        assert deleted.json()["status"] == "tombstoned"
        deleted_read = await self.request(client, actor, "P25_readback", "GET", path, 200)
        assert deleted_read.json()["state"] == "deleting"
        if verify_delete:
            await self.request(client, actor, "P25_old_header_denied", "GET", old_result, 404)
            await self.request(client, actor, "P25_old_export_denied", "GET", old_download, 404)
        self.cases.append(
            {
                "operation": "P09/P10/P25/P35",
                "size": size,
                "confirm_seconds": confirm_seconds,
                "refresh_seconds": refresh_seconds,
                "confirm_replay_same_job": True,
                "refresh_noop_and_new_snapshot": True,
                "renamed_and_tombstoned": True,
                "old_result_and_verified_export_denied_after_delete": verify_delete,
            }
        )

    async def management(self, client, owner, invited):
        ws = owner.values["workspace_id"]
        base = "/v1/workspaces/" + ws
        settings = (
            await self.request(client, owner, "P38_read", "GET", base + "/settings", 200)
        ).json()
        limit = settings["maximum_upload_bytes"]
        changed = await self.request(
            client,
            owner,
            "P38",
            "PATCH",
            base + "/settings",
            200,
            headers=self.headers(revision=settings["revision"]),
            json={"schema_version": VERSION, "maximum_upload_bytes": limit - 1},
        )
        assert changed.json()["maximum_upload_bytes"] == limit - 1
        check = await self.request(client, owner, "P38_readback", "GET", base + "/settings", 200)
        assert check.json() == changed.json()
        restored = await self.request(
            client,
            owner,
            "P38_restore",
            "PATCH",
            base + "/settings",
            200,
            headers=self.headers(revision=changed.json()["revision"]),
            json={"schema_version": VERSION, "maximum_upload_bytes": limit},
        )
        assert restored.json()["maximum_upload_bytes"] == limit
        invite = await self.request(
            client,
            owner,
            "P36",
            "POST",
            base + "/invitations",
            201,
            json={"user_id": invited.actor_id, "role": "viewer", "expires_in_seconds": 600},
        )
        invitation = invite.json()
        accepted = await self.request(
            client,
            invited,
            "P36_accept",
            "POST",
            "/v1/invitations/accept",
            200,
            json={"token": invitation["token"]},
        )
        assert accepted.json()["membership"]["role"] == "viewer"
        path = base + "/members/" + invited.actor_id
        mutated = await self.request(
            client,
            owner,
            "P28",
            "PATCH",
            path,
            200,
            headers=self.headers(revision=accepted.json()["membership"]["revision"]),
            json={"role": "reviewer"},
        )
        assert mutated.json()["role"] == "reviewer"
        revoked = await self.request(
            client,
            owner,
            "P28_revoke",
            "PATCH",
            path,
            200,
            headers=self.headers(revision=mutated.json()["revision"]),
            json={"active": False},
        )
        assert revoked.json()["active"] is False
        started = time.monotonic()
        denied = await self.request(
            client,
            invited,
            "P28_denial",
            "GET",
            base + "/capabilities",
            403,
        )
        assert denied.json()["code"] in ("membership_revoked", "permission_denied")
        milliseconds = (time.monotonic() - started) * 1000
        assert milliseconds <= 5000
        self.cases.append(
            {
                "operation": "P28/P36/P38",
                "settings_readback_and_restored": True,
                "invitation_accepted": True,
                "membership_revoke_observed_ms": milliseconds,
            }
        )

    async def cancel_job(self, client, actor):
        base = f"/v1/workspaces/{actor.values['workspace_id']}/jobs"
        response = await self.request(
            client,
            actor,
            "P21_admit",
            "POST",
            base,
            202,
            headers=self.headers(),
            json={"operation": "preview", "source_id": actor.values["source_id"]},
        )
        receipt = response.json()
        cancelled = await self.request(
            client,
            actor,
            "P21",
            "POST",
            receipt["status_url"] + "/cancel",
            200,
            json={},
        )
        job = Job.model_validate_json(cancelled.content)
        assert job.status == "cancelled"
        response = await self.request(
            client, actor, "P21_readback", "GET", receipt["status_url"], 200
        )
        assert Job.model_validate_json(response.content).status == "cancelled"
        output = await self.request(
            client, actor, "P21_output_denied", "GET", receipt["status_url"] + "/output", 404
        )
        self.cases.append(
            {
                "operation": "P21",
                "job_id": job.job_id,
                "status": job.status,
                "output_denied": output.status_code,
            }
        )

    async def logout(self, client, actor):
        active, before = await fresh_actor(self.tls, actor)
        await self.request(client, active, "P02_session", "GET", "/v1/session", 200)
        result = await self.request(client, active, "P02", "POST", "/v1/auth/logout", 204, json={})
        assert result.headers.get_list("set-cookie")
        assert all("max-age=0" in item.lower() for item in result.headers.get_list("set-cookie"))
        async with httpx2.AsyncClient(
            base_url=CONFIG["origin"], verify=self.tls, trust_env=False, timeout=10
        ) as tab:
            await self.request(tab, active, "P02_old_session", "GET", "/v1/session", 401)
        self.cases.append(
            {
                "operation": "P02",
                "real_oidc": True,
                "context_id": before.session.context_id,
                "old_session_denied": True,
            }
        )


async def main(output, phase):
    output.mkdir(exist_ok=False)
    tls = ssl.create_default_context(cafile="/run/config/ca.pem")
    fixture = Lifecycle(output, tls)
    actors = [
        Actor.model_validate_json(line)
        for line in Path("/run/load/actors.jsonl").read_text().splitlines()
    ]
    owners = [actor for actor in actors if actor.role == "owner"]
    hot = owners[0]
    quiet = next(a for a in owners if a.values["workspace_id"] != hot.values["workspace_id"])
    seeds = [
        next(
            a
            for a in owners
            if a.size == size and a.values["workspace_id"] == hot.values["workspace_id"]
        )
        for size in ("S", "M", "L")
    ]
    invitee = next(
        a
        for a in actors
        if a.role == "viewer" and a.values["workspace_id"] == quiet.values["workspace_id"]
    )
    logout_actor = next(
        a
        for a in actors
        if a.role == "reviewer" and a.values["workspace_id"] == quiet.values["workspace_id"]
    )
    stop = asyncio.Event()
    background = []
    started = time.monotonic()
    result = {"status": "failed", "phase": phase, "primary_campaign_passed": False}
    async with (
        asyncio.timeout(30),
        httpx2.AsyncClient(
            base_url=CONFIG["origin"], verify=tls, trust_env=False, timeout=3
        ) as ready_client,
    ):
        while True:
            try:
                ready = await ready_client.get("/health/ready")
                if ready.status_code == 200:
                    break
            except httpx2.TransportError:
                pass
            await asyncio.sleep(0.5)
    selected = {quiet.actor_id: quiet}
    if phase == "imports":
        selected.update({actor.actor_id: actor for actor in seeds})
    elif phase in ("management", "cancel", "delete_integrity"):
        selected[hot.actor_id] = hot
    if phase == "management":
        selected[invitee.actor_id] = invitee
    current = {}
    preflight = time.monotonic()
    for identifier, actor in selected.items():
        current[identifier], session = await fresh_actor(tls, actor)
        assert session.actor.user_id == identifier
    quiet = current[quiet.actor_id]
    hot = current.get(hot.actor_id, hot)
    seeds = [current.get(actor.actor_id, actor) for actor in seeds]
    invitee = current.get(invitee.actor_id, invitee)
    result["real_oidc_preflight"] = {
        "principals": len(current),
        "seconds": time.monotonic() - preflight,
        "separate_from_business_requests": True,
    }
    async with httpx2.AsyncClient(
        base_url=CONFIG["origin"], verify=tls, trust_env=False, timeout=30
    ) as client:

        async def read_background():
            path = f"/v1/workspaces/{quiet.values['workspace_id']}/results/{quiet.values['result_id']}/header"
            while not stop.is_set():
                before = time.monotonic()
                r = await client.get(path, headers=credentials(quiet))
                assert r.status_code == 200
                assert r.json()["workspace_id"] == quiet.values["workspace_id"]
                background.append(time.monotonic() - before)
                await asyncio.sleep(0.1)

        task = asyncio.create_task(read_background())
        try:
            if phase == "imports":
                for index, actor in enumerate(seeds):
                    await fixture.import_refresh_delete(client, actor, index)
            elif phase == "management":
                await fixture.management(client, hot, invitee)
                await fixture.logout(client, logout_actor)
            elif phase == "logout":
                await fixture.logout(client, logout_actor)
            elif phase == "cancel":
                await fixture.cancel_job(client, hot)
            elif phase == "delete_integrity":
                await fixture.import_refresh_delete(client, seeds[0], 0, verify_delete=True)
            else:
                raise ValueError("explicit dedicated phase required")
            result["status"] = "passed_dedicated_https"
        finally:
            stop.set()
            await task
            result.update(
                elapsed_seconds=time.monotonic() - started,
                request_count=len(fixture.requests),
                http_statuses=Counter(item["status"] for item in fixture.requests),
                cases=fixture.cases,
                background_reads=stats(background) if background else {},
                runtime_identity=CONFIG["release_identity"],
            )
            (output / "requests.jsonl").write_text(
                "".join(json.dumps(item) + "\n" for item in fixture.requests), encoding="utf-8"
            )
            (output / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1]), sys.argv[2]))
