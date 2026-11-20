"""Auth admission port and bounded IdP calls; no HTTP wiring or local rate store."""

from __future__ import annotations

import asyncio
import math
import random
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal, Protocol, TypeVar

from pydantic import SecretStr

from .boundary import CallbackInput
from .login import OidcExchange
from .oidc import AuthorizationAttempt, OidcRejected, OidcUnavailable
from .repositories import RepositoryDataRejected, ValidatedIdentity, repository_call
from .settings import AuthConfigurationRejected

AuthOperation = Literal["login_start", "callback", "renew", "logout", "logout_all"]
_OPERATIONS = frozenset({"login_start", "callback", "renew", "logout", "logout_all"})
_DIGEST = re.compile(r"[0-9a-f]{64}")
T = TypeVar("T")


def _positive(value: float) -> bool:
    return not isinstance(value, bool) and math.isfinite(value) and value > 0


@dataclass(frozen=True)
class RatePolicy:
    global_attempts: int
    subject_attempts: int
    window_seconds: int

    def __post_init__(self) -> None:
        values = (self.global_attempts, self.subject_attempts, self.window_seconds)
        if (
            any(type(value) is not int or value <= 0 for value in values)
            or self.subject_attempts > self.global_attempts or self.window_seconds > 3600
        ):
            raise AuthConfigurationRejected()


@dataclass(frozen=True)
class RateDecision:
    allowed: bool
    retry_after_seconds: int


class AuthAdmissionStore(Protocol):
    """K-05 shared authority, partitioned by deployment/issuer and operation.

    Atomically check global AND subject fixed-window counters using store time;
    increment both only when allowed. Never refund an attempted operation. Bound
    subject cardinality/expiry, serialize workers and retain counts over restart.
    A subject digest is server-derived, never an identity/IP header or raw cookie.
    """

    async def consume(
        self, operation: AuthOperation, subject_digest: SecretStr, policy: RatePolicy,
    ) -> RateDecision: ...


class AuthRateLimited(RuntimeError):
    """Internal result; J-01 owns its eventual HTTP code/Retry-After mapping."""

    def __init__(self, retry_after_seconds: int) -> None:
        self.retry_after_seconds = retry_after_seconds
        super().__init__("Authentication request limit reached.")


class AuthAdmission:
    def __init__(self, store: AuthAdmissionStore, policy: RatePolicy, *, timeout_seconds: float) -> None:
        if not isinstance(policy, RatePolicy) or not _positive(timeout_seconds):
            raise AuthConfigurationRejected()
        self._store, self._policy, self._timeout = store, policy, timeout_seconds

    async def check(self, operation: AuthOperation, subject_digest: SecretStr) -> None:
        if (
            operation not in _OPERATIONS or not isinstance(subject_digest, SecretStr)
            or not _DIGEST.fullmatch(subject_digest.get_secret_value())
        ):
            raise AuthConfigurationRejected()
        async with asyncio.timeout(self._timeout):
            decision = await repository_call(self._store.consume(operation, subject_digest, self._policy))
        if (
            not isinstance(decision, RateDecision) or type(decision.allowed) is not bool
            or type(decision.retry_after_seconds) is not int
            or not 0 <= decision.retry_after_seconds <= self._policy.window_seconds
            or (decision.allowed and decision.retry_after_seconds != 0)
            or (not decision.allowed and decision.retry_after_seconds == 0)
        ):
            raise RepositoryDataRejected()
        if not decision.allowed:
            raise AuthRateLimited(decision.retry_after_seconds)


class BoundedOidc:
    """Wrap one issuer client per event loop, preserving the coordinator protocol.

    No waiting queue, sleep or automatic retry. Every failed exchange remains
    spent. A later admitted request may probe once after bounded backoff/jitter.
    shortcut: cooldown is per worker, use shared admission before every auth call.
    """

    def __init__(
        self, provider: OidcExchange, *, timeout_seconds: float, maximum_inflight: int,
        initial_backoff_seconds: float, maximum_backoff_seconds: float,
        clock: Callable[[], float] = time.monotonic, jitter: Callable[[], float] = random.random,
    ) -> None:
        if (
            not all(_positive(value) for value in (timeout_seconds, initial_backoff_seconds, maximum_backoff_seconds))
            or initial_backoff_seconds > maximum_backoff_seconds or maximum_backoff_seconds > 3600
            or type(maximum_inflight) is not int or not 1 <= maximum_inflight <= 1024
        ):
            raise AuthConfigurationRejected()
        self._provider, self._timeout, self._capacity = provider, timeout_seconds, maximum_inflight
        self._initial, self._maximum = initial_backoff_seconds, maximum_backoff_seconds
        self._clock, self._jitter = clock, jitter
        self._active, self._failures, self._generation = 0, 0, 0
        self._blocked_until, self._probing = 0.0, False

    def _enter(self) -> tuple[int, bool]:
        now = self._clock()
        probe = self._failures > 0
        if (
            not math.isfinite(now) or now < self._blocked_until or self._active >= self._capacity
            or (probe and (self._probing or self._active > 0))
        ):
            raise OidcUnavailable()
        self._active += 1
        if probe:
            self._probing = True
        return self._generation, probe

    def _failed(self) -> None:
        self._failures = min(self._failures + 1, 32)
        self._generation += 1
        cap = min(self._maximum, self._initial * 2 ** (self._failures - 1))
        try:
            sample = self._jitter()
            if isinstance(sample, bool) or not math.isfinite(sample) or not 0 <= sample <= 1:
                sample = 1.0
        except Exception:
            sample = 1.0
        self._blocked_until = max(self._blocked_until, self._clock() + cap * (0.5 + 0.5 * sample))

    async def _call(self, operation: Callable[[], Awaitable[T]], *, confirms_recovery: bool) -> T:
        generation, probe = self._enter()
        try:
            async with asyncio.timeout(self._timeout):
                result = await operation()
        except OidcRejected:
            raise
        except Exception:
            self._failed()
            raise OidcUnavailable() from None
        else:
            if confirms_recovery and generation == self._generation:
                self._failures, self._blocked_until = 0, 0.0
            return result
        finally:
            self._active -= 1
            if probe:
                self._probing = False

    async def authorization_url(self, attempt: AuthorizationAttempt) -> SecretStr:
        # Building a local URL does not prove that the token endpoint recovered.
        return await self._call(lambda: self._provider.authorization_url(attempt), confirms_recovery=False)

    async def exchange(self, callback: CallbackInput, attempt: AuthorizationAttempt) -> ValidatedIdentity:
        return await self._call(lambda: self._provider.exchange(callback, attempt), confirms_recovery=True)
