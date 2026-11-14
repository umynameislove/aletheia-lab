"""Offline owner-only native capacity fixtures, streamed with finite resource guards.

Authentication bootstrap is synthetic and separate from measured workloads.
Every business record is built and published by the existing native validators;
this importer never invents analyzer output or changes production limits.
"""

from __future__ import annotations

import json
import re
import shutil
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, BinaryIO
from uuid import uuid4

from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool
from pydantic import SecretStr

from aletheia_lab.hosted.content_hashing import content_hasher
from aletheia_lab.hosted_auth.boundary import SESSION_COOKIE, new_opaque_secret
from aletheia_lab.hosted_auth.csrf import CSRF_COOKIE, CsrfTokens
from aletheia_lab.hosted_auth.sessions import credential_digest

from .async_work import run_blocking
from .authorization import PostgresAuthorization
from .canonical_results import CanonicalResults
from .capacity_seed_domain import (
    _record_result_dimensions,
    confirm_mapping,
    owned_scratch,
    source_payload,
)
from .capacity_seed_models import ROLES, SeedPlan
from .native_upload_records import build_initial_upload_result
from .object_store import FilesystemObjectStore
from .persistence_migration import verify as verify_persistence
from .postgres_objects import PostgresObjects
from .postgres_sources import PostgresSources
from .project_policy_migration import verify as verify_policy
from .project_refresh import refresh_uploaded_project
from .recovery_points import file_digest
from .review_migration import verify as verify_reviews
from .upload_analysis import METRIC_OBSERVATION_MODEL, analyze_upload_metrics
from .upload_project import UploadProject
from .upload_publication import UploadPublication


class SeedRejected(ValueError):
    def __init__(self) -> None:
        super().__init__("Seed plan, fresh owned target, or resource guard did not match.")


@dataclass(frozen=True)
class SeedLimits:
    maximum_bytes: int
    maximum_source_rows: int
    maximum_records: int
    maximum_seconds: int = 3600
    free_reserve_bytes: int = 512 * 1024**2

    def validate(self, plan: SeedPlan) -> None:
        expected = plan.snapshots + plan.results + plan.projects * (plan.generations - 1)
        if (
            any(
                type(n) is not int or n <= 0
                for n in (
                    self.maximum_bytes,
                    self.maximum_source_rows,
                    self.maximum_records,
                    self.maximum_seconds,
                    self.free_reserve_bytes,
                )
            )
            or self.maximum_seconds > 604800
            or self.maximum_source_rows < plan.total_source_rows
            or self.maximum_records < expected
        ):
            raise SeedRejected()


def _line(handle: BinaryIO, value: object) -> None:
    handle.write(
        json.dumps(
            value, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":")
        ).encode()
        + b"\n"
    )


async def _database_bytes(conn: AsyncConnection) -> int:
    row = await (
        await conn.execute(
            "SELECT COALESCE(sum(pg_total_relation_size(c.oid)),0) FROM pg_class c "
            "JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=current_schema() AND c.relkind='r'"
        )
    ).fetchone()
    if row is None:
        raise SeedRejected()
    return int(row[0])


async def _preflight(
    pool: AsyncConnectionPool,
    schema: str,
    output: Path,
    plan: SeedPlan,
    limits: SeedLimits,
    require_primary: bool,
) -> None:
    # Validate everything before a directory, identity or business row is created.
    plan = SeedPlan.model_validate_json(plan.model_dump_json())
    limits.validate(plan)
    if (require_primary and plan.primary_gaps()) or re.fullmatch(
        r"k17_seed_[0-9a-f]{32}", schema
    ) is None:
        raise SeedRejected()
    if (
        not output.is_absolute()
        or output.exists()
        or not output.parent.is_dir()
        or output.parent.is_symlink()
    ):
        raise SeedRejected()
    if shutil.disk_usage(output.parent).free < limits.maximum_bytes + limits.free_reserve_bytes:
        raise SeedRejected()
    async with pool.connection() as conn:
        identity = await (
            await conn.execute(
                "SELECT current_schema(),current_user,pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname=%s",
                (schema,),
            )
        ).fetchone()
        if identity is None or identity[0] != schema or identity[1] != identity[2]:
            raise SeedRejected()
        if not all(
            [
                await verify_persistence(conn, schema),
                await verify_policy(conn, schema),
                await verify_reviews(conn, schema),
            ]
        ):
            raise SeedRejected()
        populated = await (
            await conn.execute(
                "SELECT EXISTS(SELECT 1 FROM hosted_identity_bindings) OR EXISTS(SELECT 1 FROM j01_workspaces) OR EXISTS(SELECT 1 FROM k05_projects) OR EXISTS(SELECT 1 FROM j01_sources)"
            )
        ).fetchone()
        if populated != (False,):
            raise SeedRejected()


