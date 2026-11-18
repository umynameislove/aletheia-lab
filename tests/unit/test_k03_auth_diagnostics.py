"""Fixed auth-stage observations keep wire/session behavior and secrets unchanged."""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from pydantic import SecretStr
from test_k03_login_coordinator import POLICY, _cookie, _query, _system

from aletheia_lab.hosted_auth import _diagnostics
from aletheia_lab.hosted_auth.callback_http import CallbackApplication
from aletheia_lab.hosted_auth.oidc import OidcUnavailable
from aletheia_lab.hosted_auth.repositories import RepositoryUnavailable
from aletheia_lab.hosted_auth.start_http import LoginStartApplication

_CANARY = "synthetic-private-secret-cookie-state-code-subject"
_LOGGER = "aletheia.auth.diagnostics"


async def _fixture(tmp_path):
    flow, _, _ = _system(tmp_path)
    start = await flow.begin(None)
    completed = await flow.complete(_cookie(start), _query(start))
    begun = SimpleNamespace(authorization_url=SecretStr("https://identity.example.invalid/authorize"),
                            browser_credential=SecretStr("b" * 43),
                            expires_at=datetime.now(UTC) + timedelta(seconds=300))
    return begun, completed


async def _wire(route, coordinator, clock, *, timeout=1, messages=None):
    messages = [] if messages is None else messages

    async def fallback(scope, receive, send):
        raise AssertionError("auth route must not reach fallback")

    async def receive():
        raise AssertionError("auth GET must not read a body")

    async def send(message):
        messages.append(message)

    app = (LoginStartApplication(fallback, coordinator, maximum_cookie_bytes=4096,
                                 timeout_seconds=timeout, clock_check=clock) if route == "start" else
           CallbackApplication(fallback, coordinator, POLICY, maximum_cookie_bytes=4096,
                               timeout_seconds=timeout, clock_check=clock))
    await app({"type": "http", "path": "/v1/auth/" + route, "method": "GET", "scheme": "https",
               "query_string": b"" if route == "start" else ("code=" + _CANARY).encode(),
               "headers": [(b"sec-fetch-site", b"same-origin"), (b"cookie", _CANARY.encode())]}, receive, send)
    return messages


def _events(caplog):
    records = [record for record in caplog.records if record.name == _LOGGER]
    for record in records:
        assert record.getMessage() == "auth_stage_failed" and record.args == ()
        assert record.exc_info is record.stack_info is None
        assert type(record.elapsed_ms) is int and record.elapsed_ms >= 0
        assert _CANARY not in str(record.__dict__)
    assert _CANARY not in caplog.text
    return [(record.auth_route, record.auth_stage, record.auth_outcome) for record in records]


def _failure_wire(messages, route):
    headers = messages[0]["headers"]
    assert not any(name in (b"set-cookie", b"content-type") for name, _ in headers)
    assert (b"cache-control", b"no-store") in headers
    if route == "start":
        assert messages[0]["status"] == 503
        assert messages[1]["body"] == b"Authentication request could not be completed."
        assert not any(name == b"location" for name, _ in headers)
    else:
        assert messages[0]["status"] == 303 and messages[1]["body"] == b""
        assert (b"location", b"/#/signed-out?reason=idp_unavailable") in headers


@pytest.mark.parametrize("route", ["start", "callback"])
@pytest.mark.parametrize("stage", ["readiness_before", "coordinator", "readiness_after"])
def test_fixed_pre_coordinator_post_failure_stage_preserves_wire(tmp_path, caplog, route, stage):
    caplog.set_level(logging.WARNING, logger=_LOGGER)

    async def scenario():
        begun, completed = await _fixture(tmp_path)
        calls, checks = [], iter([False] if stage == "readiness_before" else [True, False])

        async def clock():
            return next(checks)

        async def run(*args, **kwargs):
            calls.append("coordinator")
            if stage == "coordinator":
                raise TimeoutError(_CANARY)
            return begun if route == "start" else completed

        coordinator = SimpleNamespace(begin=run, complete=run)
        messages = await _wire(route, coordinator, clock)
        _failure_wire(messages, route)
        assert len(calls) == (0 if stage == "readiness_before" else 1)
        assert _events(caplog) == [(route, stage, "unavailable")]

    asyncio.run(scenario())


@pytest.mark.parametrize("stage", ["begin.oidc", "begin.intent", "callback.claim",
                                   "callback.exchange", "callback.identity", "callback.commit"])
