"""Renewal preserves private actor identity, bounds real expiry and fails closed."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import httpx2
import pytest
from test_k17_capacity import _actor, _profile

from aletheia_lab.hosted.application import VERSION
from aletheia_lab.hosted.capacity_sessions import CapacitySessionRejected, CapacitySessions
from aletheia_lab.hosted_contract.session_capabilities import Session


def _fixture(index=0, *, expires=60, hard=120):
    actor = _actor(index)
    actor = actor.model_copy(
        update={
            "values": {
                **actor.values,
                "context_id": f"ctx_{index}",
                "absolute_expires_at": (datetime.now(UTC) + timedelta(seconds=hard)).isoformat(),
            }
        }
    )
    return actor, _session(actor, expires=expires)


def _session(actor, *, expires=60, user=None, proof=None):
    return Session.model_validate_json(
        __import__("json").dumps(
            {
                "schema_version": VERSION,
                "actor": {
                    "user_id": user or actor.actor_id,
                    "display_name": "Synthetic",
                    "email": "a@example.invalid",
                    "email_verified": True,
                },
                "session": {
                    "context_id": actor.values["context_id"],
                    "expires_at": (datetime.now(UTC) + timedelta(seconds=expires)).isoformat(),
                },
                "csrf_token": proof or actor.csrf.get_secret_value(),
                "onboarding": "ready",
                "last_workspace_id": actor.values["workspace_id"],
            }
        )
    )


def _cookies(actor, *, scope="Path=/; Secure; HttpOnly; SameSite=Lax; Max-Age=60"):
    return [
        ("set-cookie", f"__Host-aletheia_session=ROTATED_{actor.actor_id}; {scope}"),
        ("set-cookie", f"__Host-aletheia_csrf={actor.csrf.get_secret_value()}; {scope}"),
    ]


def test_near_expiry_preflight_lease_bootstraps_now_and_explicit_cookies_keep_actors_distinct():
    async def scenario():
        actors, sessions = zip(*[_fixture(i, expires=5.0) for i in range(2)], strict=True)
        profile = _profile(renew_every_seconds=300.0)
        leases = CapacitySessions(profile, list(actors))
        for actor, session in zip(actors, sessions, strict=True):
            leases.observe_session(actor, session)
        seen = []

        def handler(request):
            index = 0 if "PRIVATE_SESSION_0;" in request.headers["cookie"] else 1
            actor = actors[index]
            assert request.headers["cookie"] == actor.cookie.get_secret_value()
            assert request.headers["x-csrf-token"] == actor.csrf.get_secret_value()
            seen.append(index)
            return httpx2.Response(
                200, content=_session(actor).model_dump_json().encode(), headers=_cookies(actor)
            )

        async with httpx2.AsyncClient(
            base_url=profile.target, transport=httpx2.MockTransport(handler)
        ) as client:
            await leases.bootstrap(client)
        assert set(seen) == {0, 1}
        assert leases.report()["counts"]["cookie_rotations"] == 2
        assert all(leases.current(a).cookie != a.cookie for a in actors)
        assert "PRIVATE" not in __import__("json").dumps(leases.report())

    asyncio.run(scenario())


@pytest.mark.parametrize("problem", ["wrong_actor", "expired", "beyond_hard", "wrong_proof"])
def test_invalid_renewal_blocks_future_actor_work_without_leaking_response(problem):
    async def scenario():
        actor, session = _fixture(expires=5.0)
        profile = _profile(renew_every_seconds=300.0)
        leases = CapacitySessions(profile, [actor])
        leases.observe_session(actor, session)
        body = _session(
            actor,
            user="foreign_user" if problem == "wrong_actor" else None,
            expires=-1 if problem == "expired" else 121 if problem == "beyond_hard" else 60,
            proof="PRIVATE_WRONG_PROOF" if problem == "wrong_proof" else None,
        )
        calls = []

        def handler(request):
            calls.append(1)
            return httpx2.Response(
                200, content=body.model_dump_json().encode(), headers=_cookies(actor)
            )

        async with httpx2.AsyncClient(
            base_url=profile.target, transport=httpx2.MockTransport(handler)
        ) as client:
            with pytest.raises(CapacitySessionRejected) as error:
                async with leases.action(actor, client):
                    pass
            assert "PRIVATE" not in str(error.value)
            with pytest.raises(CapacitySessionRejected, match="recovery"):
                async with leases.action(actor, client):
                    pass
        assert len(calls) == 1 and leases.report()["counts"]["failed"] == 1

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "scope",
    [
        "Path=/; Domain=example.invalid; Secure; HttpOnly; SameSite=Lax; Max-Age=60",
        "Path=/other; Secure; HttpOnly; SameSite=Lax; Max-Age=60",
        "Path=/; HttpOnly; SameSite=Lax; Max-Age=60",
        "Path=/; Secure; SameSite=Lax; Max-Age=60",
        "Path=/; Secure; HttpOnly; SameSite=None; Max-Age=60",
    ],
)
def test_renewal_cookie_scope_cannot_relax_host_secure_httponly_lax_policy(scope):
    actor, _ = _fixture()
    with pytest.raises(CapacitySessionRejected):
        CapacitySessions._cookies([value for _, value in _cookies(actor, scope=scope)])


def test_expired_original_lease_cannot_renew_or_silently_reauthenticate():
    async def scenario():
        actor, session = _fixture(expires=-2, hard=-1)
        profile = _profile()
        leases = CapacitySessions(profile, [actor])
        leases.observe_session(actor, session)

        def handler(request):
            raise AssertionError("Expired principal must not submit renewal/login")

        async with httpx2.AsyncClient(
            base_url=profile.target, transport=httpx2.MockTransport(handler)
        ) as client:
            with pytest.raises(CapacitySessionRejected, match="expired"):
                async with leases.action(actor, client):
                    pass

    asyncio.run(scenario())


def test_deadline_during_renewal_marks_credential_outcome_unknown_and_never_reuses_it():
    async def scenario():
        actor, session = _fixture(expires=5.0)
        profile = _profile()
        leases = CapacitySessions(profile, [actor])
        leases.observe_session(actor, session)

        async def handler(request):
            await asyncio.sleep(0.5)
            return httpx2.Response(
                200, content=_session(actor).model_dump_json().encode(), headers=_cookies(actor)
            )

        async with httpx2.AsyncClient(
            base_url=profile.target, transport=httpx2.MockTransport(handler)
        ) as client:
            with pytest.raises(TimeoutError):
                async with asyncio.timeout(0.01), leases.action(actor, client):
                    pass
            with pytest.raises(CapacitySessionRejected, match="recovery"):
                async with leases.action(actor, client):
                    pass
        assert leases.report()["counts"]["interrupted_outcome_unknown"] == 1

    asyncio.run(scenario())
