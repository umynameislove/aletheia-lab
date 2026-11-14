"""Synthetic shared admission and controlled IdP schedules; no network or DB."""

import asyncio
from datetime import UTC, datetime

import pytest
from pydantic import SecretStr

from aletheia_lab.hosted_auth.abuse import (
    AuthAdmission,
    AuthRateLimited,
    BoundedOidc,
    RateDecision,
    RatePolicy,
)
from aletheia_lab.hosted_auth.boundary import CallbackInput
from aletheia_lab.hosted_auth.login import LoginRejected
from aletheia_lab.hosted_auth.oidc import OidcRejected, OidcUnavailable, new_authorization_attempt
from aletheia_lab.hosted_auth.repositories import (
    RepositoryDataRejected,
    RepositoryUnavailable,
    ValidatedIdentity,
)
from aletheia_lab.hosted_auth.settings import AuthConfigurationRejected, AuthPolicy

POLICY = AuthPolicy(default_return_to="/app", return_to_paths=("/app",))
IDENTITY = ValidatedIdentity(issuer="https://idp.example.invalid", subject=SecretStr("synthetic-subject"), email=None, email_verified=True)


class SyntheticAdmissionStore:
    """Test double only; the real shared atomic store belongs to K-05."""

    def __init__(self):
        self.lock = asyncio.Lock()
        self.now, self.calls = 0, 0
        self.counts = {}

    async def consume(self, operation, subject_digest, policy):
        self.calls += 1
        async with self.lock:
            window = self.now // policy.window_seconds
            global_key = (operation, window, "global")
            subject_key = (operation, window, subject_digest.get_secret_value())
            if (self.counts.get(global_key, 0) >= policy.global_attempts
                    or self.counts.get(subject_key, 0) >= policy.subject_attempts):
                return RateDecision(False, policy.window_seconds - self.now % policy.window_seconds)
            self.counts[global_key] = self.counts.get(global_key, 0) + 1
            self.counts[subject_key] = self.counts.get(subject_key, 0) + 1
            return RateDecision(True, 0)


def test_shared_atomic_admission_bounds_two_instances_and_makes_no_denied_effect():
    async def scenario():
        store = SyntheticAdmissionStore()
        gates = [AuthAdmission(store, RatePolicy(3, 2, 10), timeout_seconds=1.0) for _ in range(2)]
        effects = []

        async def attempt(index):
            try:
                await gates[index % 2].check("callback", SecretStr("a" * 64 if index < 8 else "b" * 64))
            except AuthRateLimited as error:
                assert error.retry_after_seconds == 10
                return
            effects.append(index)

        await asyncio.gather(*(attempt(index) for index in range(16)))
        assert len(effects) == 3
        assert sum(index < 8 for index in effects) == 2
        store.now = 10
        await gates[0].check("callback", SecretStr("a" * 64))
        await gates[0].check("login_start", SecretStr("a" * 64))
    asyncio.run(scenario())


@pytest.mark.parametrize("operation,digest", [("untrusted-route", "a" * 64), ("callback", "raw-cookie"), ("callback", "A" * 64)])
def test_admission_rejects_untrusted_scope_before_store(operation, digest):
    async def scenario():
        store = SyntheticAdmissionStore()
        gate = AuthAdmission(store, RatePolicy(3, 2, 10), timeout_seconds=1.0)
        with pytest.raises(AuthConfigurationRejected):
            await gate.check(operation, SecretStr(digest))
        assert store.calls == 0
    asyncio.run(scenario())


@pytest.mark.parametrize("decision", [None, {}, RateDecision(True, 1), RateDecision(False, 0), RateDecision(False, 11), RateDecision(True, False), RateDecision(1, 0)])
def test_corrupt_admission_answer_never_grants(decision):
    class Store:
        async def consume(self, *args):
            return decision

    async def scenario():
        gate = AuthAdmission(Store(), RatePolicy(3, 2, 10), timeout_seconds=1.0)
        with pytest.raises(RepositoryDataRejected):
            await gate.check("callback", SecretStr("a" * 64))
    asyncio.run(scenario())


def test_admission_outage_timeout_and_cancellation_fail_closed():
    class Store:
        error = RuntimeError("synthetic-private-storage-value")

        async def consume(self, *args):
            if self.error is not None:
                raise self.error
            await asyncio.Event().wait()

    async def scenario():
        store = Store()
        gate = AuthAdmission(store, RatePolicy(3, 2, 10), timeout_seconds=0.01)
        with pytest.raises(RepositoryUnavailable) as caught:
            await gate.check("callback", SecretStr("a" * 64))
        assert "synthetic-private-storage-value" not in repr(caught.value)
        assert caught.value.__suppress_context__ is True
        store.error = None
        with pytest.raises(TimeoutError):
            await gate.check("callback", SecretStr("a" * 64))
        store.error = asyncio.CancelledError()
        with pytest.raises(asyncio.CancelledError):
            await gate.check("callback", SecretStr("a" * 64))
    asyncio.run(scenario())


