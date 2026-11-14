"""Malformed session durations fail before a login can acquire a DB connection."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import cast

import pytest
from pydantic import SecretBytes, SecretStr

from aletheia_lab.hosted_auth.login import CommitLogin
from aletheia_lab.hosted_auth.login_proof_cipher import LoginProofCipher
from aletheia_lab.hosted_auth.postgres_fresh_login import PostgresFreshLogin
from aletheia_lab.hosted_auth.postgres_sessions import Pool
from aletheia_lab.hosted_auth.repositories import SessionRules


class _NoCheckout:
    def connection(self):
        raise AssertionError("invalid session rules touched the database")


@pytest.mark.parametrize('rules', [
    SessionRules(True, 100, 1), SessionRules(100, True, 1), SessionRules(100, 1000, True),
    SessionRules(100, 1000, 100), SessionRules(100, 604801, 1), SessionRules(100, 1000, 0),
])
def test_invalid_fresh_login_policy_is_rejected_before_database_checkout(rules):
    store = PostgresFreshLogin(cast(Pool, _NoCheckout()), LoginProofCipher(SecretBytes(b'k' * 32)))
    command = cast(CommitLogin, SimpleNamespace(browser_digest=SecretStr('a' * 64),
        state_digest=SecretStr('b' * 64), claim_digest=SecretStr('c' * 64),
        credential_digest=SecretStr('d' * 64), rules=rules))
    with pytest.raises(ValueError, match='invalid session policy'):
        asyncio.run(store.commit_login(command))
