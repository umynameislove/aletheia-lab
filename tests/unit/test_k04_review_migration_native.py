"""Review revision history is contiguous, checksum-bound and backward compatible."""
import asyncio
import hashlib
from pathlib import Path

import pytest

from aletheia_lab.hosted import review_migration as migration


def test_review_v1_sql_is_frozen_and_native_parent_is_an_additive_revision():
    original = Path(migration.__file__).with_name("review_schema.sql").read_bytes()
    assert hashlib.sha256(original).hexdigest() == "c0cfd9ada2b3122cee1eecea71007bc5819661d7679b7fc0eba019be4a4cb491"
    native = Path(migration.__file__).with_name("review_native_schema.sql").read_bytes()
    assert hashlib.sha256(native).hexdigest() == "02f2478a63d66c888b98a2513dd8e45ac4be50de2c6afed324e07d37240b1b65"
    assert migration._REVISIONS == ("review_schema.sql", "review_native_schema.sql", "review_retention_schema.sql")


@pytest.mark.parametrize("change", ["gap", "unknown", "checksum", "signature", "type"])
def test_unknown_forged_or_incomplete_history_is_not_a_valid_prefix(change):
    first, second, _ = migration._checksums()
    rows = [(1, first, "a" * 64), (2, second, "b" * 64)]
    if change == "gap":
        rows = rows[1:]
    elif change == "unknown":
        rows.append((3, second, "c" * 64))
    elif change == "checksum":
        rows[0] = (1, "0" * 64, "a" * 64)
    elif change == "signature":
        rows[0] = (1, first, "forged")
    else:
        rows[0] = (True, first, "a" * 64)
    assert not migration._valid_history(rows, 2)


def test_signatures_bind_preserved_v1_history_and_current_structure(monkeypatch):
    installed_check = (
        "CHECK ((((length(body) >= 1) AND (length(body) <= 4000)) AND (length(btrim(body)) > 0)))"
    )
    restored_check = (
        "CHECK (((length(body) >= 1) AND (length(body) <= 4000) AND (length(btrim(body)) > 0)))"
    )
    body_check = installed_check
    validated = True

    async def catalog_rows(_conn, query):
        if "FROM pg_constraint" in query:
            return [("j01_annotations", "j01_annotations_body_check", "c", body_check, validated)]
        return []

    monkeypatch.setattr(migration, "_rows", catalog_rows)
    first = (1, migration._checksums()[0], "a" * 64)
    installed, restored = asyncio.run(migration._signatures(None, v1=first))
    assert installed != restored
    assert asyncio.run(migration._signatures(None, v1=first)) == (installed, restored)

    body_check = restored_check
    assert asyncio.run(migration._signatures(None, v1=first)) == (restored, installed)
    original_signatures = {installed, restored}
    changed_history = (1, first[1], "b" * 64)
    assert original_signatures.isdisjoint(
        asyncio.run(migration._signatures(None, v1=changed_history))
    )

    validated = False
    assert original_signatures.isdisjoint(asyncio.run(migration._signatures(None, v1=first)))

    validated = True
    body_check = restored_check.replace("4000", "3999")
    assert original_signatures.isdisjoint(asyncio.run(migration._signatures(None, v1=first)))
