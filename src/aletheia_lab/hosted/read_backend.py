"""Read-only backend logic over explicitly injected authoritative repositories.

This module does not implement cookie verification, OIDC, session creation or
persistence. Real repository adapters must supply current server-side facts;
there is no synthetic or in-memory production fallback.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from pydantic import SecretStr

from aletheia_lab.hosted_contract.session_capabilities import (
    Actor,
    Capabilities,
    Limits,
    Membership,
    Session,
)

from .application import VERSION, AccessDenied, HostedBackend


@dataclass(frozen=True)
class SessionRecord:
    """Internal repository facts, never a client-supplied authentication DTO.

    expires_at is the effective expiry supplied by the server session store.
    The maintained auth/session adapter must already enforce its full idle,
    absolute, renewal and revocation policy; this layer also rejects stale facts.
    """

    actor: Actor
    context_id: str
    expires_at: datetime
    csrf_token: SecretStr
    onboarding: str
    last_workspace_id: str | None
    state: str
    actor_enabled: bool


@dataclass(frozen=True)
class WorkspaceAccessRecord:
    """Current same-scope session and membership snapshot from the repository.

    Revoked membership records describe this actor's prior grant only. A new
    or unauthorized workspace must return None, without its private metadata.
    """

    session: SessionRecord
    workspace_id: str
    workspace_name: str
    membership: Membership
    membership_active: bool


class SessionRepository(Protocol):
    """A maintained auth/session adapter must verify cookies before returning facts."""

    async def is_ready(self) -> bool: ...

    async def resolve_cookie(self, cookie: bytes) -> SessionRecord | None: ...


class WorkspaceAccessRepository(Protocol):
    """Read session and membership consistently from their real source of truth.

    Implementations must recheck the current session/account and membership in
    the same scoped read/transaction. They must not trust a previously loaded
    role, actor headers or browser-provided grant; no unbounded grant cache.
    For a known same-actor membership, retain session/membership state so the
    backend can distinguish expiry/revocation rather than silently granting it.
    All I/O needs deadlines. A Protocol is not evidence of these properties.
    """

    async def is_ready(self) -> bool: ...

    async def access_for_context(
        self, *, user_id: str, context_id: str, workspace_id: str,
    ) -> WorkspaceAccessRecord | None: ...


def _utcnow() -> datetime:
    return datetime.now(UTC)


class ReadOnlyHostedBackend(HostedBackend):
    """Session/capability reads only; every unimplemented product action stays off."""

    def __init__(
        self,
        sessions: SessionRepository,
        workspaces: WorkspaceAccessRepository,
        *,
        limits: Limits,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._sessions = sessions
        self._workspaces = workspaces
        # Revalidate even already-constructed frozen models/model_copy updates.
        self._limits = Limits.model_validate(limits.model_dump(warnings=False), strict=True)
        self._clock = clock

    async def is_ready(self) -> bool:
        try:
            return await self._sessions.is_ready() is True and await self._workspaces.is_ready() is True
        except Exception:
            return False

    def _session(self, record: SessionRecord) -> Session:
        if record.state == "expired":
            raise AccessDenied("session_expired")
        if record.state != "active" or record.actor_enabled is not True:
            raise AccessDenied("session_revoked")
        now = self._clock()
        if record.expires_at.utcoffset() is None or now.utcoffset() is None:
            raise ValueError("server session timestamps must include timezones")
        if record.expires_at <= now:
            raise AccessDenied("session_expired")
        return Session.model_validate({
            "schema_version": VERSION,
            "actor": record.actor.model_dump(warnings=False),
            "session": {"context_id": record.context_id, "expires_at": record.expires_at},
            "csrf_token": record.csrf_token.get_secret_value(),
            "onboarding": record.onboarding,
            "last_workspace_id": record.last_workspace_id,
        }, strict=True)

    async def load_session(self, cookie: bytes | None) -> Session:
        if not cookie:
            raise AccessDenied("session_expired")
        record = await self._sessions.resolve_cookie(cookie)
        if record is None:
            raise AccessDenied("session_expired")
        return self._session(record)

    async def capabilities(self, session: Session, workspace_id: str) -> Capabilities:
        access = await self._workspaces.access_for_context(
            user_id=session.actor.user_id,
            context_id=session.session.context_id,
            workspace_id=workspace_id,
        )
        if access is None:
            raise AccessDenied("permission_denied")
        if (
            access.workspace_id != workspace_id
            or access.session.actor.user_id != session.actor.user_id
            or access.session.context_id != session.session.context_id
        ):
            raise AccessDenied("permission_denied")
        # Re-read facts are checked again: a once-valid Session is not a grant.
        self._session(access.session)
        if access.membership_active is not True:
            raise AccessDenied("membership_revoked")
        return Capabilities.model_validate({
            "schema_version": VERSION,
            "workspace_id": access.workspace_id,
            "workspace_name": access.workspace_name,
            "membership": access.membership.model_dump(warnings=False),
            "deployment_mode": "hosted",
            "analysis_backends": [],
            "supported_source_types": [],
            "supported_export_formats": [],
            "allowed_actions": [],
            "features": {},
            "limits": self._limits.model_dump(),
        }, strict=True)
