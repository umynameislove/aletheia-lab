"""Explicit composition for the experimental hosted upload slice.

The caller owns the API database pool and login coordinator; workers run separately.
"""
from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import datetime

from pydantic import SecretStr

from aletheia_lab.hosted_auth.abuse import AuthAdmission
from aletheia_lab.hosted_auth.admission_http import AuthAdmissionApplication
from aletheia_lab.hosted_auth.boundary import SESSION_COOKIE, read_opaque_cookie
from aletheia_lab.hosted_auth.callback_http import CallbackApplication
from aletheia_lab.hosted_auth.csrf import CsrfTokens
from aletheia_lab.hosted_auth.login import LoginCoordinator
from aletheia_lab.hosted_auth.logout_http import LogoutApplication
from aletheia_lab.hosted_auth.session_http import SessionMutationApplication
from aletheia_lab.hosted_auth.sessions import ServerSessions, credential_digest
from aletheia_lab.hosted_auth.settings import AuthPolicy
from aletheia_lab.hosted_auth.start_http import LoginStartApplication
from aletheia_lab.hosted_contract.session_capabilities import Capabilities, Limits, Session

from .application import AccessDenied
from .authorization import role_allows
from .catalog_access import CatalogAccess
from .catalog_http import CatalogApplication
from .clock_guarded_backend import ClockGuardedBackend
from .composition import Application, ProcessApplication
from .import_failures import ImportFailures
from .imports_http import ImportApplication, LeasedJobs
from .job_http import JobApplication
from .job_updates import PostgresJobUpdates
from .postgres_jobs import PostgresJobs
from .postgres_sources import PostgresSources
from .postgres_workspaces import PostgresWorkspaceAccess
from .preview_leases import PreviewLeases
from .project_access import PostgresProjectAccess
from .project_analysis_http import ProjectAnalysisApplication
from .project_http import ProjectOperationsApplication
from .project_mutations import PostgresProjectMutations
from .project_refresh_http import ProjectRefreshApplication
from .read_backend import (
    ReadOnlyHostedBackend,
    SessionRepository,
    WorkspaceAccessRepository,
    _utcnow,
)
from .request_boundary import BodyLimits
from .retention import PostgresRetention
from .review_repository import PostgresReviews
from .runtime_management import RuntimeManagement
from .runtime_management_http import RuntimeManagementApplication
from .settings import RuntimeSettings
from .source_http import SourceUploadApplication
from .upload_staging import MAX_PART_BYTES
from .upload_staging_http import UploadStagingApplication
from .workspace_http import WorkspaceApplication
from .workspace_management import PostgresWorkspaceManagement, WorkspaceDenied


