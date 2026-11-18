"""Internal login coordinator over required atomic K-05 transaction ports.

No HTTP route, default store, SQL or provisioning. HTTP callers must enforce
same-origin login initiation/admission; production CSRF creation is injected.
OIDC calls occur outside DB transactions. A claimed callback is never released
for retry after an error; recovery starts a fresh explicit login intent.
"""

from __future__ import annotations

import asyncio
import hmac
import math
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal, Protocol, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    ValidationError,
    field_validator,
    model_validator,
)

from aletheia_lab.hosted.content_hashing import content_hasher
from aletheia_lab.hosted.settings import RuntimeSettings
from aletheia_lab.hosted_contract.session_capabilities import Identifier

from ._diagnostics import auth_stage
from .boundary import (
    LOGIN_COOKIE,
    SESSION_COOKIE,
    AuthInputRejected,
    CallbackInput,
    allowed_return_to,
    new_opaque_secret,
    parse_callback_query,
    read_opaque_cookie,
)
from .identity import AccountResolver
from .oidc import AuthorizationAttempt, OidcUnavailable, new_authorization_attempt
from .repositories import (
    AccountBinding,
    RepositoryDataRejected,
    SessionRules,
    SessionSnapshot,
    ValidatedIdentity,
    repository_call,
)
from .sessions import credential_digest
from .settings import AuthConfigurationRejected, AuthPolicy, validate_return_path

_DIGEST = re.compile(r"[0-9a-f]{64}")
_RANDOM = re.compile(r"[A-Za-z0-9_-]{43,128}")
_PKCE = re.compile(r"[A-Za-z0-9._~-]{43,128}")


LoginRejectionReason = Literal["invalid_attempt", "provider_cancelled"]


class LoginRejected(ValueError):
    """Typed internal outcome; J-01 owns public presentation and recovery."""

    def __init__(self, reason: LoginRejectionReason = "invalid_attempt") -> None:
        self.reason = reason
        super().__init__("The sign-in attempt is invalid or no longer current.")


def _reject_provider_response(callback: CallbackInput) -> None:
    if not callback.provider_denied:
        return
    if callback.provider_disposition == "cancelled":
        raise LoginRejected("provider_cancelled") from None
    if callback.provider_disposition == "unavailable":
        raise OidcUnavailable() from None
    raise LoginRejected() from None


def _digest(value: SecretStr, purpose: str) -> SecretStr:
    raw = ("aletheia-" + purpose + "-v1\x00").encode("ascii")
    return SecretStr(content_hasher(raw + value.get_secret_value().encode("ascii")).hexdigest())


def _same(one: SecretStr, two: SecretStr) -> bool:
    return hmac.compare_digest(one.get_secret_value().encode("utf-8"), two.get_secret_value().encode("utf-8"))


