"""Experimental atomic browser-bound login claims; not a login transaction store.

This component stores digests, identifiers and encrypted OIDC proofs. Prior
session and session commit must be handled by a future integrated store.
Installing these tables does not enable authentication or authorize a request.
"""

from __future__ import annotations

import re
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from pydantic import SecretStr

from .login_proof_cipher import LoginProofCipher

_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class Cursor(Protocol):
    async def fetchone(self) -> tuple[object, ...] | None: ...


class Connection(Protocol):
    @property
    def autocommit(self) -> bool: ...

    async def execute(self, query: str, params: tuple[object, ...] = ()) -> Cursor: ...


class Pool(Protocol):
    def connection(self) -> AbstractAsyncContextManager[Connection]: ...


@dataclass(frozen=True)
class ClaimedProof:
    intent_id: str
    nonce: SecretStr = field(repr=False)
    code_verifier: SecretStr = field(repr=False)


def _digest(value: str) -> None:
    if not isinstance(value, str) or not _DIGEST.fullmatch(value):
        raise ValueError("sha256 digest required")


class PostgresLoginClaims:
    """Atomic newest-intent and one-winner claim primitive for shared Postgres."""

    def __init__(self, pool: Pool, cipher: LoginProofCipher) -> None:
        self._pool = pool
        self._cipher = cipher

    async def begin(self, *, browser_digest: str, state_digest: str, intent_id: str,
                    expires_at: datetime, nonce: SecretStr, code_verifier: SecretStr) -> int:
        _digest(browser_digest)
        _digest(state_digest)
        if not isinstance(intent_id, str) or not _IDENTIFIER.fullmatch(intent_id):
            raise ValueError("valid intent identifier required")
        if not isinstance(expires_at, datetime) or expires_at.utcoffset() is None:
            raise ValueError("timezone-aware expiration required")
        proof = self._cipher.seal(intent_id=intent_id, browser_digest=browser_digest,
                                  state_digest=state_digest, nonce=nonce, code_verifier=code_verifier)
        async with self._pool.connection() as conn:
            if conn.autocommit:
                raise RuntimeError("transactional connection required")
            cursor = await conn.execute(
                "INSERT INTO j01_login_browsers(browser_digest, epoch, latest_intent) "
                "VALUES (%s, 1, %s) ON CONFLICT (browser_digest) DO UPDATE "
                "SET epoch = j01_login_browsers.epoch + 1, latest_intent = EXCLUDED.latest_intent "
                "RETURNING epoch", (browser_digest, intent_id),
            )
            row = await cursor.fetchone()
            if row is None or type(row[0]) is not int:
                raise RuntimeError("browser epoch unavailable")
            epoch = row[0]
            cursor = await conn.execute(
                "INSERT INTO j01_login_claims "
                "(intent_id, browser_digest, state_digest, browser_epoch, expires_at, phase, sealed_proof) "
                "SELECT %s, %s, %s, %s, %s, 'pending', %s "
                "WHERE %s > clock_timestamp() AND %s <= clock_timestamp() + interval '10 minutes' "
                "RETURNING intent_id",
                (intent_id, browser_digest, state_digest, epoch, expires_at, proof, expires_at, expires_at),
            )
            if await cursor.fetchone() is None:
                raise ValueError("expiration outside login window")
            return epoch

    async def claim(self, *, browser_digest: str, state_digest: str, claim_digest: str) -> ClaimedProof | None:
        _digest(browser_digest)
        _digest(state_digest)
        _digest(claim_digest)
        async with self._pool.connection() as conn:
            if conn.autocommit:
                raise RuntimeError("transactional connection required")
            # The same browser row is locked by begin(), serializing supersession
            # with consumption. Wrong browsers cannot lock or consume the target.
            cursor = await conn.execute(
                "SELECT epoch, latest_intent FROM j01_login_browsers "
                "WHERE browser_digest = %s FOR UPDATE", (browser_digest,),
            )
            browser = await cursor.fetchone()
            if browser is None:
                return None
            cursor = await conn.execute(
                "UPDATE j01_login_claims SET phase = 'claimed', claim_digest = %s "
                "WHERE browser_digest = %s AND state_digest = %s AND browser_epoch = %s "
                "AND intent_id = %s AND phase = 'pending' AND expires_at > clock_timestamp() "
                "RETURNING intent_id, sealed_proof",
                (claim_digest, browser_digest, state_digest, browser[0], browser[1]),
            )
            row = await cursor.fetchone()
            if row is None:
                return None
        # Decrypt after the successful claim commits. Failure must not release
        # the spent callback for a retry with another proof or encryption key.
        if len(row) != 2 or not isinstance(row[0], str) or not isinstance(row[1], bytes):
            raise RuntimeError("invalid stored intent")
        nonce, verifier = self._cipher.open(intent_id=row[0], browser_digest=browser_digest,
                                             state_digest=state_digest, ciphertext=row[1])
        return ClaimedProof(row[0], nonce, verifier)
