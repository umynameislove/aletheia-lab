"""Project-scoped hosted analysis submits to the existing durable queue."""
from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Protocol, cast

from pydantic import SecretStr

from aletheia_lab.hosted_auth.csrf import CsrfTokens
from aletheia_lab.hosted_contract.job_page import JobReceipt
from aletheia_lab.hosted_contract.project_analysis import AnalysisRequest

from .application import Send, _Response, _validated
from .composition import Application
from .job_http import JobApplication, Jobs
from .postgres_sources import SourceNotFound
from .read_backend import SessionRepository
from .request_boundary import (
    BodyLimits,
    Receive,
    RequestRejected,
    decode_json_model,
    read_json_body,
)
from .settings import RuntimeSettings

_ANALYSES = re.compile(r"/v1/workspaces/([A-Za-z0-9][A-Za-z0-9._:-]{0,127})/projects/(p3-project-[0-9a-f]{64})/analyses")


class AnalysisJobs(Jobs, Protocol):
    async def enqueue_analysis(self, *, credential_digest: SecretStr, workspace_id: str,
                               project_id: str, snapshot_id: str, question: str,
                               request_key: SecretStr) -> JobReceipt: ...


class ProjectAnalysisApplication(JobApplication):
    def __init__(self, fallback: Application, jobs: AnalysisJobs, sessions: SessionRepository,
                 tokens: CsrfTokens, runtime: RuntimeSettings, *, body_limits: BodyLimits,
                 clock_check: Callable[[], Awaitable[bool]]) -> None:
        super().__init__(fallback, jobs, sessions, tokens, runtime, body_limits=body_limits, clock_check=clock_check)
        self._analysis_jobs = jobs

    async def __call__(self, scope: Mapping[str, object], receive: Receive, send: Send) -> None:
        if scope.get("type") != "http" or _ANALYSES.fullmatch(str(scope.get("path", ""))) is None:
            await self._fallback(scope, receive, send)
            return
        await self._guard(scope, receive, send)

    @staticmethod
    def _allow(path: str) -> bytes:
        return b"POST"

    async def _dispatch(self, scope: Mapping[str, object], receive: Receive) -> _Response:
        match = _ANALYSES.fullmatch(str(scope.get("path", "")))
        if match is None:
            raise SourceNotFound()
        if scope.get("method") != "POST":
            raise RequestRejected(405, "method_not_allowed", "This method is not supported.")
        if scope.get("query_string", b""):
            raise RequestRejected(400, "invalid_request", "Query parameters are not supported.")
        digest = await self._credential(scope)
        headers = cast(Sequence[tuple[bytes, bytes]], scope.get("headers", ()))
        key = self._request_key(headers)
        raw = await read_json_body(receive, headers, self._limits)
        request = decode_json_model(raw, AnalysisRequest, maximum_bytes=self._limits.maximum_bytes,
                                    maximum_depth=self._runtime.api.maximum_json_depth)
        receipt = _validated(await self._analysis_jobs.enqueue_analysis(credential_digest=digest,
            workspace_id=match[1], project_id=match[2], snapshot_id=request.snapshot_id,
            question=request.question, request_key=key), JobReceipt)
        if receipt.workspace_id != match[1]:
            raise ValueError("Invalid project analysis receipt scope.")
        if await self._clock() is not True:
            raise TimeoutError()
        return _Response(202, receipt.model_dump_json().encode())
