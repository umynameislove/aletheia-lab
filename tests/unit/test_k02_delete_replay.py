"""Deletion receipt replay keeps one canonical effect under current authority."""
from __future__ import annotations

import asyncio
import hashlib
from contextlib import asynccontextmanager
from copy import deepcopy
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from aletheia_lab.hosted.authorization import Authorization, AuthorizationDenied
from aletheia_lab.hosted.postgres_catalog import CatalogIntegrityError
from aletheia_lab.hosted.project_mutations import PostgresProjectMutations
from aletheia_lab.hosted.transactions import TransactionOutcomeUnknown
from aletheia_lab.hosted.workspace_management import WorkspaceConflict

PROJECT = "p3-project-" + "a" * 64
DIGEST = SecretStr("d" * 64)


class Cursor:
    def __init__(self, row=None): self.row = row
    async def fetchone(self): return self.row


class Connection:
    autocommit = False

    def __init__(self):
        self.state = dict(revision=3, deletion_revision=None, tombstone=None, actor=None, records={}, audits=[])
        self.queries = []

    async def execute(self, query, params=()):
        self.queries.append((query, params))
        state = self.state
        if query.startswith("SELECT project_id FROM k05_projects"):
            return Cursor((params[1],))
        if query.startswith("SELECT p.revision,p.deletion_revision"):
            any_tombstone = any(row[3] == "deletion" for row in state["records"].values())
            return Cursor((state["revision"], state["deletion_revision"], state["tombstone"], state["actor"],
                           any_tombstone, state["tombstone"] in state["records"]))
        if query.startswith("INSERT INTO k05_records("):
            state["records"].setdefault(params[1], params)
        elif query.startswith("SELECT workspace_id,record_id"):
            return Cursor(state["records"].get(params[1]))
        elif query.startswith("UPDATE k05_projects SET revision="):
            if params[-1] != state["revision"] or state["deletion_revision"] is not None:
                return Cursor()
            state.update(revision=state["revision"] + 1, deletion_revision=state["revision"] + 1,
                         tombstone=params[0], actor=params[1])
            return Cursor((state["revision"],))
        elif query.startswith("INSERT INTO k06_action_audit"):
            state["audits"].append(params)
        return Cursor()


class Pool:
    def __init__(self):
        self.conn, self.lock, self.lose_ack = Connection(), asyncio.Lock(), False

    @asynccontextmanager
    async def connection(self):
        async with self.lock:
            before = deepcopy(self.conn.state)
            try:
                yield self.conn
            except BaseException:
                self.conn.state = before
                raise
            if self.lose_ack and before["revision"] != self.conn.state["revision"]:
                import psycopg
                self.lose_ack = False
                raise psycopg.OperationalError("Synthetic lost acknowledgement after committed delete")


class Policy:
    denied = False

    async def authorize(self, conn, digest, workspace, action):
        if self.denied or workspace != "ws1" or action != "delete":
            raise AuthorizationDenied()
        return Authorization("owner1", "owner", 1, workspace)

    async def authorize_project(self, conn, auth, workspace, project, action, *, allow_deleted=False):
        if self.denied or project != PROJECT or (conn.state["tombstone"] and not allow_deleted):
            raise AuthorizationDenied()


class Objects:
    def __init__(self):
        self.stages, self.ready_calls, self.finishes = [], [], []
        self.barrier = None

    async def stage(self, workspace, payload, **kwargs):
        staged = SimpleNamespace(descriptor=SimpleNamespace(sha256=hashlib.sha256(payload).hexdigest()))
        self.stages.append(staged)
        if self.barrier is not None:
            entered, release = self.barrier
            if len(self.stages) == 2:
                entered.set()
            await release.wait()
        return staged

    async def ready(self, conn, workspace, staged): self.ready_calls.append(staged)
    async def finish(self, conn, workspace, staged): self.finishes.append(staged)


def _system():
    pool, objects = Pool(), Objects()
    repo = PostgresProjectMutations(pool, SimpleNamespace(objects=objects), audit=True)
    repo._policy = Policy()
    return SimpleNamespace(pool=pool, conn=pool.conn, objects=objects, repo=repo)


async def _delete(system, revision=3):
    return await system.repo.delete(credential_digest=DIGEST, workspace_id="ws1", project_id=PROJECT,
                                    expected_revision=revision)


