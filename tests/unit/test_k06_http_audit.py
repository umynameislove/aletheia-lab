"""Real HTTP route methods preserve task-local action/request correlation."""
import asyncio
import logging
from types import SimpleNamespace

import pytest

from aletheia_lab.hosted.action_audit import record_action, report_outcome, request_context
from aletheia_lab.hosted.application import _Response
from aletheia_lab.hosted.catalog_http import CatalogApplication, _Delivery
from aletheia_lab.hosted.job_http import JobApplication
from aletheia_lab.hosted.request_boundary import RequestRejected
from aletheia_lab.hosted.source_http import SourceUploadApplication
from aletheia_lab.hosted.workspace_http import WorkspaceApplication


@pytest.mark.parametrize("application", [SourceUploadApplication, JobApplication, WorkspaceApplication, CatalogApplication])
@pytest.mark.parametrize("rejected", [False, True])
def test_route_dispatch_error_mapping_send_and_context_reset_use_same_request(application, rejected, caplog):
    params, output = [], []

    class Connection:
        autocommit = False

        async def execute(self, query, values):
            params.append(values)

    async def dispatch(scope, receive):
        await record_action(Connection(), actor_id="user_a", workspace_id="wa", action="probe.dispatch", effect_id="effect_a")
        if rejected:
            raise RequestRejected(400, "invalid_request", "Synthetic safe rejection.")
        response = _Response(200, b"{}")
        return _Delivery(response) if application is CatalogApplication else response

    async def checkpoint(value):
        pass

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(message):
        output.append(message)

    async def scenario():
        app = object.__new__(application)
        app._runtime = SimpleNamespace(api=SimpleNamespace(backend_timeout_seconds=1, request_timeout_seconds=2,
                                                          maximum_response_bytes=1000))
        app._limits = SimpleNamespace(total_timeout_seconds=1)
        app._upload = app._dispatch = dispatch
        app._checkpoint = checkpoint
        with request_context("req_outer"):
            await app._route({"type": "http", "path": "/synthetic", "method": "GET"}, receive, send)
            report_outcome("outcome_unknown")

    with caplog.at_level(logging.INFO, logger="aletheia.action"):
        asyncio.run(scenario())
    assert output[0]["status"] == (400 if rejected else 200)
    headers = dict(output[0]["headers"])
    request_id = headers[b"x-request-id"].decode("ascii")
    assert request_id.startswith("req_") and params[0][1] == request_id
    assert [(row.action_outcome, row.request_id) for row in caplog.records] == (
        [("rejected", request_id)] if rejected else []) + [("outcome_unknown", "req_outer")]
