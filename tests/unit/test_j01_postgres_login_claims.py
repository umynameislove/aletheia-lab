"""Validate claim inputs before any database transaction is acquired."""

import asyncio
from datetime import UTC, datetime

import pytest
from pydantic import SecretBytes, SecretStr

from aletheia_lab.hosted_auth.login_proof_cipher import LoginProofCipher
from aletheia_lab.hosted_auth.postgres_login_claims import PostgresLoginClaims


class NoDatabase:
    def connection(self):
        raise AssertionError("invalid input reached database")


@pytest.mark.parametrize("digest", ["A" * 64, "x" * 64, "1" * 63, "", None])
def test_invalid_digest_is_rejected_before_database(digest):
    claims = PostgresLoginClaims(NoDatabase(), LoginProofCipher(SecretBytes(b"k" * 32)))
    with pytest.raises(ValueError):
        asyncio.run(claims.claim(browser_digest=digest, state_digest="a" * 64, claim_digest="b" * 64))


@pytest.mark.parametrize("intent_id,expires", [
    ("", datetime.now(UTC)),
    ("../escape", datetime.now(UTC)),
    ("intent", datetime(2030, 1, 1)),
])
def test_invalid_intent_is_rejected_before_database(intent_id, expires):
    claims = PostgresLoginClaims(NoDatabase(), LoginProofCipher(SecretBytes(b"k" * 32)))
    with pytest.raises(ValueError):
        asyncio.run(claims.begin(browser_digest="a" * 64, state_digest="b" * 64,
                                 intent_id=intent_id, expires_at=expires,
                                 nonce=SecretStr("n" * 43), code_verifier=SecretStr("v" * 43)))
