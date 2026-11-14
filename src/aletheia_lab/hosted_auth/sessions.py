"""K-02 SessionRepository adapter and INTERNAL renew/revoke orchestration.

Requires an injected authoritative atomic store. No store, HTTP mutation route,
login issuer, CSRF guard or runtime is created here. Route owners must enforce
CSRF/origin/admission before invoking mutations and fence late responses.
"""

from __future__ import annotations

import asyncio
import hmac
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from pydantic import SecretStr, ValidationError

from aletheia_lab.hosted.application import AccessDenied
from aletheia_lab.hosted.read_backend import SessionRecord
from aletheia_lab.project.identity import content_hasher

from .boundary import SESSION_COOKIE, AuthInputRejected, new_opaque_secret, read_opaque_cookie
from .repositories import (
    AtomicSessionStore,
    RepositoryDataRejected,
    RotateSession,
    SessionRules,
    SessionSnapshot,
    repository_call,
)
from .settings import AuthPolicy


class RenewalConflict(RuntimeError):
    """Internal outcome; HTTP mapping/client recovery still needs J-01 review."""

    def __init__(self) -> None:
        super().__init__("Session renewal did not acquire the current generation.")


@dataclass(frozen=True)
class SessionRenewal:
    record: SessionRecord = field(repr=False)
    # None means no rotation, and the route must emit no Set-Cookie.
    credential: SecretStr | None


def credential_digest(value: SecretStr) -> SecretStr:
    """Domain-separated digest; raw cookie never reaches a repository port."""
    raw = b"aletheia-session-v1\x00" + value.get_secret_value().encode("ascii")
    return SecretStr(content_hasher(raw).hexdigest())


def _utcnow() -> datetime:
    return datetime.now(UTC)


