"""Review input boundaries, author ownership, append CAS and bounded history."""
import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest
from pydantic import SecretStr

from aletheia_lab.hosted.authorization import AuthorizationDenied
from aletheia_lab.hosted.review_repository import PostgresReviews
from aletheia_lab.hosted.workspace_management import WorkspaceConflict


class Cursor:
    def __init__(self, rows):
        self.rows = iter(rows)

    async def fetchone(self):
        return next(self.rows, None)


class Pool:
    autocommit = False

    autocommit = False

    def __init__(self, scripts):
        self.scripts = iter(scripts)
        self.queries = []

    @asynccontextmanager
    async def connection(self):
        yield self

    async def execute(self, query, params=()):
        self.queries.append((query, params))
        if query.startswith("SET LOCAL ") and "timeout" in query:
            return Cursor([])
        if query.startswith("SELECT set_config(") and "timeout" in query:
            return Cursor([])
        return Cursor(next(self.scripts))


class Reviews(PostgresReviews):
    async def _load_result(self, *args):
        return "project"

    async def _current(self, *args):
        return "author"


@pytest.mark.parametrize("body,status,revision,annotation", [
    ("", "comment", 0, "ann"), ("  ", "comment", 0, "ann"), ("x" * 4001, "comment", 0, "ann"),
    ("body", "unknown", 0, "ann"), ("body", [], 0, "ann"), ("body", "comment", True, "ann"),
    ("body", "comment", -1, "ann"), ("body", "comment", 0, "bad/id"),
])
def test_invalid_review_input_never_reads_store(body, status, revision, annotation):
    pool = Pool([])
    with pytest.raises(ValueError):
        asyncio.run(PostgresReviews(pool, None).put(credential_digest=SecretStr("a" * 64), workspace_id="wa",
            result_id="result", annotation_id=annotation, body=body, status=status, expected_revision=revision))
    assert not pool.queries


@pytest.mark.parametrize("prior,expected,error", [(None, 1, WorkspaceConflict), ((2, "author"), 1, WorkspaceConflict),
                                                 ((1, "another-author"), 1, AuthorizationDenied)])
def test_stale_revision_or_another_author_never_writes(prior, expected, error):
    pool = Pool([[prior] if prior is not None else []])
    with pytest.raises(error):
        asyncio.run(Reviews(pool, None).put(credential_digest=SecretStr("a" * 64), workspace_id="wa", result_id="result",
            annotation_id="ann", body="Review", status="comment", expected_revision=expected))
    assert all(not query.startswith("INSERT") for query, _ in pool.queries)


@pytest.mark.parametrize("prior,expected", [(None, 0), ((1, "author"), 1)])
def test_review_appends_next_revision_and_preserves_plain_text(prior, expected):
    text = "<b>untrusted text</b>"
    now = datetime.now(UTC)
    pool = Pool([[prior] if prior is not None else [], [(expected + 1, "author", text, "comment", now)]])
    value = asyncio.run(Reviews(pool, None).put(credential_digest=SecretStr("a" * 64), workspace_id="wa", result_id="result",
        annotation_id="ann", body=text, status="comment", expected_revision=expected))
    assert value["body"] == text and value["revision"] == expected + 1
    assert value["author_id"] == "author" and value["created_at"] == now
    assert all(not query.startswith(("UPDATE", "DELETE")) for query, _ in pool.queries)


def test_duplicate_insert_loser_is_a_revision_conflict():
    pool = Pool([[], []])
    with pytest.raises(WorkspaceConflict):
        asyncio.run(Reviews(pool, None).put(credential_digest=SecretStr("a" * 64), workspace_id="wa", result_id="result",
            annotation_id="ann", body="Review", status="comment", expected_revision=0))


def test_history_is_bounded_and_reports_only_scoped_next_revision():
    now = datetime.now(UTC)
    pool = Pool([[(1, "author", "First", "comment", now), (2, "author", "Second", "approved", now)]])
    value = asyncio.run(Reviews(pool, None).list_history(credential_digest=SecretStr("a" * 64), workspace_id="wa",
        result_id="result", annotation_id="ann", limit=1))
    assert [item["revision"] for item in value["items"]] == [1] and value["next_after_revision"] == 1
    assert pool.queries[0][1] == ("wa", "project", "result", "ann", 0, 2)


@pytest.mark.parametrize("limit", [0, 201, True])
def test_invalid_history_limit_never_reads_store(limit):
    pool = Pool([])
    with pytest.raises(ValueError):
        asyncio.run(PostgresReviews(pool, None).list_history(credential_digest=SecretStr("a" * 64), workspace_id="wa",
            result_id="result", annotation_id="ann", limit=limit))
    assert not pool.queries
