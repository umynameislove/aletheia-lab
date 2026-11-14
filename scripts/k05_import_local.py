"""Import a checkpointed local copy into an explicitly migrated hosted schema.

DB URL and the current session credential digest are read from environment only.
No provisioning, automatic DDL, destructive rollback or source write is performed.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path
from typing import cast

from pydantic import SecretStr

from aletheia_lab.hosted.legacy_import import (
    ImportTarget,
    LegacyImporter,
    LegacyImportError,
    LegacySource,
)
from aletheia_lab.hosted.object_store import FilesystemObjectStore
from aletheia_lab.hosted.persistence_migration import migration_schema, verify
from aletheia_lab.hosted.postgres_objects import PostgresObjects
from aletheia_lab.hosted.project_policy_migration import verify as verify_project_policy


def arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-copy", type=Path, required=True)
    parser.add_argument("--mapping", type=Path, required=True)
    parser.add_argument("--object-root", type=Path, required=True)
    parser.add_argument("--schema", required=True)
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--database-env", default="ALETHEIA_HOSTED_DATABASE_URL")
    parser.add_argument("--credential-env", default="ALETHEIA_IMPORT_CREDENTIAL_DIGEST")
    return parser.parse_args(argv)


def mapping(path: Path) -> tuple[tuple[ImportTarget, ...], dict[str, str]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or set(payload) != {"schema_version", "projects", "preview_projects"} or payload["schema_version"] != "k05-local-import/v1" or not isinstance(payload["projects"], list) or not isinstance(payload["preview_projects"], dict):
        raise LegacyImportError("Versioned explicit mapping required")
    projects = tuple(ImportTarget(**value) for value in payload["projects"])
    if not projects or len({project.project_id for project in projects}) != len(projects):
        raise LegacyImportError("One explicit mapping per legacy project required")
    if any(not isinstance(value, str) for value in payload["preview_projects"].values()):
        raise LegacyImportError("Explicit canonical preview mapping required")
    return projects, cast(dict[str, str], payload["preview_projects"])


async def execute(args: argparse.Namespace) -> dict[str, object]:
    from psycopg_pool import AsyncConnectionPool

    schema = migration_schema(args.schema)
    database = os.environ[args.database_env]
    credential = SecretStr(os.environ[args.credential_env])
    targets, previews = mapping(args.mapping)
    with LegacySource(args.source_copy, preview_projects=previews) as source:
        if set(source.project_ids()) != {target.project_id for target in targets}:
            raise LegacyImportError("Mapping must cover every legacy project exactly")
        async with AsyncConnectionPool(database, kwargs={"options": f"-csearch_path={schema} -cstatement_timeout=30000 -clock_timeout=2000"}, min_size=0, max_size=2, max_waiting=2, timeout=5, open=False) as pool:
            await pool.wait(timeout=10)
            async with pool.connection() as conn:
                if not await verify(conn, schema):
                    raise LegacyImportError("Explicit persistence migration must be verified before import")
                if not await verify_project_policy(conn, schema):
                    raise LegacyImportError("Explicit project policy migration must be verified before import")
            objects = PostgresObjects(pool, FilesystemObjectStore(args.object_root))
            importer = LegacyImporter(pool, objects)
            reports = []
            for target in targets:
                project = source.export_project(target.project_id)
                report = await importer.import_project(project, target, credential_digest=credential, batch_id=args.batch_id)
                reports.append(asdict(report))
            return {"schema_version": "k05-import-report/v1", "projects": reports, "parity": "verified", "source": "read_only_copy"}


def main(argv: list[str] | None = None) -> int:
    args = arguments(argv)
    try:
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None) as runner:
            result = runner.run(execute(args))
    except Exception:
        # Driver and validation diagnostics can contain SQL, DSNs or private paths.
        print(json.dumps({"error": "legacy_import_failed", "recovery": "keep_source_copy_and_rerun_same_batch_after_fix"}))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