class UploadReadBackend(ReadOnlyHostedBackend):
    """Advertise only the upload operations composed by this runtime."""

    def __init__(self, sessions: SessionRepository, workspaces: WorkspaceAccessRepository, *,
                 limits: Limits, clock: Callable[[], datetime] = _utcnow,
                 membership_management: PostgresWorkspaceManagement | None = None,
                 catalog_available: bool = False, jobs: PostgresJobs | None = None,
                 catalog_access: CatalogAccess | None = None,
                 resumable_uploads: bool = False, follow_up_available: bool = False,
                 job_updates: PostgresJobUpdates | None = None,
                 retention: PostgresRetention | None = None) -> None:
        super().__init__(sessions, workspaces, limits=limits, clock=clock)
        self._membership_management = membership_management
        self._catalog_available, self._jobs = catalog_available, jobs
        self._catalog_access = catalog_access
        self._resumable_uploads, self._follow_up_available = resumable_uploads, follow_up_available
        self._job_updates, self._retention = job_updates, retention

    async def is_ready(self) -> bool:
        return await super().is_ready() and (
            self._membership_management is None or await self._membership_management.is_ready())

    async def load_session(self, cookie: bytes | None) -> Session:
        session = await super().load_session(cookie)
        if self._membership_management is None:
            return session
        token = read_opaque_cookie(cookie, name=SESSION_COOKIE, maximum_bytes=65536)
        if token is None:
            raise AccessDenied('session_expired')
        data = session.model_dump(mode='python')
        data['can_create_workspace'] = await self.creation_hint(credential_digest(token))
        return Session.model_validate(data, strict=True)

    async def creation_hint(self, digest: SecretStr) -> bool:
        """GET session and renewal use the same current server creation policy."""
        if self._membership_management is None:
            return False
        try:
            allowed = await self._membership_management.can_create_workspace(credential_digest=digest)
        except WorkspaceDenied as error:
            raise AccessDenied('session_expired') from error
        if type(allowed) is not bool:
            raise ValueError('Invalid workspace creation policy response.')
        return allowed

    async def _job_features(self, role: str, features: dict[str, bool]) -> None:
        if self._jobs is not None:
            features["jobs.idempotency"] = await self._jobs.is_ready()
            controls = await self._jobs.controls_ready() and role_allows(role, "analyze")
            features.update({"jobs.cancel": controls, "jobs.retry": controls})
            if getattr(self._jobs, 'execution', False) is True:
                features['jobs.execution_v1'] = await self._jobs.execution_ready()
                features['comparisons.upload_v1'] = features['jobs.execution_v1'] and self._catalog_available
                conflict_ready = getattr(self._jobs, 'conflicts_ready', None)
                features['jobs.conflict_v1'] = conflict_ready is not None and await conflict_ready()
            if getattr(self._jobs, 'analysis_model', '') == 'upload-snapshot-metric-observation/v2':
                features['analysis.metric_observations_v2'] = True

    async def capabilities(self, session: Session, workspace_id: str) -> Capabilities:
        value = await super().capabilities(session, workspace_id)
        data = value.model_dump(mode="python")
        role = value.membership.role
        features = {"mapping.class_unit_v2": True}
        if self._membership_management is not None:
            features["members.app_invitation"] = role_allows(role, "manage_members")
        await self._job_features(role, features)
        if self._job_updates is not None:
            features['jobs.polling_v1'] = await self._job_updates.ready()
        if self._retention is not None:
            features['projects.deletion_status'] = await self._retention.is_ready()
        if self._follow_up_available and self._catalog_available:
            features["projects.follow_up"] = (self._jobs is not None and await self._jobs.follow_up_ready()
                                              and role_allows(role, "analyze"))
            features["projects.refresh"] = features["projects.follow_up"]
        if self._resumable_uploads:
            features.update({'sources.resumable': role_allows(role, 'import'),
                             'sources.preview_lease': role_allows(role, 'import'), 'sources.connector': False})
        actions: list[str] = []
        if self._catalog_available:
            actions.extend(action for action in ("read", "review", "export", "delete") if role_allows(role, action))
            features.update({"catalog.read": True, "projects.logical_delete": role_allows(role, "delete"),
                             "reviews.annotations": role_allows(role, "review"),
                             "exports.download": role_allows(role, "export"), "events.polling": True})
            data["supported_export_formats"] = ["json", "markdown"] if role_allows(role, "export") else []
            if self._catalog_access is not None:
                features['catalog.paged_read'] = await self._catalog_access.paged_ready()
                if role_allows(role, 'export') and await self._catalog_access.exports.pdf_ready():
                    data['supported_export_formats'].append('pdf')
        if role_allows(role, "import"):
            actions.extend(["import", "analyze"])
            data.update(analysis_backends=["local_evidence"], supported_source_types=["zip_bundle"])
        data.update(allowed_actions=actions, features=features)
        return Capabilities.model_validate(data, strict=True)


