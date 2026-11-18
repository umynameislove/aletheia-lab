"""Browser-scoped login transactions with ordered account/session row locks."""
from __future__ import annotations

from typing import cast

from pydantic import SecretStr

from .login import BeginLogin, CommitLogin, LoginIntent
from .login_proof_cipher import LoginProofCipher
from .postgres_sessions import _READ, Connection, Pool, _digest, _snapshot
from .repositories import RepositoryDataRejected, SessionRules, SessionSnapshot

_INTENT = ("intent_id,browser_digest,state_digest,issuer,client_id,redirect_uri,sealed_proof,"
           "created_at,expires_at,browser_epoch,return_to,reauthenticate,phase,previous_context_id")


def _validate_session_rules(rules: SessionRules) -> None:
    if (type(rules.idle_seconds) is not int or type(rules.absolute_seconds) is not int
            or type(rules.rotation_seconds) is not int
            or not 0 < rules.rotation_seconds < rules.idle_seconds <= rules.absolute_seconds <= 604800):
        raise ValueError("invalid session policy")


class PostgresFreshLogin:
    """Serialize each browser flow without blocking unrelated accounts."""

    def __init__(self, pool: Pool, cipher: LoginProofCipher, *, clock_skew_seconds: int = 0) -> None:
        if type(clock_skew_seconds) is not int or not 0 <= clock_skew_seconds <= 60:
            raise ValueError("bounded clock skew required")
        self._pool, self._cipher = pool, cipher
        self._skew = clock_skew_seconds

    @staticmethod
    async def _gate(conn: Connection, browser: str) -> None:
        if conn.autocommit:
            raise RuntimeError("transactional connection required")
        await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended("
                           "current_schema()||':k03-login-browser:'||%s,0))", (browser,))

    def _intent(self, row: tuple[object, ...]) -> LoginIntent:
        if len(row) != 14 or not all(isinstance(row[index], str) for index in (0, 1, 2)) or not isinstance(row[6], bytes):
            raise RepositoryDataRejected()
        intent, browser, state = cast(str, row[0]), cast(str, row[1]), cast(str, row[2])
        nonce, verifier = self._cipher.open(intent_id=intent, browser_digest=browser,
                                             state_digest=state, ciphertext=row[6])
        return LoginIntent.model_validate({
            "intent_id": intent, "browser_digest": SecretStr(browser), "state_digest": SecretStr(state),
            "issuer": row[3], "client_id": row[4], "redirect_uri": row[5], "nonce": nonce,
            "code_verifier": verifier, "created_at": row[7], "expires_at": row[8],
            "browser_epoch": row[9], "return_to": row[10], "reauthenticate": row[11],
            "phase": row[12], "previous_context_id": row[13],
        })

    async def begin_intent(self, command: BeginLogin) -> LoginIntent | None:
        browser, state = _digest(command.browser_digest), _digest(command.state_digest)
        proof = self._cipher.seal(intent_id=command.intent_id, browser_digest=browser, state_digest=state,
                                  nonce=command.nonce, code_verifier=command.code_verifier)
        async with self._pool.connection() as conn:
            await self._gate(conn, browser)
            previous = None
            if command.previous_credential_digest is not None:
                credential = _digest(command.previous_credential_digest)
                prior = await (await conn.execute("SELECT s.context_id FROM j01_session_credentials c "
                    "JOIN j01_sessions s ON s.context_id=c.context_id "
                    "JOIN hosted_identity_bindings a ON a.user_id=s.user_id "
                    "WHERE c.digest=%s AND c.state='active' AND c.generation=s.generation "
                    "AND s.state='active' AND s.session_epoch=a.account_epoch AND a.enabled "
                    "AND s.idle_expires_at>clock_timestamp() AND s.absolute_expires_at>clock_timestamp()",
                    (credential,))).fetchone()
                if prior is not None:
                    if not isinstance(prior[0], str):
                        raise RepositoryDataRejected()
                    previous = prior[0]
                elif command.reauthenticate:
                    return None
            elif command.reauthenticate:
                return None
            existing = await (await conn.execute("SELECT current_context FROM j01_fresh_login_browsers "
                                                "WHERE browser_digest=%s", (browser,))).fetchone()
            if existing is not None and existing[0] is not None and existing[0] != previous:
                # Retained browser bindings must recover after logout/expiry.
                live = await (await conn.execute("SELECT s.context_id FROM j01_sessions s "
                    "JOIN hosted_identity_bindings a ON a.user_id=s.user_id "
                    "WHERE s.context_id=%s AND s.state='active' AND a.enabled "
                    "AND s.session_epoch=a.account_epoch AND s.idle_expires_at>clock_timestamp() "
                    "AND s.absolute_expires_at>clock_timestamp()", (existing[0],))).fetchone()
                if live is not None:
                    return None
            cur = await conn.execute("INSERT INTO j01_fresh_login_browsers VALUES (%s,1,%s,%s) "
                "ON CONFLICT (browser_digest) DO UPDATE SET epoch=j01_fresh_login_browsers.epoch+1, "
                "latest_intent=EXCLUDED.latest_intent,current_context=EXCLUDED.current_context RETURNING epoch", (browser, command.intent_id, previous))
            row = await cur.fetchone()
            if row is None:
                raise RepositoryDataRejected()
            cur = await conn.execute("INSERT INTO j01_fresh_login_intents (" + _INTENT + ") "
                "SELECT %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'pending',%s "
                "WHERE %s <= clock_timestamp() + %s * interval '1 second' AND %s > clock_timestamp() AND "
                "%s <= %s + interval '10 minutes' RETURNING " + _INTENT,
                (command.intent_id, browser, state, command.issuer, command.client_id, command.redirect_uri,
                 proof, command.created_at, command.expires_at, row[0], command.return_to, command.reauthenticate, previous,
                 command.created_at, self._skew, command.expires_at, command.expires_at, command.created_at))
            saved = await cur.fetchone()
            if saved is None:
                raise ValueError("login transaction time invalid")
            return self._intent(saved)

    async def claim_intent(self, *, browser_digest: SecretStr, state_digest: SecretStr,
                           claim_digest: SecretStr) -> LoginIntent | None:
        from psycopg import sql

        browser, state, claim = _digest(browser_digest), _digest(state_digest), _digest(claim_digest)
        async with self._pool.connection() as conn:
            await self._gate(conn, browser)
            cur = await conn.execute(sql.SQL("UPDATE j01_fresh_login_intents i SET phase='claimed',claim_digest=%s "
                "FROM j01_fresh_login_browsers b WHERE b.browser_digest=%s AND i.browser_digest=b.browser_digest "
                "AND i.state_digest=%s AND i.intent_id=b.latest_intent AND i.browser_epoch=b.epoch "
                "AND b.current_context IS NOT DISTINCT FROM i.previous_context_id AND i.phase='pending' AND i.expires_at>clock_timestamp() "
                "RETURNING {}").format(sql.SQL(",").join(sql.Identifier("i", field) for field in _INTENT.split(","))).as_string(),
                (claim, browser, state))
            row = await cur.fetchone()
        # Claim must remain spent even if the ciphertext is corrupt.
        return None if row is None else self._intent(row)

    async def commit_login(self, command: CommitLogin) -> SessionSnapshot | None:
        browser, state = _digest(command.browser_digest), _digest(command.state_digest)
        claim, digest = _digest(command.claim_digest), _digest(command.credential_digest)
        rules = command.rules
        _validate_session_rules(rules)
        async with self._pool.connection() as conn:
            await self._gate(conn, browser)
            # Revocation and begin ordering use one DB clock, independent of allowed API skew.
            query = ("SELECT i.database_created_at,GREATEST(i.created_at,clock_timestamp()),i.previous_context_id FROM j01_fresh_login_intents i "
                "JOIN j01_fresh_login_browsers b ON b.browser_digest=i.browser_digest "
                "WHERE i.intent_id=%s AND i.browser_digest=%s AND i.state_digest=%s AND i.claim_digest=%s "
                "AND i.phase='claimed' AND i.browser_epoch=%s AND i.issuer=%s AND b.epoch=i.browser_epoch "
                "AND b.latest_intent=i.intent_id AND b.current_context IS NOT DISTINCT FROM i.previous_context_id "
                "AND i.created_at <= clock_timestamp() + %s * interval '1 second' AND i.expires_at > clock_timestamp()")
            params = (command.intent_id, browser, state, claim, command.expected_browser_epoch, command.account.issuer, self._skew)
            times = await (await conn.execute(query, params)).fetchone()
            if times is None:
                return None
            account = command.account
            users = {account.actor.user_id}
            if times[2] is not None:
                previous = await (await conn.execute("SELECT user_id FROM j01_sessions WHERE context_id=%s",
                                                     (times[2],))).fetchone()
                if previous is None:
                    return None
                if len(previous) != 1 or not isinstance(previous[0], str):
                    raise RepositoryDataRejected()
                users.add(previous[0])
            # Switching locks both accounts in the same order as membership writers.
            for user in sorted(users):
                await conn.execute("SELECT user_id FROM hosted_identity_bindings WHERE user_id=%s FOR UPDATE",
                                   (user,))
            # Lock waits may outlive the claim; refresh its clock before any effect.
            current = await (await conn.execute(query, params)).fetchone()
            if current is None or current[2] != times[2]:
                return None
            times = current
            cur = await conn.execute("SELECT user_id FROM hosted_identity_bindings WHERE issuer=%s AND subject=%s "
                "AND user_id=%s AND enabled=true AND account_epoch=%s "
                "AND (NOT %s OR email_verified=true) AND (logout_all_at IS NULL OR logout_all_at < %s) FOR UPDATE",
                (account.issuer, account.subject.get_secret_value(), account.actor.user_id, account.account_epoch,
                 command.require_verified_email, times[0]))
            if await cur.fetchone() is None:
                return None
            if times[2] is not None:
                prior = await (await conn.execute("UPDATE j01_sessions s SET state='revoked' "
                    "FROM hosted_identity_bindings a WHERE s.context_id=%s AND s.user_id=a.user_id "
                    "AND s.state='active' AND s.session_epoch=a.account_epoch AND a.enabled "
                    "AND s.idle_expires_at>clock_timestamp() AND s.absolute_expires_at>clock_timestamp() "
                    "RETURNING s.context_id", (times[2],))).fetchone()
                if prior is None:
                    return None
            await conn.execute("INSERT INTO j01_sessions VALUES (%s,%s,%s,'active',%s,%s,%s, "
                "%s+%s*interval '1 second',%s+%s*interval '1 second',0,%s)",
                (command.context_id, account.actor.user_id, command.csrf_token.get_secret_value(),
                 times[1], times[1], times[1], times[1], rules.idle_seconds,
                 times[1], rules.absolute_seconds, account.account_epoch))
            await conn.execute("INSERT INTO j01_session_credentials VALUES (%s,%s,'active',0)",
                               (digest, command.context_id))
            await conn.execute("UPDATE j01_fresh_login_browsers SET current_context=%s WHERE browser_digest=%s",
                               (command.context_id, browser))
            await conn.execute("UPDATE j01_fresh_login_intents SET phase='committed' WHERE intent_id=%s",
                               (command.intent_id,))
            return _snapshot(await (await conn.execute(_READ, (digest,))).fetchone())
