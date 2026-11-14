"""Explicit ASGI factory for the experimental hosted runtime.

Database schema and account provisioning are operator responsibilities.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import os
import ssl
import sys
from collections.abc import Mapping
from contextlib import AsyncExitStack, suppress
from dataclasses import dataclass
from pathlib import Path

import httpx2
from pydantic import SecretBytes, SecretStr

from aletheia_lab.hosted_auth.abuse import AuthAdmission, BoundedOidc, RatePolicy
from aletheia_lab.hosted_auth.admission_http import admission_key
from aletheia_lab.hosted_auth.clock_guard import probe_clock
from aletheia_lab.hosted_auth.csrf import CsrfTokens
from aletheia_lab.hosted_auth.identity import AccountResolver
from aletheia_lab.hosted_auth.login import LoginCoordinator
from aletheia_lab.hosted_auth.login_proof_cipher import LoginProofCipher
from aletheia_lab.hosted_auth.oidc_discovery import RefreshingOidc
from aletheia_lab.hosted_auth.postgres_admission import PostgresAuthAdmission
from aletheia_lab.hosted_auth.postgres_fresh_login import PostgresFreshLogin
from aletheia_lab.hosted_auth.postgres_identity import PostgresIdentityDirectory
from aletheia_lab.hosted_auth.postgres_sessions import Pool, PostgresSessionStore
from aletheia_lab.hosted_auth.sessions import ServerSessions
from aletheia_lab.hosted_auth.settings import (
    AuthConfigurationRejected,
    AuthPolicy,
    load_auth_policy,
)
from aletheia_lab.hosted_contract.runtime_management import WorkspacePolicyLimits
from aletheia_lab.hosted_contract.session_capabilities import Limits

from .action_audit import is_ready as audit_is_ready
from .action_audit_migration import verify as verify_action_audit
from .application import AdapterLimits, HostedApplication, Send
from .catalog_access import CatalogAccess
from .composition import Application
from .cursors import ScopedCursors
from .edge import EdgeGuard
from .job_updates import PostgresJobUpdates
from .object_store import FilesystemObjectStore
from .observability import ObservedApplication, process_telemetry
from .persistence_migration import verify as verify_catalog
from .postgres_catalog import PostgresCatalog
from .postgres_jobs import PostgresJobs
from .postgres_objects import PostgresObjects
from .postgres_pool import postgres_pool
from .postgres_sources import PostgresSources
from .postgres_workspaces import PostgresWorkspaceAccess
from .preview_leases import PreviewLeases
from .project_access import PostgresProjectAccess
from .project_mutations import PostgresProjectMutations
from .project_policy_migration import verify as verify_project_policy
from .report_renderer import ReportRenderer
from .request_boundary import Receive
from .retention import PostgresRetention
from .review_migration import verify as verify_reviews
from .review_repository import PostgresReviews
from .runtime_binding import verify_runtime_binding
from .runtime_management import RuntimeManagement
from .settings import RuntimeSettings, load_runtime_settings, resolve_runtime_secrets
from .upload_profile import upload_profile
from .upload_runtime import compose_upload_runtime
from .workspace_management import PostgresWorkspaceManagement

_LOG = logging.getLogger(__name__)


class ManagedRuntime:
    """Acquire resources per ASGI process and release them after its lifespan."""

    def __init__(self, environment: Mapping[str, str]) -> None:
        self._env = dict(environment)
        self._settings = load_runtime_settings(Path(self._env['ALETHEIA_HOSTED_CONFIG']))
        self._policy = load_auth_policy(Path(self._env['ALETHEIA_HOSTED_AUTH_POLICY']))
        self._policy.require_runtime(self._settings)
        namespace = self._policy.admission_namespace
        if namespace is None:
            raise AuthConfigurationRejected()
        self._admission_namespace = namespace
        self._app: Application | None = None
        self._observed = ObservedApplication(self._serve_request, telemetry=process_telemetry())
        self._used = False
        api = self._settings.api
        self._fallback = EdgeGuard(self._settings.public_origin, HostedApplication(None, limits=AdapterLimits(
            backend_timeout_seconds=api.backend_timeout_seconds, maximum_response_bytes=api.maximum_response_bytes,
            maximum_cookie_bytes=api.maximum_cookie_bytes)))

    async def _catalog_resources(self, stack: AsyncExitStack, pool: Pool, cursors: ScopedCursors,
                                 audit_enabled: bool) -> tuple[PostgresCatalog | None, CatalogAccess | None,
                                 PostgresProjectAccess | None, PostgresReviews | None,
                                 PostgresProjectMutations | None, PostgresRetention | None]:
        settings = self._settings
        catalog = None
        catalog_access = project_access = reviews = project_mutations = None
        retention = None
        if settings.storage.expected_schema is not None:
            async with asyncio.timeout(settings.api.startup_timeout_seconds):
                async with pool.connection() as conn:
                    if not (await verify_catalog(conn, settings.storage.expected_schema)
                            and await verify_project_policy(conn, settings.storage.expected_schema)
                            and await verify_reviews(conn, settings.storage.expected_schema)
                            and (not audit_enabled or await verify_action_audit(conn, settings.storage.expected_schema))):
                        raise RuntimeError('Canonical catalog is unavailable.')
            objects = PostgresObjects(pool, FilesystemObjectStore(Path(settings.storage.object_root) / 'catalog'))
            catalog = PostgresCatalog(pool, objects)
            renderer = ReportRenderer(timeout_seconds=min(15, settings.api.backend_timeout_seconds))
            stack.push_async_callback(renderer.aclose)
            catalog_access = CatalogAccess(catalog, cursors, renderer)
            project_access = PostgresProjectAccess(pool, audit=audit_enabled)
            reviews = PostgresReviews(pool, catalog, audit=audit_enabled)
            project_mutations = PostgresProjectMutations(pool, catalog, audit=audit_enabled)
            retention = PostgresRetention(pool, catalog)
        return catalog, catalog_access, project_access, reviews, project_mutations, retention

    async def _compose(self, stack: AsyncExitStack) -> Application:
        _require_selector_loop()
        settings, policy = self._settings, self._policy
        audit_mode = self._env.get("ALETHEIA_HOSTED_ACTION_AUDIT", "disabled")
        if audit_mode not in ("enabled", "disabled") or (audit_mode == "enabled" and settings.storage.expected_schema is None):
            raise ValueError("Action audit configuration is unavailable.")
        audit_enabled = audit_mode == "enabled"
        secrets = resolve_runtime_secrets(settings, self._env)
        key = base64.b64decode(self._env['ALETHEIA_HOSTED_LOGIN_PROOF_KEY'], validate=True)
        cipher = LoginProofCipher(SecretBytes(key))
        tokens = CsrfTokens.from_environment(policy, self._env)
        rate_key = admission_key(self._env.get(policy.admission_secret_env, ''))
        pool = await stack.enter_async_context(postgres_pool(settings, self._env, role='api'))
        process_telemetry().bind_pool(pool)
        process_telemetry().watch_loop(asyncio.get_running_loop())
        async with asyncio.timeout(settings.api.startup_timeout_seconds):
            if not await verify_runtime_binding(pool, settings.storage, strict=settings.environment == 'staging'):
                raise RuntimeError('Runtime database binding is unavailable.')
        cursors = ScopedCursors(SecretStr(self._env[policy.csrf_secret_env]))
        catalog, catalog_access, project_access, reviews, project_mutations, retention = await self._catalog_resources(stack, pool, cursors, audit_enabled)
        management = PostgresWorkspaceManagement(pool, cursors=cursors, audit=audit_enabled)
        rate_policy = RatePolicy(policy.admission_global_attempts, policy.admission_subject_attempts,
                                 policy.admission_window_seconds)
        rate_store = PostgresAuthAdmission(pool, namespace=self._admission_namespace)
        async with asyncio.timeout(settings.api.backend_timeout_seconds):
            await rate_store.prepare(rate_policy)
        admission = AuthAdmission(rate_store, rate_policy, timeout_seconds=settings.api.backend_timeout_seconds)
        ca = self._env.get('ALETHEIA_HOSTED_IDP_CA_FILE')
        tls = ssl.create_default_context(cafile=ca) if ca else ssl.create_default_context()
        material = RefreshingOidc(settings, policy, client_secret=secrets.oidc_client_secret,
                                  transport_factory=lambda:httpx2.AsyncHTTPTransport(verify=tls))
        async with asyncio.timeout(policy.idp_timeout_seconds):
            await material._current()
        logout_mode = self._env.get('ALETHEIA_HOSTED_IDP_LOGOUT', 'disabled')
        if logout_mode not in ('enabled', 'disabled'):
            raise ValueError('invalid identity logout mode')
        if logout_mode == 'enabled':
            async with asyncio.timeout(policy.idp_timeout_seconds):
                await material.end_session_url()
        oidc = BoundedOidc(material, timeout_seconds=policy.idp_timeout_seconds,
            maximum_inflight=policy.idp_maximum_inflight,
            initial_backoff_seconds=policy.idp_initial_backoff_seconds,
            maximum_backoff_seconds=policy.idp_maximum_backoff_seconds)
        session_store = PostgresSessionStore(pool)
        sessions = ServerSessions(session_store, policy, timeout_seconds=settings.api.backend_timeout_seconds,
                                  maximum_cookie_bytes=settings.api.maximum_cookie_bytes)
        workspaces = PostgresWorkspaceAccess(pool, sessions)
        sources = PostgresSources(pool, maximum_upload_bytes=200*1024*1024,
                                   maximum_expanded_bytes=512*1024*1024, maximum_entries=100000, audit=audit_enabled, catalog=catalog)
        jobs = PostgresJobs(pool, sources, audit=audit_enabled, catalog=catalog,
                            execution=True, analysis_model='upload-snapshot-metric-observation/v2')
        job_updates = PostgresJobUpdates(jobs, cursors)
        preview_leases = PreviewLeases(sources)
        async with asyncio.timeout(settings.api.startup_timeout_seconds):
            if not await jobs.execution_ready():
                raise RuntimeError('Current job execution schema is unavailable.')
            if not await job_updates.ready() or (retention is not None and not await retention.is_ready()):
                raise RuntimeError('Current polling or retention schema is unavailable.')
            resumable_uploads = await upload_profile(sources, preview_leases)
            follow_up_available = catalog is not None and await jobs.follow_up_ready()
            runtime_management = None
            if catalog is not None:
                runtime_management = RuntimeManagement(pool, audit=audit_enabled, platform_limits=WorkspacePolicyLimits(
                    maximum_upload_bytes=200*1024*1024 if resumable_uploads else 10*1024*1024,
                    maximum_workspace_bytes=sources.maximum_workspace_bytes))
                if not await runtime_management.is_ready():
                    raise RuntimeError('Runtime operation schema is unavailable.')
        accounts = AccountResolver(PostgresIdentityDirectory(pool), policy, expected_issuer=settings.identity.issuer,
                                   timeout_seconds=settings.api.backend_timeout_seconds)
        flow = LoginCoordinator(PostgresFreshLogin(pool, cipher, clock_skew_seconds=policy.clock_skew_seconds),
            oidc, accounts, settings, policy, csrf_factory=tokens.issue,
            timeout_seconds=settings.api.backend_timeout_seconds)
        limits = Limits(question_chars=4000, upload_bundle_bytes=200*1024*1024 if resumable_uploads else 10*1024*1024, page_max=200, job_poll_min_ms=2000)
        readiness = _RuntimeReadiness(settings, policy, pool, jobs, job_updates, retention,
                                      management, sources, preview_leases, resumable_uploads,
                                      follow_up_available, audit_enabled, catalog_access,
                                      runtime_management, rate_store, rate_policy)
        clock = readiness.clock
        return compose_upload_runtime(settings=settings, sessions=sessions, workspaces=workspaces,
            sources=sources, jobs=jobs, tokens=tokens,
            coordinator=flow, auth_policy=policy, limits=limits, clock_check=clock,
            admission=admission, admission_subject_key=rate_key,
            idp_logout_url=material.end_session_url if logout_mode == 'enabled' else None,
            membership_management=management, catalog_access=catalog_access,
            project_access=project_access, reviews=reviews, project_mutations=project_mutations,
            resumable_uploads=resumable_uploads, follow_up_available=follow_up_available,
            runtime_management=runtime_management, job_updates=job_updates, retention=retention)

    async def _close_after_lifespan(
        self, stack: AsyncExitStack, shutdown: Mapping[str, object] | None,
    ) -> Mapping[str, object] | None:
        try:
            async with asyncio.timeout(self._settings.api.resource_close_timeout_seconds):
                await stack.aclose()
        except Exception:
            return {'type':'lifespan.shutdown.failed','message':'Runtime shutdown did not complete.'}
        return shutdown

    async def _serve_request(self, scope: Mapping[str, object], receive: Receive, send: Send) -> None:
        await (self._app or self._fallback)(scope, receive, send)

    async def __call__(self, scope: Mapping[str, object], receive: Receive, send: Send) -> None:
        if scope.get('type') != 'lifespan':
            await self._observed(scope, receive, send)
            return
        initial = await receive()
        if self._used or initial.get('type') != 'lifespan.startup':
            await send({'type':'lifespan.startup.failed','message':'Runtime startup is unavailable.'})
            return
        self._used = True
        stack = AsyncExitStack()
        try:
            self._app = await self._compose(stack)
        except BaseException as error:
            with suppress(Exception):
                async with asyncio.timeout(self._settings.api.resource_close_timeout_seconds):
                    await stack.aclose()
            if not isinstance(error, Exception):
                raise
            await send({'type':'lifespan.startup.failed','message':'Runtime dependencies are unavailable.'})
            return

        first: Mapping[str, object] | None = initial
        async def receive_initial() -> Mapping[str, object]:
            nonlocal first
            if first is not None:
                event, first = first, None
                return event
            return await receive()
        shutdown: Mapping[str, object] | None = None
        async def send_with_cleanup(event: Mapping[str, object]) -> None:
            nonlocal shutdown
            if event.get('type') in ('lifespan.shutdown.complete', 'lifespan.shutdown.failed'):
                shutdown = event
            else:
                await send(event)
        try:
            await self._app(scope, receive_initial, send_with_cleanup)
        finally:
            self._app = None
            shutdown = await self._close_after_lifespan(stack, shutdown)
        if shutdown is not None:
            await send(shutdown)


def create_from_environment() -> ManagedRuntime:
    return ManagedRuntime(os.environ)


@dataclass(frozen=True)
class _RuntimeReadiness:
    settings: RuntimeSettings
    policy: AuthPolicy
    pool: Pool
    jobs: PostgresJobs
    job_updates: PostgresJobUpdates
    retention: PostgresRetention | None
    management: PostgresWorkspaceManagement
    sources: PostgresSources
    preview_leases: PreviewLeases
    resumable_uploads: bool
    follow_up_available: bool
    audit_enabled: bool
    catalog_access: CatalogAccess | None
    runtime_management: RuntimeManagement | None
    rate_store: PostgresAuthAdmission
    rate_policy: RatePolicy

    async def _dependency_failure(self) -> str | None:
        reason: str | None = "jobs_schema"
        try:
            reason = 'jobs_schema'
            if not await self.jobs.is_ready():
                return "jobs_schema"
            reason = 'polling_retention'
            if not await self.job_updates.ready() or (self.retention is not None and not await self.retention.is_ready()):
                return "polling_retention"
            reason = 'workspace_management'
            if not await self.management.is_ready():
                return "workspace_management"
            reason = 'upload_staging'
            if await upload_profile(self.sources, self.preview_leases) != self.resumable_uploads:
                return "upload_staging"
            reason = 'follow_up'
            if self.follow_up_available and not await self.jobs.follow_up_ready():
                return "follow_up"
            reason = 'audit'
            if self.audit_enabled and not await audit_is_ready(self.pool):
                return "audit"
            reason = 'catalog'
            if self.catalog_access is not None and not await self.catalog_access.is_ready():
                return "catalog"
            reason = 'runtime_operations'
            if self.runtime_management is not None and not await self.runtime_management.is_ready():
                return "runtime_operations"
            return None
        except Exception as error:
            return "timeout" if isinstance(error, TimeoutError) else reason

    async def clock(self) -> bool:
        reason: str | None = "jobs_schema"
        try:
            async with asyncio.timeout(min(2, self.settings.api.backend_timeout_seconds)):
                reason = await self._dependency_failure()
                if reason == "runtime_operations":
                    return False
                if reason is None:
                    reason = "admission"
                    if await self.rate_store.is_ready(self.rate_policy):
                        reason = "clock_bound"
                        if await probe_clock(self.pool, max_skew_seconds=self.policy.clock_skew_seconds,
                                             timeout_seconds=min(2, self.settings.api.backend_timeout_seconds)):
                            return True
        except TimeoutError:
            reason = "timeout"
        except Exception:
            pass
        _LOG.warning("hosted_readiness_unavailable reason=%s", reason)
        return False
def _require_selector_loop() -> None:
    if sys.platform == 'win32':
        if isinstance(asyncio.get_running_loop(), asyncio.ProactorEventLoop):
            raise RuntimeError('Windows runtime requires a selector loop')