def compose_upload_runtime(*, settings: RuntimeSettings, sessions: ServerSessions,
                           workspaces: PostgresWorkspaceAccess, sources: PostgresSources,
                           jobs: PostgresJobs, tokens: CsrfTokens,
                           coordinator: LoginCoordinator, auth_policy: AuthPolicy, limits: Limits,
                           clock_check: Callable[[], Awaitable[bool]], admission: AuthAdmission,
                           admission_subject_key: SecretStr,
                           idp_logout_url: Callable[[], Awaitable[str]] | None = None,
                           membership_management: PostgresWorkspaceManagement | None = None,
                           catalog_access: CatalogAccess | None = None,
                           project_access: PostgresProjectAccess | None = None,
                           reviews: PostgresReviews | None = None,
                           project_mutations: PostgresProjectMutations | None = None,
                           resumable_uploads: bool = False, follow_up_available: bool = False,
                           runtime_management: RuntimeManagement | None = None,
                           job_updates: PostgresJobUpdates | None = None,
                           retention: PostgresRetention | None = None) -> ProcessApplication:
    """Compose existing boundaries; no resources or sockets open at import time."""
    if follow_up_available and catalog_access is None:
        raise ValueError('Follow-up routes require the canonical catalog.')
    # Base64 and mapping metadata must both fit the worker's input budget.
    values = limits.model_dump()
    values["upload_bundle_bytes"] = min(limits.upload_bundle_bytes, sources.maximum_upload_bytes,
                                        200 * 1024 * 1024 if resumable_uploads else 10 * 1024 * 1024)
    values["question_chars"] = min(limits.question_chars, 4000)
    effective_limits = Limits.model_validate(values, strict=True)
    reader = UploadReadBackend(sessions, workspaces, limits=effective_limits,
        membership_management=membership_management, catalog_available=catalog_access is not None,
        catalog_access=catalog_access, jobs=jobs,
        resumable_uploads=resumable_uploads, follow_up_available=follow_up_available,
        job_updates=job_updates, retention=retention)
    backend = ClockGuardedBackend(reader, clock_check)
    api = settings.api
    json_limits = BodyLimits(api.maximum_json_bytes, api.body_idle_timeout_seconds,
                             api.body_total_timeout_seconds, api.maximum_body_events)
    # shortcut: base64 limits ZIP size; use streaming before raising this ceiling.
    upload_limits = BodyLimits(effective_limits.upload_bundle_bytes, api.body_idle_timeout_seconds,
                               api.body_total_timeout_seconds, api.maximum_body_events)

    @asynccontextmanager
    async def resources() -> AsyncIterator[ClockGuardedBackend]:
        yield backend

    def routes(fallback: Application) -> Application:
        app: Application = CallbackApplication(fallback, coordinator, auth_policy,
            maximum_cookie_bytes=api.maximum_cookie_bytes, timeout_seconds=api.backend_timeout_seconds,
            clock_check=clock_check)
        app = LoginStartApplication(app, coordinator, maximum_cookie_bytes=api.maximum_cookie_bytes,
                                    timeout_seconds=api.backend_timeout_seconds, clock_check=clock_check,
                                    allow_direct_switch=idp_logout_url is None)
        app = LogoutApplication(app, sessions, tokens, settings, body_limits=json_limits, clock_check=clock_check,
                                idp_logout_url=idp_logout_url)
        app = SessionMutationApplication(app, sessions, tokens, settings,
                                         body_limits=json_limits, clock_check=clock_check,
                                         creation_hint=reader.creation_hint)
        if resumable_uploads:
            leases = PreviewLeases(sources)
            guarded_jobs = LeasedJobs(jobs, leases, ImportFailures(sources._pool))
            part_limits = BodyLimits(MAX_PART_BYTES, api.body_idle_timeout_seconds,
                                     api.body_total_timeout_seconds, api.maximum_body_events)
            app = UploadStagingApplication(app, sources, sessions, tokens, settings,
                body_limits=json_limits, part_limits=part_limits, clock_check=clock_check)
            app = ImportApplication(app, guarded_jobs, sessions, tokens, settings, leases=leases,
                body_limits=json_limits, clock_check=clock_check)
            app = JobApplication(app, guarded_jobs, sessions, tokens, settings,
                body_limits=json_limits, clock_check=clock_check, updates=job_updates)
        else:
            app = SourceUploadApplication(app, sources, sessions, tokens, settings,
                                          body_limits=upload_limits, clock_check=clock_check)
            app = JobApplication(app, jobs, sessions, tokens, settings, body_limits=json_limits,
                                 clock_check=clock_check, updates=job_updates)
        if membership_management is not None:
            app = WorkspaceApplication(app, membership_management, sessions, tokens, settings,
                                       body_limits=json_limits, clock_check=clock_check)
        if catalog_access is not None:
            if project_access is None or reviews is None or project_mutations is None:
                raise ValueError('Canonical routes require their policy repositories.')
            app = CatalogApplication(app, catalog_access, project_access, reviews, sessions, tokens, settings,
                body_limits=json_limits, clock_check=clock_check, project_mutations=project_mutations,
                retention=retention)
        if runtime_management is not None:
            app = RuntimeManagementApplication(app, runtime_management, sessions, tokens, settings,
                body_limits=json_limits, clock_check=clock_check)
        if follow_up_available:
            app = ProjectRefreshApplication(app, jobs, sessions, tokens, settings,
                body_limits=json_limits, clock_check=clock_check)
            app = ProjectAnalysisApplication(app, jobs, sessions, tokens, settings,
                body_limits=json_limits, clock_check=clock_check)
            app = ProjectOperationsApplication(app, jobs, sessions, tokens, settings,
                body_limits=json_limits, clock_check=clock_check)
        return AuthAdmissionApplication(app, admission, subject_key=admission_subject_key,
                                         timeout_seconds=api.backend_timeout_seconds)

    return ProcessApplication(settings, resources, routes=routes)
