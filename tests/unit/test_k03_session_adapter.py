"""Synthetic atomic store schedules, not PostgreSQL or browser qualification.

The store below is a test double for the K-05 port. Real DB atomicity, shared
state, restart/restore and propagation latency require a separate integration run.
"""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import SecretStr

from aletheia_lab.hosted.application import AccessDenied, AdapterLimits, HostedApplication
from aletheia_lab.hosted.read_backend import ReadOnlyHostedBackend, SessionRepository
from aletheia_lab.hosted_auth.boundary import SESSION_COOKIE
from aletheia_lab.hosted_auth.repositories import (
    RepositoryDataRejected,
    RepositoryUnavailable,
    SessionSnapshot,
)
from aletheia_lab.hosted_auth.sessions import (
    RenewalConflict,
    ServerSessions,
    credential_digest,
)
from aletheia_lab.hosted_auth.settings import AuthPolicy
from aletheia_lab.hosted_contract.session_capabilities import Actor, Limits, Problem

NOW = datetime(2030, 1, 1, tzinfo=UTC)
POLICY = AuthPolicy(default_return_to="/app", return_to_paths=("/app",))
TOKEN_A = "a" * 43
TOKEN_B = "b" * 43
TOKEN_C = "c" * 43


def _cookie(token):
    return f"{SESSION_COOKIE}={token}".encode()


def _facts(token=TOKEN_A, user="usr_synthetic_a", context="ctx_synthetic_a"):
    issued = NOW - timedelta(seconds=600)
    return SessionSnapshot(
        credential_digest=credential_digest(SecretStr(token)),
        actor=Actor(user_id=user, display_name="Synthetic Account", email="synthetic@example.invalid", email_verified=True),
        context_id=context, csrf_token=SecretStr("synthetic-csrf-proof"),
        onboarding="needs_workspace", last_workspace_id=None,
        state="active", actor_enabled=True,
        issued_at=issued, rotated_at=issued, last_seen_at=issued,
        idle_expires_at=issued + timedelta(seconds=900),
        absolute_expires_at=issued + timedelta(seconds=28800),
        generation=0, session_epoch=0, account_epoch=0,
    )


class SyntheticAtomicStore:
    """Test-only authority implementing the draft port, never a runtime fallback."""

    def __init__(self):
        self.records = {}
        self.epochs = {}
        self.enabled = {}
        self.revoked_contexts = set()
        self.now = NOW
        self.lock = asyncio.Lock()
        self.ready = True
        self.inspect_calls = []
        self.effects = 0
        self.rotate_requests = 0
        self.rotate_entered = asyncio.Event()
        self.two_rotates_entered = asyncio.Event()
        self.allow_rotate = asyncio.Event()
        self.allow_rotate.set()
        self.change_reply = lambda value: value
        self.seed(_facts())

    def seed(self, record):
        self.records[record.credential_digest.get_secret_value()] = record
        self.epochs.setdefault(record.actor.user_id, 0)
        self.enabled.setdefault(record.actor.user_id, True)

    def current(self, digest):
        record = self.records.get(digest.get_secret_value())
        if record is None:
            return None
        changes = {
            "account_epoch": self.epochs[record.actor.user_id],
            "actor_enabled": self.enabled[record.actor.user_id],
        }
        if record.context_id in self.revoked_contexts:
            changes["state"] = "revoked"
        return record.model_copy(update=changes)

    async def is_ready(self):
        return self.ready

    async def inspect(self, digest):
        self.inspect_calls.append(digest)
        async with self.lock:
            return self.current(digest)

    def _can_rotate(self, record, command):
        if record is None:
            return False
        return (
            record.state == "active" and record.actor_enabled
            and record.session_epoch == record.account_epoch == command.expected_account_epoch
            and record.actor.user_id == command.expected_user_id
            and record.context_id == command.expected_context_id
            and record.generation == command.expected_generation
            and record.idle_expires_at > self.now
            and record.absolute_expires_at > self.now
            and record.last_seen_at + timedelta(seconds=command.rules.idle_seconds) > self.now
            and record.issued_at + timedelta(seconds=command.rules.absolute_seconds) > self.now
            and record.rotated_at + timedelta(seconds=command.rules.rotation_seconds) <= self.now
        )

    async def rotate_if_current(self, command):
        self.rotate_requests += 1
        self.rotate_entered.set()
        if self.rotate_requests == 2:
            self.two_rotates_entered.set()
        await self.allow_rotate.wait()
        async with self.lock:
            record = self.current(command.presented_digest)
            if not self._can_rotate(record, command):
                return None
            updated = record.model_copy(update={
                "credential_digest": command.replacement_digest,
                "generation": record.generation + 1,
                "rotated_at": self.now, "last_seen_at": self.now,
                "idle_expires_at": min(
                    self.now + timedelta(seconds=command.rules.idle_seconds),
                    record.absolute_expires_at,
                    record.issued_at + timedelta(seconds=command.rules.absolute_seconds),
                ),
            })
            self.records[command.presented_digest.get_secret_value()] = record.model_copy(update={"state": "rotated"})
            self.seed(updated)
            self.effects += 1
            return self.change_reply(updated)

    async def revoke_presented(self, digest, *, all_sessions):
        async with self.lock:
            record = self.current(digest)
            if record is None or record.state not in ("active", "rotated"):
                return False
            if not record.actor_enabled or record.session_epoch != record.account_epoch:
                return False
            # A rotated alias is useful only while its exact family is live.
            family = [item for item in self.records.values() if item.context_id == record.context_id and item.state == "active"]
            if not family or family[0].idle_expires_at <= self.now or family[0].absolute_expires_at <= self.now:
                return False
            if all_sessions:
                self.epochs[record.actor.user_id] += 1
                self.revoked_contexts.update(
                    item.context_id for item in self.records.values()
                    if item.actor.user_id == record.actor.user_id
                )
            else:
                self.revoked_contexts.add(record.context_id)
            self.effects += 1
            return True


