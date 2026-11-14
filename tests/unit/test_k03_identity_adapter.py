"""Account lookup policy over trusted synthetic identity, not real OIDC."""

import asyncio

import pytest
from pydantic import SecretStr

from aletheia_lab.hosted_auth.identity import AccountResolver, IdentityRejected
from aletheia_lab.hosted_auth.repositories import (
    AccountBinding,
    RepositoryDataRejected,
    RepositoryUnavailable,
    ValidatedIdentity,
)
from aletheia_lab.hosted_auth.settings import AuthPolicy
from aletheia_lab.hosted_contract.session_capabilities import Actor

ISSUER = "https://identity.example.invalid/realm"
SUBJECT = "synthetic-Subject-A"
POLICY = AuthPolicy(default_return_to="/app", return_to_paths=("/app",))


def _identity(**changes):
    return ValidatedIdentity.model_validate({
        "issuer": ISSUER, "subject": SecretStr(SUBJECT),
        "email": SecretStr("synthetic@example.invalid"), "email_verified": True,
        **changes,
    })


def _binding(**changes):
    return AccountBinding.model_validate({
        "issuer": ISSUER, "subject": SecretStr(SUBJECT),
        "actor": Actor(user_id="usr_synthetic_a", display_name="Synthetic Account", email="synthetic@example.invalid", email_verified=True),
        "enabled": True, "account_epoch": 3,
        "onboarding": "needs_workspace", "last_workspace_id": None,
        **changes,
    })


class SyntheticDirectory:
    def __init__(self):
        self.calls = []
        self.binding = _binding()
        self.force_wrong_binding = False

    async def find_identity(self, *, issuer, subject):
        self.calls.append((issuer, subject))
        if self.force_wrong_binding:
            return self.binding
        if self.binding is not None and (issuer, subject) == (self.binding.issuer, self.binding.subject):
            return self.binding
        return None


def _resolver(directory, **changes):
    return AccountResolver(directory, POLICY, expected_issuer=ISSUER, timeout_seconds=changes.get("timeout_seconds", 1.0))


def test_preprovisioned_account_is_returned_without_creating_workspace_or_role():
    async def scenario():
        directory = SyntheticDirectory()
        binding = await _resolver(directory).resolve(_identity())
        assert binding.actor.user_id == "usr_synthetic_a"
        assert binding.account_epoch == 3
        assert binding.onboarding == "needs_workspace"
        assert binding.last_workspace_id is None
        assert "role" not in type(binding).model_fields
        assert directory.calls == [(ISSUER, SecretStr(SUBJECT))]
        assert "synthetic@example.invalid" not in repr(binding)
        assert SUBJECT not in repr(binding)
        assert SUBJECT not in repr(_identity())
    asyncio.run(scenario())


@pytest.mark.parametrize("changes,reason", [
    ({"issuer": "https://other.example.invalid/realm"}, "issuer_mismatch"),
    ({"issuer": ISSUER + "/"}, "issuer_mismatch"),
    ({"email_verified": False}, "email_unverified"),
    ({"email": None}, "email_unverified"),
])
def test_issuer_and_verified_email_policy_deny_before_directory_lookup(changes, reason):
    async def scenario():
        directory = SyntheticDirectory()
        with pytest.raises(IdentityRejected) as caught:
            await _resolver(directory).resolve(_identity(**changes))
        assert caught.value.reason == reason
        assert directory.calls == []
        assert "synthetic@example.invalid" not in str(caught.value)
    asyncio.run(scenario())


@pytest.mark.parametrize("subject", ["synthetic-subject-a", SUBJECT + " ", "other-subject-same-email"])
def test_same_email_or_domain_cannot_link_another_subject(subject):
    async def scenario():
        directory = SyntheticDirectory()
        original = directory.binding
        with pytest.raises(IdentityRejected) as caught:
            await _resolver(directory).resolve(_identity(subject=SecretStr(subject)))
        assert caught.value.reason == "unprovisioned"
        assert directory.binding == original
    asyncio.run(scenario())


def test_unprovisioned_and_currently_disabled_accounts_are_distinct_internal_denials():
    async def scenario():
        directory = SyntheticDirectory()
        resolver = _resolver(directory)
        directory.binding = None
        with pytest.raises(IdentityRejected) as caught:
            await resolver.resolve(_identity())
        assert caught.value.reason == "unprovisioned"
        directory.binding = _binding(enabled=False)
        with pytest.raises(IdentityRejected) as caught:
            await resolver.resolve(_identity())
        assert caught.value.reason == "disabled"
        directory.binding = _binding()
        assert await resolver.resolve(_identity()) == directory.binding
        directory.binding = _binding(enabled=False)
        with pytest.raises(IdentityRejected):
            await resolver.resolve(_identity())
        assert len(directory.calls) == 4
    asyncio.run(scenario())


def test_current_local_email_policy_is_also_checked():
    async def scenario():
        directory = SyntheticDirectory()
        actor = _binding().actor.model_copy(update={"email_verified": False})
        directory.binding = _binding(actor=actor)
        with pytest.raises(IdentityRejected) as caught:
            await _resolver(directory).resolve(_identity())
        assert caught.value.reason == "email_unverified"
    asyncio.run(scenario())


@pytest.mark.parametrize("changes", [
    {"issuer": "https://other.example.invalid/realm"},
    {"subject": SecretStr("another-subject")},
    {"actor": _binding().actor.model_copy(update={"email_verified": "yes"})},
])
def test_wrong_or_corrupt_directory_binding_fails_closed(changes):
    async def scenario():
        directory = SyntheticDirectory()
        directory.force_wrong_binding = True
        directory.binding = _binding().model_copy(update=changes)
        with pytest.raises(RepositoryDataRejected):
            await _resolver(directory).resolve(_identity())
    asyncio.run(scenario())


def test_invalid_internal_identity_model_does_not_bypass_validation():
    async def scenario():
        directory = SyntheticDirectory()
        identity = _identity().model_copy(update={"email_verified": "yes"})
        with pytest.raises(RepositoryDataRejected):
            await _resolver(directory).resolve(identity)
        assert directory.calls == []
    asyncio.run(scenario())


def test_lookup_outage_has_fixed_safe_message_and_no_retry_loop():
    async def scenario():
        directory = SyntheticDirectory()
        calls = []

        async def broken(**kwargs):
            calls.append(1)
            raise RuntimeError("synthetic-private-directory-secret")

        directory.find_identity = broken
        with pytest.raises(RepositoryUnavailable) as caught:
            await _resolver(directory).resolve(_identity())
        assert calls == [1]
        assert "synthetic-private-directory-secret" not in str(caught.value)
        assert caught.value.__suppress_context__ is True
    asyncio.run(scenario())


def test_lookup_deadline_and_external_cancellation():
    async def scenario():
        directory = SyntheticDirectory()

        async def stalled(**kwargs):
            await asyncio.Event().wait()

        directory.find_identity = stalled
        with pytest.raises(TimeoutError):
            await _resolver(directory, timeout_seconds=0.01).resolve(_identity())

        async def cancelled(**kwargs):
            raise asyncio.CancelledError

        directory.find_identity = cancelled
        with pytest.raises(asyncio.CancelledError):
            await _resolver(directory).resolve(_identity())
    asyncio.run(scenario())