class LoginIntent(BaseModel):
    """Trusted stored intent; browser/state are digests, PKCE/nonce stay server-side.

    K-05 must encrypt/protect verifier/nonce at rest and keep intent metadata
    immutable. This internal model is not a browser request/response schema.
    """

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)

    intent_id: Identifier
    browser_digest: SecretStr
    state_digest: SecretStr
    issuer: str = Field(min_length=1, max_length=2048, repr=False)
    client_id: str = Field(min_length=1, max_length=256, repr=False)
    redirect_uri: str = Field(min_length=1, max_length=2048, repr=False)
    nonce: SecretStr
    code_verifier: SecretStr
    created_at: datetime
    expires_at: datetime
    browser_epoch: int = Field(ge=1)
    return_to: str = Field(repr=False)
    reauthenticate: bool
    previous_context_id: Identifier | None
    phase: Literal["pending", "claimed", "committed", "rejected"]

    @field_validator("browser_digest", "state_digest")
    @classmethod
    def _hash(cls, value: SecretStr) -> SecretStr:
        if not _DIGEST.fullmatch(value.get_secret_value()):
            raise ValueError("digest required")
        return value

    @field_validator("nonce", "code_verifier")
    @classmethod
    def _opaque(cls, value: SecretStr) -> SecretStr:
        if not _PKCE.fullmatch(value.get_secret_value()):
            raise ValueError("opaque transaction proof required")
        return value

    @field_validator("created_at", "expires_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("timezone required")
        return value

    @field_validator("return_to")
    @classmethod
    def _destination(cls, value: str) -> str:
        return validate_return_path(value)

    @model_validator(mode="after")
    def _window(self) -> Self:
        if not timedelta(0) < self.expires_at - self.created_at <= timedelta(seconds=600):
            raise ValueError("bounded login transaction required")
        if not _RANDOM.fullmatch(self.nonce.get_secret_value()):
            raise ValueError("nonce required")
        return self


@dataclass(frozen=True)
class BeginLogin:
    intent_id: str
    browser_digest: SecretStr
    state_digest: SecretStr
    issuer: str
    client_id: str
    redirect_uri: str
    nonce: SecretStr
    code_verifier: SecretStr
    created_at: datetime
    expires_at: datetime
    return_to: str = field(repr=False)
    reauthenticate: bool
    previous_credential_digest: SecretStr | None


@dataclass(frozen=True)
class CommitLogin:
    intent_id: str
    browser_digest: SecretStr
    state_digest: SecretStr
    claim_digest: SecretStr
    expected_browser_epoch: int
    account: AccountBinding = field(repr=False)
    credential_digest: SecretStr
    context_id: str
    csrf_token: SecretStr
    rules: SessionRules
    require_verified_email: bool


class LoginTransactionStore(Protocol):
    """Atomic K-05 port, coordinated with AtomicSessionStore's source of truth.

    begin_intent: validate prior cookie digest against current server facts,
    derive prior context; create/update the browser binding and increment its
    intent epoch, superseding every older pending/claimed intent for that binding.
    Persist BEFORE returning. Never use browser binding as authenticated identity.

    claim_intent: ONE transaction verifies exact browser+state hashes, newest
    intent/epoch, pending phase and store-clock expiry; moves pending -> claimed
    with the unique supplied claim digest. No release/retry/second winner.
    A wrong browser must not consume the rightful browser's intent.

    commit_login: ONE transaction rechecks claim/epoch/newest intent/deadline,
    current exact issuer+subject binding, enabled/email policy and account epoch.
    Deny if account logout-all/credential-change watermark is at or after intent
    creation, even if lookup already observed the new epoch. Recheck captured
    prior context remains live; serialize with logout/revoke/newer login intent.
    Retire prior browser context and its rotated credentials, create ONLY the
    fresh server-generated context/digest, copy current safe account/onboarding
    facts and mark intent committed atomically. Issued/idle/absolute timestamps
    come from the agreed authoritative clock and supplied bounded policy.

    None means denied, not success. No IdP/network/client wait inside transactions.
    Claim remains spent after error/timeout/cancellation; lost commit reply may
    have an effect but must not produce a second session on callback replay.
    Expiry/quotas/cleanup, clocks and multi-process DB proof belong to K-05.
    """

    async def begin_intent(self, command: BeginLogin) -> LoginIntent | None: ...

    async def claim_intent(
        self, *, browser_digest: SecretStr, state_digest: SecretStr, claim_digest: SecretStr,
    ) -> LoginIntent | None: ...

    async def commit_login(self, command: CommitLogin) -> SessionSnapshot | None: ...


class OidcExchange(Protocol):
    """Use AuthlibOidcClient in production; synthetic implementations are tests."""

    async def authorization_url(self, attempt: AuthorizationAttempt) -> SecretStr: ...

    async def exchange(self, callback: CallbackInput, attempt: AuthorizationAttempt) -> ValidatedIdentity: ...


@dataclass(frozen=True)
class LoginStart:
    authorization_url: SecretStr
    browser_credential: SecretStr
    expires_at: datetime


@dataclass(frozen=True)
class LoginCompletion:
    session: SessionSnapshot = field(repr=False)
    credential: SecretStr
    return_to: str = field(repr=False)


def _utcnow() -> datetime:
    return datetime.now(UTC)


class LoginCoordinator:
    """No process-local intent state; repeated/new instances use the same store."""

    def __init__(
        self, store: LoginTransactionStore, oidc: OidcExchange, accounts: AccountResolver,
        runtime: RuntimeSettings, policy: AuthPolicy, *,
        csrf_factory: Callable[[str], SecretStr], timeout_seconds: float,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._policy = AuthPolicy.model_validate(policy.model_dump(warnings=False))
        self._runtime = RuntimeSettings.model_validate(runtime.model_dump(warnings=False))
        self._policy.require_runtime(self._runtime)
        if (
            isinstance(timeout_seconds, bool) or not math.isfinite(timeout_seconds)
            or timeout_seconds <= self._policy.idp_timeout_seconds
            or timeout_seconds > self._runtime.api.backend_timeout_seconds
        ):
            raise AuthConfigurationRejected()
        self._store, self._oidc, self._accounts = store, oidc, accounts
        self._csrf_factory, self._timeout, self._clock = csrf_factory, timeout_seconds, clock

    def _now(self) -> datetime:
        value = self._clock()
        if value.utcoffset() is None:
            raise AuthConfigurationRejected()
        return value

    def _cookie(self, cookie: bytes | None, name: str) -> SecretStr | None:
        try:
            return read_opaque_cookie(cookie, name=name, maximum_bytes=self._runtime.api.maximum_cookie_bytes)
        except AuthInputRejected:
            raise LoginRejected() from None

    def _intent(
        self, value: LoginIntent, *, browser: SecretStr, state: SecretStr,
        phase: Literal["pending", "claimed"],
    ) -> LoginIntent:
        try:
            value = LoginIntent.model_validate(value.model_dump(warnings=False))
        except (ValidationError, AttributeError, TypeError):
            raise RepositoryDataRejected() from None
        identity = self._runtime.identity
        if (
            not _same(value.browser_digest, browser) or not _same(value.state_digest, state)
            or value.issuer != identity.issuer or value.client_id != identity.client_id
            or value.redirect_uri != identity.redirect_uri or value.phase != phase
        ):
            raise RepositoryDataRejected()
        now = self._now()
        if not value.created_at <= now < value.expires_at:
            raise LoginRejected()
        if value.expires_at - value.created_at > timedelta(seconds=self._policy.login_transaction_seconds):
            raise RepositoryDataRejected()
        try:
            allowed_return_to(value.return_to, self._policy)
        except AuthInputRejected:
            raise LoginRejected() from None
        return value

    async def begin(
        self, cookie: bytes | None, *, return_to: str | None = None, reauthenticate: bool = False,
    ) -> LoginStart:
        """Internal; same-origin initiation and admission must precede this call."""
        destination = allowed_return_to(return_to, self._policy)
        browser = self._cookie(cookie, LOGIN_COOKIE) or new_opaque_secret()
        previous = self._cookie(cookie, SESSION_COOKIE)
        attempt = new_authorization_attempt(self._policy, now=self._now(), reauthenticate=reauthenticate)
        identity = self._runtime.identity
        command = BeginLogin(
            "login_" + new_opaque_secret().get_secret_value(),
            _digest(browser, "login-browser"), _digest(attempt.state, "login-state"),
            identity.issuer, identity.client_id, identity.redirect_uri,
            attempt.nonce, attempt.code_verifier, attempt.created_at, attempt.expires_at,
            destination, reauthenticate, credential_digest(previous) if previous is not None else None,
        )
        async with asyncio.timeout(self._timeout):
            with auth_stage("start", "begin.oidc"):
                url = await self._oidc.authorization_url(attempt)
            with auth_stage("start", "begin.intent"):
                intent = await repository_call(self._store.begin_intent(command))
            if intent is None:
                raise LoginRejected()
            intent = self._intent(intent, browser=command.browser_digest, state=command.state_digest, phase="pending")
            self._begun_matches(intent, command)
        return LoginStart(url, browser, intent.expires_at)

    @staticmethod
    def _begun_matches(intent: LoginIntent, command: BeginLogin) -> None:
        if (
            intent.intent_id != command.intent_id or intent.created_at != command.created_at
            or intent.expires_at != command.expires_at or intent.return_to != command.return_to
            or intent.reauthenticate != command.reauthenticate
            or not _same(intent.nonce, command.nonce) or not _same(intent.code_verifier, command.code_verifier)
        ):
            raise RepositoryDataRejected()

    async def complete(self, cookie: bytes | None, query: bytes) -> LoginCompletion:
        browser = self._cookie(cookie, LOGIN_COOKIE)
        if browser is None:
            raise LoginRejected()
        try:
            callback = parse_callback_query(query, expected_issuer=self._runtime.identity.issuer, policy=self._policy)
        except AuthInputRejected:
            raise LoginRejected() from None
        browser_hash, state_hash = _digest(browser, "login-browser"), _digest(callback.state, "login-state")
        claim = _digest(new_opaque_secret(), "login-claim")
        async with asyncio.timeout(self._timeout):
            with auth_stage("callback", "callback.claim"):
                intent = await repository_call(self._store.claim_intent(
                    browser_digest=browser_hash, state_digest=state_hash, claim_digest=claim,
                ))
            if intent is None:
                raise LoginRejected()
            intent = self._intent(intent, browser=browser_hash, state=state_hash, phase="claimed")
            _reject_provider_response(callback)
            attempt = AuthorizationAttempt(
                callback.state, intent.nonce, intent.code_verifier,
                intent.created_at, intent.expires_at, intent.reauthenticate,
            )
            with auth_stage("callback", "callback.exchange"):
                identity = await self._oidc.exchange(callback, attempt)
            with auth_stage("callback", "callback.identity"):
                account = await self._accounts.resolve(identity)
            if account.issuer != intent.issuer or not _same(account.subject, identity.subject):
                raise RepositoryDataRejected()
            # Recheck deadline/policy after potentially slow external work.
            self._intent(intent, browser=browser_hash, state=state_hash, phase="claimed")
            token = new_opaque_secret()
            context = "ctx_" + new_opaque_secret().get_secret_value()
            csrf = self._csrf_factory(context)
            if not isinstance(csrf, SecretStr) or not 1 <= len(csrf.get_secret_value()) <= 512:
                raise AuthConfigurationRejected()
            command = CommitLogin(
                intent.intent_id, browser_hash, state_hash, claim, intent.browser_epoch,
                account, credential_digest(token), context, csrf,
                SessionRules(self._policy.idle_seconds, self._policy.absolute_seconds, self._policy.rotation_seconds),
                self._policy.require_verified_email,
            )
            with auth_stage("callback", "callback.commit"):
                snapshot = await repository_call(self._store.commit_login(command))
            if snapshot is None:
                raise LoginRejected()
            snapshot = self._committed(snapshot, command, intent)
        return LoginCompletion(snapshot, token, intent.return_to)

    def _committed(self, snapshot: SessionSnapshot, command: CommitLogin, intent: LoginIntent) -> SessionSnapshot:
        try:
            snapshot = SessionSnapshot.model_validate(snapshot.model_dump(warnings=False))
        except (ValidationError, AttributeError, TypeError):
            raise RepositoryDataRejected() from None
        if (
            snapshot.state != "active" or not snapshot.actor_enabled
            or snapshot.actor.user_id != command.account.actor.user_id
            or snapshot.context_id != command.context_id or snapshot.context_id == intent.previous_context_id
            or not _same(snapshot.credential_digest, command.credential_digest)
            or not _same(snapshot.csrf_token, command.csrf_token)
            or snapshot.generation != 0 or snapshot.session_epoch != command.account.account_epoch
            or snapshot.session_epoch != snapshot.account_epoch
        ):
            raise RepositoryDataRejected()
        now = self._now()
        if now >= intent.expires_at:
            raise LoginRejected()
        if not intent.created_at <= snapshot.issued_at == snapshot.last_seen_at == snapshot.rotated_at <= now + timedelta(seconds=self._policy.clock_skew_seconds):
            raise RepositoryDataRejected()
        if (
            snapshot.idle_expires_at <= now
            or snapshot.idle_expires_at > snapshot.last_seen_at + timedelta(seconds=command.rules.idle_seconds)
            or snapshot.absolute_expires_at > snapshot.issued_at + timedelta(seconds=command.rules.absolute_seconds)
        ):
            raise RepositoryDataRejected()
        if self._policy.require_verified_email and not snapshot.actor.email_verified:
            raise RepositoryDataRejected()
        return snapshot