def _system():
    store = SyntheticAtomicStore()
    adapter = ServerSessions(store, POLICY, timeout_seconds=1.0, maximum_cookie_bytes=4096, clock=lambda: store.now)
    return adapter, store


def test_session_adapter_implements_k02_protocol_with_only_hashed_cookie_input():
    async def scenario():
        adapter, store = _system()
        repository: SessionRepository = adapter
        record = await repository.resolve_cookie(_cookie(TOKEN_A))
        assert record.actor.user_id == "usr_synthetic_a"
        assert record.context_id == "ctx_synthetic_a"
        assert record.expires_at == NOW + timedelta(seconds=300)
        assert record.onboarding == "needs_workspace"
        assert record.last_workspace_id is None
        assert store.effects == 0
        assert store.inspect_calls == [credential_digest(SecretStr(TOKEN_A))]
        assert TOKEN_A not in repr(store.inspect_calls)
        assert "synthetic-csrf-proof" not in repr(record)
        assert "synthetic@example.invalid" not in repr(_facts())
    asyncio.run(scenario())


@pytest.mark.parametrize("cookie", [
    b"", b"role=owner; user_id=attacker", b"X-P6-Bridge=public",
    _cookie("short"), _cookie(TOKEN_A) + b"; " + _cookie(TOKEN_A), b"\xff", b"a" * 4097,
])
def test_malformed_cookie_never_reaches_store(cookie):
    async def scenario():
        adapter, store = _system()
        assert await adapter.resolve_cookie(cookie) is None
        assert store.inspect_calls == []
        assert store.effects == 0
    asyncio.run(scenario())


def test_well_formed_unknown_credential_cannot_create_session():
    async def scenario():
        adapter, store = _system()
        assert await adapter.resolve_cookie(_cookie(TOKEN_B)) is None
        with pytest.raises(AccessDenied):
            await adapter.renew(_cookie(TOKEN_B))
        assert len(store.records) == 1
        assert store.effects == 0
    asyncio.run(scenario())