@pytest.mark.parametrize("values", [(0, 1, 10), (True, 1, 10), (1, 2, 10), (3, 2, 3601)])
def test_invalid_rate_policy_cannot_start(values):
    with pytest.raises(AuthConfigurationRejected):
        RatePolicy(*values)


class SyntheticProvider:
    def __init__(self):
        self.calls, self.active, self.peak = 0, 0, 0
        self.release = asyncio.Event()
        self.release.set()
        self.error = None

    async def exchange(self, callback, attempt):
        self.calls += 1
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            await self.release.wait()
            if self.error is not None:
                raise self.error
            return IDENTITY
        finally:
            self.active -= 1

    async def authorization_url(self, attempt):
        await self.exchange(None, attempt)
        return SecretStr("https://idp.example.invalid/authorize")


def _system(*, sample=1.0, capacity=2, timeout=1.0):
    provider = SyntheticProvider()
    clock = [100.0]
    guard = BoundedOidc(provider, timeout_seconds=timeout, maximum_inflight=capacity,
                        initial_backoff_seconds=2.0, maximum_backoff_seconds=8.0,
                        clock=lambda: clock[0], jitter=lambda: sample)
    attempt = new_authorization_attempt(POLICY, now=datetime.now(UTC))
    callback = CallbackInput(attempt.state, SecretStr("synthetic-code"), False)
    return provider, guard, clock, attempt, callback


def test_idp_concurrency_has_no_queue_and_overload_never_calls_provider():
    async def scenario():
        provider, guard, _, attempt, callback = _system()
        provider.release.clear()
        tasks = [asyncio.create_task(guard.exchange(callback, attempt)) for _ in range(20)]
        await asyncio.sleep(0)
        assert provider.calls == provider.peak == 2
        provider.release.set()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        assert sum(isinstance(value, OidcUnavailable) for value in results) == 18
        assert sum(value is IDENTITY for value in results) == 2
        assert await guard.exchange(callback, attempt) is IDENTITY
    asyncio.run(scenario())


@pytest.mark.parametrize("sample,expected", [(0.0, 1.0), (0.5, 1.5), (1.0, 2.0), (float("nan"), 2.0), (-1.0, 2.0)])
def test_backoff_is_bounded_jittered_and_never_retries_the_failed_call(sample, expected):
    async def scenario():
        provider, guard, clock, attempt, callback = _system(sample=sample)
        provider.error = RuntimeError("synthetic-private-idp-value")
        with pytest.raises(OidcUnavailable) as caught:
            await guard.exchange(callback, attempt)
        assert "synthetic-private-idp-value" not in repr(caught.value)
        assert caught.value.__suppress_context__ is True
        assert provider.calls == 1
        assert guard._blocked_until == 100 + expected
        with pytest.raises(OidcUnavailable):
            await guard.authorization_url(attempt)
        assert provider.calls == 1
        clock[0] += expected
        provider.error = None
        assert await guard.authorization_url(attempt) == SecretStr("https://idp.example.invalid/authorize")
        assert guard._failures == 1
        assert await guard.exchange(callback, attempt) is IDENTITY
        assert guard._failures == 0
    asyncio.run(scenario())


def test_repeated_outage_caps_backoff_and_only_one_recovery_probe_is_inflight():
    async def scenario():
        provider, guard, clock, attempt, callback = _system()
        provider.error = OidcUnavailable()
        for expected in (2, 4, 8, 8, 8):
            with pytest.raises(OidcUnavailable):
                await guard.exchange(callback, attempt)
            assert guard._blocked_until - clock[0] == expected
            clock[0] += expected
        provider.release.clear()
        probe = asyncio.create_task(guard.exchange(callback, attempt))
        await asyncio.sleep(0)
        calls = provider.calls
        results = await asyncio.gather(*(guard.exchange(callback, attempt) for _ in range(20)), return_exceptions=True)
        assert all(isinstance(value, OidcUnavailable) for value in results)
        assert provider.calls == calls
        provider.error = None
        provider.release.set()
        assert await probe is IDENTITY
        assert guard._failures == guard._active == 0
    asyncio.run(scenario())