async def _session(
    conn: AsyncConnection, user: str, tokens: CsrfTokens, plan: SeedPlan
) -> tuple[SecretStr, str, str, str, str]:
    context = "k17ctx_" + uuid4().hex
    raw = new_opaque_secret()
    proof = tokens.issue(context)
    digest = credential_digest(raw)
    inserted = await conn.execute(
        "INSERT INTO j01_sessions(context_id,user_id,csrf_token,state,issued_at,rotated_at,last_seen_at,idle_expires_at,absolute_expires_at,session_epoch,generation) "
        "VALUES(%s,%s,%s,'active',statement_timestamp(),statement_timestamp(),statement_timestamp(),statement_timestamp()+%s*interval '1 second',statement_timestamp()+%s*interval '1 second',0,0) RETURNING absolute_expires_at",
        (context, user, proof.get_secret_value(), plan.idle_seconds, plan.absolute_seconds),
    )
    expiry = await inserted.fetchone()
    if expiry is None or not isinstance(expiry[0], datetime) or expiry[0].utcoffset() is None:
        raise SeedRejected()
    await conn.execute(
        "INSERT INTO j01_session_credentials VALUES(%s,%s,'active',0)",
        (digest.get_secret_value(), context),
    )
    return (
        digest,
        context,
        raw.get_secret_value(),
        proof.get_secret_value(),
        expiry[0].astimezone(UTC).isoformat(),
    )


async def _bootstrap(
    pool: AsyncConnectionPool, plan: SeedPlan, tokens: CsrfTokens, workspace_index: int
) -> tuple[SecretStr, str]:
    workspace = plan.workspace(workspace_index)
    async with pool.connection() as conn:
        await conn.execute(
            "INSERT INTO j01_workspaces(workspace_id,name) VALUES(%s,%s)",
            (workspace, "Synthetic K17 workspace"),
        )
        first = workspace_index * plan.projects_per_workspace * len(ROLES)
        for offset in range(plan.projects_per_workspace * len(ROLES)):
            user = plan.user(first + offset)
            await conn.execute(
                "INSERT INTO hosted_identity_bindings(issuer,subject,user_id,display_name,email,email_verified,enabled,account_epoch,onboarding) "
                "VALUES('https://k17-synthetic.example.invalid',%s,%s,'Synthetic capacity actor',%s,true,true,0,'ready')",
                (user, user, user + "@example.invalid"),
            )
            await conn.execute(
                "INSERT INTO j01_memberships(workspace_id,user_id,role,active,revision) VALUES(%s,%s,%s,true,1)",
                (workspace, user, ROLES[offset % len(ROLES)]),
            )
        digest, context, _, _, _ = await _session(conn, plan.user(first), tokens, plan)
    return digest, context


def _actions(role: str) -> list[str]:
    return (
        ["read", "write", "review", "export", "delete"]
        if role in ("owner", "admin", "analyst")
        else ["read", "review", "export"]
        if role == "reviewer"
        else ["read", "export"]
    )