@pytest.mark.parametrize("change,code", [
    ({"state": "expired"}, "session_expired"),
    ({"state": "revoked"}, "session_revoked"),
    ({"state": "rotated"}, "session_revoked"),
    ({"idle_expires_at": NOW}, "session_expired"),
    ({"idle_expires_at": NOW, "absolute_expires_at": NOW}, "session_expired"),
    ({"session_epoch": 1}, "session_revoked"),
])
def test_expiry_revocation_rotation_and_epoch_fail_closed(change, code):
    async def scenario():
        adapter, store = _system()
        store.seed(_facts().model_copy(update=change))
        with pytest.raises(AccessDenied) as caught:
            await adapter.resolve_cookie(_cookie(TOKEN_A))
        assert caught.value.code == code
        assert store.effects == 0
    asyncio.run(scenario())


def test_current_account_disable_and_logout_all_epoch_are_rechecked_on_every_read():
    async def scenario():
        adapter, store = _system()
        await adapter.resolve_cookie(_cookie(TOKEN_A))
        store.enabled["usr_synthetic_a"] = False
        with pytest.raises(AccessDenied, match="no longer active"):
            await adapter.resolve_cookie(_cookie(TOKEN_A))
        store.enabled["usr_synthetic_a"] = True
        store.epochs["usr_synthetic_a"] += 1
        with pytest.raises(AccessDenied, match="no longer active"):
            await adapter.resolve_cookie(_cookie(TOKEN_A))
    asyncio.run(scenario())


def test_reads_never_slide_idle_and_early_renewal_has_no_effect_or_cookie():
    async def scenario():
        adapter, store = _system()
        facts = _facts().model_copy(update={"rotated_at": NOW - timedelta(seconds=60), "last_seen_at": NOW - timedelta(seconds=60)})
        store.seed(facts)
        first = await adapter.resolve_cookie(_cookie(TOKEN_A))
        store.now += timedelta(seconds=1)
        second = await adapter.resolve_cookie(_cookie(TOKEN_A))
        renewal = await adapter.renew(_cookie(TOKEN_A))
        assert first.expires_at == second.expires_at == renewal.record.expires_at
        assert renewal.credential is None
        assert store.effects == store.rotate_requests == 0
    asyncio.run(scenario())


def test_renewal_changes_only_credential_generation_and_idle_window():
    async def scenario():
        adapter, store = _system()
        result = await adapter.renew(_cookie(TOKEN_A))
        token = result.credential.get_secret_value()
        assert token != TOKEN_A
        assert result.record.context_id == "ctx_synthetic_a"
        assert result.record.csrf_token == _facts().csrf_token
        assert result.record.expires_at == NOW + timedelta(seconds=900)
        assert store.current(credential_digest(result.credential)).absolute_expires_at == _facts().absolute_expires_at
        assert (await adapter.resolve_cookie(_cookie(token))).actor == _facts().actor
        with pytest.raises(AccessDenied):
            await adapter.resolve_cookie(_cookie(TOKEN_A))
        with pytest.raises(RenewalConflict):
            await adapter.renew(_cookie(TOKEN_A))
        assert token not in repr(result)
        assert store.effects == 1
    asyncio.run(scenario())


def test_two_renewals_of_one_generation_have_exactly_one_winner():
    async def scenario():
        async with asyncio.timeout(2):
            adapter, store = _system()
            store.allow_rotate.clear()
            one = asyncio.create_task(adapter.renew(_cookie(TOKEN_A)))
            two = asyncio.create_task(adapter.renew(_cookie(TOKEN_A)))
            await store.two_rotates_entered.wait()
            store.allow_rotate.set()
            results = await asyncio.gather(one, two, return_exceptions=True)
            assert sum(isinstance(item, RenewalConflict) for item in results) == 1
            winners = [item for item in results if not isinstance(item, BaseException)]
            assert len(winners) == 1
            assert winners[0].credential is not None
            assert store.effects == 1
    asyncio.run(scenario())


@pytest.mark.parametrize("all_sessions", [False, True])
def test_logout_wins_before_pending_rotation_and_no_late_credential_is_returned(all_sessions):
    async def scenario():
        async with asyncio.timeout(2):
            adapter, store = _system()
            store.allow_rotate.clear()
            pending = asyncio.create_task(adapter.renew(_cookie(TOKEN_A)))
            await store.rotate_entered.wait()
            operation = adapter.logout_all if all_sessions else adapter.logout
            await operation(_cookie(TOKEN_A))
            store.allow_rotate.set()
            with pytest.raises(RenewalConflict):
                await pending
            with pytest.raises(AccessDenied):
                await adapter.resolve_cookie(_cookie(TOKEN_A))
            assert store.effects == 1
            assert len(store.records) == 1
    asyncio.run(scenario())


