"""Safe action metadata enlisted in the business transaction, never a receipt ledger."""
from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Literal
from uuid import uuid4

from aletheia_lab.hosted_auth.postgres_sessions import Connection, Pool

_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_ACTION = re.compile(r"[a-z][a-z0-9_.:-]{0,63}")
_REQUEST_ID: ContextVar[str | None] = ContextVar("action_request_id", default=None)
_AUDIT = logging.getLogger("aletheia.action")
Outcome = Literal["rejected", "rolled_back", "outcome_unknown"]


def _identifier(value: object) -> str:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise ValueError("A bounded audit identifier is required.")
    return value


@contextmanager
def request_context(request_id: str) -> Iterator[None]:
    token = _REQUEST_ID.set(_identifier(request_id))
    try:
        yield
    finally:
        _REQUEST_ID.reset(token)


def current_request_id() -> str | None:
    return _REQUEST_ID.get()


def report_outcome(outcome: Outcome) -> None:
    """Only fixed failure outcomes; a log entry never asserts a committed effect."""
    if outcome not in ("rejected", "rolled_back", "outcome_unknown"):
        raise ValueError("A known action outcome is required.")
    _AUDIT.info("action_" + outcome, extra={"action_outcome": outcome, "request_id": _REQUEST_ID.get()})


async def record_action(conn: Connection, *, actor_id: str, workspace_id: str, action: str,
                        effect_id: str, revision: int | None = None, operation_id: str | None = None,
                        job_id: str | None = None, request_id: str | None = None,
                        parent_id: str | None = None, subject_id: str | None = None) -> str:
    """Caller commits or rolls back this row with its effect; replay must not append."""
    if conn.autocommit:
        raise RuntimeError("Transactional connection required for action audit.")
    actor_id, workspace_id, effect_id = (_identifier(value) for value in (actor_id, workspace_id, effect_id))
    if not isinstance(action, str) or _ACTION.fullmatch(action) is None:
        raise ValueError("A bounded audit action is required.")
    if revision is not None and (type(revision) is not int or not 0 <= revision <= 9223372036854775807):
        raise ValueError("A nonnegative audit revision is required.")
    operation_id = _identifier(operation_id) if operation_id is not None else "op_" + uuid4().hex
    request_id = _REQUEST_ID.get() if request_id is None else _identifier(request_id)
    job_id = None if job_id is None else _identifier(job_id)
    parent_id = None if parent_id is None else _identifier(parent_id)
    subject_id = None if subject_id is None else _identifier(subject_id)
    audit_id = "audit_" + uuid4().hex
    await conn.execute("INSERT INTO k06_action_audit(audit_id,request_id,operation_id,actor_id,workspace_id,"
        "action,effect_id,revision,job_id,parent_id,subject_id) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (audit_id, request_id, operation_id, actor_id, workspace_id, action, effect_id, revision, job_id, parent_id, subject_id))
    return audit_id


async def is_ready(pool: Pool) -> bool:
    """Cheap read/INSERT-privilege probe; startup owns full migration verification."""
    try:
        async with pool.connection() as conn:
            await conn.execute("SELECT audit_id,request_id,operation_id,actor_id,workspace_id,action,effect_id,"
                "revision,job_id,recorded_at,parent_id,subject_id FROM k06_action_audit LIMIT 0")
            await conn.execute("SELECT version,checksum,signature,applied_at FROM k06_action_audit_migrations LIMIT 0")
            row = await (await conn.execute("SELECT bool_and(has_column_privilege(current_user,"
                "'k06_action_audit',column_name,'INSERT')) FROM unnest(%s::text[]) AS required(column_name)",
                (["audit_id", "request_id", "operation_id", "actor_id", "workspace_id", "action", "effect_id", "revision", "job_id", "parent_id", "subject_id"],))).fetchone()
            return bool(row == (True,))
    except Exception:
        return False
