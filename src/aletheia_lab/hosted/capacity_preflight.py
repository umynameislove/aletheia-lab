"""Prove each private fixture label against current real session and membership."""

from __future__ import annotations

import asyncio
import hmac
import re
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TypeVar

import httpx2
from pydantic import BaseModel

from aletheia_lab.hosted_contract.session_capabilities import Capabilities, Session
from aletheia_lab.project.identity import content_hasher

from .capacity_models import Actor, Profile

_Model = TypeVar("_Model", bound=BaseModel)
_MAX_RESPONSE_BYTES = 1024 * 1024
_REQUEST_SECONDS = 10
_TOTAL_SECONDS = 300


class CapacityPreflightRejected(ValueError):
    def __init__(self) -> None:
        super().__init__("Capacity actor/session/workspace preflight did not match.")


def _validate_actor_identities(actors: list[Actor]) -> None:
    identities, credentials = set(), set()
    for actor in actors:
        credential = content_hasher(actor.cookie.get_secret_value().encode()).digest()
        if actor.actor_id in identities or credential in credentials:
            raise CapacityPreflightRejected()
        identities.add(actor.actor_id)
        credentials.add(credential)
        workspace = actor.values.get("workspace_id")
        if (
            not isinstance(workspace, str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", workspace) is None
        ):
            raise CapacityPreflightRejected()


def _validate_session(actor: Actor, session: Session) -> None:
    if (
        session.actor.user_id != actor.actor_id
        or not session.actor.email_verified
        or session.session.expires_at <= datetime.now(UTC)
        or not hmac.compare_digest(
            session.csrf_token.encode(), actor.csrf.get_secret_value().encode()
        )
    ):
        raise CapacityPreflightRejected()


async def _read_preflight(profile: Profile, client: httpx2.AsyncClient,
                          counts: Counter[str], actor: Actor, path: str,
                          model: type[_Model], counter: str) -> _Model:
    counts[counter] += 1
    headers = {
        "host": profile.host,
        "origin": profile.origin,
        "cookie": actor.cookie.get_secret_value(),
        "x-csrf-token": actor.csrf.get_secret_value(),
        "accept": "application/json",
    }
    async with (
        asyncio.timeout(_REQUEST_SECONDS),
        client.stream("GET", path, headers=headers) as response,
    ):
        if response.status_code != 200:
            raise CapacityPreflightRejected()
        data = bytearray()
        async for chunk in response.aiter_bytes():
            if len(data) + len(chunk) > _MAX_RESPONSE_BYTES:
                raise CapacityPreflightRejected()
            data.extend(chunk)
        return model.model_validate_json(data)


async def _verify_actor(profile: Profile, client: httpx2.AsyncClient, actor: Actor,
                        counts: Counter[str], roles: Counter[str],
                        lease_observer: Callable[[Actor, Session], None] | None) -> None:
    session = await _read_preflight(profile, client, counts, actor, "/v1/session", Session, "session_http")
    _validate_session(actor, session)
    if lease_observer is not None:
        lease_observer(actor, session)
    capabilities = await _read_preflight(profile, client, counts, 
        actor,
        "/v1/workspaces/" + actor.values["workspace_id"] + "/capabilities",
        Capabilities,
        "capability_http",
    )
    if (
        capabilities.workspace_id != actor.values["workspace_id"]
        or capabilities.membership.role != actor.role
    ):
        raise CapacityPreflightRejected()
    counts["verified_principals"] += 1
    roles[actor.role] += 1


async def verify_actors(
    profile: Profile,
    actors: list[Actor],
    client: httpx2.AsyncClient,
    *,
    lease_observer: Callable[[Actor, Session], None] | None = None,
) -> dict[str, object]:
    """Bounded read-only bootstrap, excluded from offered business-action counters."""
    tasks: list[asyncio.Task[None]] = []
    counts: Counter[str] = Counter()
    roles: Counter[str] = Counter()
    try:
        if not 1 <= len(actors) <= 100000 or not 1 <= profile.max_inflight <= 50000:
            raise CapacityPreflightRejected()
        _validate_actor_identities(actors)
        pending = iter(actors)

        async def worker() -> None:
            while True:
                actor = next(pending, None)
                if actor is None:
                    return
                await _verify_actor(profile, client, actor, counts, roles, lease_observer)

        async with asyncio.timeout(_TOTAL_SECONDS):
            tasks = [
                asyncio.create_task(worker())
                for _ in range(min(32, profile.max_inflight, len(actors)))
            ]
            await asyncio.gather(*tasks)
        return {
            "verified_principals": counts["verified_principals"],
            "session_http": counts["session_http"],
            "capability_http": counts["capability_http"],
            "verified_role_counts": dict(roles),
        }
    except asyncio.CancelledError:
        raise
    except Exception:
        raise CapacityPreflightRejected() from None
    finally:
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