@pytest.mark.parametrize("all_sessions", [False, True])
def test_rotated_alias_can_revoke_replacement_without_revoking_other_account(all_sessions):
    async def scenario():
        adapter, store = _system()
        store.seed(_facts(TOKEN_B, context="ctx_synthetic_other_tab"))
        store.seed(_facts(TOKEN_C, user="usr_synthetic_b", context="ctx_synthetic_b"))
        result = await adapter.renew(_cookie(TOKEN_A))
        operation = adapter.logout_all if all_sessions else adapter.logout
        await operation(_cookie(TOKEN_A))
        with pytest.raises(AccessDenied):
            await adapter.resolve_cookie(_cookie(result.credential.get_secret_value()))
        assert (await adapter.resolve_cookie(_cookie(TOKEN_C))).actor.user_id == "usr_synthetic_b"
        if all_sessions:
            with pytest.raises(AccessDenied):
                await adapter.resolve_cookie(_cookie(TOKEN_B))
            assert store.epochs["usr_synthetic_a"] == 1
        else:
            assert (await adapter.resolve_cookie(_cookie(TOKEN_B))).context_id == "ctx_synthetic_other_tab"
        assert store.effects == 2
    asyncio.run(scenario())


def test_single_logout_is_idempotent_and_unknown_cookie_has_no_effect():
    async def scenario():
        adapter, store = _system()
        await adapter.logout(_cookie(TOKEN_A))
        await adapter.logout(_cookie(TOKEN_A))
        await adapter.logout(_cookie(TOKEN_B))
        await adapter.logout(None)
        with pytest.raises(AccessDenied):
            await adapter.logout_all(_cookie(TOKEN_A))
        assert store.effects == 1
    asyncio.run(scenario())


@pytest.mark.parametrize("change", [
    {"context_id": "ctx_wrong"}, {"generation": 99},
    {"csrf_token": SecretStr("wrong-csrf")},
    {"issued_at": NOW - timedelta(seconds=601)},
    {"absolute_expires_at": NOW + timedelta(days=2)},
])
def test_corrupt_rotation_reply_never_returns_a_credential(change):
    async def scenario():
        adapter, store = _system()
        store.change_reply = lambda record: record.model_copy(update=change)
        with pytest.raises(RepositoryDataRejected):
            await adapter.renew(_cookie(TOKEN_A))
    asyncio.run(scenario())


@pytest.mark.parametrize("change", [
    {"credential_digest": SecretStr("f" * 64)},
    {"issued_at": NOW.replace(tzinfo=None)},
    {"last_seen_at": NOW + timedelta(seconds=31), "rotated_at": NOW},
    {"actor": _facts().actor.model_copy(update={"email_verified": "yes"})},
])
def test_corrupt_read_snapshot_fails_closed(change):
    async def scenario():
        adapter, store = _system()
        original = _facts().credential_digest.get_secret_value()
        store.records[original] = _facts().model_copy(update=change)
        with pytest.raises(RepositoryDataRejected):
            await adapter.resolve_cookie(_cookie(TOKEN_A))
    asyncio.run(scenario())


def test_tighter_absolute_policy_cannot_be_extended_by_renewal():
    async def scenario():
        adapter, store = _system()
        record = _facts().model_copy(update={"issued_at": NOW - timedelta(seconds=28800)})
        store.seed(record)
        with pytest.raises(AccessDenied, match="expired"):
            await adapter.renew(_cookie(TOKEN_A))
        assert store.effects == 0
    asyncio.run(scenario())


def test_store_idle_window_cannot_override_tighter_adapter_policy():
    async def scenario():
        adapter, store = _system()
        old = NOW - timedelta(seconds=1000)
        store.seed(_facts().model_copy(update={
            "issued_at": old, "rotated_at": old,
            "last_seen_at": NOW - timedelta(seconds=900),
            "idle_expires_at": NOW + timedelta(seconds=3600),
        }))
        with pytest.raises(AccessDenied, match="expired"):
            await adapter.resolve_cookie(_cookie(TOKEN_A))
        assert store.effects == 0
    asyncio.run(scenario())