def test_stale_success_cannot_clear_a_concurrent_outage_cooldown():
    class Provider(SyntheticProvider):
        async def exchange(self, callback, attempt):
            self.calls += 1
            if self.calls == 1:
                await self.release.wait()
                return IDENTITY
            raise OidcUnavailable()

    async def scenario():
        provider = Provider()
        provider.release.clear()
        guard = BoundedOidc(provider, timeout_seconds=1.0, maximum_inflight=2,
                            initial_backoff_seconds=2.0, maximum_backoff_seconds=8.0)
        attempt = new_authorization_attempt(POLICY, now=datetime.now(UTC))
        old = asyncio.create_task(guard.exchange(None, attempt))
        await asyncio.sleep(0)
        with pytest.raises(OidcUnavailable):
            await guard.exchange(None, attempt)
        provider.release.set()
        assert await old is IDENTITY
        with pytest.raises(OidcUnavailable):
            await guard.exchange(None, attempt)
        assert provider.calls == 2 and guard._failures == 1
    asyncio.run(scenario())


def test_terminal_token_rejection_does_not_create_global_outage():
    async def scenario():
        provider, guard, _, attempt, callback = _system()
        provider.error = OidcRejected()
        with pytest.raises(OidcRejected):
            await guard.exchange(callback, attempt)
        provider.error = None
        assert await guard.exchange(callback, attempt) is IDENTITY
        assert provider.calls == 2 and guard._failures == 0
    asyncio.run(scenario())


def test_total_timeout_and_external_cancellation_release_slots():
    async def scenario():
        provider, guard, clock, attempt, callback = _system(timeout=0.01)
        provider.release.clear()
        with pytest.raises(OidcUnavailable):
            await guard.exchange(callback, attempt)
        assert provider.calls == 1 and provider.active == guard._active == 0
        clock[0] += 2
        task = asyncio.create_task(guard.exchange(callback, attempt))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert guard._active == 0 and guard._probing is False
        provider.release.set()
        assert await guard.exchange(callback, attempt) is IDENTITY
    asyncio.run(scenario())


@pytest.mark.parametrize("changes", [{"maximum_inflight": True}, {"maximum_inflight": 0}, {"timeout_seconds": float("inf")}, {"initial_backoff_seconds": 9.0}])
def test_invalid_provider_bounds_fail_before_calls(changes):
    options = {"maximum_inflight": 2, "timeout_seconds": 1.0, "initial_backoff_seconds": 2.0, "maximum_backoff_seconds": 8.0}
    options.update(changes)
    with pytest.raises(AuthConfigurationRejected):
        BoundedOidc(SyntheticProvider(), **options)


def test_coordinator_outage_leaves_claim_spent_and_no_session(tmp_path):
    from test_k03_login_coordinator import (
        SyntheticLoginAuthority,
        SyntheticOidc,
        _cookie,
        _flow,
        _query,
    )

    async def scenario():
        store, provider = SyntheticLoginAuthority(), SyntheticOidc()
        guard = BoundedOidc(provider, timeout_seconds=1.0, maximum_inflight=2,
                            initial_backoff_seconds=2.0, maximum_backoff_seconds=8.0)
        flow = _flow(tmp_path, store, guard)
        start = await flow.begin(None)
        provider.error = OidcUnavailable()
        with pytest.raises(OidcUnavailable):
            await flow.complete(_cookie(start), _query(start))
        with pytest.raises(LoginRejected):
            await flow.complete(_cookie(start), _query(start))
        assert provider.calls == store.claim_effects == 1
        assert store.commit_effects == 0 and store.sessions == {}
    asyncio.run(scenario())


def test_actual_discovery_outage_storm_is_bounded_without_sockets(tmp_path):
    import httpx2
    from test_k03_login_coordinator import _runtime

    from aletheia_lab.hosted_auth.oidc_discovery import RefreshingOidc

    async def scenario():
        calls, entered, release = [], asyncio.Event(), asyncio.Event()

        async def handler(request):
            calls.append(request)
            entered.set()
            await release.wait()
            return httpx2.Response(503)

        provider = RefreshingOidc(_runtime(tmp_path), POLICY,
                                  transport_factory=lambda: httpx2.MockTransport(handler))
        guard = BoundedOidc(provider, timeout_seconds=1.0, maximum_inflight=2,
                            initial_backoff_seconds=10.0, maximum_backoff_seconds=30.0)
        attempt = new_authorization_attempt(POLICY, now=datetime.now(UTC))
        tasks = [asyncio.create_task(guard.authorization_url(attempt)) for _ in range(20)]
        await entered.wait()
        await asyncio.sleep(0)
        release.set()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        assert all(isinstance(value, OidcUnavailable) for value in results)
        assert len(calls) == 2
        results = await asyncio.gather(*(guard.authorization_url(attempt) for _ in range(20)), return_exceptions=True)
        assert all(isinstance(value, OidcUnavailable) for value in results)
        assert len(calls) == 2
    asyncio.run(scenario())
