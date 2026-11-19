"""Private labels cannot turn aliases of one owner into many verified principals."""

import asyncio
import json
from datetime import UTC, datetime, timedelta

import httpx2
import pytest
from test_k17_capacity import _actor, _profile

from aletheia_lab.hosted.capacity_preflight import CapacityPreflightRejected, verify_actors


def _session(actor):
    return {
        "schema_version": "product-api/v1-draft",
        "actor": {
            "user_id": actor.actor_id,
            "display_name": "Synthetic",
            "email": "synthetic@example.invalid",
            "email_verified": True,
        },
        "session": {
            "context_id": "ctx_" + actor.actor_id,
            "expires_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
        },
        "csrf_token": actor.csrf.get_secret_value(),
        "onboarding": "ready",
        "last_workspace_id": actor.values["workspace_id"],
        "can_create_workspace": False,
    }


def _capabilities(actor):
    return {
        "schema_version": "product-api/v1-draft",
        "workspace_id": actor.values["workspace_id"],
        "workspace_name": "Synthetic",
        "membership": {"role": actor.role, "revision": 1},
        "deployment_mode": "hosted",
        "analysis_backends": [],
        "supported_source_types": [],
        "supported_export_formats": [],
        "allowed_actions": ["read"],
        "features": {},
        "limits": {
            "question_chars": 100,
            "upload_bundle_bytes": 100,
            "page_max": 100,
            "job_poll_min_ms": 2000,
        },
    }


def test_preflight_checks_real_actor_role_and_distinct_cookie_without_shared_jar():
    async def run():
        actors = [_actor(index) for index in range(20)]
        active = peak = 0
        seen = []

        async def handler(request):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            try:
                actor = next(
                    actor
                    for actor in actors
                    if request.headers["cookie"] == actor.cookie.get_secret_value()
                )
                seen.append((actor.actor_id, request.url.path))
                await asyncio.sleep(0.001)
                return httpx2.Response(
                    200,
                    json=_session(actor)
                    if request.url.path == "/v1/session"
                    else _capabilities(actor),
                    headers={
                        "set-cookie": "__Host-aletheia_session=PRIVATE_FORGED_SHARED_COOKIE; Path=/"
                    },
                )
            finally:
                active -= 1

        async with httpx2.AsyncClient(
            base_url="http://127.0.0.1", transport=httpx2.MockTransport(handler)
        ) as client:
            report = await verify_actors(_profile(max_inflight=3), actors, client)
        assert report == {
            "verified_principals": 20,
            "session_http": 20,
            "capability_http": 20,
            "verified_role_counts": {"viewer": 20},
        }
        assert len(seen) == 40 and peak <= 3 and active == 0
        assert "PRIVATE" not in json.dumps(report) and "actor_" not in json.dumps(report)

    asyncio.run(run())


@pytest.mark.parametrize(
    "mismatch",
    [
        "principal_alias",
        "csrf",
        "role",
        "workspace",
        "expired",
        "invalid_json",
        "unauthorized",
        "transport",
        "oversize",
    ],
)
def test_preflight_rejects_mislabeled_or_stale_authority_without_private_diagnostics(mismatch):
    async def run():
        actor = _actor(1)

        async def handler(request):
            if mismatch == "transport":
                raise httpx2.ConnectError("PRIVATE_DRIVER_DIAGNOSTIC", request=request)
            if mismatch == "unauthorized":
                return httpx2.Response(401, json={"private": "PRIVATE_COOKIE"})
            if mismatch == "invalid_json":
                return httpx2.Response(200, content=b"PRIVATE_not_json")
            if mismatch == "oversize":
                return httpx2.Response(200, content=b"x" * (1024 * 1024 + 1))
            if request.url.path == "/v1/session":
                body = _session(actor)
                if mismatch == "principal_alias":
                    body["actor"]["user_id"] = "one_shared_owner"
                elif mismatch == "csrf":
                    body["csrf_token"] = "PRIVATE_OTHER_PROOF"
                elif mismatch == "expired":
                    body["session"]["expires_at"] = (
                        datetime.now(UTC) - timedelta(seconds=1)
                    ).isoformat()
            else:
                body = _capabilities(actor)
                if mismatch == "role":
                    body["membership"]["role"] = "owner"
                elif mismatch == "workspace":
                    body["workspace_id"] = "foreign"
            return httpx2.Response(200, json=body)

        async with httpx2.AsyncClient(
            base_url="http://127.0.0.1", transport=httpx2.MockTransport(handler)
        ) as client:
            with pytest.raises(CapacityPreflightRejected) as error:
                await verify_actors(_profile(), [actor], client)
        assert str(error.value) == "Capacity actor/session/workspace preflight did not match."
        assert "PRIVATE" not in str(error.value)

    asyncio.run(run())


def test_cancelled_preflight_releases_all_bounded_network_workers():
    async def run():
        active = 0
        entered = asyncio.Event()

        async def handler(request):
            nonlocal active
            active += 1
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                active -= 1

        async with httpx2.AsyncClient(
            base_url="http://127.0.0.1", transport=httpx2.MockTransport(handler)
        ) as client:
            parent = asyncio.create_task(
                verify_actors(_profile(max_inflight=2), [_actor(i) for i in range(10)], client)
            )
            await entered.wait()
            parent.cancel()
            with pytest.raises(asyncio.CancelledError):
                await parent
        assert active == 0

    asyncio.run(run())


def test_private_expiry_observer_receives_actual_session_without_adding_public_labels():
    async def run():
        actor = _actor()
        observed = []

        def capture(actual, session):
            observed.append(
                (actual.actor_id, session.session.context_id, session.session.expires_at)
            )

        def handler(request):
            return httpx2.Response(
                200,
                json=_session(actor) if request.url.path == "/v1/session" else _capabilities(actor),
            )

        async with httpx2.AsyncClient(
            base_url="http://127.0.0.1", transport=httpx2.MockTransport(handler)
        ) as client:
            result = await verify_actors(_profile(), [actor], client, lease_observer=capture)
        assert len(observed) == 1 and observed[0][0] == actor.actor_id
        assert observed[0][2] > datetime.now(UTC)
        assert "context" not in json.dumps(result) and "expires" not in json.dumps(result)

    asyncio.run(run())
