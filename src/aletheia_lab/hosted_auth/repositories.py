"""Draft INTERNAL K-03 ports for K-05; no SQL, schema, pool or default store.

Implementing a Protocol does not establish atomicity or current authorization.
The production adapters and their DB/concurrency evidence belong to K-05.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol, Self, TypeVar

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from aletheia_lab.hosted_contract.session_capabilities import Actor, Identifier

_DIGEST = re.compile(r"[0-9a-f]{64}")
T = TypeVar("T")


class RepositoryUnavailable(TimeoutError):
    """Fixed safe outage; existing K-02 maps TimeoutError to service_unavailable."""

    def __init__(self) -> None:
        super().__init__("Authentication storage is temporarily unavailable.")


class RepositoryDataRejected(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Authentication storage returned invalid data.")


async def repository_call(operation: Awaitable[T]) -> T:
    """Redact driver diagnostics; owning adapter supplies the total deadline.

    External cancellation propagates because CancelledError is a BaseException.
    """
    try:
        return await operation
    except Exception:
        raise RepositoryUnavailable() from None


class _Facts(BaseModel):
    model_config = ConfigDict(
        extra="forbid", strict=True, frozen=True, hide_input_in_errors=True,
    )

    @field_validator("actor", mode="before", check_fields=False)
    @classmethod
    def _actor(cls, value: object) -> object:
        if isinstance(value, Actor):
            return Actor.model_validate(value.model_dump(warnings=False), strict=True)
        return value


class SessionSnapshot(_Facts):
    """Current authoritative facts, including account status/epoch in one read.

    A rotated credential is a denial/tombstone, never a grant to its replacement.
    Raw cookies/tokens are not stored in this record. Actor fields are server data.
    """

    credential_digest: SecretStr
    actor: Actor = Field(repr=False)
    context_id: Identifier
    csrf_token: SecretStr
    onboarding: Literal["ready", "needs_workspace"]
    last_workspace_id: Identifier | None
    state: Literal["active", "expired", "revoked", "rotated"]
    actor_enabled: bool
    issued_at: datetime
    last_seen_at: datetime
    rotated_at: datetime
    idle_expires_at: datetime
    absolute_expires_at: datetime
    generation: int = Field(ge=0)
    session_epoch: int = Field(ge=0)
    account_epoch: int = Field(ge=0)

    @field_validator("credential_digest")
    @classmethod
    def _digest(cls, value: SecretStr) -> SecretStr:
        if not _DIGEST.fullmatch(value.get_secret_value()):
            raise ValueError("credential digest required")
        return value

    @field_validator("csrf_token")
    @classmethod
    def _csrf(cls, value: SecretStr) -> SecretStr:
        if not 1 <= len(value.get_secret_value()) <= 512:
            raise ValueError("bounded CSRF token required")
        return value

    @field_validator(
        "issued_at", "last_seen_at", "rotated_at", "idle_expires_at", "absolute_expires_at",
    )
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("timezone required")
        return value

    @model_validator(mode="after")
    def _ordering(self) -> Self:
        if not (
            self.issued_at <= self.rotated_at <= self.last_seen_at
            < self.idle_expires_at <= self.absolute_expires_at
        ):
            raise ValueError("inconsistent session deadlines")
        return self


@dataclass(frozen=True)
class SessionRules:
    idle_seconds: int
    absolute_seconds: int
    rotation_seconds: int


@dataclass(frozen=True)
class RotateSession:
    presented_digest: SecretStr
    replacement_digest: SecretStr
    expected_user_id: str
    expected_context_id: str
    expected_generation: int
    expected_account_epoch: int
    rules: SessionRules


class AtomicSessionStore(Protocol):
    """Authoritative, shared store; no process-local production fallback.

    inspect: read credential, session AND current account state/epoch
    consistently without sliding idle expiry. Return rotated/revoked tombstones
    when retained; None for unknown. Never cache grants without invalidation.

    rotate_if_current: in ONE transaction recheck current credential, actor,
    context, generation, both epochs, enabled status, idle/absolute expiry and
    supplied policy at the store's authoritative time. CAS must have one winner.
    Replace digest, increment generation, advance rotated/last-seen time, and
    set idle deadline to min(now+idle, original absolute, issued+absolute policy).
    Preserve actor identity, context, CSRF and original issuance/absolute bound.
    Retain old-digest -> exact context tombstone for revoke races; do not grant
    reads/renewal with it. Return None if CAS or current authorization fails.

    revoke_presented: derive actor/context from credential, never caller IDs.
    Accept a just-rotated credential only for revocation of its still-live
    family. Single logout revokes that exact context, including replacements.
    Logout-all requires current enabled account/family, increments account epoch
    and revokes all its sessions atomically. Serialize with renew and login
    commit. Expired/unknown/already invalid families cannot revoke a different
    or newly authenticated account. Return whether a live family was revoked.

    All I/O must cancel/rollback safely. No external IdP call inside transactions.
    K-05 must decide DB clocks, indexes, alias retention, expiry cleanup and
    restore policy; real adapters must pass DB tests of these invariants.
    """

    async def is_ready(self) -> bool: ...

    async def inspect(self, credential_digest: SecretStr) -> SessionSnapshot | None: ...

    async def rotate_if_current(self, command: RotateSession) -> SessionSnapshot | None: ...

    async def revoke_presented(self, credential_digest: SecretStr, *, all_sessions: bool) -> bool: ...


class ValidatedIdentity(_Facts):
    """Trusted OIDC adapter output ONLY after full library validation.

    This type cannot authenticate input by itself. Never construct it from an
    HTTP body or unverified JWT. Full OIDC verification is a separate K-03 step.
    """

    issuer: str = Field(min_length=1, max_length=2048, repr=False)
    subject: SecretStr
    email: SecretStr | None
    email_verified: bool

    @field_validator("subject")
    @classmethod
    def _subject(cls, value: SecretStr) -> SecretStr:
        text = value.get_secret_value()
        if not 1 <= len(text) <= 255 or any(ord(char) < 32 or ord(char) == 127 for char in text):
            raise ValueError("bounded subject required")
        return value

    @field_validator("email")
    @classmethod
    def _email(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None:
            text = value.get_secret_value()
            if not 3 <= len(text) <= 254 or any(ord(char) <= 32 or ord(char) == 127 for char in text):
                raise ValueError("bounded email required")
        return value


class AccountBinding(_Facts):
    """Current pre-provisioned local account; no role/domain inference."""

    issuer: str = Field(min_length=1, max_length=2048, repr=False)
    subject: SecretStr
    actor: Actor = Field(repr=False)
    enabled: bool
    account_epoch: int = Field(ge=0)
    onboarding: Literal["ready", "needs_workspace"]
    last_workspace_id: Identifier | None


class IdentityDirectory(Protocol):
    """K-04/K-05 current explicit binding, keyed by exact (issuer, subject).

    No automatic creation/linking by email/domain. No implicit membership/owner
    creation. The future login commit must recheck enabled status and epoch in
    its own transaction; a previously loaded AccountBinding is not a grant.
    """

    async def find_identity(self, *, issuer: str, subject: SecretStr) -> AccountBinding | None: ...