def test_coordinator_reports_only_the_failed_fixed_substage(tmp_path, monkeypatch, caplog, stage):
    caplog.set_level(logging.WARNING, logger=_LOGGER)

    async def scenario():
        flow, store, oidc = _system(tmp_path)
        start = await flow.begin(None) if stage.startswith("callback.") else None

        async def unavailable(*args, **kwargs):
            raise TimeoutError(_CANARY)

        targets = {
            "begin.oidc": (oidc, "authorization_url"), "begin.intent": (store, "begin_intent"),
            "callback.claim": (store, "claim_intent"), "callback.exchange": (oidc, "exchange"),
            "callback.identity": (flow._accounts, "resolve"), "callback.commit": (store, "commit_login"),
        }
        target, method = targets[stage]
        monkeypatch.setattr(target, method, unavailable)
        with pytest.raises(TimeoutError):
            if start is None:
                await flow.begin(None)
            else:
                await flow.complete(_cookie(start), _query(start))
        assert store.commit_effects == len(store.sessions) == 0
        assert _events(caplog) == [("start" if start is None else "callback", stage, "unavailable")]

    asyncio.run(scenario())


@pytest.mark.parametrize("route", ["start", "callback"])
def test_outer_adapter_deadline_logs_interrupted_stage_without_provider_outage_claim(caplog, route):
    caplog.set_level(logging.WARNING, logger=_LOGGER)

    async def scenario():
        async def clock():
            return True

        async def never(*args, **kwargs):
            await asyncio.Event().wait()

        _failure_wire(await _wire(route, SimpleNamespace(begin=never, complete=never), clock, timeout=0.01), route)
        assert _events(caplog) == [(route, "coordinator", "interrupted")]

    asyncio.run(scenario())


@pytest.mark.parametrize("route", ["start", "callback"])
def test_external_cancellation_is_rethrown_unchanged_without_a_wire_response(caplog, route):
    caplog.set_level(logging.WARNING, logger=_LOGGER)

    async def scenario():
        error, messages = asyncio.CancelledError(_CANARY), []

        async def clock():
            return True

        async def interrupted(*args, **kwargs):
            raise error

        with pytest.raises(asyncio.CancelledError) as caught:
            await _wire(route, SimpleNamespace(begin=interrupted, complete=interrupted), clock, messages=messages)
        assert caught.value is error and messages == []
        assert _events(caplog) == [(route, "coordinator", "interrupted")]

    asyncio.run(scenario())


@pytest.mark.parametrize("route", ["start", "callback"])
def test_healthy_routes_emit_no_failure_diagnostics(tmp_path, caplog, route):
    caplog.set_level(logging.WARNING, logger=_LOGGER)

    async def scenario():
        begun, completed = await _fixture(tmp_path)

        async def clock():
            return True

        async def run(*args, **kwargs):
            return begun if route == "start" else completed

        messages = await _wire(route, SimpleNamespace(begin=run, complete=run), clock)
        assert messages[0]["status"] == 303
        assert (b"location", b"/app") in messages[0]["headers"] if route == "callback" else True
        assert _events(caplog) == []

    asyncio.run(scenario())


@pytest.mark.parametrize("error", [TimeoutError(_CANARY), OidcUnavailable(), RepositoryUnavailable()])
def test_timeout_subclass_identity_and_safe_elapsed_record_are_preserved(monkeypatch, caplog, error):
    caplog.set_level(logging.WARNING, logger=_LOGGER)
    times = iter([10.0, 10.125])
    monkeypatch.setattr(_diagnostics, "monotonic", lambda: next(times))
    with pytest.raises(type(error)) as caught, _diagnostics.auth_stage("callback", "callback.exchange"):
        raise error
    assert caught.value is error
    assert _events(caplog) == [("callback", "callback.exchange", "unavailable")]
    assert caplog.records[-1].elapsed_ms == 125


def test_non_timeout_and_broken_logging_never_change_auth_exception(monkeypatch, caplog):
    error = RuntimeError(_CANARY)
    with pytest.raises(RuntimeError) as caught, _diagnostics.auth_stage("start", "begin.intent"):
        raise error
    assert caught.value is error and _events(caplog) == []

    def broken(*args, **kwargs):
        raise RuntimeError("synthetic logging handler defect")

    monkeypatch.setattr(_diagnostics._LOG, "warning", broken)
    timeout = TimeoutError(_CANARY)
    with pytest.raises(TimeoutError) as caught, _diagnostics.auth_stage("start", "begin.oidc"):
        raise timeout
    assert caught.value is timeout


def test_diagnostic_stage_input_cannot_reflect_arbitrary_values(caplog):
    with pytest.raises(ValueError) as caught, _diagnostics.auth_stage("callback", _CANARY):
        pytest.fail("untrusted stage cannot enter")
    assert _CANARY not in str(caught.value) + repr(caught.value)
    assert _events(caplog) == []


def test_globally_disabled_logging_cannot_replace_or_expose_auth_failure(caplog):
    caplog.set_level(logging.WARNING, logger=_LOGGER)
    previous = logging.root.manager.disable
    error = TimeoutError(_CANARY)
    try:
        logging.disable(logging.CRITICAL)
        with pytest.raises(TimeoutError) as caught, _diagnostics.auth_stage("start", "begin.intent"):
            raise error
        assert caught.value is error
    finally:
        logging.disable(previous)
    assert _events(caplog) == []
