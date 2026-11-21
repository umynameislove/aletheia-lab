"""HTTP boundary for evidence review of a saved claim."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Protocol, cast

from pydantic import SecretStr

from aletheia_lab.hosted_auth.csrf import CsrfTokens
from aletheia_lab.hosted_contract.claim_review import (
    AssertedVerdict,
    ClaimReviewReceipt,
    ClaimReviewRequest,
)
from aletheia_lab.hosted_contract.job_page import JobReceipt

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

_IDENTIFIER = r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}"
_REVIEWS = re.compile(
    rf"/v1/workspaces/(?P<workspace>{_IDENTIFIER})/results/(?P<result>p6-result-[0-9a-f]{{64}})"
    rf"/claims/(?P<claim>{_IDENTIFIER})/reviews(?:/(?P<review>{_IDENTIFIER}))?"
)


class ClaimReviewJobs(Jobs, Protocol):
    async def enqueue_claim_review(self, *, credential_digest: SecretStr, workspace_id: str, result_id: str,
                                   claim_id: str, asserted_verdict: AssertedVerdict,
                                   request_key: SecretStr) -> JobReceipt: ...

    async def read_claim_review(self, *, credential_digest: SecretStr, workspace_id: str, result_id: str,
                                claim_id: str, review_id: str) -> ClaimReviewReceipt: ...


class ClaimReviewApplication(JobApplication):
    def __init__(self, fallback: Application, jobs: ClaimReviewJobs, sessions: SessionRepository,
                 tokens: CsrfTokens, runtime: RuntimeSettings, *, body_limits: BodyLimits,
                 clock_check: Callable[[], Awaitable[bool]]) -> None:
        super().__init__(fallback, jobs, sessions, tokens, runtime, body_limits=body_limits, clock_check=clock_check)
        self._review_jobs = jobs

    async def __call__(self, scope: Mapping[str, object], receive: Receive, send: Send) -> None:
        if scope.get("type") != "http" or _REVIEWS.fullmatch(str(scope.get("path", ""))) is None:
            await self._fallback(scope, receive, send)
            return
        await self._guard(scope, receive, send)

    @staticmethod
    def _allow(path: str) -> bytes:
        match = _REVIEWS.fullmatch(path)
        return b"GET" if match is not None and match["review"] is not None else b"POST"

    async def _dispatch(self, scope: Mapping[str, object], receive: Receive) -> _Response:
        path = str(scope.get("path", ""))
        match = _REVIEWS.fullmatch(path)
        if match is None:
            raise SourceNotFound()
        if scope.get("method") != self._allow(path).decode("ascii"):
            raise RequestRejected(405, "method_not_allowed", "This method is not supported.")
        if scope.get("query_string", b""):
            raise RequestRejected(400, "invalid_request", "Query parameters are not supported.")
        digest = await self._credential(scope)
        workspace, result, claim, review = match.groups()
        if review is not None:
            return await self._read_review(digest, workspace, result, claim, review)
        headers = cast(Sequence[tuple[bytes, bytes]], scope.get("headers", ()))
        key = self._request_key(headers)
        raw = await read_json_body(receive, headers, self._limits)
        request = decode_json_model(raw, ClaimReviewRequest, maximum_bytes=self._limits.maximum_bytes,
                                    maximum_depth=self._runtime.api.maximum_json_depth)
        receipt = _validated(await self._review_jobs.enqueue_claim_review(credential_digest=digest,
            workspace_id=workspace, result_id=result, claim_id=claim, request_key=key,
            asserted_verdict=request.asserted_verdict), JobReceipt)
        if receipt.workspace_id != workspace:
            raise ValueError("Invalid claim review receipt scope.")
        if await self._clock() is not True:
            raise TimeoutError()
        return _Response(202, receipt.model_dump_json().encode())

    async def _read_review(self, digest: SecretStr, workspace: str, result: str, claim: str, review: str) -> _Response:
        receipt = _validated(await self._review_jobs.read_claim_review(credential_digest=digest,
            workspace_id=workspace, result_id=result, claim_id=claim, review_id=review), ClaimReviewReceipt)
        binding = receipt.binding
        if (binding.workspace_id, binding.result_id, binding.claim_id, receipt.job_id) != (workspace, result, claim, review):
            raise ValueError("Invalid claim review result scope.")
        body = receipt.model_dump_json().encode()
        if len(body) > self._runtime.api.maximum_response_bytes:
            raise ValueError("Claim review output too large.")
        if await self._clock() is not True:
            raise TimeoutError()
        return _Response(200, body)