def test_rotation_preserves_near_absolute_deadline():
    async def scenario():
        adapter, store = _system()
        deadline = NOW + timedelta(seconds=100)
        store.seed(_facts().model_copy(update={
            "idle_expires_at": deadline, "absolute_expires_at": deadline,
        }))
        result = await adapter.renew(_cookie(TOKEN_A))
        assert result.record.expires_at == deadline
        store.now = deadline
        with pytest.raises(AccessDenied, match="expired"):
            await adapter.resolve_cookie(_cookie(result.credential.get_secret_value()))
    asyncio.run(scenario())


def test_rotation_commit_with_lost_reply_is_not_success_and_old_alias_can_logout():
    async def scenario():
        adapter, store = _system()
        original_rotate = store.rotate_if_current

        async def committed_then_failed(command):
            await original_rotate(command)
            raise RuntimeError("synthetic-private-lost-reply")

        store.rotate_if_current = committed_then_failed
        with pytest.raises(RepositoryUnavailable):
            await adapter.renew(_cookie(TOKEN_A))
        assert store.effects == 1
        with pytest.raises(AccessDenied):
            await adapter.resolve_cookie(_cookie(TOKEN_A))
        await adapter.logout(_cookie(TOKEN_A))
        assert store.effects == 2
        for record in store.records.values():
            assert store.current(record.credential_digest).state == "revoked"
    asyncio.run(scenario())


def test_store_exception_is_redacted_and_maps_to_existing_k02_outage_problem():
    class Workspaces:
        async def is_ready(self):
            return True

        async def access_for_context(self, **kwargs):
            raise AssertionError("unused")

    async def scenario():
        adapter, store = _system()

        async def broken(digest):
            raise RuntimeError("synthetic-private-driver-secret")

        store.inspect = broken
        with pytest.raises(RepositoryUnavailable) as caught:
            await adapter.resolve_cookie(_cookie(TOKEN_A))
        assert "synthetic-private-driver-secret" not in str(caught.value)
        assert caught.value.__suppress_context__ is True
        backend = ReadOnlyHostedBackend(adapter, Workspaces(), limits=Limits(
            question_chars=2000, upload_bundle_bytes=1024, page_max=20, job_poll_min_ms=1000,
        ), clock=lambda: NOW)
        app = HostedApplication(backend, limits=AdapterLimits(1.0, 10000, 4096))
        messages = []

        async def receive():
            raise AssertionError("GET must not read body")

        async def send(message):
            messages.append(message)

        await app({"type": "http", "method": "GET", "path": "/v1/session", "headers": [(b"cookie", _cookie(TOKEN_A))]}, receive, send)
        assert messages[0]["status"] == 503
        assert Problem.model_validate_json(messages[1]["body"]).code == "service_unavailable"
        assert b"synthetic-private-driver-secret" not in messages[1]["body"]
    asyncio.run(scenario())


def test_read_deadline_and_external_cancellation():
    async def scenario():
        adapter, store = _system()
        adapter = ServerSessions(store, POLICY, timeout_seconds=0.01, maximum_cookie_bytes=4096, clock=lambda: NOW)

        async def stalled(digest):
            await asyncio.Event().wait()

        store.inspect = stalled
        with pytest.raises(TimeoutError):
            await adapter.resolve_cookie(_cookie(TOKEN_A))

        async def cancelled(digest):
            raise asyncio.CancelledError

        store.inspect = cancelled
        with pytest.raises(asyncio.CancelledError):
            await adapter.resolve_cookie(_cookie(TOKEN_A))
    asyncio.run(scenario())


def test_readiness_is_not_a_cached_or_fabricated_success():
    async def scenario():
        adapter, store = _system()
        assert await adapter.is_ready() is True
        store.ready = False
        assert await adapter.is_ready() is False

        async def broken():
            raise RuntimeError("synthetic-private-readiness-secret")

        store.is_ready = broken
        assert await adapter.is_ready() is False
    asyncio.run(scenario())