class ServerSessions:
    """Read adapter plus bounded lifecycle calls; no in-memory auth fallback."""

    def __init__(
        self, store: AtomicSessionStore, policy: AuthPolicy, *,
        timeout_seconds: float, maximum_cookie_bytes: int,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        if isinstance(timeout_seconds, bool) or not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("positive finite session deadline required")
        if type(maximum_cookie_bytes) is not int or maximum_cookie_bytes <= 0:
            raise ValueError("positive cookie byte limit required")
        self._store = store
        self._policy = AuthPolicy.model_validate(policy.model_dump(warnings=False))
        self._timeout = timeout_seconds
        self._maximum_cookie_bytes = maximum_cookie_bytes
        self._clock = clock

    async def is_ready(self) -> bool:
        try:
            async with asyncio.timeout(self._timeout):
                return await repository_call(self._store.is_ready()) is True
        except Exception:
            return False

    def _digest(self, cookie: bytes | None) -> SecretStr | None:
        try:
            token = read_opaque_cookie(cookie, name=SESSION_COOKIE, maximum_bytes=self._maximum_cookie_bytes)
        except AuthInputRejected:
            return None
        return credential_digest(token) if token is not None else None

    def _now(self) -> datetime:
        now = self._clock()
        if now.utcoffset() is None:
            raise RepositoryDataRejected()
        return now

    def _effective_expiry(self, snapshot: SessionSnapshot) -> datetime:
        return min(
            snapshot.idle_expires_at,
            snapshot.absolute_expires_at,
            snapshot.last_seen_at + timedelta(seconds=self._policy.idle_seconds),
            snapshot.issued_at + timedelta(seconds=self._policy.absolute_seconds),
        )

    def _checked(self, snapshot: SessionSnapshot, digest: SecretStr, *, renewal: bool = False) -> SessionSnapshot:
        try:
            snapshot = SessionSnapshot.model_validate(snapshot.model_dump(warnings=False))
        except (ValidationError, AttributeError, TypeError):
            raise RepositoryDataRejected() from None
        if not hmac.compare_digest(snapshot.credential_digest.get_secret_value(), digest.get_secret_value()):
            raise RepositoryDataRejected()
        if snapshot.state == "expired":
            raise AccessDenied("session_expired")
        if snapshot.state == "revoked" or not snapshot.actor_enabled or snapshot.session_epoch != snapshot.account_epoch:
            raise AccessDenied("session_revoked")
        if snapshot.state == "rotated":
            if renewal:
                raise RenewalConflict()
            raise AccessDenied("session_revoked")
        now = self._now()
        if snapshot.last_seen_at > now + timedelta(seconds=self._policy.clock_skew_seconds):
            raise RepositoryDataRejected()
        if self._effective_expiry(snapshot) <= now:
            raise AccessDenied("session_expired")
        if self._policy.require_verified_email and not snapshot.actor.email_verified:
            raise AccessDenied("session_revoked")
        return snapshot

    def _record(self, snapshot: SessionSnapshot) -> SessionRecord:
        return SessionRecord(
            actor=snapshot.actor, context_id=snapshot.context_id,
            expires_at=self._effective_expiry(snapshot), csrf_token=snapshot.csrf_token,
            onboarding=snapshot.onboarding, last_workspace_id=snapshot.last_workspace_id,
            state="active", actor_enabled=True,
        )

    async def resolve_cookie(self, cookie: bytes) -> SessionRecord | None:
        digest = self._digest(cookie)
        if digest is None:
            return None
        async with asyncio.timeout(self._timeout):
            snapshot = await repository_call(self._store.inspect(digest))
            if snapshot is None:
                return None
            return self._record(self._checked(snapshot, digest))

    async def renew(self, cookie: bytes | None) -> SessionRenewal:
        """Explicit mutation only; the HTTP caller must first enforce CSRF."""
        digest = self._digest(cookie)
        if digest is None:
            raise AccessDenied("session_expired")
        async with asyncio.timeout(self._timeout):
            before = await repository_call(self._store.inspect(digest))
            if before is None:
                raise AccessDenied("session_expired")
            before = self._checked(before, digest, renewal=True)
            if self._now() < before.rotated_at + timedelta(seconds=self._policy.rotation_seconds):
                return SessionRenewal(self._record(before), None)
            token = new_opaque_secret()
            replacement = credential_digest(token)
            command = RotateSession(
                digest, replacement, before.actor.user_id, before.context_id,
                before.generation, before.account_epoch,
                SessionRules(self._policy.idle_seconds, self._policy.absolute_seconds, self._policy.rotation_seconds),
            )
            after = await repository_call(self._store.rotate_if_current(command))
            if after is None:
                raise RenewalConflict()
            after = self._checked(after, replacement)
            self._check_rotation(before, after)
            return SessionRenewal(self._record(after), token)

    @staticmethod
    def _check_rotation(before: SessionSnapshot, after: SessionSnapshot) -> None:
        if (
            after.actor.user_id != before.actor.user_id
            or after.context_id != before.context_id
            or after.session_epoch != before.session_epoch
            or after.account_epoch != before.account_epoch
            or after.generation != before.generation + 1
            or after.issued_at != before.issued_at
            or after.absolute_expires_at > before.absolute_expires_at
            or after.rotated_at < before.rotated_at
            or after.last_seen_at < before.last_seen_at
            or not hmac.compare_digest(
                after.csrf_token.get_secret_value().encode("utf-8"),
                before.csrf_token.get_secret_value().encode("utf-8"),
            )
        ):
            raise RepositoryDataRejected()

    async def logout(self, cookie: bytes | None) -> None:
        """Idempotent single-context revocation, including a just-rotated alias."""
        digest = self._digest(cookie)
        if digest is not None:
            async with asyncio.timeout(self._timeout):
                await repository_call(self._store.revoke_presented(digest, all_sessions=False))

    async def logout_all(self, cookie: bytes | None) -> None:
        """Store derives the actor; HTTP CSRF/reauth policy is required upstream."""
        digest = self._digest(cookie)
        if digest is None:
            raise AccessDenied("session_expired")
        async with asyncio.timeout(self._timeout):
            revoked = await repository_call(self._store.revoke_presented(digest, all_sessions=True))
            if revoked is not True:
                raise AccessDenied("session_revoked")