def test_locked_state_uses_fresh_statement_after_waiting_project_lock():
    async def run():
        tombstone = "p6-deletion-" + "b" * 64

        class WaitedConnection(Connection):
            locked = False

            async def execute(self, query, params=()):
                if query.startswith("SELECT project_id FROM k05_projects"):
                    # The lock waiter resumes after a concurrent winner committed its row and record.
                    self.locked = True
                    self.state.update(revision=4, deletion_revision=4, tombstone=tombstone, actor="owner1",
                                      records={tombstone: ("ws1", tombstone, PROJECT, "deletion")})
                if query.startswith("SELECT p.revision,p.deletion_revision"):
                    assert self.locked and "FOR UPDATE" not in query
                return await super().execute(query, params)

        conn = WaitedConnection()
        row = await PostgresProjectMutations._state(conn, "ws1", PROJECT, lock=True)
        assert row == (4, 4, tombstone, "owner1", True, True)
        assert len(conn.queries) == 2 and conn.queries[0][0].endswith("FOR UPDATE")
        assert all(params == ("ws1", PROJECT) for _, params in conn.queries)
        receipt = PostgresProjectMutations._replay(row, "ws1", PROJECT, 3)
        assert receipt["tombstone_id"] == tombstone and receipt["revision"] == 4
    asyncio.run(run())


def test_lost_ack_replay_returns_identical_saved_receipt_without_second_effect():
    async def run():
        system = _system()
        system.pool.lose_ack = True
        with pytest.raises(TransactionOutcomeUnknown):
            await _delete(system)
        receipt = await _delete(system)
        assert receipt == await _delete(system)
        assert (receipt["revision"], receipt["status"], receipt["retention"]) == (4, "tombstoned", "retained")
        assert receipt["tombstone_id"] == system.conn.state["tombstone"]
        assert len(system.objects.stages) == len(system.objects.ready_calls) == 1
        assert len(system.conn.state["records"]) == len(system.conn.state["audits"]) == 1
        assert system.conn.state["actor"] == "owner1"
    asyncio.run(run())


def test_identical_concurrent_deletes_replay_one_effect_and_release_both_staging_leases():
    async def run():
        system = _system()
        entered, release = asyncio.Event(), asyncio.Event()
        system.objects.barrier = entered, release
        tasks = [asyncio.create_task(_delete(system)) for _ in range(2)]
        await asyncio.wait_for(entered.wait(), 2)
        release.set()
        first, second = await asyncio.gather(*tasks)
        assert first == second and first["revision"] == 4
        assert len(system.objects.stages) == len(system.objects.finishes) == 2
        assert len(system.objects.ready_calls) == len(system.conn.state["audits"]) == len(system.conn.state["records"]) == 1
    asyncio.run(run())


@pytest.mark.parametrize("change,error", [
    ({"denied": True}, AuthorizationDenied), ({"revision": 4}, WorkspaceConflict),
    ({"current_revision": 5}, WorkspaceConflict), ({"actor": None}, CatalogIntegrityError),
    ({"deletion_revision": True}, CatalogIntegrityError), ({"tombstone": "p6-deletion-" + "0" * 64}, CatalogIntegrityError),
])
def test_saved_receipt_requires_current_delete_permission_and_exact_original_fence(change, error):
    async def run():
        system = _system()
        await _delete(system)
        if "denied" in change:
            system.repo._policy.denied = True
        if "current_revision" in change:
            system.conn.state["revision"] = change["current_revision"]
        system.conn.state.update({key: value for key, value in change.items()
                                  if key in ("actor", "deletion_revision", "tombstone")})
        with pytest.raises(error):
            await _delete(system, change.get("revision", 3))
        assert len(system.objects.stages) == len(system.conn.state["audits"]) == 1
    asyncio.run(run())


def test_legacy_tombstone_without_saved_receipt_is_conflict_instead_of_guessed_replay():
    async def run():
        system = _system()
        await _delete(system)
        system.conn.state.update(deletion_revision=None, tombstone=None, actor=None)
        with pytest.raises(WorkspaceConflict):
            await _delete(system)
        assert len(system.objects.stages) == len(system.conn.state["records"]) == 1
    asyncio.run(run())