async def _grants(
    pool: AsyncConnectionPool,
    plan: SeedPlan,
    project_index: int,
    project: str,
    source: str,
    *,
    first: bool,
) -> None:
    workspace = plan.workspace(project_index // plan.projects_per_workspace)
    async with pool.connection() as conn:
        for offset, role in enumerate(ROLES):
            user = plan.user(project_index * len(ROLES) + offset)
            if first:
                await conn.execute(
                    "INSERT INTO j01_project_grants(workspace_id,project_id,user_id,actions,revision) VALUES(%s,%s,%s,%s,0)",
                    (workspace, project, user, _actions(role)),
                )
            await conn.execute(
                "INSERT INTO j01_source_grants(workspace_id,source_id,user_id,actions,revision) VALUES(%s,%s,%s,%s,0)",
                (workspace, source, user, _actions(role)),
            )


class _Budget:
    def __init__(self, limits: SeedLimits, output: Path, pool: AsyncConnectionPool):
        self.limits, self.output, self.pool = limits, output, pool
        self.started = time.monotonic()
        self.logical_written = 0
        self.peak_database_bytes = 0

    def quick(self) -> None:
        if (
            self.logical_written + self.metadata_bytes() > self.limits.maximum_bytes
            or time.monotonic() - self.started > self.limits.maximum_seconds
            or shutil.disk_usage(self.output).free < self.limits.free_reserve_bytes
        ):
            raise SeedRejected()

    def metadata_bytes(self) -> int:
        return sum(
            path.stat().st_size
            for name in (
                "INCOMPLETE",
                "plan.json",
                "inventory.jsonl",
                "latest-projects.jsonl",
                "actors.jsonl",
                "manifest.json",
            )
            if (path := self.output / name).is_file()
        )

    async def check(self) -> None:
        self.quick()
        async with self.pool.connection() as conn:
            database = await _database_bytes(conn)
        self.peak_database_bytes = max(self.peak_database_bytes, database)
        if database + self.logical_written + self.metadata_bytes() > self.limits.maximum_bytes:
            raise SeedRejected()


async def seed_native(
    pool: AsyncConnectionPool,
    *,
    schema: str,
    output: Path,
    plan: SeedPlan,
    csrf_secret: SecretStr,
    limits: SeedLimits,
    require_primary: bool = False,
) -> dict[str, Any]:
    """Fresh dedicated targets only. A partial run stays explicitly incomplete.

    Output contains PRIVATE actor cookies: do not include actors.jsonl in a
    source/evidence handoff or logs. No real IdP or production identity is used.
    """
    tokens = CsrfTokens(csrf_secret)
    await _preflight(pool, schema, output, plan, limits, require_primary)
    output.mkdir(mode=0o700)
    (output / "INCOMPLETE").write_text("Synthetic seed not accepted until final manifest exists.\n")
    (output / "plan.json").write_text(plan.model_dump_json(indent=2) + "\n")
    scratch = owned_scratch(output / "scratch")
    objects = PostgresObjects(pool, FilesystemObjectStore(output / "objects"))
    sources = PostgresSources(
        pool,
        maximum_upload_bytes=16 * 1024**2,
        maximum_expanded_bytes=64 * 1024**2,
        maximum_entries=16,
    )
    worker = UploadProject(
        scratch,
        maximum_upload_bytes=16 * 1024**2,
        maximum_expanded_bytes=64 * 1024**2,
        maximum_entries=16,
    )
    uploads, results = UploadPublication(objects), CanonicalResults(objects)
    access = PostgresAuthorization()
    budget = _Budget(limits, output, pool)
    latest_path = output / "latest-projects.jsonl"
    inventory_path = output / "inventory.jsonl"
    physical_sizes: dict[str, dict[str, Any]] = {
        size: {
            "projects": 0,
            "source_rows_per_generation": plan.source_rows[size],
            "metric_series_per_run": plan.metric_series[size],
            "actual_claims_per_result": 2 * plan.metric_series[size],
            "actual_graph_nodes_per_result": None,
            "actual_graph_edges_per_result": None,
            "minimum_source_zip_bytes": None,
            "maximum_source_zip_bytes": 0,
            "minimum_result_json_bytes": None,
            "maximum_result_json_bytes": 0,
        }
        for size in ("S", "M", "L")
    }
    try:
        with latest_path.open("xb") as latest, inventory_path.open("xb") as inventory:
            for wi in range(plan.workspaces):
                owner, bootstrap_context = await _bootstrap(pool, plan, tokens, wi)
                workspace = plan.workspace(wi)
                for local in range(plan.projects_per_workspace):
                    project_index = wi * plan.projects_per_workspace + local
                    size = plan.size(project_index)
                    dimensions = physical_sizes[size]
                    dimensions["projects"] += 1
                    previous = None
                    previous_payload = None
                    origin = None
                    revision = 0
                    for generation in range(plan.generations):
                        budget.quick()
                        payload = await run_blocking(
                            source_payload, plan, project_index, generation
                        )
                        budget.logical_written += len(payload)
                        budget.quick()
                        source = await sources.put(
                            credential_digest=owner,
                            workspace_id=workspace,
                            request_key=SecretStr(
                                f"{plan.namespace}-source-{project_index}-{generation}"
                            ),
                            payload=payload,
                        )
                        captured = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=generation)
                        if previous is None:
                            preview = await run_blocking(
                                worker.preview, payload, captured_at=captured.isoformat()
                            )
                            snapshot = await run_blocking(
                                worker.confirm,
                                payload,
                                workspace_id=workspace,
                                source_id=source.source_id,
                                mapping=confirm_mapping(preview, plan.metric_series[size]),
                                captured_at=captured.isoformat(),
                            )
                            origin = source.sha256
                        else:
                            assert origin is not None and previous_payload is not None
                            prepared = await run_blocking(
                                refresh_uploaded_project,
                                parent=previous,
                                origin_source_sha256=origin,
                                before_payload=previous_payload,
                                new_payload=payload,
                                source_id=source.source_id,
                                private_root=scratch,
                                captured_at=captured,
                                maximum_upload_bytes=16 * 1024**2,
                                maximum_expanded_bytes=64 * 1024**2,
                                maximum_entries=16,
                            )
                            if prepared.status != "new_snapshot":
                                raise SeedRejected()
                            snapshot = prepared.snapshot
                        prepared_snapshot = await uploads.prepare_snapshot(
                            snapshot, parent=previous
                        )
                        async with pool.connection() as conn:
                            auth = await access.authorize(
                                conn, owner.get_secret_value(), workspace, "confirm"
                            )
                            published = await uploads.enlist_snapshot(
                                conn,
                                authorization=auth,
                                prepared=prepared_snapshot,
                                expected_revision=revision if previous is not None else None,
                            )
                            # The normal job publisher enlists this exact legacy
                            # read projection with the canonical snapshot too.
                            await conn.execute(
                                "INSERT INTO j01_upload_snapshots(snapshot_id,workspace_id,source_id,payload,canonical_record_id) "
                                "VALUES(%s,%s,%s,%s::jsonb,%s)",
                                (
                                    snapshot.snapshot_id,
                                    workspace,
                                    source.source_id,
                                    snapshot.model_dump_json(),
                                    snapshot.snapshot_id,
                                ),
                            )
                        revision = published.revision
                        project = published.project_id
                        await _grants(
                            pool,
                            plan,
                            project_index,
                            project,
                            source.source_id,
                            first=previous is None,
                        )
                        budget.logical_written += prepared_snapshot.staged.descriptor.byte_size
                        if prepared_snapshot.staged_comparison:
                            budget.logical_written += (
                                prepared_snapshot.staged_comparison.descriptor.byte_size
                            )
                        result_ids = []
                        for analysis in range(plan.results_per_generation):
                            budget.quick()
                            question = f"Synthetic observation for project{project_index}, generation{generation}, analysis{analysis}: what is recorded in these validated metrics?"
                            view = await run_blocking(analyze_upload_metrics, snapshot, question)
                            record = await run_blocking(
                                build_initial_upload_result, snapshot, question, view
                            )
                            prepared_result = await results.prepare_initial_result(
                                workspace, snapshot=snapshot, record=record
                            )
                            async with pool.connection() as conn:
                                auth = await access.authorize(
                                    conn, owner.get_secret_value(), workspace, "analyze"
                                )
                                published_result = await results.enlist_result(
                                    conn,
                                    authorization=auth,
                                    prepared=prepared_result,
                                    expected_revision=revision,
                                )
                            revision = published_result.revision
                            budget.logical_written += prepared_result.staged.descriptor.byte_size
                            actual_claims, count = _record_result_dimensions(view, 2 * plan.metric_series[size], prepared_result, dimensions)
                            result_ids.append(record.record_id)
                            _line(
                                inventory,
                                {
                                    "kind": "result",
                                    "workspace_id": workspace,
                                    "project_id": project,
                                    "snapshot_id": snapshot.snapshot_id,
                                    "result_id": record.record_id,
                                    "sha256": prepared_result.staged.descriptor.sha256,
                                    "bytes": count,
                                    "size": size,
                                    "claims": actual_claims,
                                    "evidence": len(view.evidence),
                                    "graph_nodes": len(view.graph.nodes),
                                    "graph_edges": len(view.graph.edges),
                                },
                            )
                        dimensions["minimum_source_zip_bytes"] = min(
                            dimensions["minimum_source_zip_bytes"] or len(payload), len(payload)
                        )
                        dimensions["maximum_source_zip_bytes"] = max(
                            dimensions["maximum_source_zip_bytes"], len(payload)
                        )
                        _line(
                            inventory,
                            {
                                "kind": "generation",
                                "workspace_id": workspace,
                                "project_id": project,
                                "generation": generation,
                                "source_id": source.source_id,
                                "source_sha256": source.sha256,
                                "source_zip_bytes": len(payload),
                                "source_rows": plan.source_rows[size],
                                "snapshot_id": snapshot.snapshot_id,
                                "snapshot_object_sha256": prepared_snapshot.staged.descriptor.sha256,
                                "snapshot_record_sha256": snapshot.record_sha256,
                                "result_ids": result_ids,
                                "size": size,
                            },
                        )
                        previous, previous_payload = snapshot, payload
                        inventory.flush()
                        latest.flush()
                        await budget.check()
                    _line(
                        latest,
                        {
                            "workspace_index": wi,
                            "project_index": project_index,
                            "workspace_id": workspace,
                            "project_id": project,
                            "snapshot_id": snapshot.snapshot_id,
                            "source_id": source.source_id,
                            "source_sha256": source.sha256,
                            "result_id": record.record_id,
                            "claim_id": view.claims[0].id,
                            "evidence_id": view.evidence[0].id,
                            "question": question,
                            "size": size,
                            "revision": revision,
                            "source_rows": plan.source_rows[size],
                            "claims": len(view.claims),
                            "graph_nodes": len(view.graph.nodes),
                        },
                    )
                async with pool.connection() as conn:
                    await conn.execute(
                        "UPDATE j01_sessions SET state='revoked' WHERE context_id=%s",
                        (bootstrap_context,),
                    )
        counts, physical = await _verify_all(pool, objects, plan, budget)
        actors_hash, issued = await _actors(
            pool, latest_path, output / "actors.jsonl", plan, tokens, budget
        )
        await budget.check()
        async with pool.connection() as conn:
            physical["schema_allocated_bytes"] = await _database_bytes(conn)
            active = await (
                await conn.execute(
                    "SELECT count(*),count(DISTINCT user_id) FROM j01_sessions WHERE state='active'"
                )
            ).fetchone()
            if active != (plan.principals, plan.principals):
                raise SeedRejected()
        if physical["unique_source_payloads"] != plan.snapshots:
            raise SeedRejected()
        manifest = {
            "schema_version": "k17-native-seed-manifest/v1",
            "plan_sha256": plan.fingerprint(),
            "namespace": plan.namespace,
            "schema": schema,
            "synthetic_authentication_bootstrap": True,
            "real_idp_exercised": False,
            "construction": "Genuine ZIP->native confirm/refresh->exact pinned metric analyzer->typed canonical records/projections",
            "follow_up_chains_generated": False,
            "results_are_independent_questions_per_snapshot": True,
            "primary_dataset_qualified": False,
            "primary_gaps": plan.primary_gaps(),
            "logical_counts": counts,
            "physical": physical,
            "strata": physical_sizes,
            "actors_file_sha256": actors_hash,
            "inventory_sha256": file_digest(inventory_path),
            "latest_projects_sha256": file_digest(latest_path),
            "plan_file_sha256": file_digest(output / "plan.json"),
            "actors_issued": issued,
            "session_idle_seconds": plan.idle_seconds,
            "session_absolute_seconds": plan.absolute_seconds,
            "session_renewal_required_for_long_runs": True,
            "session_issuance_after_dataset_import": True,
            "active_distinct_actor_sessions_verified": True,
            "elapsed_seconds": time.monotonic() - budget.started,
            "peak_checked_schema_bytes": budget.peak_database_bytes,
            "maximum_bytes": limits.maximum_bytes,
            "object_root": str(objects.store.root),
            "private_files_do_not_share": ["actors.jsonl"],
            "status": "generated_not_capacity_accepted",
        }
        encoded = json.dumps(
            manifest, ensure_ascii=True, sort_keys=True, separators=(",", ":")
        ).encode()
        manifest["dataset_sha256"] = content_hasher(encoded).hexdigest()
        (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        await budget.check()
        (output / "INCOMPLETE").unlink()
        return manifest
    finally:
        shutil.rmtree(scratch)
        _secure_seed_files(output)


async def _verify_all(
    pool: AsyncConnectionPool, objects: PostgresObjects, plan: SeedPlan, budget: _Budget
) -> tuple[dict[str, int], dict[str, Any]]:
    async with pool.connection() as conn:
        counts = await _verify_seed_counts(conn, plan)
        malformed = await (
            await conn.execute(
                "SELECT EXISTS(SELECT 1 FROM k05_records r LEFT JOIN k05_records s ON s.workspace_id=r.workspace_id AND s.project_id=r.project_id AND s.record_id=r.snapshot_id WHERE r.record_type='upload_result' AND (s.record_type IS DISTINCT FROM 'upload_snapshot'))"
            )
        ).fetchone()
        if malformed != (False,):
            raise SeedRejected()
        projections = await (
            await conn.execute(
                "SELECT EXISTS(SELECT 1 FROM j01_upload_snapshots p LEFT JOIN k05_records r "
                "ON r.workspace_id=p.workspace_id AND r.record_id=p.canonical_record_id WHERE r.record_type IS DISTINCT FROM 'upload_snapshot' "
                "OR r.source_id IS DISTINCT FROM p.source_id OR r.record_id IS DISTINCT FROM p.snapshot_id "
                "OR p.payload->>'snapshot_id' IS DISTINCT FROM p.snapshot_id OR p.payload->>'workspace_id' IS DISTINCT FROM p.workspace_id)"
            )
        ).fetchone()
        if projections != (False,):
            raise SeedRejected()
        source_hash = content_hasher()
        logical_source = physical_source = 0
        async with conn.cursor(name="k17_source_hashes") as cursor:
            await cursor.execute(
                "SELECT workspace_id,source_id,sha256,payload,pg_column_size(payload) FROM j01_sources ORDER BY workspace_id,source_id"
            )
            async for workspace, source, digest, payload, stored in cursor:
                budget.quick()
                payload = bytes(payload)
                if content_hasher(payload).hexdigest() != digest:
                    raise SeedRejected()
                source_hash.update(
                    json.dumps([workspace, source, digest], separators=(",", ":")).encode() + b"\n"
                )
                logical_source += len(payload)
                physical_source += stored
        source_count = await (
            await conn.execute("SELECT count(DISTINCT sha256) FROM j01_sources")
        ).fetchone()
        if source_count is None:
            raise SeedRejected()
        unique_sources = source_count[0]
        unique_objects = object_bytes = 0
        object_hash = content_hasher()
        async with conn.cursor(name="k17_object_hashes") as cursor:
            await cursor.execute(
                "SELECT sha256,min(byte_size),max(byte_size) FROM k05_objects GROUP BY sha256 ORDER BY sha256"
            )
            async for digest, minimum, maximum in cursor:
                budget.quick()
                if (
                    minimum != maximum
                    or objects.store.path(digest).stat().st_size != minimum
                    or await run_blocking(file_digest, objects.store.path(digest)) != digest
                ):
                    raise SeedRejected()
                object_hash.update(
                    json.dumps([digest, minimum], separators=(",", ":")).encode() + b"\n"
                )
                unique_objects += 1
                object_bytes += minimum
        metadata_count = await (await conn.execute("SELECT count(*) FROM k05_objects")).fetchone()
        if metadata_count is None:
            raise SeedRejected()
        metadata_objects = metadata_count[0]
        schema_bytes = await _database_bytes(conn)
    return counts, {
        "logical_source_zip_bytes": logical_source,
        "postgres_source_column_bytes": physical_source,
        "schema_allocated_bytes": schema_bytes,
        "unique_source_payloads": unique_sources,
        "source_scope_hash_sha256": source_hash.hexdigest(),
        "metadata_object_rows": metadata_objects,
        "unique_physical_object_files": unique_objects,
        "physical_object_file_bytes": object_bytes,
        "object_hash_inventory_sha256": object_hash.hexdigest(),
        "object_metadata_to_physical_ratio": metadata_objects / unique_objects
        if unique_objects
        else 0,
        "all_source_payload_hashes_verified": True,
        "all_object_file_hashes_verified": True,
        "physical_source_bytes_note": "pg_column_size and total schema allocation are reported separately from logical ZIP bytes; no logical count is presented as freed/unique disk bytes.",
    }


async def _actors(
    pool: AsyncConnectionPool,
    latest: Path,
    path: Path,
    plan: SeedPlan,
    tokens: CsrfTokens,
    budget: _Budget,
) -> tuple[str, dict[str, Any]]:
    # Keep only one project's actor rows and one foreign workspace row in memory.
    first_by_workspace: dict[int, dict[str, Any]] = {}
    with latest.open("r") as handle:
        for line in handle:
            row = json.loads(line)
            first_by_workspace.setdefault(row["workspace_index"], row)
    roles = {role: 0 for role in ROLES}
    sizes = {size: 0 for size in ("S", "M", "L")}
    with path.open("xb") as output:
        path.chmod(0o600)
        with latest.open("r") as projects:
            for line in projects:
                budget.quick()
                row = json.loads(line)
                foreign = first_by_workspace[(row["workspace_index"] + 1) % plan.workspaces]
                for offset, role in enumerate(ROLES):
                    index = row["project_index"] * len(ROLES) + offset
                    user = plan.user(index)
                    async with pool.connection() as conn:
                        _, context, raw, proof, absolute_expiry = await _session(
                            conn, user, tokens, plan
                        )
                    values = {
                        key: str(row[key])
                        for key in (
                            "workspace_id",
                            "project_id",
                            "snapshot_id",
                            "result_id",
                            "claim_id",
                            "evidence_id",
                            "source_id",
                            "source_sha256",
                            "source_rows",
                            "claims",
                            "graph_nodes",
                            "revision",
                            "question",
                        )
                    }
                    values.update(
                        context_id=context,
                        absolute_expires_at=absolute_expiry,
                        analysis_model=METRIC_OBSERVATION_MODEL,
                        foreign_workspace_id=foreign["workspace_id"],
                        foreign_project_id=foreign["project_id"],
                        foreign_result_id=foreign["result_id"],
                    )
                    _line(
                        output,
                        {
                            "actor_id": user,
                            "role": role,
                            "size": row["size"],
                            "cookie": f"{SESSION_COOKIE}={raw}; {CSRF_COOKIE}={proof}",
                            "csrf": proof,
                            "values": values,
                        },
                    )
                    roles[role] += 1
                    sizes[row["size"]] += 1
                output.flush()
                budget.quick()
    return file_digest(path), {
        "count": sum(roles.values()),
        "roles": roles,
        "sizes": sizes,
        "unique_identity_per_actor": True,
        "unique_session_per_actor": True,
        "hard_expiry_is_not_extended_by_fixture": True,
    }
async def _verify_seed_counts(conn: Any, plan: SeedPlan) -> dict[str, int]:
    counts = {}
    for name, query in {
        "principals": "SELECT count(*) FROM hosted_identity_bindings",
        "workspaces": "SELECT count(*) FROM j01_workspaces",
        "projects": "SELECT count(*) FROM k05_projects",
        "snapshots": "SELECT count(*) FROM k05_records WHERE record_type='upload_snapshot'",
        "results": "SELECT count(*) FROM k05_records WHERE record_type='upload_result'",
        "sources": "SELECT count(*) FROM j01_sources",
        "projections": "SELECT count(*) FROM k10_result_views",
        "upload_projections": "SELECT count(*) FROM j01_upload_snapshots",
    }.items():
        counted = await (await conn.execute(query)).fetchone()
        if counted is None:
            raise SeedRejected()
        counts[name] = counted[0]
    if counts != {
        "principals": plan.principals,
        "workspaces": plan.workspaces,
        "projects": plan.projects,
        "snapshots": plan.snapshots,
        "results": plan.results,
        "sources": plan.snapshots,
        "projections": plan.results,
        "upload_projections": plan.snapshots,
    }:
        raise SeedRejected()
    return counts

def _secure_seed_files(output: Path) -> None:
    # Actor credentials and all intermediate inventories remain private,
    # including on failure. A partial database requires owner disposal.
    for path in output.iterdir():
        if path.is_file():
            path.chmod(0o600)

