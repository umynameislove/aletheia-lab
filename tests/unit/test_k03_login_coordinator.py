"""Login coordinator over a synthetic atomic shared authority, not a real DB.

Controlled schedules check adapter/orchestration behavior under the draft port's
guarantees. Real K-05 transactions, multiple processes, HTTP/CSRF and browser
journeys need separate qualification. No production store is implemented here.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx2
import pytest
from joserfc import jwt
from joserfc.jwk import RSAKey
from pydantic import SecretStr

from aletheia_lab.hosted.application import AccessDenied
from aletheia_lab.hosted.settings import RuntimeSettings
from aletheia_lab.hosted_auth.boundary import LOGIN_COOKIE, SESSION_COOKIE, AuthInputRejected
from aletheia_lab.hosted_auth.identity import AccountResolver, IdentityRejected
from aletheia_lab.hosted_auth.login import LoginCoordinator, LoginIntent, LoginRejected
from aletheia_lab.hosted_auth.oidc import AuthlibOidcClient, OidcRejected
from aletheia_lab.hosted_auth.repositories import (
    AccountBinding,
    RepositoryDataRejected,
    RepositoryUnavailable,
    SessionSnapshot,
    ValidatedIdentity,
)
from aletheia_lab.hosted_auth.sessions import ServerSessions, credential_digest
from aletheia_lab.hosted_auth.settings import AuthConfigurationRejected, AuthPolicy
from aletheia_lab.hosted_contract.session_capabilities import Actor

NOW = datetime(2030, 1, 1, tzinfo=UTC)
ISSUER = "https://identity.example.invalid/realm"
CLIENT_ID = "synthetic-client"
CALLBACK = "https://app.example.invalid/auth/callback"
POLICY = AuthPolicy(default_return_to="/app", return_to_paths=("/app", "/account"))


def _binding(subject="subject-a", user="usr_synthetic_a"):
    return AccountBinding(
        issuer=ISSUER, subject=SecretStr(subject),
        actor=Actor(user_id=user, display_name="Synthetic User", email="synthetic@example.invalid", email_verified=True),
        enabled=True, account_epoch=0, onboarding="needs_workspace", last_workspace_id=None,
    )


def _identity(subject="subject-a"):
    return ValidatedIdentity(
        issuer=ISSUER, subject=SecretStr(subject),
        email=SecretStr("synthetic@example.invalid"), email_verified=True,
    )


class SyntheticLoginAuthority:
    """Test-only shared authority. No file/network/SQL store or runtime fallback."""

    def __init__(self):
        self.now = NOW
        self.lock = asyncio.Lock()
        self.intents = {}
        self.browsers = {}
        self.claims = {}
        self.accounts = {"subject-a": _binding(), "subject-b": _binding("subject-b", "usr_synthetic_b")}
        self.revoked_at = {}
        self.sessions = {}
        self.credentials = {}
        self.begin_commands = []
        self.claim_requests = []
        self.lookup_calls = 0
        self.claim_effects = 0
        self.commit_calls = 0
        self.commit_effects = 0
        self.commit_entered = asyncio.Event()
        self.allow_commit = asyncio.Event()
        self.allow_commit.set()
        self.change_claim = lambda value: value
        self.change_commit = lambda value: value
        self.lose_commit_reply = False

    def seed_session(self, token, account=None, context="ctx_prior"):
        account = account or self.accounts["subject-a"]
        issued = self.now - timedelta(seconds=60)
        record = SessionSnapshot(
            credential_digest=credential_digest(SecretStr(token)), actor=account.actor,
            context_id=context, csrf_token=SecretStr("synthetic-prior-csrf"),
            onboarding=account.onboarding, last_workspace_id=account.last_workspace_id,
            state="active", actor_enabled=account.enabled,
            issued_at=issued, last_seen_at=issued, rotated_at=issued,
            idle_expires_at=issued + timedelta(seconds=900), absolute_expires_at=issued + timedelta(seconds=28800),
            generation=0, session_epoch=account.account_epoch, account_epoch=account.account_epoch,
        )
        self.sessions[context] = record
        self.credentials[record.credential_digest.get_secret_value()] = context

    def live_context(self, context):
        record = self.sessions.get(context)
        if record is None or record.state != "active":
            return False
        account = next(item for item in self.accounts.values() if item.actor.user_id == record.actor.user_id)
        return (
            account.enabled and record.session_epoch == account.account_epoch
            and record.idle_expires_at > self.now and record.absolute_expires_at > self.now
        )

    async def begin_intent(self, command):
        async with self.lock:
            self.begin_commands.append(command)
            browser_key = command.browser_digest.get_secret_value()
            browser = self.browsers.get(browser_key, {"epoch": 0, "current": None})
            previous = browser["current"]
            if not self.live_context(previous):
                previous = None
            if command.previous_credential_digest is not None:
                supplied = self.credentials.get(command.previous_credential_digest.get_secret_value())
                if self.live_context(supplied):
                    if previous is not None and previous != supplied:
                        return None
                    previous = supplied
            intent = LoginIntent(
                intent_id=command.intent_id, browser_digest=command.browser_digest,
                state_digest=command.state_digest, issuer=command.issuer,
                client_id=command.client_id, redirect_uri=command.redirect_uri,
                nonce=command.nonce, code_verifier=command.code_verifier,
                created_at=command.created_at, expires_at=command.expires_at,
                browser_epoch=browser["epoch"] + 1, return_to=command.return_to,
                reauthenticate=command.reauthenticate, previous_context_id=previous, phase="pending",
            )
            self.intents[intent.intent_id] = intent
            self.browsers[browser_key] = {"epoch": intent.browser_epoch, "latest": intent.intent_id, "current": previous}
            return intent

    def current_intent(self, intent):
        browser = self.browsers[intent.browser_digest.get_secret_value()]
        return (
            browser["latest"] == intent.intent_id and browser["epoch"] == intent.browser_epoch
            and intent.created_at <= self.now < intent.expires_at
        )

    async def claim_intent(self, *, browser_digest, state_digest, claim_digest):
        async with self.lock:
            self.claim_requests.append((browser_digest, state_digest, claim_digest))
            for intent in self.intents.values():
                if intent.browser_digest != browser_digest or intent.state_digest != state_digest:
                    continue
                if not self.current_intent(intent) or intent.phase != "pending":
                    return None
                claimed = intent.model_copy(update={"phase": "claimed"})
                self.intents[intent.intent_id] = claimed
                self.claims[intent.intent_id] = claim_digest
                self.claim_effects += 1
                return self.change_claim(claimed)
            return None

    async def find_identity(self, *, issuer, subject):
        self.lookup_calls += 1
        return self.accounts.get(subject.get_secret_value()) if issuer == ISSUER else None

    def _account_for_commit(self, intent, command):
        account = self.accounts.get(command.account.subject.get_secret_value())
        if account is None or not account.enabled or account.issuer != command.account.issuer:
            return None
        if account.actor.user_id != command.account.actor.user_id or account.account_epoch != command.account.account_epoch:
            return None
        watermark = self.revoked_at.get(account.actor.user_id)
        if watermark is not None and watermark >= intent.created_at:
            return None
        if command.require_verified_email and not account.actor.email_verified:
            return None
        return account

    def _claim_for_commit(self, command):
        intent = self.intents.get(command.intent_id)
        if intent is None or intent.phase != "claimed" or not self.current_intent(intent):
            return None
        if (
            intent.browser_digest != command.browser_digest or intent.state_digest != command.state_digest
            or intent.browser_epoch != command.expected_browser_epoch
            or self.claims.get(intent.intent_id) != command.claim_digest
        ):
            return None
        if intent.previous_context_id is not None and not self.live_context(intent.previous_context_id):
            return None
        return intent

    async def commit_login(self, command):
        # The test schedule pauses BEFORE the transaction, never holding the lock.
        self.commit_calls += 1
        self.commit_entered.set()
        await self.allow_commit.wait()
        async with self.lock:
            intent = self._claim_for_commit(command)
            if intent is None:
                return None
            account = self._account_for_commit(intent, command)
            if account is None or command.context_id in self.sessions:
                return None
            digest = command.credential_digest.get_secret_value()
            if digest in self.credentials:
                return None
            snapshot = SessionSnapshot(
                credential_digest=command.credential_digest, actor=account.actor,
                context_id=command.context_id, csrf_token=command.csrf_token,
                onboarding=account.onboarding, last_workspace_id=account.last_workspace_id,
                state="active", actor_enabled=True,
                issued_at=self.now, last_seen_at=self.now, rotated_at=self.now,
                idle_expires_at=self.now + timedelta(seconds=min(command.rules.idle_seconds, command.rules.absolute_seconds)),
                absolute_expires_at=self.now + timedelta(seconds=command.rules.absolute_seconds),
                generation=0, session_epoch=account.account_epoch, account_epoch=account.account_epoch,
            )
            if intent.previous_context_id is not None:
                old = self.sessions[intent.previous_context_id]
                self.sessions[intent.previous_context_id] = old.model_copy(update={"state": "revoked"})
            self.sessions[command.context_id] = snapshot
            self.credentials[digest] = command.context_id
            self.browsers[intent.browser_digest.get_secret_value()]["current"] = command.context_id
            self.intents[intent.intent_id] = intent.model_copy(update={"phase": "committed"})
            self.commit_effects += 1
            if self.lose_commit_reply:
                raise RuntimeError("synthetic-private-lost-commit-reply")
            return self.change_commit(snapshot)

    async def revoke_account(self, subject):
        async with self.lock:
            account = self.accounts[subject]
            self.accounts[subject] = account.model_copy(update={"account_epoch": account.account_epoch + 1})
            self.revoked_at[account.actor.user_id] = self.now

    async def revoke_context(self, context):
        async with self.lock:
            self.sessions[context] = self.sessions[context].model_copy(update={"state": "revoked"})

    async def is_ready(self):
        return True

    async def inspect(self, digest):
        context = self.credentials.get(digest.get_secret_value())
        if context is None:
            return None
        snapshot = self.sessions[context]
        account = next(item for item in self.accounts.values() if item.actor.user_id == snapshot.actor.user_id)
        return snapshot.model_copy(update={"actor_enabled": account.enabled, "account_epoch": account.account_epoch})


class SyntheticOidc:
    """Deliberate OIDC test double; no signature/PKCE verification claim."""

    def __init__(self):
        self.identity = _identity()
        self.error = None
        self.calls = 0
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.release.set()
        self.attempts = []

    async def authorization_url(self, attempt):
        return SecretStr(ISSUER + "/authorize?" + urlencode({"state": attempt.state.get_secret_value()}))

    async def exchange(self, callback, attempt):
        self.calls += 1
        self.attempts.append(attempt)
        self.entered.set()
        await self.release.wait()
        if self.error is not None:
            raise self.error
        return self.identity


def _runtime(tmp_path):
    return RuntimeSettings.model_validate({
        "environment": "staging", "public_origin": "https://app.example.invalid",
        "identity": {"issuer": ISSUER, "client_id": CLIENT_ID, "redirect_uri": CALLBACK, "client_kind": "public"},
        "storage": {"database_url_env": "ALETHEIA_HOSTED_DATABASE_URL", "object_root": str(tmp_path)},
    })


def _flow(tmp_path, store, oidc, *, policy=POLICY, csrf_factory=None, timeout_seconds=5.0):
    accounts = AccountResolver(store, policy, expected_issuer=ISSUER, timeout_seconds=1.0)
    return LoginCoordinator(
        store, oidc, accounts, _runtime(tmp_path), policy,
        csrf_factory=csrf_factory or (lambda context: SecretStr("synthetic-csrf-" + context)),
        timeout_seconds=timeout_seconds, clock=lambda: store.now,
    )


def _system(tmp_path):
    store, oidc = SyntheticLoginAuthority(), SyntheticOidc()
    return _flow(tmp_path, store, oidc), store, oidc


def _cookie(start, *, session=None):
    text = f"{LOGIN_COOKIE}={start.browser_credential.get_secret_value()}"
    if session is not None:
        text += f"; {SESSION_COOKIE}={session}"
    return text.encode()


def _query(start, **changes):
    state = parse_qs(urlsplit(start.authorization_url.get_secret_value()).query)["state"][0]
    return urlencode({"state": state, "iss": ISSUER, "code": "synthetic-code", **changes}).encode()


def test_persisted_intent_can_complete_in_another_coordinator_and_read_via_k02_port(tmp_path):
    async def scenario():
        flow, store, oidc = _system(tmp_path)
        start = await flow.begin(None, return_to="/account")
        fresh_instance = _flow(tmp_path, store, oidc)
        result = await fresh_instance.complete(_cookie(start), _query(start))
        assert result.return_to == "/account"
        assert result.session.actor.user_id == "usr_synthetic_a"
        assert result.session.onboarding == "needs_workspace"
        assert result.session.last_workspace_id is None
        assert result.session.generation == 0
        assert result.session.context_id.startswith("ctx_")
        assert result.session.idle_expires_at == NOW + timedelta(seconds=900)
        assert result.session.absolute_expires_at == NOW + timedelta(seconds=28800)
        assert "role" not in type(result.session).model_fields
        reader = ServerSessions(store, POLICY, timeout_seconds=1.0, maximum_cookie_bytes=4096, clock=lambda: store.now)
        record = await reader.resolve_cookie(f"{SESSION_COOKIE}={result.credential.get_secret_value()}".encode())
        assert record.context_id == result.session.context_id
        assert store.claim_effects == store.commit_effects == oidc.calls == 1
        state = parse_qs(urlsplit(start.authorization_url.get_secret_value()).query)["state"][0]
        assert state not in repr(store.begin_commands)
        assert start.browser_credential.get_secret_value() not in repr(store.begin_commands)
        assert result.credential.get_secret_value() not in repr(result)
        assert oidc.attempts[0].code_verifier.get_secret_value() not in repr(store.intents)
    asyncio.run(scenario())


@pytest.mark.parametrize("changes", [{"state": "short"}, {"code": ""}, {"iss": "https://evil.invalid"}])
def test_bad_callback_shape_is_rejected_before_claim_and_idp(tmp_path, changes):
    async def scenario():
        flow, store, oidc = _system(tmp_path)
        start = await flow.begin(None)
        with pytest.raises(LoginRejected):
            await flow.complete(_cookie(start), _query(start, **changes))
        assert store.claim_requests == []
        assert store.commit_effects == oidc.calls == 0
    asyncio.run(scenario())


def test_wrong_browser_cannot_consume_the_rightful_browser_intent(tmp_path):
    async def scenario():
        flow, store, oidc = _system(tmp_path)
        start = await flow.begin(None)
        wrong = f"{LOGIN_COOKIE}={'x' * 43}".encode()
        with pytest.raises(LoginRejected):
            await flow.complete(wrong, _query(start))
        assert store.claim_effects == oidc.calls == store.commit_effects == 0
        await flow.complete(_cookie(start), _query(start))
        assert store.commit_effects == 1
    asyncio.run(scenario())


@pytest.mark.parametrize("cookie", [None, b"", b"role=owner", f"{LOGIN_COOKIE}=short".encode()])
def test_absent_or_bad_browser_cookie_cannot_complete_login(tmp_path, cookie):
    async def scenario():
        flow, store, oidc = _system(tmp_path)
        start = await flow.begin(None)
        with pytest.raises(LoginRejected):
            await flow.complete(cookie, _query(start))
        assert store.claim_requests == []
        assert oidc.calls == store.commit_effects == 0
    asyncio.run(scenario())


def test_callback_replay_after_commit_cannot_create_another_session(tmp_path):
    async def scenario():
        flow, store, oidc = _system(tmp_path)
        start = await flow.begin(None)
        await flow.complete(_cookie(start), _query(start))
        with pytest.raises(LoginRejected):
            await _flow(tmp_path, store, oidc).complete(_cookie(start), _query(start))
        assert oidc.calls == store.claim_effects == store.commit_effects == len(store.sessions) == 1
    asyncio.run(scenario())


def test_concurrent_callbacks_have_one_claim_one_exchange_and_one_commit(tmp_path):
    async def scenario():
        async with asyncio.timeout(2):
            flow, store, oidc = _system(tmp_path)
            start = await flow.begin(None)
            oidc.release.clear()
            pending = asyncio.create_task(flow.complete(_cookie(start), _query(start)))
            await oidc.entered.wait()
            with pytest.raises(LoginRejected):
                await _flow(tmp_path, store, oidc).complete(_cookie(start), _query(start))
            oidc.release.set()
            await pending
            assert store.claim_effects == oidc.calls == store.commit_effects == 1
    asyncio.run(scenario())


def test_expired_pending_intent_does_not_call_idp(tmp_path):
    async def scenario():
        flow, store, oidc = _system(tmp_path)
        start = await flow.begin(None)
        store.now += timedelta(seconds=300)
        with pytest.raises(LoginRejected):
            await flow.complete(_cookie(start), _query(start))
        assert oidc.calls == store.claim_effects == store.commit_effects == 0
    asyncio.run(scenario())


def test_expiry_while_idp_is_pending_prevents_commit(tmp_path):
    async def scenario():
        async with asyncio.timeout(2):
            flow, store, oidc = _system(tmp_path)
            start = await flow.begin(None)
            oidc.release.clear()
            pending = asyncio.create_task(flow.complete(_cookie(start), _query(start)))
            await oidc.entered.wait()
            store.now += timedelta(seconds=300)
            oidc.release.set()
            with pytest.raises(LoginRejected):
                await pending
            assert store.commit_calls == store.commit_effects == 0
    asyncio.run(scenario())


def test_new_intent_supersedes_pending_old_intent_on_same_browser(tmp_path):
    async def scenario():
        flow, store, oidc = _system(tmp_path)
        old = await flow.begin(None)
        new = await flow.begin(_cookie(old))
        assert new.browser_credential == old.browser_credential
        with pytest.raises(LoginRejected):
            await flow.complete(_cookie(old), _query(old))
        await flow.complete(_cookie(new), _query(new))
        assert oidc.calls == store.commit_effects == 1
    asyncio.run(scenario())


def test_new_intent_also_fences_a_callback_that_already_claimed_state(tmp_path):
    async def scenario():
        async with asyncio.timeout(2):
            flow, store, oidc = _system(tmp_path)
            old = await flow.begin(None)
            oidc.release.clear()
            pending = asyncio.create_task(flow.complete(_cookie(old), _query(old)))
            await oidc.entered.wait()
            new = await flow.begin(_cookie(old))
            oidc.release.set()
            with pytest.raises(LoginRejected):
                await pending
            assert store.commit_effects == 0
            await flow.complete(_cookie(new), _query(new))
            assert store.commit_effects == 1
    asyncio.run(scenario())


def test_failed_token_verification_spends_claim_without_creating_session(tmp_path):
    async def scenario():
        flow, store, oidc = _system(tmp_path)
        start = await flow.begin(None)
        oidc.error = OidcRejected()
        with pytest.raises(OidcRejected):
            await flow.complete(_cookie(start), _query(start))
        oidc.error = None
        with pytest.raises(LoginRejected):
            await flow.complete(_cookie(start), _query(start))
        assert oidc.calls == store.claim_effects == 1
        assert store.lookup_calls == store.commit_calls == store.commit_effects == 0
    asyncio.run(scenario())


@pytest.mark.parametrize("condition", ["disabled", "unprovisioned"])
def test_invalid_account_never_reaches_session_commit(tmp_path, condition):
    async def scenario():
        flow, store, _ = _system(tmp_path)
        start = await flow.begin(None)
        if condition == "disabled":
            store.accounts["subject-a"] = store.accounts["subject-a"].model_copy(update={"enabled": False})
        else:
            store.accounts.pop("subject-a")
        with pytest.raises(IdentityRejected):
            await flow.complete(_cookie(start), _query(start))
        assert store.commit_calls == store.commit_effects == len(store.sessions) == 0
    asyncio.run(scenario())


@pytest.mark.parametrize("condition", ["disabled", "email_unverified", "epoch"])
def test_account_change_after_lookup_is_rechecked_at_commit(tmp_path, condition):
    async def scenario():
        async with asyncio.timeout(2):
            flow, store, _ = _system(tmp_path)
            start = await flow.begin(None)
            store.allow_commit.clear()
            pending = asyncio.create_task(flow.complete(_cookie(start), _query(start)))
            await store.commit_entered.wait()
            if condition == "disabled":
                store.accounts["subject-a"] = store.accounts["subject-a"].model_copy(update={"enabled": False})
            elif condition == "email_unverified":
                account = store.accounts["subject-a"]
                actor = account.actor.model_copy(update={"email_verified": False})
                store.accounts["subject-a"] = account.model_copy(update={"actor": actor})
            else:
                store.now += timedelta(microseconds=1)
                await store.revoke_account("subject-a")
            store.allow_commit.set()
            with pytest.raises(LoginRejected):
                await pending
            assert store.commit_effects == len(store.sessions) == 0
    asyncio.run(scenario())


@pytest.mark.parametrize("microseconds", [0, 1])
def test_logout_all_before_lookup_still_fences_an_older_login_intent(tmp_path, microseconds):
    async def scenario():
        async with asyncio.timeout(2):
            flow, store, oidc = _system(tmp_path)
            start = await flow.begin(None)
            oidc.release.clear()
            pending = asyncio.create_task(flow.complete(_cookie(start), _query(start)))
            await oidc.entered.wait()
            store.now += timedelta(microseconds=microseconds)
            await store.revoke_account("subject-a")
            oidc.release.set()
            with pytest.raises(LoginRejected):
                await pending
            assert store.lookup_calls == 1  # It saw the new epoch, yet commit denied.
            assert store.commit_effects == 0
    asyncio.run(scenario())


def test_fresh_explicit_login_after_logout_all_can_recover(tmp_path):
    async def scenario():
        flow, store, oidc = _system(tmp_path)
        old = await flow.begin(None)
        store.now += timedelta(microseconds=1)
        await store.revoke_account("subject-a")
        with pytest.raises(LoginRejected):
            await flow.complete(_cookie(old), _query(old))
        store.now += timedelta(microseconds=1)
        fresh = await flow.begin(_cookie(old))
        result = await flow.complete(_cookie(fresh), _query(fresh))
        assert result.session.session_epoch == result.session.account_epoch == 1
        assert oidc.calls == 2
        assert store.commit_effects == 1
    asyncio.run(scenario())


def test_switch_account_uses_fresh_context_and_retires_only_prior_browser_session(tmp_path):
    async def scenario():
        flow, store, oidc = _system(tmp_path)
        old_token, other_token = "a" * 43, "b" * 43
        store.seed_session(old_token, context="ctx_prior")
        store.seed_session(other_token, context="ctx_other_browser")
        oidc.identity = _identity("subject-b")
        start = await flow.begin(f"{SESSION_COOKIE}={old_token}; role=owner; context_id=client_context".encode())
        result = await flow.complete(_cookie(start, session=old_token), _query(start))
        assert result.session.actor.user_id == "usr_synthetic_b"
        assert result.session.context_id not in ("ctx_prior", "client_context")
        assert result.credential.get_secret_value() not in (old_token, other_token)
        assert store.sessions["ctx_prior"].state == "revoked"
        assert store.sessions["ctx_other_browser"].state == "active"
        reader = ServerSessions(store, POLICY, timeout_seconds=1.0, maximum_cookie_bytes=4096, clock=lambda: store.now)
        with pytest.raises(AccessDenied):
            await reader.resolve_cookie(f"{SESSION_COOKIE}={old_token}".encode())
        assert (await reader.resolve_cookie(f"{SESSION_COOKIE}={other_token}".encode())).context_id == "ctx_other_browser"
    asyncio.run(scenario())


def test_prior_logout_while_callback_waits_cannot_be_undone_by_login_commit(tmp_path):
    async def scenario():
        async with asyncio.timeout(2):
            flow, store, oidc = _system(tmp_path)
            token = "a" * 43
            store.seed_session(token)
            start = await flow.begin(f"{SESSION_COOKIE}={token}".encode())
            oidc.release.clear()
            pending = asyncio.create_task(flow.complete(_cookie(start), _query(start)))
            await oidc.entered.wait()
            await store.revoke_context("ctx_prior")
            oidc.release.set()
            with pytest.raises(LoginRejected):
                await pending
            assert store.commit_effects == 0
            assert store.sessions["ctx_prior"].state == "revoked"
    asyncio.run(scenario())


def test_lost_commit_reply_is_not_success_and_callback_retry_cannot_duplicate_effect(tmp_path):
    async def scenario():
        flow, store, oidc = _system(tmp_path)
        start = await flow.begin(None)
        store.lose_commit_reply = True
        with pytest.raises(RepositoryUnavailable) as caught:
            await flow.complete(_cookie(start), _query(start))
        assert "synthetic-private-lost-commit-reply" not in str(caught.value)
        with pytest.raises(LoginRejected):
            await flow.complete(_cookie(start), _query(start))
        assert store.commit_effects == len(store.sessions) == oidc.calls == 1
    asyncio.run(scenario())


@pytest.mark.parametrize("changes", [
    {"browser_digest": SecretStr("f" * 64)}, {"state_digest": SecretStr("f" * 64)},
    {"issuer": "https://evil.invalid"}, {"client_id": "wrong-client"},
    {"redirect_uri": "https://evil.invalid/callback"}, {"phase": "committed"},
])
def test_corrupt_claim_scope_or_provider_cannot_reach_idp(tmp_path, changes):
    async def scenario():
        flow, store, oidc = _system(tmp_path)
        start = await flow.begin(None)
        store.change_claim = lambda intent: intent.model_copy(update=changes)
        with pytest.raises(RepositoryDataRejected):
            await flow.complete(_cookie(start), _query(start))
        assert oidc.calls == store.commit_effects == 0
    asyncio.run(scenario())


@pytest.mark.parametrize("changes", [
    {"context_id": "ctx_wrong"}, {"credential_digest": SecretStr("f" * 64)},
    {"generation": 1}, {"csrf_token": SecretStr("wrong-proof")},
    {"absolute_expires_at": NOW + timedelta(days=2)},
])
def test_corrupt_commit_reply_never_returns_a_credential(tmp_path, changes):
    async def scenario():
        flow, store, _ = _system(tmp_path)
        start = await flow.begin(None)
        store.change_commit = lambda snapshot: snapshot.model_copy(update=changes)
        with pytest.raises(RepositoryDataRejected):
            await flow.complete(_cookie(start), _query(start))
    asyncio.run(scenario())


@pytest.mark.parametrize("destination", ["https://evil.invalid", "//evil.invalid", "/app/private", "/app?next=evil"])
def test_return_to_rejection_happens_before_persisting_intent(tmp_path, destination):
    async def scenario():
        flow, store, oidc = _system(tmp_path)
        with pytest.raises(AuthInputRejected):
            await flow.begin(None, return_to=destination)
        assert store.begin_commands == []
        assert oidc.calls == 0
    asyncio.run(scenario())


def test_changed_return_allowlist_denies_stored_old_destination_before_idp(tmp_path):
    async def scenario():
        flow, store, oidc = _system(tmp_path)
        start = await flow.begin(None)
        changed = AuthPolicy(default_return_to="/account", return_to_paths=("/account",))
        with pytest.raises(LoginRejected):
            await _flow(tmp_path, store, oidc, policy=changed).complete(_cookie(start), _query(start))
        assert oidc.calls == store.commit_effects == 0
    asyncio.run(scenario())


def test_cancellation_after_claim_does_not_release_callback_for_replay(tmp_path):
    async def scenario():
        async with asyncio.timeout(2):
            flow, store, oidc = _system(tmp_path)
            start = await flow.begin(None)
            oidc.release.clear()
            pending = asyncio.create_task(flow.complete(_cookie(start), _query(start)))
            await oidc.entered.wait()
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
            oidc.release.set()
            with pytest.raises(LoginRejected):
                await flow.complete(_cookie(start), _query(start))
            assert oidc.calls == store.claim_effects == 1
            assert store.commit_effects == 0
    asyncio.run(scenario())


def test_overall_callback_deadline_spends_claim_and_does_not_retry_idp(tmp_path):
    async def scenario():
        store, oidc = SyntheticLoginAuthority(), SyntheticOidc()
        policy = POLICY.model_copy(update={"idp_timeout_seconds": 0.01})
        flow = _flow(tmp_path, store, oidc, policy=policy, timeout_seconds=0.03)
        start = await flow.begin(None)
        oidc.release.clear()
        with pytest.raises(TimeoutError):
            await flow.complete(_cookie(start), _query(start))
        oidc.release.set()
        with pytest.raises(LoginRejected):
            await flow.complete(_cookie(start), _query(start))
        assert oidc.calls == store.claim_effects == 1
        assert store.commit_effects == 0
    asyncio.run(scenario())


def test_missing_csrf_configuration_cannot_issue_session(tmp_path):
    async def scenario():
        flow, store, oidc = _system(tmp_path)
        flow = _flow(tmp_path, store, oidc, csrf_factory=lambda context: SecretStr(""))
        start = await flow.begin(None)
        with pytest.raises(AuthConfigurationRejected):
            await flow.complete(_cookie(start), _query(start))
        assert store.commit_calls == store.commit_effects == 0
    asyncio.run(scenario())


@pytest.mark.parametrize("bad_nonce", [False, True])
@pytest.mark.parametrize("acr", ["urn:aletheia:local-test:mfa", "0", None])
def test_real_authlib_exchange_with_signed_token_then_one_use_commit(tmp_path, bad_nonce, acr):
    async def scenario():
        store = SyntheticLoginAuthority()
        required_acr = "urn:aletheia:local-test:mfa"
        policy = AuthPolicy.model_validate({**POLICY.model_dump(), "required_acr": required_acr})
        key = RSAKey.generate_key(2048, {"kid": "synthetic-key"})
        expected_nonce = []
        requests = []

        def handle(request):
            requests.append(request)
            value = jwt.encode({"alg": "RS256", "kid": "synthetic-key"}, {
                "iss": ISSUER, "sub": "subject-a", "aud": CLIENT_ID,
                "iat": int(NOW.timestamp()), "exp": int(NOW.timestamp()) + 120,
                "nonce": "wrong-nonce" if bad_nonce else expected_nonce[0],
                "email": "synthetic@example.invalid", "email_verified": True,
                **({"acr": acr} if acr is not None else {}),
            }, key)
            return httpx2.Response(200, json={"access_token": "synthetic-access", "token_type": "Bearer", "id_token": value})

        oidc = AuthlibOidcClient(
            _runtime(tmp_path), policy, authorization_endpoint=ISSUER + "/authorize", token_endpoint=ISSUER + "/token",
            public_jwks={"keys": [key.as_dict(private=False)]}, material_expires_at=NOW + timedelta(seconds=300),
            transport_factory=lambda: httpx2.MockTransport(handle), clock=lambda: store.now,
        )
        flow = _flow(tmp_path, store, oidc, policy=policy)
        start = await flow.begin(None)
        expected_nonce.append(parse_qs(urlsplit(start.authorization_url.get_secret_value()).query)["nonce"][0])
        if bad_nonce or acr != required_acr:
            with pytest.raises(OidcRejected):
                await flow.complete(_cookie(start), _query(start))
            assert store.lookup_calls == store.commit_effects == 0
        else:
            result = await flow.complete(_cookie(start), _query(start))
            assert result.session.actor.user_id == "usr_synthetic_a"
            assert store.commit_effects == 1
        with pytest.raises(LoginRejected):
            await flow.complete(_cookie(start), _query(start))
        assert len(requests) == 1
    asyncio.run(scenario())


def test_recovered_new_subject_with_same_email_cannot_inherit_local_account(tmp_path):
    async def scenario():
        flow, store, oidc = _system(tmp_path)
        start = await flow.begin(None)
        oidc.identity = _identity("recovered-new-subject")
        with pytest.raises(IdentityRejected) as caught:
            await flow.complete(_cookie(start), _query(start))
        assert caught.value.reason == "unprovisioned"
        assert store.claim_effects == oidc.calls == 1
        assert store.commit_calls == store.commit_effects == len(store.sessions) == 0
        with pytest.raises(LoginRejected):
            await flow.complete(_cookie(start), _query(start))
        assert oidc.calls == 1
    asyncio.run(scenario())
