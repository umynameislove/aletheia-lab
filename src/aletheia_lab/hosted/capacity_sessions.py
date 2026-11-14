"""Private actor leases and bounded native renewal; no shared cookie-jar identity."""

from __future__ import annotations

import asyncio
import hmac
from collections import Counter
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from http.cookies import SimpleCookie

import httpx2
from pydantic import SecretStr

from aletheia_lab.hosted_contract.session_capabilities import Session
from aletheia_lab.project.identity import content_hasher

from .capacity_models import Actor, Profile


class CapacitySessionRejected(RuntimeError):
    """Only fixed protocol diagnostics; never includes credentials or HTTP input."""


@dataclass
class _Lease:
    actor: Actor
    context_id: str
    expires_at: datetime
    absolute_expires_at: datetime
    next_renew_at: datetime
    failed: bool = False
    renewal_number: int = 0


class CapacitySessions:
    def __init__(self, profile: Profile, actors: list[Actor]) -> None:
        self.profile = profile
        self._initial = {a.actor_id: a for a in actors}
        self._locks = {a.actor_id: asyncio.Lock() for a in actors}
        self._leases: dict[str, _Lease] = {}
        self._counts: Counter[str] = Counter()
        self._seconds = 0.0

    def observe_session(self, actor: Actor, session: Session) -> None:
        try:
            hard = datetime.fromisoformat(
                actor.values["absolute_expires_at"].replace("Z", "+00:00")
            )
        except (ValueError, KeyError):
            raise CapacitySessionRejected("absolute_expiry_missing") from None
        if (
            hard.utcoffset() is None
            or session.session.expires_at > hard
            or session.actor.user_id != actor.actor_id
            or actor.values.get("context_id") != session.session.context_id
        ):
            raise CapacitySessionRejected("session_lease_scope_or_expiry")
        lease = _Lease(
            actor, session.session.context_id, session.session.expires_at, hard, datetime.now(UTC)
        )
        self._leases[actor.actor_id] = lease
        self._schedule(lease)

    def _schedule(self, lease: _Lease) -> None:
        from datetime import timedelta

        now = datetime.now(UTC)
        seed = f"{self.profile.seed}:{lease.actor.actor_id}:{lease.renewal_number}".encode()
        jitter = int.from_bytes(content_hasher(seed).digest()[:8], "big") / 2**64
        interval = self.profile.renew_every_seconds * (0.8 + 0.2 * jitter)
        margin = min(30.0, max(1.0, self.profile.renew_every_seconds * 0.1))
        remaining = (min(lease.expires_at, lease.absolute_expires_at) - now).total_seconds()
        lease.next_renew_at = now + timedelta(seconds=max(0.0, min(interval, remaining - margin)))

    def current(self, actor: Actor) -> Actor:
        lease = self._leases.get(actor.actor_id)
        return lease.actor if lease is not None else self._initial[actor.actor_id]

    async def bootstrap(self, client: httpx2.AsyncClient) -> None:
        """Recheck aging preflight leases before starting the business measurement clock."""
        actors = iter(self._initial.values())

        async def worker() -> None:
            while (actor := next(actors, None)) is not None:
                async with self.action(actor, client):
                    pass

        tasks = [
            asyncio.create_task(worker())
            for _ in range(min(32, self.profile.max_inflight, len(self._initial)))
        ]
        try:
            async with asyncio.timeout(300):
                await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    @asynccontextmanager
    async def action(
        self, actor: Actor, client: httpx2.AsyncClient, *, verified: bool = True
    ) -> AsyncIterator[None]:
        """Same-principal actions/renewal serialize; callers include this wait in their deadline."""
        async with self._locks[actor.actor_id]:
            if verified:
                lease = self._leases.get(actor.actor_id)
                if lease is None or lease.failed:
                    self._counts["blocked_actor_actions"] += 1
                    raise CapacitySessionRejected("actor_needs_session_recovery")
                if datetime.now(UTC) >= min(lease.expires_at, lease.absolute_expires_at):
                    lease.failed = True
                    self._counts["expired_leases"] += 1
                    raise CapacitySessionRejected("session_expired")
                if datetime.now(UTC) >= lease.next_renew_at:
                    await self._renew(lease, client)
            yield

    async def _renew(self, lease: _Lease, client: httpx2.AsyncClient) -> None:
        import time

        started = time.monotonic()
        self._counts["http_requests"] += 1
        actor = lease.actor
        headers = {
            "host": self.profile.host,
            "origin": self.profile.origin,
            "cookie": actor.cookie.get_secret_value(),
            "x-csrf-token": actor.csrf.get_secret_value(),
            "content-type": "application/json",
            "accept": "application/json",
        }
        try:
            async with (
                asyncio.timeout(10),
                client.stream("POST", "/v1/auth/renew", headers=headers, content=b"{}") as response,
            ):
                self._counts[f"http_{response.status_code}"] += 1
                if response.status_code != 200:
                    if response.status_code >= 500:
                        self._counts["server_error_outcome_unknown"] += 1
                    raise CapacitySessionRejected("renewal_refused")
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(data) + len(chunk) > 1024 * 1024:
                        raise CapacitySessionRejected("renewal_response_bound")
                    data.extend(chunk)
                session = Session.model_validate_json(data)
                now = datetime.now(UTC)
                if (
                    session.actor.user_id != actor.actor_id
                    or not session.actor.email_verified
                    or session.session.context_id != lease.context_id
                    or not now < session.session.expires_at <= lease.absolute_expires_at
                ):
                    raise CapacitySessionRejected("renewal_scope_or_absolute_expiry")
                self._apply_renewal_cookies(lease, actor, session, response.headers.get_list("set-cookie"))
                lease.expires_at = session.session.expires_at
                lease.renewal_number += 1
                self._schedule(lease)
                self._counts["completed"] += 1
        except asyncio.CancelledError:
            lease.failed = True
            self._counts["interrupted_outcome_unknown"] += 1
            raise
        except (TimeoutError, httpx2.TransportError):
            lease.failed = True
            self._counts["failed"] += 1
            self._counts["transport_outcome_unknown"] += 1
            raise CapacitySessionRejected("renewal_outcome_unknown_needs_recovery") from None
        except CapacitySessionRejected:
            lease.failed = True
            self._counts["failed"] += 1
            raise
        except Exception:
            lease.failed = True
            self._counts["failed"] += 1
            raise CapacitySessionRejected("renewal_failed_needs_recovery") from None
        finally:
            self._seconds += time.monotonic() - started

    def _apply_renewal_cookies(self, lease: _Lease, actor: Actor, session: Session,
                               headers: list[str]) -> None:
        cookies = self._cookies(headers)
        if cookies:
            if not hmac.compare_digest(cookies["__Host-aletheia_csrf"], session.csrf_token):
                raise CapacitySessionRejected("renewal_csrf_mismatch")
            cookie = f"__Host-aletheia_session={cookies['__Host-aletheia_session']}; __Host-aletheia_csrf={cookies['__Host-aletheia_csrf']}"
            candidate = Actor.model_validate(
                {
                    **actor.model_dump(),
                    "cookie": SecretStr(cookie),
                    "csrf": SecretStr(session.csrf_token),
                }
            )
            if hmac.compare_digest(
                candidate.cookie.get_secret_value(), actor.cookie.get_secret_value()
            ):
                raise CapacitySessionRejected("renewal_cookie_did_not_rotate")
            lease.actor = candidate
            self._counts["cookie_rotations"] += 1
        elif not hmac.compare_digest(session.csrf_token, actor.csrf.get_secret_value()):
            raise CapacitySessionRejected("renewal_proof_changed_without_cookie")

    @staticmethod
    def _cookies(headers: list[str]) -> dict[str, str]:
        if not headers:
            return {}
        result = {}
        for header in headers:
            if len(header) > 8192 or any(ord(c) < 32 or ord(c) == 127 for c in header):
                raise CapacitySessionRejected("invalid_renewal_cookie")
            parsed = SimpleCookie()
            parsed.load(header)
            if len(parsed) != 1:
                raise CapacitySessionRejected("invalid_renewal_cookie")
            for name, morsel in parsed.items():
                if (
                    name not in ("__Host-aletheia_session", "__Host-aletheia_csrf")
                    or name in result
                    or morsel["domain"]
                    or morsel["path"] != "/"
                    or not morsel["secure"]
                    or not morsel["httponly"]
                    or morsel["samesite"].lower() != "lax"
                    or not morsel["max-age"].isdecimal()
                    or int(morsel["max-age"]) < 1
                ):
                    raise CapacitySessionRejected("invalid_renewal_cookie_scope")
                result[name] = morsel.value
        if set(result) != {"__Host-aletheia_session", "__Host-aletheia_csrf"}:
            raise CapacitySessionRejected("incomplete_renewal_cookies")
        return result

    def report(self) -> dict[str, object]:
        return {
            "counts": dict(self._counts),
            "seconds_sum": self._seconds,
            "scope": "Native session maintenance, excluded from business action/engaged/success counts. Actor lock/renew wait remains in the original action deadline. Unknown renewal or absolute expiry requires authorized login recovery; hard expiry is never extended.",
        }
